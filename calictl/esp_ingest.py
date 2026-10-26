"""``calictl esp-ingest``: fill the gaps of buspi's own InfluxDB history from the ESP satellite's SD log.

The satellite logs every unit frame to its microSD card, stamped at log time (``R_FW_SD_LOG``,
``R_FW_TIME``), and serves it page by page at ``GET /api/log?since=<seq>&max=<n>``
(``R_FW_LOG_API``). This command, run by a systemd timer every 5 min, is NOT BLE (``serve`` stays the
single BLE owner):

1. pull pages from the cursor (``~/.cache/calictl/esp_cursor.json``) until ``more`` is false;
2. stamp: a record with ``ts`` is written at exactly that time; one without is back-stamped from the
   first stamped record of the same ``boot`` that follows it (``ts_anchor - (up_anchor - up)``, fixed
   on the card, so a re-run gives the same time); a boot with no later stamped record is skipped and
   counted, except the CURRENT boot, where the cursor holds (an anchor may still come);
3. replay the frames in seq order through serve's own ``interpret_frames`` + ``guard_water`` (decode,
   lighting latch, interpret, firmware corrections, the water hold and ramp debounce), keeping the
   replay state (decoded cache + water hold) next to the cursor;
4. gap fill (owner decision 1A, buspi's live data wins): one Flux query per batch lists buspi's own
   points (the series WITHOUT a ``source`` tag) per function; an ESP point is written only when buspi
   has none for that function within ``WINDOW_MS`` (inclusive); of those, only the last per function
   and 30 s slot (``SLOT_MS``, buspi's poll) is written, once a later slot's record arrives — or at
   once when its alert codes / ignition change (:meth:`Replay.thin`);
5. write with ``influx.points_for`` at the record's own time, tag ``source=esp``; the cursor (and the
   replay state) move only after the write succeeded. InfluxDB dedups same series + time, so a re-run
   writes identical points.

The satellite's own diagnostics (``R_FW_DIAG_LOG``: ``diag``, ``evt``, ``perf``, ``log``, ``ux``) ride
in the same stream; only the ESP has them, so they skip replay and gap fill and are written as they are
at the time from step 2, one measurement per ``ev`` (:data:`DIAG`, :func:`diag_point`). The
satellite's own BLE sensor readings (``ext``: Govee, Victron) are timed the same way and become the
rows buspi's own readers write (:data:`EXT`, :func:`ext_point`), gap-filled against them.

``--dry-run`` does steps 1-4, writes nothing, keeps the cursor and prints a per-function summary;
``--verify`` checks what is in InfluxDB (see :func:`verify`). Config: the ``serve`` env
(``INFLUXDB_TOKEN``, ``INFLUX_URL``/``ORG``/``BUCKET``). Stdlib at import; ``influxdb_client`` lazily.

.. req:: Fill buspi's InfluxDB gaps from the ESP's SD log, never overwriting buspi's own data
   :id: R_ESP_INGEST_GAPFILL
   :status: implemented
   :tags: esp32, influx, ingest

   ``calictl esp-ingest`` shall pull the ESP's log from its cursor, time every record from the card
   alone (``ts`` as is; an unstamped record back-stamped from the next stamped record of its boot;
   the current boot's unanchored records held, an older boot's skipped and counted), replay the
   frames in seq order through serve's decode, interpret and water guard, write a point (tag
   ``source=esp``, the record's time) only where buspi has no point of that function within ±90 s
   — of those only the last record per function and 30 s slot (``floor(ts / 30 s)``), plus every
   record whose ``*_code`` fields or ``ignition_on`` differ from the function's previous point —
   keep the slot's pending point and the last written codes in the cursor, advance its cursor only
   after a successful write, and log ``{pulled, written, skipped_covered, thinned,
   skipped_no_time}``. A re-run writes identical points.

.. req:: Write the ESP's diagnostics records to InfluxDB as they are
   :id: R_ESP_INGEST_DIAG
   :status: implemented
   :tags: esp32, influx, ingest

   ``calictl esp-ingest`` shall write every timed ``diag``/``perf``/``evt``/``log`` record of the
   ESP's log as one point at the record's time — ``esp_diag`` (numeric fields, ``stack_min``
   flattened to ``stack_min_<task>``), ``esp_perf`` (tags ``route`` or ``char`` + ``op``),
   ``esp_evt`` (tag ``kind``; numeric and string fields), ``esp_log`` (tags ``lvl``, ``tag``; string
   ``msg``; numeric ``dropped``) — with no gap-fill rule, under the frames' timing and cursor rules,
   and count them per measurement in its summary (``diag_points``). A ``ux`` record (the web UI's
   usage beacon, ``R_UX_TELEMETRY``) shall become the ``ui_ux`` point calictl writes for its own
   ``POST /api/ux`` — same tags and field types — tagged ``source=esp``.

.. req:: ESP sensor rows
   :id: R_ESP_INGEST_EXT
   :status: open
   :tags: esp32, influx, ingest

   An ``ext`` record becomes one point in the measurement of its type (``govee``, ``victron_mppt``,
   ``victron_battery``), tags ``device=<alias>`` and ``source=esp``, floats only, written only when
   buspi's own series (no ``source`` tag) has no point within WINDOW_MS; unknown types are dropped.
"""

