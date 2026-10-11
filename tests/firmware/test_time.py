"""cali_time: the boot counter, the SNTP sync state and the unit clock the SD log stamps records with.

``firmware/components/cali_core/time.c`` driven through ``test/time_cli.c`` over the real host
platform (``platform_host.c``: a file-backed kv store, so one process = one boot and the counter
persists like NVS). The SNTP client itself (``components/cali_sntp``) is ESP-only.

.. test:: Boot counter +1 per boot, ts null until the first SNTP sync, device.time shape
   :id: T_FW_TIME
   :links: R_FW_TIME

.. test:: Unit clock (1004 CarTime*, Europe/Berlin) -> UTC across both DST switches; ntp > unit > none
   :id: T_FW_TIME_UNIT
   :links: R_FW_TIME
"""

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
PLATFORM = ROOT / "firmware" / "components" / "platform"


@pytest.fixture(scope="module")
def time_cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("time") / "time_cli"
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
            str(CORE / "time.c"),
            str(ROOT / "csrc" / "codec.c"),
            str(CORE / "json.c"),
            str(PLATFORM / "host" / "platform_host.c"),
            str(CORE / "test" / "time_cli.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def run(cli, store, *lines):
    out = subprocess.run(
        [str(cli), str(store)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def times(out):
    """Every ``json`` line's device.time, ``up`` checked (uptime ms of this run) and dropped."""
    objs = [json.loads(ln[5:])["time"] for ln in out if ln.startswith("JSON ")]
    for o in objs:
        assert isinstance(o.pop("up"), int)
    return objs


def time_obj(out):
    return times(out)[-1]


def test_boot_counter_counts_every_boot(time_cli, tmp_path):
    boots = [run(time_cli, tmp_path, "init") for _ in range(3)]
    assert [b[-1] for b in boots] == ["BOOT 1", "BOOT 2", "BOOT 3"]
    assert "LOG time: boot 3" in boots[-1]


def test_corrupt_counter_restarts_at_one_and_says_so(time_cli, tmp_path):
    run(time_cli, tmp_path, "init", "init")
    next(tmp_path.glob("boot_count*")).write_bytes(b"\x04\x00\x00\x00junk")  # CRC broken
    out = run(time_cli, tmp_path, "init")
    assert out[-1] == "BOOT 1" and "LOG time: boot counter corrupt, restarting at 1" in out
    assert run(time_cli, tmp_path, "init")[-1] == "BOOT 2"  # and counts on from there


def test_no_source_never_has_a_timestamp(time_cli, tmp_path):
    out = run(time_cli, tmp_path, "init", "synced", "ts", "json")
    assert "TS none" in out
    assert time_obj(out) == {"source": "none", "synced": False, "boot": 1}


def test_ts_is_null_until_the_first_sync_then_the_clock(time_cli, tmp_path):
    out = run(time_cli, tmp_path, "init", "sntp", "clock 1789639127412", "ts", "json", "synced", "ts", "json")
    assert [ln for ln in out if ln.startswith("TS ")] == ["TS none", "TS 1789639127412 ntp"]
    assert time_obj(out) == {"source": "sntp", "synced": True, "boot": 1}
    assert [o["synced"] for o in times(out)] == [False, True]


def test_a_new_boot_starts_unsynced(time_cli, tmp_path):
    """A clock already holding real time (it survived the reset) is not synced: only this boot's
    SNTP callback counts, never a plausible-looking clock."""
    run(time_cli, tmp_path, "init", "sntp", "synced")
    out = run(time_cli, tmp_path, "init", "sntp", "clock 1789639127412", "ts", "json")
    assert "TS none" in out and time_obj(out) == {"source": "sntp", "synced": False, "boot": 2}


BERLIN = ZoneInfo("Europe/Berlin")


def vehicle_frame(y, mo, d, h, mi, s):
    """A 1004 state frame (11 bytes, ignition on) carrying the unit's CarTime* as the unit encodes it
    (year - 1900, month - 1), the inverse of calictl.semantics.vehicle()."""
    return "80" + bytes([y - 1900, mo - 1, d, h, mi, s, 0, 0, 0, 0]).hex()


def ts_lines(out):
    return [ln.split()[1:] for ln in out if ln.startswith("TS ")]


@pytest.mark.parametrize(
    "day",
    [datetime(2026, 3, 29), datetime(2026, 10, 25), datetime(2027, 3, 28), datetime(2027, 10, 31)],
    ids=["spring-2026", "autumn-2026", "spring-2027", "autumn-2027"],
)
def test_unit_clock_to_utc_across_the_dst_switches(time_cli, tmp_path, day):
    """Every quarter hour from 22:00 the evening before to 05:00 after a switch, against zoneinfo:
    the March gap hour maps as CET (fold 0, the same instant as 03:xx CEST); the October hour that
    happens twice has no answer."""
    local = [day - timedelta(hours=2) + timedelta(minutes=15 * i) for i in range(29)]
    out = run(time_cli, tmp_path, *("utc %d %d %d %d %d %d" % t.timetuple()[:6] for t in local))
    got = [ln for ln in out if ln.startswith("UTC ")]
    want = []
    for t in local:
        a, b = t.replace(tzinfo=BERLIN, fold=0), t.replace(tzinfo=BERLIN, fold=1)
        ambiguous = a.utcoffset() != b.utcoffset() and t.month == 10
        want.append("UTC none" if ambiguous else "UTC %d" % round(a.timestamp() * 1000))
    assert got == want
    assert sum(w == "UTC none" for w in want) == (4 if day.month == 10 else 0)


def test_unit_clock_rejects_day_zero_and_bad_dates(time_cli, tmp_path):
    out = run(time_cli, tmp_path, "utc 1900 1 0 0 0 0", "utc 2026 2 29 12 0 0", "utc 2028 2 29 12 0 0")
    assert [ln for ln in out if ln.startswith("UTC")][:2] == ["UTC none", "UTC none"]
    assert out[-1] == "UTC %d" % (datetime(2028, 2, 29, 11, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)


def test_all_zero_unit_clock_gives_no_timestamp(time_cli, tmp_path):
    """The parked unit's all-zero clock (day 0): ts stays null, source none."""
    out = run(time_cli, tmp_path, "init", "vehicle " + "80" + "00" * 10, "ts", "json")
    assert "TS none" in out and time_obj(out)["source"] == "none"


def test_unit_clock_stamps_and_advances_with_uptime(time_cli, tmp_path):
    t0 = datetime(2026, 10, 11, 14, 30, 5, tzinfo=BERLIN)
    out = run(
        time_cli,
        tmp_path,
        "init",
        "vehicle " + vehicle_frame(2026, 10, 11, 14, 30, 5),
        "ts",
        "sleep 300",
        "ts",
        "json",
    )
    (ms1, src1), (ms2, src2) = ts_lines(out)
    assert src1 == src2 == "unit" and time_obj(out) == {"source": "unit", "synced": False, "boot": 1}
    assert int(ms1) - t0.timestamp() * 1000 < 200  # stamped right after the frame
    assert 300 <= int(ms2) - int(ms1) < 1300  # advanced by uptime between 1004 frames


def test_ambiguous_or_zero_frame_keeps_the_previous_anchor(time_cli, tmp_path):
    """01:59:58 CEST on the October switch day, then a frame in the ambiguous hour and an all-zero
    one: both skipped, the first anchor keeps counting."""
    t0 = datetime(2026, 10, 25, 1, 59, 58, tzinfo=BERLIN)
    out = run(
        time_cli,
        tmp_path,
        "init",
        "vehicle " + vehicle_frame(2026, 10, 25, 1, 59, 58),
        "vehicle " + vehicle_frame(2026, 10, 25, 2, 30, 0),
        "vehicle " + "80" + "00" * 10,
        "ts",
    )
    ((ms, src),) = ts_lines(out)
    assert src == "unit" and 0 <= int(ms) - t0.timestamp() * 1000 < 200


def test_ntp_wins_over_the_unit_clock(time_cli, tmp_path):
    out = run(
        time_cli,
        tmp_path,
        "init",
        "sntp",
        "clock 1789639127412",
        "vehicle " + vehicle_frame(2026, 10, 11, 14, 30, 5),
        "json",
        "ts",
        "synced",
        "ts",
        "json",
    )
    assert [src for _, src in ts_lines(out)] == ["unit", "ntp"]
    assert ts_lines(out)[1][0] == "1789639127412"
    assert [o["source"] for o in times(out)] == ["unit", "sntp"]
