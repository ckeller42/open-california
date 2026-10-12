"""cali_diag: the diagnostics records on the SD log (R_FW_DIAG_LOG) — record bodies, the diag cadence,
perf sampling and the rate-limited log mirror.

``firmware/components/cali_core/diag.c`` driven through ``test/diag_cli.c``, whose sink prints each
record body. The stamp/seq/queue side is ``test_sdlog.py`` (T_FW_DIAG_SDLOG); the call sites in
the session, the control sequencer and the WiFi runner are ``test_session_fake.py``
(T_FW_DIAG_EVENTS); the HTTP core's perf record is ``test_http_core.py`` (T_FW_DIAG_HTTP_PERF). The
ESP hooks (heap/stack/RSSI collection, the ``esp_log_set_vprintf`` hook, the reset reason) are
board-only.

.. test:: Diagnostics record shapes, the 60 s cadence, BLE perf sampling, the log mirror's rate limit
   :id: T_FW_DIAG_LOG
   :links: R_FW_DIAG_LOG
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
BODY_MAX = int(
    re.search(r"#define CALI_DIAG_BODY_MAX (\d+)", (CORE / "include" / "cali_diag.h").read_text())[1]
)


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("diag") / "diag_cli"
    subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include"), str(CORE / "diag.c"),
         str(CORE / "test" / "diag_cli.c"), "-o", str(out)],
        check=True,
    )  # fmt: skip
    return out


def run(cli, *lines):
    out = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    assert not [ln for ln in out.stdout.splitlines() if ln.startswith("ERR")], out.stdout
    return out.stdout.splitlines()


def recs(out):
    return [json.loads(ln[4:]) for ln in out if ln.startswith("REC ")]


def test_evt_and_rec_shapes(cli):
    out = run(cli, 'evt link_up "reconnect_ms":1200,"attempts":2', "evt sd", "nosink", "evt x")
    assert recs(out) == [
        {"ev": "evt", "kind": "link_up", "reconnect_ms": 1200, "attempts": 2},
        {"ev": "evt", "kind": "sd"},
    ]
    assert out[-1] == "RC -1"  # no sink: nothing recorded
    too_long = 'evt x "msg":"%s"' % ("y" * BODY_MAX)
    assert run(cli, too_long) == ["RC -1"]  # never a cut (invalid) record


def test_health_record_fits_at_its_longest_and_nulls_the_unknown(cli):
    out = run(cli, "health max", "health min")
    longest, unknown = recs(out)
    assert out.count("RC 0") == 2
    assert len(json.dumps(longest, separators=(",", ":"))) < BODY_MAX
    assert list(longest) == [
        "ev", "heap_free", "heap_min", "psram_free", "cpu_pct", "stack_min",
        "wifi_rssi", "ble_rssi", "link_up", "uptime_s",
    ]  # fmt: skip
    assert len(longest["stack_min"]) == 8 and longest["link_up"] is True
    assert unknown == {
        "ev": "diag", "heap_free": None, "heap_min": None, "psram_free": None, "cpu_pct": None,
        "stack_min": {}, "wifi_rssi": None, "ble_rssi": None, "link_up": False, "uptime_s": 0,
    }  # fmt: skip


def test_boot_event(cli):
    out = run(cli, "boot panic 5ff05a2 17 3600", "boot poweron 5ff05a2-dirty 18 -1")
    assert recs(out) == [
        {"ev": "evt", "kind": "boot", "reset_reason": "panic", "fw": "5ff05a2", "boot": 17, "prev_up_s": 3600},
        {"ev": "evt", "kind": "boot", "reset_reason": "poweron", "fw": "5ff05a2-dirty", "boot": 18, "prev_up_s": None},
    ]  # fmt: skip


def test_diag_is_due_every_60_s_from_the_first_call(cli):
    out = run(cli, *("due %d" % t for t in (100, 60099, 60100, 60200, 120099, 120100, 500000)))
    assert [ln[4:] for ln in out] == ["0", "0", "1", "0", "0", "1", "1"]


def test_http_route_is_the_path_never_a_404s_or_a_quote(cli):
    out = run(cli, "http 200 4711 100 /api/state", "http 404 9 0 /wp-admin.php", "http 400 11 0",
              'http 200 1 0 /a"b', "http 200 1 0 /" + "x" * 60)  # fmt: skip
    r = recs(out)
    assert r[0] == {"ev": "perf", "route": "/api/state", "status": 200, "bytes": 4711, "ms": 100}
    assert [x["route"] for x in r[1:4]] == ["other", "-", '/a"b']
    assert len(r[4]["route"]) == 40


def test_ble_ops_are_timed_each_at_most_1_per_s_else_1_in_10(cli):
    # water re-read every 30 s: every one; heartbeat every 600 ms: one a minute; a char's first op always
    lines = ["ble 1302 read 80 %d" % t for t in (0, 30000, 60000)]
    lines += ["ble 1003 hb 40 %d" % (t * 600) for t in range(250)]  # 0 .. 149.4 s
    lines += ["ble 1101 write 30 %d" % (t * 600) for t in range(25)]  # any other op asked often: 1 in 10
    out = recs(run(cli, *lines))
    assert [r["ms"] for r in out if r["char"] == "1302"] == [80, 80, 80]
    hb = [r for r in out if r["char"] == "1003"]
    assert len(hb) == 3 and hb[0] == {"ev": "perf", "char": "1003", "op": "hb", "ms": 40}  # 0, 60, 120 s
    assert len([r for r in out if r["char"] == "1101"]) == 3


def test_log_mirror_keeps_warnings_and_errors_only_20_a_minute(cli):
    w = '\\e[0;33mW (1234) wifi: sta: "beacon" timeout\\e[0m'  # \e: ESC for the driver
    lines = [
        "log 1000 " + w,
        "log 1001 I (1) wifi: chatter",
        "log 1002 E (5) sd: write failed",
        "log 1003 junk",
    ]
    lines += ["log %d W (9) ble: storm %d" % (2000 + i, i) for i in range(30)]
    lines += ["log 61000 W (9) ble: next minute"]
    out = recs(run(cli, *lines))
    assert out[0] == {"ev": "log", "lvl": "W", "tag": "wifi", "msg": 'sta: "beacon" timeout'}
    assert out[1] == {"ev": "log", "lvl": "E", "tag": "sd", "msg": "write failed"}
    storm = [r for r in out if r.get("tag") == "ble"]
    assert len(storm) == 19  # 20 a minute incl. the two above, so 18 of the storm + the next minute's
    assert {"ev": "log", "dropped": 12} in out and out[-1]["msg"] == "next minute"
    # the diag tick ends a minute without a further warning
    quiet = recs(
        run(cli, *("log %d W (9) x: y" % (10 + i) for i in range(25)), "logflush 30000", "logflush 60010")
    )
    assert quiet[-1] == {"ev": "log", "dropped": 5} and len(quiet) == 21