from __future__ import annotations

import bisect
import http.client
import json
import os
import time
import urllib.request
from datetime import UTC, datetime

from . import influx, overrides, protocol, semantics, serve
from . import log as _log
from .freshness import WATER_SETTLE_S

log = _log.get(__name__)

WINDOW_MS = 90_000  # 3x buspi's 30 s poll: a buspi point this close covers an ESP record
SLOT_MS = 30_000  # buspi's poll: a gap keeps the LAST ESP record per function and slot (Replay.thin)
PAGE = 500  # records per /api/log request (~64 KB): the ESP serves one connection at a time
FRAME_EVS = ("notify", "read", "snap")
DEFAULT_CURSOR = os.path.expanduser("~/.cache/calictl/esp_cursor.json")
# --verify: the side-by-side fields (ESP replay vs buspi's point)
# The ESP's own records (R_FW_DIAG_LOG): ev -> (measurement, tag keys). Add a row for a new ev.
DIAG = {
    "diag": ("esp_diag", ()),
    "perf": ("esp_perf", ("route", "char", "op")),
    "evt": ("esp_evt", ("kind",)),
    "log": ("esp_log", ("lvl", "tag")),
    # the web UI's usage beacons (R_UX_TELEMETRY): calictl's own ui_ux layout + source=esp (ux_point)
    "ux": ("ui_ux", ("type", "ua_class", "origin", "standalone", "screen", "fn", "control")),
}
UX_BATCH = ("sid", "ua_class", "standalone", "origin")  # a ux record's beacon-wide keys; the rest = event
# The satellite's own BLE sensors (ev "ext"): type -> (measurement, the fields buspi's own readers
# write, all floats). The first field is the presence probe of the gap query (every buspi point has it).
EXT = {
    "govee": ("govee", ("temp_c", "humidity", "battery")),
    "victron_mppt": (
        "victron_mppt",
        (
            "battery_voltage",
            "battery_current",
            "charge_state",
            "error_code",
            "load_current",
            "pv_power",
            "yield_today_kwh",
        ),
    ),
    "victron_battery": ("victron_battery", ("battery_voltage", "temperature_c")),
}
STAMP = ("seq", "boot", "up", "ts", "tsrc", "ev")  # the log's own keys, never fields
COMPARE = {
    "water": ("fresh_liters", "waste_liters"),
    "energy": ("soc1_level", "soc2_level", "batt2_current", "dcdc_current", "shore_current"),
}


# ---- 1. the ESP ---------------------------------------------------------------------------------


