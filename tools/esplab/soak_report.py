#!/usr/bin/env python3
"""ESP soak report: 10 criteria over the last N hours of the ESP's diagnostics in InfluxDB (R_SOAK_REPORT).

Runs on buspi under calictl-esp-soak.timer (daily 08:00) with the serve env (INFLUXDB_TOKEN from
/etc/buspi/secrets.env). Prints one line per criterion, ``VERDICT PASS|FAIL``, writes the text to
``--out DIR/<date>.txt`` and one ``esp_soak`` point. Exit 0 PASS, 1 FAIL, 2 cannot run.

.. req:: Soak report
   :id: R_SOAK_REPORT
   :status: open

   Criteria: (1) telemetry: esp_diag samples exist; (2) no reboot with reset_reason in BAD_RESETS;
   (3) fw: every boot in the window ran the current build; (4) <= 10 link_down events per boot;
   (5) touch NACKs <= 5/h per boot; (6) esp-ingest lost = 0 in every summary, held = 0 in the last;
   (7) heap_min >= 32 KB in every diag sample; (8) screen: SKIP until an evt display record exists;
   (9) i2c.master timeout log lines <= 2 per day; (10) sensors: every reading the ESP and buspi
   both hold fresh (<= 5 min) agrees within SENSOR_TOL (SKIP when none). A SKIP never fails the
   verdict.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

BAD_RESETS = {"panic", "int_wdt", "task_wdt", "wdt", "brownout", "cpu_lockup"}
LINK_DOWN_MAX = 10
NACKS_PER_H_MAX = 5.0
HEAP_MIN_B = 32768
I2C_PER_DAY_MAX = 2
H = 3_600_000.0
SENSOR_MAX_AGE_S = 300
# the ESP's default aliases (sensors.c TYPES) -> buspi's measurement (esp_ingest.EXT)
SENSOR_MEAS = {"govee": "govee", "mppt-75-15": "victron_mppt", "battery-sense": "victron_battery"}
SENSOR_TOL = {"battery_voltage": 0.05, "temperature_c": 0.5, "temp_c": 0.5, "humidity": 2.0, "battery": 5.0}


@dataclass
class SoakData:
    start_ms: int
    end_ms: int
    boots: list  # [{"t", "reset_reason", "fw"}] sorted by t
    link_down: list  # [{"t"}]
    diag: list  # [{"t", "heap_min", "link_up"}]
    touch: list  # [{"t", "errors"}]
    ingest: object  # [{"lost", "held"}] in journal order, or None = no journal
    i2c_lines: list  # [{"t", "msg"}]
    fw: str
    boot: int
    ext: dict = field(default_factory=dict)  # the ESP's /api/state "ext" ({} when absent/unreachable)
    buspi_sensors: dict = field(default_factory=dict)  # {measurement: {field: value, "age_s"}}, buspi's own


def _since_ms(s):
    """``--since`` ISO text to epoch ms; a value without an offset is UTC."""
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return int(t.timestamp() * 1000)


def _intervals(d):
    """Boot intervals [(lo, hi)] from the boot points; the first starts at the window start."""
    edges = [d.start_ms] + [b["t"] for b in d.boots if b["t"] > d.start_ms] + [d.end_ms]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def evaluate(d):
    lines = [
        (
            "telemetry",
            "PASS" if d.diag else "FAIL",
            "%d diag samples" % len(d.diag) if d.diag else "no esp_diag samples in the window",
        )
    ]
    bad = [b for b in d.boots if b["reset_reason"] in BAD_RESETS]
    reasons = ",".join(b["reset_reason"] for b in d.boots) or "none"
    lines.append(("reboots", "FAIL" if bad else "PASS", "%d boots (%s)" % (len(d.boots), reasons)))

    seen = sorted({b["fw"] for b in d.boots if b.get("fw")})
    if d.fw == "?" or not seen:
        lines.append(("fw", "SKIP", "ESP unreachable or no boot point"))
    else:
        other = [f for f in seen if not (d.fw.startswith(f[:7]) or f.startswith(d.fw[:7]))]
        lines.append(
            (
                "fw",
                "FAIL" if other else "PASS",
                ("boots ran %s, now %s" % (",".join(other), d.fw)) if other else "all boots on %s" % d.fw,
            )
        )

    worst = 0
    for lo, hi in _intervals(d):
        worst = max(worst, sum(1 for e in d.link_down if lo <= e["t"] < hi))
    up = [x["link_up"] for x in d.diag if x.get("link_up") is not None]
    frac = (sum(up) / len(up) * 100.0) if up else 0.0
    lines.append(
        (
            "link",
            "FAIL" if worst > LINK_DOWN_MAX else "PASS",
            "max %d link_down per boot (limit %d); link_up %.1f%% of %d diag samples"
            % (worst, LINK_DOWN_MAX, frac, len(up)),
        )
    )

    worst_rate = 0.0
    for lo, hi in _intervals(d):
        errs = [x["errors"] for x in d.touch if lo <= x["t"] < hi]
        hours = max((hi - lo) / H, 1 / 60.0)
        if errs:
            worst_rate = max(worst_rate, max(errs) / hours)
    lines.append(
        (
            "touch_nacks",
            "FAIL" if worst_rate > NACKS_PER_H_MAX else "PASS",
            "worst boot %.1f/h (limit %.0f/h)" % (worst_rate, NACKS_PER_H_MAX),
        )
    )

    if not d.ingest:
        lines.append(("ingest", "SKIP", "no esp-ingest summary in the journal"))
    else:
        lost = sum(s.get("lost", 0) for s in d.ingest)
        held = d.ingest[-1].get("held", 0)
        lines.append(
            (
                "ingest",
                "FAIL" if lost or held else "PASS",
                "lost %d held %d over %d runs" % (lost, held, len(d.ingest)),
            )
        )

    heaps = [x["heap_min"] for x in d.diag if x.get("heap_min") is not None]
    hmin = min(heaps) if heaps else None
    lines.append(
        (
            "heap_min",
            "SKIP" if hmin is None else ("FAIL" if hmin < HEAP_MIN_B else "PASS"),
            "min %s B (limit %d)" % (hmin, HEAP_MIN_B),
        )
    )

    lines.append(("screen", "SKIP", "no display record (evt display is reserved, firmware.md)"))

    buckets: dict = {}
    for x in d.i2c_lines:
        k = (x["t"] - d.start_ms) // int(24 * H)
        buckets[k] = buckets.get(k, 0) + 1
    worst_day = max(buckets.values(), default=0)
    lines.append(
        (
            "i2c_timeouts",
            "FAIL" if worst_day > I2C_PER_DAY_MAX else "PASS",
            "worst day %d lines (limit %d/day), %d in total" % (worst_day, I2C_PER_DAY_MAX, len(d.i2c_lines)),
        )
    )
    lines.append(_sensors(d))
    return lines


def _fresh(r):
    return isinstance(r, dict) and r.get("age_s") is not None and r["age_s"] <= SENSOR_MAX_AGE_S


def _sensors(d):
    """Value drift: the ESP's live readings vs buspi's own last point per measurement."""
    n, bad = 0, []
    for alias, esp in sorted(d.ext.items()):
        bus = d.buspi_sensors.get(SENSOR_MEAS.get(alias))
        if not (_fresh(esp) and _fresh(bus)):
            continue
        for f, tol in SENSOR_TOL.items():
            a, b = esp.get(f), bus.get(f)
            if a is None or b is None:
                continue
            n += 1
            if round(abs(a - b), 6) > tol:  # 12.98 - 12.93 is 0.05000000000000071 in binary floats
                bad.append("%s.%s esp=%s buspi=%s" % (alias, f, a, b))
    if not n:
        return ("sensors", "SKIP", "no reading fresh (<= %d s) on both the ESP and buspi" % SENSOR_MAX_AGE_S)
    if bad:
        return ("sensors", "FAIL", "%d of %d fields off: %s" % (len(bad), n, "; ".join(bad)))
    return ("sensors", "PASS", "%d fields agree" % n)


def verdict(lines):
    return "FAIL" if any(v == "FAIL" for _, v, _ in lines) else "PASS"


def render(d, lines):
    hours = (d.end_ms - d.start_ms) / H
    out = ["soak fw=%s boot=%s window=%.1fh %s..%s" % (d.fw, d.boot, hours, _iso(d.start_ms), _iso(d.end_ms))]
    out += ["%s %s %s" % (v, n, detail) for n, v, detail in lines]
    out.append("VERDICT " + verdict(lines))
    return "\n".join(out) + "\n"


def point(d, lines):
    return {
        "measurement": "esp_soak",
        "tags": {"fw": d.fw},
        "fields": {
            "verdict": verdict(lines),
            "fails": float(sum(v == "FAIL" for _, v, _ in lines)),
            "boot": float(d.boot),
        },
    }


def parse_journal(text):
    """The ``esp-ingest: {...}`` summaries in journalctl output, in order."""
    out = []
    for m in re.finditer(r"esp-ingest: (\{.*\})\s*$", text, re.M):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:  # a truncated line must not crash the report (exit 1 reads as FAIL)
            pass
    return out


def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%MZ")


def _rfc(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _flux(bucket, lo, hi, flt, fields):
    return (
        'from(bucket:"%s") |> range(start:%s, stop:%s) |> filter(fn:(r) => %s) '
        "|> filter(fn:(r) => contains(value:r._field, set:[%s])) "
        '|> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'
        % (bucket, _rfc(lo), _rfc(hi), flt, ",".join('"%s"' % f for f in fields))
    )


def _pull(db, lo, hi):
    """SoakData's Influx halves via esp_ingest.Influx (lazy influxdb_client)."""

    def rows(flt, fields):
        out = []
        for r in db.rows(_flux(db.bucket, lo, hi, flt, fields)):
            v = {"t": int(r.values["_time"].timestamp() * 1000)}
            v.update({f: r.values.get(f) for f in fields})
            out.append(v)
        return sorted(out, key=lambda x: x["t"])

    return dict(
        boots=rows('r._measurement == "esp_evt" and r.kind == "boot"', ["reset_reason", "fw"]),
        link_down=rows('r._measurement == "esp_evt" and r.kind == "link_down"', ["reason"]),
        diag=rows('r._measurement == "esp_diag"', ["heap_min", "link_up"]),
        touch=rows('r._measurement == "esp_evt" and r.kind == "touch"', ["errors"]),
        i2c_lines=[
            x
            for x in rows('r._measurement == "esp_log"', ["msg"])
            if "i2c.master" in (x["msg"] or "") and "timeout" in (x["msg"] or "")
        ],
    )


