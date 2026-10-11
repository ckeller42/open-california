"""calictl esp-ingest: time, replay, gap fill and the cursor (R_ESP_INGEST_GAPFILL).

Everything around the network is faked: ``fetch`` serves pages of hand-built ESP records (the
``/api/log`` shape, ``R_FW_LOG_API``), the Influx side answers buspi's point times from a dict and
records writes, and ``influxdb_client`` is a stand-in module (tests run without it installed).
"""

import asyncio
import json
import sys
import types

import pytest

from calictl import esp_ingest, overrides, protocol, serve
from tools.mock_unit import MockCamperUnit, _pack_state

T0 = 1_789_639_000_000  # epoch ms
ENERGY_FN = "energy"


class _Point:
    def __init__(self, measurement):
        self.measurement, self.tags, self.fields, self.t = measurement, {}, {}, None

    def tag(self, k, v):
        self.tags[k] = v
        return self

    def field(self, k, v):
        self.fields[k] = v
        return self

    def time(self, t, precision):
        self.t = (t, precision)
        return self

    def key(self):
        return (self.measurement, sorted(self.tags.items()), sorted(self.fields.items()), self.t)


@pytest.fixture(autouse=True)
def fake_influx(monkeypatch):
    mod = types.ModuleType("influxdb_client")
    mod.Point = _Point
    mod.WritePrecision = types.SimpleNamespace(MS="ms")
    monkeypatch.setitem(sys.modules, "influxdb_client", mod)


@pytest.fixture(scope="module")
def funcs():
    f = protocol.load()
    overrides.apply(f)
    return f


@pytest.fixture(scope="module")
def frames(funcs):
    u = MockCamperUnit()
    return {
        fn: _pack_state(funcs[fn], u.decoded(fn)).hex() for fn in ("energy", "water", "general", "cooler")
    }


def rec(seq, fn, hexs, *, ts=None, up=0, boot=1, ev="notify", tsrc=None):
    if fn is None:
        return {"seq": seq, "boot": boot, "up": up, "ts": ts, "tsrc": tsrc, "ev": ev, "addr": None}
    return {
        "seq": seq,
        "boot": boot,
        "up": up,
        "ts": ts,
        "tsrc": tsrc or ("unit" if ts is not None else None),
        "ev": ev,
        "char": "x",
        "fn": fn,
        "hex": hexs,
    }


def pager(records, boot):
    """A fake /api/log over ``records`` (the C pager's contract)."""
    calls = []

    def fetch(since, max_):
        calls.append(since)
        page = [r for r in records if r["seq"] > since][:max_]
        nxt = page[-1]["seq"] if page else since
        more = any(r["seq"] > nxt for r in records)
        return page, {"next": nxt, "more": more, "boot": boot, "lost": 0}

    fetch.calls = calls
    return fetch


class DB:
    """buspi's own point times + the written points."""

    def __init__(self, have=None, fail=False):
        self.have, self.fail, self.writes, self.queries = have or {}, fail, [], []

    def times(self, lo, hi, source=None):
        self.queries.append((lo, hi))
        return {fn: sorted(t for t in ts if lo <= t <= hi) for fn, ts in self.have.items()}

    def write(self, points):
        if self.fail:
            raise OSError("influx down")
        self.writes.append([p.key() for p in points])


# ---- 2. time -------------------------------------------------------------------------------------


def test_back_stamping_from_the_next_stamped_record_of_the_same_boot():
    """Unstamped records before a boot's first stamp are back-stamped from it by uptime; a stamped
    record keeps its ts; an old boot that never got a stamp is skipped (None); the current boot's
    unanchored tail is held; a re-run gives the same times.

    .. test:: esp-ingest back-stamps, skips never-stamped boots and holds the current boot
       :id: T_ESP_INGEST_TIME
       :links: R_ESP_INGEST_GAPFILL
    """
    buf = [
        rec(1, None, None, boot=1, up=100),  # boot 1: never stamped -> skipped
        rec(2, "cooler", "00", boot=1, up=200),
        rec(3, None, None, boot=2, up=50),  # boot 2: anchored by seq 5
        rec(4, "cooler", "00", boot=2, up=1050),
        rec(5, "cooler", "00", boot=2, up=2050, ts=T0),
        rec(6, "cooler", "00", boot=2, up=3050, ts=T0 + 1000),
        rec(7, "cooler", "00", boot=2, up=4000),  # after boot 2's last stamp, boot 3 follows -> skipped
        rec(8, "cooler", "00", boot=3, up=10),  # current boot, no stamp yet -> held
        rec(9, "cooler", "00", boot=3, up=20),
    ]
    done, rest = esp_ingest.resolve(buf, current_boot=3, final=True)
    assert [(r["seq"], t) for r, t in done] == [
        (1, None),
        (2, None),
        (3, T0 - 2000),
        (4, T0 - 1000),
        (5, T0),
        (6, T0 + 1000),
        (7, None),
    ]
    assert [r["seq"] for r in rest] == [8, 9]
    assert esp_ingest.resolve(buf, current_boot=3, final=True) == (done, rest)  # deterministic
    # mid-run (more pages follow): an unanchored boot-2 tail could still get its stamp -> carried
    done, rest = esp_ingest.resolve(buf[2:4], current_boot=3, final=False)
    assert done == [] and [r["seq"] for r in rest] == [3, 4]
    # the last page, and boot 2 is not the current one: never stampable
    done, rest = esp_ingest.resolve(buf[2:4], current_boot=3, final=True)
    assert [t for _, t in done] == [None, None] and rest == []