def http_fetch(url, timeout=30.0, tries=3, backoff_s=2.0, sleep=time.sleep):
    """A ``fetch(since, max) -> (records, trailer)`` over ``GET <url>/api/log``.

    A satellite on WiFi drops a page now and then (a reset connection, a timeout, a cut body), so
    each page is tried up to ``tries`` times, ``backoff_s * attempt`` apart; the last error is raised.

    :param url: the ESP's base URL.
    :param timeout: per-request timeout, s.
    :param tries: attempts per page (>= 1).
    :param backoff_s: the wait before attempt n+1 is ``backoff_s * n``.
    :param sleep: injectable for tests.
    :returns: the ``fetch`` callable :func:`ingest` takes.
    """

    def fetch(since, max_):
        for attempt in range(1, tries + 1):
            try:
                with urllib.request.urlopen(
                    "%s/api/log?since=%d&max=%d" % (url.rstrip("/"), since, max_), timeout=timeout
                ) as r:
                    body = r.read()
                break
            # URLError, timeouts, RemoteDisconnected (all OSError); IncompleteRead (HTTPException)
            except (OSError, http.client.HTTPException) as e:
                if attempt == tries:
                    raise
                log.warning(
                    "esp-ingest: page since=%d failed (%s), retry %d/%d", since, e, attempt, tries - 1
                )
                sleep(backoff_s * attempt)
        lines = [json.loads(ln) for ln in body.decode().splitlines() if ln.strip()]
        if not lines or "next" not in lines[-1]:
            raise ValueError("/api/log page without its trailer")
        return lines[:-1], lines[-1]

    return fetch


# ---- 2. time -------------------------------------------------------------------------------------


def resolve(buf, current_boot, final):
    """Time the buffered records (seq order).

    :param buf: records not yet timed.
    :param current_boot: the trailer's ``boot`` (the ESP's running boot).
    :param final: no more pages follow.
    :returns: ``(done, rest)`` — ``done`` = ``[(record, ts_ms | None)]``, the timed prefix (None =
        never stampable: skip and count); ``rest`` = the records still waiting for an anchor (the
        next page, or — when final — the current boot's held tail).
    """
    anchor: dict = {}  # boot -> (ts, up) of the nearest stamped record after the one being looked at
    later_boots: set = set()  # boots seen after the record (its own boot then ended)
    timed: list = [None] * len(buf)
    for i in range(len(buf) - 1, -1, -1):
        r = buf[i]
        b = r.get("boot")
        if r.get("ts") is not None:
            anchor[b] = (r["ts"], r["up"])
            timed[i] = r["ts"]
        elif b in anchor:
            timed[i] = anchor[b][0] - (anchor[b][1] - r["up"])
        elif later_boots - {b} or (final and b != current_boot):
            timed[i] = False  # its boot is over without an anchor: never stampable
        later_boots.add(b)
    n = next((i for i, t in enumerate(timed) if t is None), len(buf))
    return [(buf[i], timed[i] if timed[i] is not False else None) for i in range(n)], buf[n:]


# ---- 3. replay -----------------------------------------------------------------------------------