def _pull_sensors(db, end):
    """buspi's own (no ``source`` tag) last value per field of the sensor measurements in the 5 min
    before ``end``: ``{measurement: {field: value, "age_s": s}}``. ponytail: one device per
    measurement (the van has one of each); group by ``device`` when that changes."""
    flux = (
        'from(bucket:"%s") |> range(start:%s, stop:%s) '
        "|> filter(fn:(r) => contains(value:r._measurement, set:[%s]) and not exists r.source) "
        "|> filter(fn:(r) => contains(value:r._field, set:[%s])) "
        '|> group(columns:["_measurement","_field"]) |> last()'
        % (
            db.bucket,
            _rfc(end - SENSOR_MAX_AGE_S * 1000),
            _rfc(end + 1000),
            ",".join('"%s"' % m for m in SENSOR_MEAS.values()),
            ",".join('"%s"' % f for f in SENSOR_TOL),
        )
    )
    out: dict = {}
    for r in db.rows(flux):
        m = out.setdefault(r.values["_measurement"], {"age_s": None})
        m[r.values["_field"]] = r.values["_value"]
        age = int((end - r.values["_time"].timestamp() * 1000) // 1000)
        m["age_s"] = age if m["age_s"] is None else max(m["age_s"], age)  # the stalest field counts
    return out


def _journal(lo):
    try:
        out = subprocess.run(
            [
                "journalctl",
                "-u",
                "calictl-esp-ingest",
                "--since",
                "@%d" % (lo // 1000),
                "-o",
                "short",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_journal(out) or None


def _esp(url):
    try:
        st = json.load(urllib.request.urlopen(url.rstrip("/") + "/api/state", timeout=10))
        return st["device"]["fw"], st["device"]["time"]["boot"], st.get("ext") or {}
    except Exception as e:  # unreachable ESP: the report still runs on Influx alone
        print("soak: ESP unreachable (%s)" % e, file=sys.stderr)
        return "?", -1, {}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="the ESP, e.g. http://192.168.8.183 (omit with --no-esp)")
    ap.add_argument("--no-esp", action="store_true")
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--since", help="ISO start (UTC), overrides --hours")
    ap.add_argument("--out", help="directory for <date>.txt")
    ap.add_argument("--no-influx-write", action="store_true")
    a = ap.parse_args(argv)
    if not a.url and not a.no_esp:
        ap.error("--url or --no-esp")
    from calictl.esp_ingest import Influx  # lazy: influxdb_client lives in solix-env

    try:
        db = Influx()
    except (SystemExit, Exception) as e:  # no token, no client, unreachable: cannot run
        print("soak: cannot run: %s" % e, file=sys.stderr)
        return 2
    end = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    start = _since_ms(a.since) if a.since else end - int(a.hours * H)
    fw, boot, ext = _esp(a.url) if a.url else ("?", -1, {})
    try:
        pulled = _pull(db, start, end)
        pulled["buspi_sensors"] = _pull_sensors(db, end)
    except Exception as e:  # unreachable Influx, bad query: cannot run
        print("soak: cannot run: %s" % e, file=sys.stderr)
        return 2
    d = SoakData(start_ms=start, end_ms=end, ingest=_journal(start), fw=fw, boot=boot, ext=ext, **pulled)
    lines = evaluate(d)
    text = render(d, lines)
    sys.stdout.write(text)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, dt.date.today().isoformat() + ".txt"), "w") as f:
            f.write(text)
    if not a.no_influx_write:
        from influxdb_client import Point, WritePrecision

        p = point(d, lines)
        pt = Point(p["measurement"]).time(end, WritePrecision.MS)
        for k, v in p["tags"].items():
            pt = pt.tag(k, v)
        for k, v in p["fields"].items():
            pt = pt.field(k, v)
        db.write([pt])
    return 0 if verdict(lines) == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