# ---- 4. gap fill ---------------------------------------------------------------------------------


def test_gap_fill_edges_are_inclusive_at_90_s():
    """A buspi point within ±90 s (inclusive) covers an ESP record; 90.001 s does not.

    .. test:: esp-ingest writes only where buspi has no point within ±90 s
       :id: T_ESP_INGEST_GAPFILL
       :links: R_ESP_INGEST_GAPFILL
    """
    have = [T0]
    assert esp_ingest.covered(have, T0)
    assert esp_ingest.covered(have, T0 + 90_000) and esp_ingest.covered(have, T0 - 90_000)
    assert not esp_ingest.covered(have, T0 + 90_001) and not esp_ingest.covered(have, T0 - 90_001)
    assert not esp_ingest.covered([], T0)


def test_ingest_writes_only_the_gaps_tagged_esp_at_the_record_time(tmp_path, frames):
    records = [
        rec(1, "cooler", frames["cooler"], ts=T0),  # buspi point 30 s later: covered
        rec(2, "cooler", frames["cooler"], ts=T0 + 200_000),  # nothing near: gap
        rec(3, "energy", frames["energy"], ts=T0 + 200_000),  # buspi has cooler only: gap
        rec(4, "cooler", frames["cooler"], ts=T0 + 400_000),  # exactly 90 s before a buspi point: covered
    ]
    db = DB({"cooler": [T0 + 30_000, T0 + 490_000]})
    s = esp_ingest.ingest(pager(records, boot=1), db, str(tmp_path / "c.json"))
    assert s == {"pulled": 4, "written": 2, "skipped_covered": 2, "skipped_no_time": 0, "held": 0, "lost": 0}
    assert len(db.queries) == 1  # one query per batch
    pts = [p for w in db.writes for p in w]
    assert [(dict(p[1])["function"], dict(p[1])["source"], p[3]) for p in pts] == [
        ("cooler", "esp", (T0 + 200_000, "ms")),
        ("energy", "esp", (T0 + 200_000, "ms")),
    ]
    assert json.loads((tmp_path / "c.json").read_text())["seq"] == 4


# ---- 5. the cursor -------------------------------------------------------------------------------


def test_cursor_moves_only_after_a_successful_write_and_a_rerun_writes_identical_points(tmp_path, frames):
    """A failed write leaves the cursor; a good one moves it, holding at the current boot's unstamped
    record; the next run starts there; a re-run from the same start writes identical points.

    .. test:: esp-ingest advances its cursor only after a successful write; a re-run is identical
       :id: T_ESP_INGEST_CURSOR
       :links: R_ESP_INGEST_GAPFILL
    """
    records = [
        rec(1, "general", frames["general"], ts=T0),
        rec(2, "energy", frames["energy"], ts=T0 + 1000),
        rec(3, "cooler", frames["cooler"], up=5000, boot=2),  # current boot, no stamp yet: held
    ]
    cur = str(tmp_path / "c.json")
    with pytest.raises(OSError):
        esp_ingest.ingest(pager(records, boot=2), DB(fail=True), cur)
    assert not (tmp_path / "c.json").exists()  # nothing moved
    a = DB()
    s = esp_ingest.ingest(pager(records, boot=2), a, cur)
    assert s["held"] == 1 and s["written"] == 2
    saved = json.loads((tmp_path / "c.json").read_text())
    assert saved["seq"] == 2  # held at the current boot's unstamped record
    energy = next(dict(p[2]) for p in a.writes[0] if dict(p[1])["function"] == "energy")
    assert energy["dcdc_current"] == 2.0  # serve's +2 on AmbSwVersion 0410, from the replayed general
    # the next run starts at the hold; a re-run from the same cursor writes byte-identical points
    nxt = pager(records, boot=2)
    esp_ingest.ingest(nxt, DB(), cur)
    assert nxt.calls[0] == 2
    b = DB()
    esp_ingest.ingest(pager(records, boot=2), b, str(tmp_path / "c2.json"))
    assert b.writes == a.writes