class Replay:
    """serve's stateful post-processing over the ESP's frames: the decoded cache (lighting latch), the
    last ``general`` frame (energy's firmware correction) and the water hold (``serve.guard_water``'s
    ``_water_*`` holder). JSON-able via :meth:`dump`, so it rides in the cursor file."""

    def __init__(self, funcs, state=None, settle=WATER_SETTLE_S):
        s = state or {}
        self.funcs = funcs
        self.settle = settle
        self._last = s.get("last", {})
        self._general = bytes.fromhex(s["general"]) if s.get("general") else None
        self._water_good = s.get("water_good")
        p = s.get("water_pending")
        self._water_pending = ((tuple(p[0]), p[1])) if p else None
        self._water_measured_at = s.get("water_measured_at")
        self._water_stale_since = s.get("water_stale_since")
        t = s.get("thin") or {}
        self._pending = {f: tuple(v) for f, v in t.get("pending", {}).items()}  # fn -> (ts, interp)
        self._codes = t.get("codes", {})  # fn -> state codes of its last written point
        self.thinned = 0  # this run's gap records dropped for a later one of the same slot

    def dump(self):
        p = self._water_pending
        return {
            "last": self._last,
            "general": self._general.hex() if self._general else None,
            "water_good": self._water_good,
            "water_pending": [list(p[0]), p[1]] if p else None,
            "water_measured_at": self._water_measured_at,
            "water_stale_since": self._water_stale_since,
            "thin": {"pending": {f: list(v) for f, v in self._pending.items()}, "codes": self._codes},
        }

    def close(self, ts_ms):
        """``[(fn, ts, interp)]`` to write: the pending points whose 30 s slot ended before ``ts_ms``."""
        slot = ts_ms // SLOT_MS
        done = sorted(f for f, (t, _) in self._pending.items() if t // SLOT_MS < slot)
        return [self._emit(f, *self._pending.pop(f)) for f in done]

    def thin(self, fn, ts_ms, interp):
        """A gap record of ``fn`` -> ``[(fn, ts, interp)]`` to write now. It becomes the slot's pending
        point (an older pending one of the same slot is dropped); a record whose state codes
        (:func:`state_codes`) differ from ``fn``'s previous point (the pending one, else the last
        written) is written at once, after the pending point it follows."""
        out = self.close(ts_ms)
        prev = state_codes(self._pending[fn][1]) if fn in self._pending else self._codes.get(fn)
        if prev is not None and state_codes(interp) != prev:
            if fn in self._pending:
                out.append(self._emit(fn, *self._pending.pop(fn)))
            out.append(self._emit(fn, ts_ms, interp))
            return out
        self.thinned += fn in self._pending
        self._pending[fn] = (ts_ms, interp)
        return out

    def _emit(self, fn, ts_ms, interp):
        self._codes[fn] = state_codes(interp)
        return fn, ts_ms, interp

    def process(self, rec, ts_ms):
        """One timed record -> ``{function: interpreted}`` to store at ``ts_ms`` (installed functions
        only, like serve). A water level waiting to settle is re-checked at every record's time: the
        ESP sees the ramp's notifies, not 30 s polls, so "unchanged for 5 s" = no new water frame."""
        now = ts_ms / 1000.0
        out = {}
        fn = rec.get("fn")
        if rec.get("ev") in FRAME_EVS and fn in self.funcs and rec.get("hex") is not None:
            data = bytes.fromhex(rec["hex"])
            raw = {fn: data}
            if fn == "energy" and self._general is not None:
                raw["general"] = self._general
            if fn == "general":
                self._general = data
            states, self._last = serve.interpret_frames(self.funcs, raw, self._last)
            out = {fn: states[fn]}
            if fn == "water":
                serve.guard_water(self, out, now, now, self.settle)
        if fn != "water" and self._water_pending is not None and "water" in self._last:
            w = {"water": semantics.interpret("water", self._last["water"])}
            if serve.guard_water(self, w, now, now, self.settle):
                out.update(w)
        return {f: i for f, i in out.items() if i.get("installed")}


def state_codes(interp):
    """The fields whose change is always written: the ``*_code`` alert enums and ``ignition_on``."""
    return {
        k: v for k, v in influx.numeric_fields(interp).items() if k.endswith("_code") or k == "ignition_on"
    }


def diag_fields(rec):
    """``(tags, fields)`` of one diagnostics record: tag keys as strings, a nested object flattened
    to ``<key>_<sub>``, numbers + booleans as floats (one type per field, bool -> 1.0/0.0), the other
    strings as string fields, ``null`` dropped."""
    _, tag_keys = DIAG[rec["ev"]]
    tags: dict = {}
    fields: dict = {}
    flat: dict = {}
    for k, v in rec.items():
        if k in STAMP:
            continue
        if isinstance(v, dict):
            flat.update(("%s_%s" % (k, sub), x) for sub, x in v.items())
        else:
            flat[k] = v
    for k, v in flat.items():
        if v is None:
            continue
        if k in tag_keys:
            tags[k] = str(v)
        elif isinstance(v, (bool, int, float)):
            fields[k] = float(v)
        elif isinstance(v, str):
            fields[k] = v
    return tags, fields


def diag_point(rec, ts_ms):
    """One InfluxDB point for a diagnostics record at ``ts_ms`` (None without any field)."""
    from influxdb_client import Point, WritePrecision

    if rec["ev"] == "ux":
        return ux_point(rec, ts_ms)
    tags, fields = diag_fields(rec)
    if not fields:
        return None
    p = Point(DIAG[rec["ev"]][0])
    for k, v in tags.items():
        p.tag(k, v)
    for k, v in fields.items():
        p.field(k, v)
    return p.time(int(ts_ms), WritePrecision.MS)


def ux_point(rec, ts_ms):
    """A ``ux`` record as the ``ui_ux`` point calictl writes for its own ``POST /api/ux``
    (:func:`calictl.influx.ux_points`: same tags, int fields), plus tag ``source=esp`` — buspi's own
    points carry no ``source`` — at ``ts_ms`` plus ``seq`` nanoseconds: a beacon's events share the
    millisecond on the card, and InfluxDB keeps one point per series + time (stable on a re-run)."""
    from influxdb_client import WritePrecision

    batch = {k: rec[k] for k in UX_BATCH}
    event = {k: v for k, v in rec.items() if k not in STAMP and k not in UX_BATCH}
    (p,) = influx.ux_points({**batch, "events": [event]})
    return p.tag("source", "esp").time(int(ts_ms) * 1_000_000 + rec["seq"] % 1_000_000, WritePrecision.NS)


def ext_point(rec, ts_ms):
    """One sensor point for an ``ext`` record at ``ts_ms`` (``R_ESP_INGEST_EXT``): the :data:`EXT`
    fields present as floats (``online 1.0`` for Govee, like buspi's reader), tags ``device`` +
    ``source=esp``. None for an unknown type, a non-string ``dev`` or no field."""
    from influxdb_client import Point, WritePrecision

    if not isinstance(rec.get("type"), str) or not isinstance(rec.get("dev"), str):
        return None  # a JSON array/object type would be unhashable for the lookup
    row = EXT.get(rec["type"])
    if row is None:
        return None
    m, names = row
    fields = {
        f: float(rec[f])
        for f in names
        if isinstance(rec.get(f), (int, float)) and not isinstance(rec[f], bool)
    }
    if not fields:
        return None
    if m == "govee":
        fields["online"] = 1.0
    p = Point(m).tag("device", rec["dev"]).tag("source", "esp")
    for k, v in fields.items():
        p.field(k, v)
    return p.time(int(ts_ms), WritePrecision.MS)


# ---- 4. gap fill ---------------------------------------------------------------------------------


def covered(times, ts_ms, window=WINDOW_MS):
    """True when the sorted epoch-ms list ``times`` holds a point within ``±window`` of ``ts_ms``."""
    i = bisect.bisect_left(times, ts_ms - window)
    return i < len(times) and times[i] <= ts_ms + window


def _rfc3339(ms):
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def flux_times(bucket, start_ms, stop_ms, source=None, measurements=("camper",)):
    """The Flux listing one row per point of ``measurements`` in [start, stop] — buspi's own series
    (no ``source`` tag) or ``source``'s. ``camper`` by its ``installed`` field (every stored point has
    it: serve and the ingest store installed functions only), an :data:`EXT` measurement by its first
    field."""
    probe = {"camper": "installed", **{m: f[0] for m, f in EXT.values()}}
    meas = " or ".join('(r._measurement == "%s" and r._field == "%s")' % (m, probe[m]) for m in measurements)
    src = 'r.source == "%s"' % source if source else "not exists r.source"
    return (
        'from(bucket:"%s") |> range(start:%s, stop:%s) '
        "|> filter(fn:(r) => (%s) and %s) "
        '|> keep(columns:["_time","_measurement","function"])'
        % (bucket, _rfc3339(start_ms), _rfc3339(stop_ms + 1), meas, src)
    )


class Influx:
    """The real InfluxDB side (lazy ``influxdb_client``), from the serve env."""

    def __init__(self):
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        token = os.environ.get("INFLUXDB_TOKEN")
        if not token:
            raise SystemExit("esp-ingest: no INFLUXDB_TOKEN")
        self.org = os.environ.get("INFLUX_ORG", "home")
        self.bucket = os.environ.get("INFLUX_BUCKET", "buspi")
        self.client = InfluxDBClient(
            url=os.environ.get("INFLUX_URL", "http://localhost:8086"), token=token, org=self.org
        )
        self._write = self.client.write_api(write_options=SYNCHRONOUS)

    def rows(self, flux):
        return [r for t in self.client.query_api().query(flux, org=self.org) for r in t.records]

    def times(self, start_ms, stop_ms, source=None, measurements=("camper",)):
        """``{function: sorted [epoch ms]}`` of the ``camper`` points in range (buspi's own when source
        is None); ``{measurement: …}`` for the other ``measurements``."""
        out: dict = {}
        for r in self.rows(flux_times(self.bucket, start_ms, stop_ms, source, measurements)):
            m = r.values["_measurement"]
            key = r.values["function"] if m == "camper" else m
            out.setdefault(key, []).append(int(r.get_time().timestamp() * 1000))
        return {f: sorted(t) for f, t in out.items()}

    def write(self, points):
        self._write.write(bucket=self.bucket, org=self.org, record=points)

    def diag_counts(self, lo, hi):
        """``{measurement: points}`` of the :data:`DIAG` measurements in range (distinct times)."""
        flt = " or ".join(
            ('(r._measurement == "%s" and r.source == "esp")' if ev == "ux" else 'r._measurement == "%s"') % m
            for ev, (m, _) in DIAG.items()
        )
        # count() can't aggregate a time column (distinct's output) or a group-key one: count rows
        flux = (
            'from(bucket:"%s") |> range(start:%s, stop:%s) |> filter(fn:(r) => %s) '
            '|> keep(columns:["_time","_measurement"]) |> group(columns:["_measurement"]) '
            '|> unique(column:"_time") |> map(fn:(r) => ({r with _value: 1})) |> count()'
            % (self.bucket, _rfc3339(lo), _rfc3339(hi + 1), flt)
        )
        return {r.values["_measurement"]: int(r.get_value()) for r in self.rows(flux)}

    def values(self, lo, hi, compare):
        """``{fn: [(ms, {field: value})]}`` of buspi's own points for the compare fields."""
        out: dict = {}
        for fn, fields in compare.items():
            flt = " or ".join('r._field == "%s"' % f for f in fields)
            flux = (
                'from(bucket:"%s") |> range(start:%s, stop:%s) '
                '|> filter(fn:(r) => r._measurement == "camper" and r.function == "%s" and not exists r.source and (%s)) '
                '|> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'
                % (self.bucket, _rfc3339(lo), _rfc3339(hi + 1), fn, flt)
            )
            rows = [
                (int(r.get_time().timestamp() * 1000), {f: r.values.get(f) for f in fields})
                for r in self.rows(flux)
            ]
            out[fn] = sorted(rows, key=lambda x: x[0])
        return out


# ---- 5. the run ----------------------------------------------------------------------------------


def load_cursor(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"seq": 0, "state": None}


def save_cursor(path, seq, state):
    serve.atomic_write_json(path, {"seq": seq, "state": state})


def _funcs():
    funcs = protocol.load()
    overrides.apply(funcs)
    return funcs


def ingest(
    fetch, db, cursor_path, *, dry_run=False, page=PAGE, funcs=None, from_seq=None, report=None, diag=None
):
    """One ingest run (see the module doc).

    :param fetch: ``fetch(since, max) -> (records, trailer)`` (:func:`http_fetch`).
    :param db: ``times(start_ms, stop_ms, source=None, measurements=("camper",)) -> {fn | measurement:
        [ms]}`` + ``write(points)`` (:class:`Influx`).
    :param cursor_path: the cursor file (seq + replay state).
    :param dry_run: write nothing, keep the cursor; per-point decisions go to ``report``.
    :param from_seq: start here with a fresh replay state instead of the cursor (``--verify``).
    :param report: a list that receives ``(fn, ts_ms, tsrc, covered, interp)`` per point (dry run, verify).
    :param diag: a list that receives ``(measurement, ts_ms)`` per diagnostics point (verify).
    :returns: the summary ``{pulled, written, skipped_covered, skipped_no_time, held, lost,
        diag_points}`` — ``diag_points`` = ``{measurement: points}`` of the diagnostics and ``ext``
        records (written, or in a dry run would be); a covered ``ext`` record counts as
        ``skipped_covered``.
    """
    funcs = funcs or _funcs()
    cur = load_cursor(cursor_path) if from_seq is None else {"seq": from_seq, "state": None}
    replay = Replay(funcs, cur.get("state"))
    since = cur["seq"]
    buf: list = []
    summary: dict = dict.fromkeys(
        ("pulled", "written", "skipped_covered", "thinned", "skipped_no_time", "held", "lost"), 0
    )
    summary["diag_points"] = dict.fromkeys((m for m, _ in DIAG.values()), 0)
    while True:
        recs, tr = fetch(since, page)
        summary["pulled"] += len(recs)
        if tr.get("lost"):
            log.warning(
                "esp-ingest: %d records after seq %d are gone from the card (deleted at 90 %% full)",
                tr["lost"],
                since,
            )
            summary["lost"] += tr["lost"]
        buf += recs
        since = tr["next"]
        final = not tr["more"]
        done, buf = resolve(buf, tr["boot"], final)
        if done:
            batch = []  # (fn, ts, tsrc, interp)
            ext = []  # (measurement, ts, point)
            points = []
            for rec, ts in done:
                if ts is None:
                    summary["skipped_no_time"] += 1
                    continue
                if rec.get("ev") in DIAG:  # only the ESP has these: no replay, no gap fill
                    p, m = diag_point(rec, ts), DIAG[rec["ev"]][0]
                    if p is not None:
                        points.append(p)
                        summary["diag_points"][m] += 1
                        if diag is not None:
                            diag.append((m, ts))
                    continue
                if rec.get("ev") == "ext":  # the satellite's own sensors: gap-filled below, no replay
                    p = ext_point(rec, ts)
                    if p is not None:
                        ext.append((EXT[rec["type"]][0], ts, p))
                    continue
                for fn, interp in replay.process(rec, ts).items():
                    batch.append((fn, ts, rec.get("tsrc") or "back", interp))
            have = (
                db.times(min(b[1] for b in batch) - WINDOW_MS, max(b[1] for b in batch) + WINDOW_MS)
                if batch
                else {}
            )
            have_ext = (
                db.times(
                    min(e[1] for e in ext) - WINDOW_MS,
                    max(e[1] for e in ext) + WINDOW_MS,
                    measurements=tuple(m for m, _ in EXT.values()),
                )
                if ext
                else {}
            )
            for m, ts, p in ext:
                if covered(have_ext.get(m, []), ts):
                    summary["skipped_covered"] += 1
                else:
                    points.append(p)
                    summary["diag_points"][m] = summary["diag_points"].get(m, 0) + 1
            for fn, ts, tsrc, interp in batch:
                cov = covered(have.get(fn, []), ts)
                summary["skipped_covered"] += cov
                if report is not None:
                    report.append((fn, ts, tsrc, cov, interp))
                for f, t, i in replay.close(ts) if cov else replay.thin(fn, ts, interp):
                    summary["written"] += 1
                    points += influx.points_for({f: i}, time_ms=t, tags={"source": "esp"})
            if not dry_run:
                if points:
                    db.write(points)  # raises -> the cursor stays where it was
                save_cursor(cursor_path, done[-1][0]["seq"], replay.dump())
        if final:
            break
    summary["held"] = len(buf)  # the current boot's records still waiting for a time
    summary["thinned"] = replay.thinned
    if dry_run:
        summary["written"] = 0
    log.info("esp-ingest: %s" % json.dumps(summary))
    return summary


# ---- dry run + verify reports --------------------------------------------------------------------


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


def print_dry_run(report, out=print, samples=3):
    """Per function: points, time range, tsrc mix, covered vs gap; then a few sample gap points."""
    by: dict = {}
    for fn, ts, tsrc, cov, interp in report:
        by.setdefault(fn, []).append((ts, tsrc, cov, interp))
    for fn in sorted(by):
        rows = by[fn]
        mix: dict = {}
        for _, tsrc, _, _ in rows:
            mix[tsrc] = mix.get(tsrc, 0) + 1
        gaps = [r for r in rows if not r[2]]
        out(
            "%-12s %5d points  %s .. %s  tsrc %s  covered %d  gap %d"
            % (fn, len(rows), _iso(rows[0][0]), _iso(rows[-1][0]), mix, len(rows) - len(gaps), len(gaps))
        )
        for ts, tsrc, _, interp in gaps[:samples]:
            out(
                "    %s %s %s"
                % (_iso(ts), tsrc, json.dumps(influx.numeric_fields(interp), sort_keys=True)[:160])
            )


def verify(report, db, out=print, samples=5):
    """Check what is in InfluxDB over the replayed range (``report`` = a dry run's per-point list).

    Per function: buspi's points vs ``source=esp`` points; ESP points lying within ±90 s of a buspi
    point (must be 0 — gap fill never writes those); and, for the replayed records buspi ALSO
    covered, the ESP replay's value vs buspi's neighbouring points (the one just before or after, within
    ±90 s) for the :data:`COMPARE` fields — the same frame must decode to the same value, so water and
    SoC should agree almost always; fast currents may differ between two reads. Returns the number of
    overlaps (the hard failure); the side-by-side is printed for a human to judge.
    """
    if not report:
        out("verify: no ESP records in range")
        return 0
    lo, hi = min(r[1] for r in report) - WINDOW_MS, max(r[1] for r in report) + WINDOW_MS
    mine, esp = db.times(lo, hi), db.times(lo, hi, source="esp")
    problems = 0
    out("verify %s .. %s" % (_iso(lo), _iso(hi)))
    for fn in sorted(set(mine) | set(esp)):
        bad = [t for t in esp.get(fn, []) if covered(mine.get(fn, []), t)]
        problems += len(bad)
        out(
            "%-12s buspi %6d  esp %6d  esp-within-90s %d%s"
            % (fn, len(mine.get(fn, [])), len(esp.get(fn, [])), len(bad), "  <-- PROBLEM" if bad else "")
        )
    vals = db.values(lo, hi, COMPARE)
    for fn, fields in COMPARE.items():
        series = vals.get(fn, [])  # [(ms, {field: value})] sorted
        times = [t for t, _ in series]
        same = diff = 0
        shown = 0
        for f, ts, _, _, interp in report:
            if f != fn or not times:
                continue
            i = bisect.bisect_left(times, ts)
            near = [k for k in (i - 1, i) if 0 <= k < len(times) and abs(times[k] - ts) <= WINDOW_MS]
            if not near:
                continue
            e = influx.numeric_fields(interp)
            mine_e = {k: e.get(k) for k in fields}
            # the same as buspi's point just before OR just after (a value may change between two polls)
            ok = any(all(mine_e[k] == series[k2][1].get(k) for k in fields) for k2 in near)
            same, diff = same + ok, diff + (not ok)
            if not ok and shown < samples:
                shown += 1
                theirs = [series[k2][1] for k2 in near]
                out("    %s %s esp %s vs buspi %s" % (fn, _iso(ts), mine_e, theirs))
        out("%-12s side by side %s: %d same, %d differ" % (fn, ",".join(fields), same, diff))
    return problems


def verify_diag(diag, db, out=print):
    """Per :data:`DIAG` measurement: the replayed diagnostics points (``diag`` = ingest's
    ``(measurement, ts_ms)`` list, distinct times) vs the points InfluxDB holds over their range;
    fewer in InfluxDB is marked (not yet ingested, or lost). Printed only — never an exit code."""
    if not diag:
        out("verify: no ESP diagnostics records in range")
        return
    have = db.diag_counts(min(t for _, t in diag), max(t for _, t in diag))
    for m, _ in DIAG.values():
        mine = len({t for m2, t in diag if m2 == m})
        got = have.get(m, 0)
        out("%-12s replayed %6d  influx %6d%s" % (m, mine, got, "  <-- missing" if got < mine else ""))


def main(args):
    """``calictl esp-ingest`` (cli.py). Returns the exit code."""
    fetch = http_fetch(args.url)
    db = Influx()
    report: list = []
    if args.verify:
        diag: list = []
        s = ingest(fetch, db, args.cursor, dry_run=True, from_seq=args.from_seq, report=report, diag=diag)
        problems = verify(report, db)
        verify_diag(diag, db)
        print(json.dumps(s))
        return 1 if problems else 0
    s = ingest(fetch, db, args.cursor, dry_run=args.dry_run, report=report if args.dry_run else None)
    if args.dry_run:
        print_dry_run(report)
        print(json.dumps(s))
    return 0
