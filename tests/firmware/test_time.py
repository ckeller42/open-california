"""cali_time: the boot counter and the SNTP sync state the SD log stamps records with.

``firmware/components/cali_core/time.c`` driven through ``test/time_cli.c`` over the real host
platform (``platform_host.c``: a file-backed kv store, so one process = one boot and the counter
persists like NVS). The SNTP client itself (``components/cali_sntp``) is ESP-only.

.. test:: Boot counter +1 per boot, ts null until the first SNTP sync, device.time shape
   :id: T_FW_TIME
   :links: R_FW_TIME
"""

import json
import shutil
import subprocess
from pathlib import Path

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
            str(CORE / "time.c"),
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


def time_obj(out):
    return [json.loads(ln[5:])["time"] for ln in out if ln.startswith("JSON ")][-1]


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
    assert [ln for ln in out if ln.startswith("TS ")] == ["TS none", "TS 1789639127412"]
    assert time_obj(out) == {"source": "sntp", "synced": True, "boot": 1}
    assert [json.loads(ln[5:])["time"]["synced"] for ln in out if ln.startswith("JSON ")] == [False, True]


def test_a_new_boot_starts_unsynced(time_cli, tmp_path):
    run(time_cli, tmp_path, "init", "sntp", "synced")
    out = run(time_cli, tmp_path, "init", "sntp", "ts", "json")
    assert "TS none" in out and time_obj(out) == {"source": "sntp", "synced": False, "boot": 2}