def test_dry_run_writes_nothing_and_keeps_the_cursor(tmp_path, frames, capsys):
    records = [rec(1, "cooler", frames["cooler"], ts=T0), rec(2, "cooler", frames["cooler"], ts=T0 + 300_000)]
    db, report = DB({"cooler": [T0]}), []
    s = esp_ingest.ingest(pager(records, boot=1), db, str(tmp_path / "c.json"), dry_run=True, report=report)
    assert db.writes == [] and not (tmp_path / "c.json").exists()
    assert s["skipped_covered"] == 1 and s["written"] == 0
    assert [(fn, ts, cov) for fn, ts, _, cov, _ in report] == [
        ("cooler", T0, True),
        ("cooler", T0 + 300_000, False),
    ]
    esp_ingest.print_dry_run(report)
    out = capsys.readouterr().out
    assert "cooler" in out and "covered 1  gap 1" in out and "'unit': 2" in out


class VerifyDB(DB):
    def __init__(self, have, esp, values):
        super().__init__(have)
        self.esp, self.vals = esp, values

    def times(self, lo, hi, source=None):
        return self.esp if source == "esp" else super().times(lo, hi)

    def values(self, lo, hi, compare):
        return self.vals


def test_verify_flags_esp_points_inside_buspi_coverage_and_value_mismatches(tmp_path, frames):
    records = [
        rec(1, "water", frames["water"], ts=T0, ev="read"),
        rec(2, "water", frames["water"], ts=T0 + 30_000, ev="read"),
    ]
    report = []
    esp_ingest.ingest(pager(records, boot=1), DB(), str(tmp_path / "c"), dry_run=True, report=report)
    assert report  # the second read 30 s later adopts the level (serve's settle)
    fresh = report[-1][4]["fresh"]["liters"]
    good = VerifyDB(
        {"water": [T0 + 31_000]},
        {},
        {"water": [(T0 + 31_000, {"fresh_liters": float(fresh), "waste_liters": 0.0})]},
    )
    lines = []
    assert esp_ingest.verify(report, good, out=lines.append) == 0
    assert any("1 same, 0 differ" in ln for ln in lines)
    bad = VerifyDB(
        {"water": [T0 + 31_000]},
        {"water": [T0 + 100_000]},
        {"water": [(T0 + 31_000, {"fresh_liters": 3.0, "waste_liters": 0.0})]},
    )
    lines = []
    assert esp_ingest.verify(report, bad, out=lines.append) == 1  # the overlap
    assert any("PROBLEM" in ln for ln in lines) and any("0 same, 1 differ" in ln for ln in lines)


# ---- 3. replay = serve's live path ---------------------------------------------------------------


def test_water_replay_equals_the_live_poll_path(monkeypatch):
    """The same water frames, once through serve.poll (30 s polls) and once as ESP read records at
    the same times through the replay: the same water is stored after every read — the hold on the
    1 L latch, the mid-ramp level not adopted, the settled level adopted, then held again.

    .. test:: esp-ingest's replay applies serve's water guard exactly like a live poll
       :id: T_ESP_INGEST_REPLAY
       :links: R_ESP_INGEST_GAPFILL, R_WATER_RAMP_DEBOUNCE
    """
    trace = [
        (0, "03011d010016"),
        (30, "03101d010016"),
        (31, "03141d010016"),
        (61, "03141d010016"),
        (91, "03011d010016"),
    ]
    s = serve.Server(influx_enabled=False)
    clock = {"t": 0.0}
    monkeypatch.setattr(serve.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(serve.time, "time", lambda: T0 / 1000 + clock["t"])
    frames = iter(trace)

    async def fake_read_all(fns):
        return {"water": bytes.fromhex(next(frames)[1])}

    monkeypatch.setattr(s.dev, "read_all", fake_read_all)

    async def live():
        s._ble = asyncio.Lock()
        out = []
        for t, _ in trace:
            clock["t"] = t
            out.append((await s.poll()).get("water"))
        return out

    want = asyncio.run(live())
    replay = esp_ingest.Replay(s.funcs)
    got = [
        replay.process(rec(i, "water", h, ts=T0 + t * 1000, ev="read"), T0 + t * 1000).get("water")
        for i, (t, h) in enumerate(trace)
    ]
    assert got == want
    assert [w and w["fresh"]["liters"] for w in got] == [None, None, None, 20, 20]


def test_replay_adopts_a_settled_ramp_on_a_later_record(funcs, frames):
    """The ESP logs the ramp's notifies, not polls: a level no newer water frame replaced for 5 s is
    adopted at the next record's time (like the satellite's own tick), and a JSON round trip of the
    replay state (the cursor file) changes nothing."""
    r = esp_ingest.Replay(funcs)
    for i, lvl in enumerate((2, 9, 20)):
        assert r.process(rec(i, "water", "03%02x1d010016" % lvl, ts=T0 + i * 300), T0 + i * 300) == {}
    r = esp_ingest.Replay(funcs, json.loads(json.dumps(r.dump())))
    assert "water" not in r.process(rec(9, "cooler", frames["cooler"], ts=T0 + 4000), T0 + 4000)
    out = r.process(rec(10, "cooler", frames["cooler"], ts=T0 + 6000), T0 + 6000)
    assert out["water"]["fresh"]["liters"] == 20 and "cooler" in out
