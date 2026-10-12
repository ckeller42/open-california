"""cali_sdlog: the SD log's portable core — record format, 1 MB segments, seq recovery, the bounded queue.

``firmware/components/cali_core/sdlog.c`` driven through ``test/sdlog_cli.c`` against a temp
directory (the ESP's card is FATFS behind the same stdio/dirent calls); the session and the
owner->SD queue are faked there. Mount, card power and the SD task (``components/cali_sdcard``) are
ESP-only.

.. test:: SD log records, segments, seq recovery after a cut line, drop counting, device.sd
   :id: T_FW_SD_LOG
   :links: R_FW_SD_LOG
"""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from calictl import trace

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
PLATFORM = ROOT / "firmware" / "components" / "platform"
SEGMENT = 1024 * 1024
NOTIFY, READ, UP, DOWN = 0, 1, 2, 3  # cali_session.h CALI_SESSION_IO_*
ENERGY = "00d0400a0b0c0d0e"


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("sdlog") / "sdlog_cli"
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            "-I",
            str(PLATFORM / "include"),
            "-I",
            str(ROOT / "csrc"),
            str(CORE / "sdlog.c"),
            str(CORE / "time.c"),
            str(CORE / "json.c"),
            str(ROOT / "csrc" / "codec.c"),
            str(PLATFORM / "host" / "platform_host.c"),
            str(CORE / "test" / "sdlog_cli.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


@pytest.fixture
def dirs(tmp_path):
    (tmp_path / "kv").mkdir()
    return tmp_path / "kv", tmp_path / "log"


def run(cli, kv, *lines):
    out = subprocess.run(
        [str(cli), str(kv)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    assert not [ln for ln in out.stdout.splitlines() if ln.startswith("ERR")], out.stdout
    return out.stdout.splitlines()


def records(log):
    """Every record of every segment, oldest segment first, through calictl's own trace reader."""
    out = []
    for seg in sorted(log.glob("*.jsonl"), key=lambda p: int(p.stem)):
        out += list(trace.read_events(seg))
    return out


def sd(out):
    return [json.loads(ln[5:])["sd"] for ln in out if ln.startswith("JSON ")][-1]


def logging_session(log, *lines):
    return ("boot", "init", "card ok 30400 28800", "open %s" % log, *lines, "drain", "close")


def test_records_have_the_trace_shape_plus_the_stamps(cli, dirs):
    kv, log = dirs
    run(
        cli,
        kv,
        *logging_session(
            log,
            "io %d 0 " % UP,
            "io %d 1602 %s" % (NOTIFY, ENERGY),
            "io %d 1302 0102" % READ,
            "io %d beef 01" % NOTIFY,
            "io %d 0 " % DOWN,
        ),
    )
    recs = records(log)
    assert [r["ev"] for r in recs] == ["connect", "notify", "read", "notify", "disconnect"]
    assert [r["seq"] for r in recs] == [1, 2, 3, 4, 5]
    assert {r["boot"] for r in recs} == {1}
    assert all(r["ts"] is None and r["tsrc"] is None and isinstance(r["up"], int) for r in recs)
    assert recs[0] == {k: recs[0][k] for k in ("seq", "boot", "up", "ts", "tsrc")} | {
        "ev": "connect",
        "addr": None,
    }
    assert recs[1] | {"up": 0} == {
        "seq": 2,
        "boot": 1,
        "up": 0,
        "ts": None,
        "tsrc": None,
        "ev": "notify",
        "char": "1602",
        "fn": "energy",
        "hex": ENERGY,
    }
    assert recs[2]["fn"] == "water" and recs[2]["hex"] == "0102"
    assert recs[3]["fn"] is None  # a char outside CODEC_CHARS: logged, unnamed
    reopened = sd(run(cli, kv, "card ok 1 1", "open %s" % log, "json"))
    assert (log / "1.jsonl").exists() and reopened["last_seq"] == 5


def test_a_1004_frame_is_stamped_by_its_own_unit_clock(cli, dirs):
    kv, log = dirs
    frame = "80" + bytes([126, 9, 11, 14, 30, 5, 0, 0, 0, 0]).hex()  # 2026-10-11 14:30:05 CEST
    run(
        cli,
        kv,
        *logging_session(
            log, "io %d 1302 01" % NOTIFY, "io %d 1004 %s" % (NOTIFY, frame), "io %d 1302 01" % NOTIFY
        ),
    )
    recs = records(log)
    assert [r["tsrc"] for r in recs] == [None, "unit", "unit"]
    want = datetime(2026, 10, 11, 12, 30, 5, tzinfo=UTC).timestamp() * 1000
    assert 0 <= recs[1]["ts"] - want < 200 and recs[1]["fn"] == "vehicle"


def test_segments_roll_at_1_mb_and_seq_runs_on(cli, dirs):
    kv, log = dirs
    out = run(cli, kv, "boot", "open %s" % log, "fill 9000", "close")
    assert out[-1] == "ROLLED 1"
    segs = sorted(log.glob("*.jsonl"), key=lambda p: int(p.stem))
    assert len(segs) == 2 and segs[0].name == "1.jsonl"
    assert SEGMENT - 200 < segs[0].stat().st_size <= SEGMENT
    assert segs[1].name == "%d.jsonl" % (len(segs[0].read_text().splitlines()) + 1)
    assert [r["seq"] for r in records(log)] == list(range(1, 9001))


def test_seq_resumes_after_a_power_cut_mid_record(cli, dirs):
    """A record cut by a power loss is truncated away; the next boot continues the seq without a gap."""
    kv, log = dirs
    run(cli, kv, "boot", "open %s" % log, "fill 10", "close")
    seg = log / "1.jsonl"
    whole = seg.read_bytes()
    seg.write_bytes(whole + b'{"seq":11,"boot":1,"up":10')  # the cut line
    out = run(cli, kv, "boot", "open %s" % log, "fill 2", "close")
    assert "OPEN 0 NEXT 11" in out and any("ends in a cut record" in ln for ln in out)
    recs = records(log)  # every line parses again
    assert [r["seq"] for r in recs] == list(range(1, 13))
    assert [r["boot"] for r in recs[-2:]] == [2, 2] and seg.read_bytes().startswith(whole)


def test_an_empty_newest_segment_names_the_next_seq(cli, dirs):
    """Power lost right after a roll: the new segment exists but is empty — its name is the next seq."""
    kv, log = dirs
    run(cli, kv, "open %s" % log, "fill 3", "close")
    (log / "4.jsonl").write_bytes(b"")
    out = run(cli, kv, "open %s" % log, "fill 1", "close")
    assert "OPEN 0 NEXT 4" in out and [r["seq"] for r in records(log)] == [1, 2, 3, 4]


def test_a_fresh_card_starts_at_seq_1_and_unrelated_files_are_ignored(cli, dirs):
    kv, log = dirs
    log.mkdir()
    (log / "notes.txt").write_text("x")
    (log / "0.jsonl").write_text("x")
    assert "OPEN 0 NEXT 1" in run(cli, kv, "open %s" % log)


def test_delete_oldest_never_touches_the_open_segment(cli, dirs):
    kv, log = dirs
    log.mkdir()
    for first in (1, 100, 200):
        (log / ("%d.jsonl" % first)).write_text('{"seq":%d,"boot":1}\n' % first)
    out = run(cli, kv, "open %s" % log, "deloldest", "deloldest", "deloldest", "close")
    assert [ln for ln in out if ln.startswith("DEL")] == ["DEL 0", "DEL 0", "DEL -1"]
    assert sorted(p.name for p in log.iterdir()) == ["200.jsonl"]


def test_full_queue_drops_and_counts_and_no_card_queues_nothing(cli, dirs):
    kv, log = dirs
    notify = "io %d 1602 %s" % (NOTIFY, ENERGY)
    out = run(
        cli, kv, "init 2", "json", notify, "card ok 30400 28800", notify, notify, notify, "queued", "json"
    )
    assert "QUEUED 2" in out
    assert sd(out) == {"state": "ok", "total_mb": 30400, "free_mb": 28800, "last_seq": None, "dropped": 1}
    out = run(cli, kv, "init", "card absent 0 0", notify, "queued", "json")
    assert "QUEUED 0" in out
    assert sd(out) == {"state": "absent", "total_mb": None, "free_mb": None, "last_seq": None, "dropped": 0}
    assert sd(run(cli, kv, "init none", "card error 0 0", notify, "json"))["state"] == "error"


def test_snapshot_every_5_min_only_while_linked(cli, dirs):
    kv, log = dirs
    run(
        cli,
        kv,
        *logging_session(
            log,
            "frame 3 %s" % ENERGY,  # CODEC_CHARS[3] = energy
            "frame 13 0102",  # water
            "tick 1000",  # link down: no snapshot, next due at 301000
            "link 1",
            "tick 300999",
            "tick 301000",
            "tick 400000",
            "tick 601000",
        ),
    )
    recs = records(log)
    assert [(r["ev"], r["fn"]) for r in recs] == [("snap", "energy"), ("snap", "water")] * 2
    assert recs[0]["hex"] == ENERGY


def test_last_seq_is_published_once_fsynced(cli, dirs):
    """device.sd.last_seq is what is on the card: buffered records count only after the fsync."""
    kv, log = dirs
    out = run(cli, kv, "card ok 1 1", "open %s" % log, "fill 3", "json", "sync", "json", "close")
    assert [o["last_seq"] for o in (json.loads(ln[5:])["sd"] for ln in out if ln.startswith("JSON "))] == [
        None,
        3,
    ]


# ---- GET /api/log paging core (R_FW_LOG_API) ----


def page(cli, kv, log, since, max_=0, cap=1 << 20, boot=7):
    """One cali_sdlog_page: (records, trailer) through calictl's JSON reader."""
    out = run(cli, kv, "open %s" % log, "page %d %d %d %d" % (since, max_, cap, boot), "close")
    body = out[out.index(next(ln for ln in out if ln.startswith("PAGE"))) + 1 : out.index("END")]
    lines = [json.loads(ln) for ln in body]
    return lines[:-1], lines[-1]


def test_log_page_spans_segments_and_resumes_inside_one(cli, dirs):
    """Pages run across 1 MB segments in seq order; since inside a segment starts right after it;
    max and the buffer bound a page and say more; since past the end is an empty, final page.

    .. test:: /api/log paging over segments, a cut last line and a deleted oldest segment
       :id: T_FW_LOG_API_PAGE
       :links: R_FW_LOG_API
    """
    kv, log = dirs
    run(cli, kv, "boot", "open %s" % log, "fill 9000", "close")
    second = min(int(p.stem) for p in log.glob("*.jsonl") if p.stem != "1")
    recs, tr = page(cli, kv, log, since=second - 3, max_=5)
    assert [r["seq"] for r in recs] == list(range(second - 2, second + 3))  # across the roll
    assert tr == {"next": second + 2, "more": True, "boot": 7, "lost": 0}
    assert all(r["ev"] == "notify" and r["fn"] == "energy" for r in recs)
    recs, tr = page(cli, kv, log, since=100)  # default max 2000
    assert [r["seq"] for r in recs] == list(range(101, 2101)) and tr["more"] is True
    recs, tr = page(cli, kv, log, since=8990)
    assert [r["seq"] for r in recs] == list(range(8991, 9001)) and tr == {
        "next": 9000,
        "more": False,
        "boot": 7,
        "lost": 0,
    }
    recs, tr = page(cli, kv, log, since=9000)
    assert recs == [] and tr == {"next": 9000, "more": False, "boot": 7, "lost": 0}
    recs, tr = page(cli, kv, log, since=0, cap=2000)  # the buffer bounds the page too
    assert 5 <= len(recs) < 20 and tr["more"] is True and tr["next"] == recs[-1]["seq"]


def test_log_page_skips_a_cut_last_line_and_reports_a_deleted_oldest_segment(cli, dirs):
    """Segments 1..99 deleted (90 % full): the page resumes at the oldest one and reports the lost
    seqs; a half-written last line (no '\\n') is never sent. Read without cali_sdlog_open, which
    would truncate the cut line first."""
    kv, log = dirs
    log.mkdir()
    for first in (100, 200):
        (log / ("%d.jsonl" % first)).write_text(
            "".join('{"seq":%d,"boot":1,"ev":"connect","addr":null}\n' % s for s in range(first, first + 3))
        )
    with open(log / "200.jsonl", "a") as f:
        f.write('{"seq":203,"boot":1,"up":1')

    def raw_page(since):
        out = run(cli, kv, "page %d 0 65536 2 %s" % (since, log))
        lines = [json.loads(ln) for ln in out[1 : out.index("END")]]  # out[0] = "PAGE <bytes>"
        return lines[:-1], lines[-1]

    recs, tr = raw_page(50)
    assert [r["seq"] for r in recs] == [100, 101, 102, 200, 201, 202]
    assert tr == {"next": 202, "more": False, "boot": 2, "lost": 49}
    recs, tr = raw_page(101)
    assert [r["seq"] for r in recs] == [102, 200, 201, 202] and tr["lost"] == 0
    recs, tr = raw_page(0)  # seq 1 is the first ever: 1..99 lost
    assert tr["lost"] == 99 and recs[0]["seq"] == 100


def test_esp_ingest_reads_the_c_pager_over_http(cli, dirs, tmp_path, monkeypatch):
    """The contract end to end: segments on "the card", the C pager (cali_sdlog_page) behind a real HTTP
    socket, calictl's http_fetch + ingest paging through them in small pages until more=false.

    .. test:: esp-ingest pages the firmware's real /api/log output
       :id: T_ESP_INGEST_E2E
       :links: R_ESP_INGEST_GAPFILL, R_FW_LOG_API
    """
    import http.server
    import sys
    import threading
    import types
    import urllib.parse

    from calictl import esp_ingest

    kv, log = dirs
    log.mkdir()
    cooler = "0843000000000000"
    lines = [
        '{"seq":%d,"boot":4,"up":%d,"ts":%d,"tsrc":"ntp","ev":"notify","char":"1102","fn":"cooler","hex":"%s"}\n'
        % (s, s * 1000, 1789639000000 + s * 200000, cooler)
        for s in range(1, 8)
    ]
    (log / "1.jsonl").write_text("".join(lines[:4]))
    (log / "5.jsonl").write_text("".join(lines[4:]))

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            out = run(cli, kv, "page %s %s 65536 4 %s" % (q["since"][0], q["max"][0], log))
            body = ("\n".join(out[1 : out.index("END")]) + "\n").encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    mod = types.ModuleType("influxdb_client")

    class P:  # a stand-in influxdb_client.Point: the tag/field/time chain
        def __init__(self, m):
            self.tags, self.fields, self.t = {}, {}, None

        def tag(self, k, v):
            self.tags[k] = v
            return self

        def field(self, k, v):
            self.fields[k] = v
            return self

        def time(self, t, prec):
            self.t = t
            return self

    mod.Point, mod.WritePrecision = P, types.SimpleNamespace(MS="ms")
    monkeypatch.setitem(sys.modules, "influxdb_client", mod)
    written = []

    class DB:
        def times(self, lo, hi, source=None):
            return {}

        def write(self, pts):
            written.extend(pts)

    try:
        s = esp_ingest.ingest(
            esp_ingest.http_fetch("http://127.0.0.1:%d" % srv.server_port),
            DB(),
            str(tmp_path / "cur.json"),
            page=3,
        )
    finally:
        srv.shutdown()
    assert s["pulled"] == 7 and s["written"] == 7
    assert [p.t for p in written] == [1789639000000 + n * 200000 for n in range(1, 8)]
    assert json.loads((tmp_path / "cur.json").read_text())["seq"] == 7
