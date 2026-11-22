"""tools/esplab/bench.sh against stubs of ssh, the board console and esptool (no hardware).

.. test:: Bench script: status names a bond to the bench's fake unit; release erases only a confirmed board
   :id: T_ESP_BENCH
   :links: R_FAKE_UNIT_FIDELITY
"""

import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCH = ROOT / "tools/esplab/bench.sh"
FAKE_UNIT = "02:00:00:00:00:01"  # a locally administered example address
BOARD = "AA:BB:CC:DD:EE:01"


def stub(path: pathlib.Path, body: str) -> str:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def bench(tmp_path):
    log = tmp_path / "calls.log"
    port = tmp_path / f"usb-Espressif_USB_JTAG_serial_debug_unit_{BOARD}-if00"
    port.touch()
    state = tmp_path / "state.json"
    env = dict(
        os.environ,
        ESP_PORT=str(port),
        BENCH_STATE=str(tmp_path / "bench"),
        BENCH_SSH=stub(
            tmp_path / "ssh",
            f'echo "ssh $*" >> {log}\n'
            'case "$*" in *hciconfig*) echo "identity '
            + FAKE_UNIT
            + '"; echo "BD Address: 02:00:00:00:00:02 ACL";; '
            "*is-active*) echo active;; esac\n",
        ),
        ESP_CMD=stub(tmp_path / "esp_cmd", f'echo "esp_cmd $*" >> {log}\necho "STATE $(cat {state})"\n'),
        ESPTOOL=stub(tmp_path / "esptool", f'echo "esptool $*" >> {log}\n'),
        PATH=f"{tmp_path}:{os.environ['PATH']}",
    )
    stub(tmp_path / "sleep", "")  # release waits for the boot: no real wait in the test
    build = tmp_path / "build"
    build.mkdir()
    (build / "flasher_args.json").write_text("{}")
    stub(build / "flash.sh", f'echo "flash.sh $*" >> {log}\n')

    def run(*args, bond=None, **extra):
        state.write_text(
            json.dumps({"state": "idle", "address": bond, "wifi": {"mode": "setup", "ssid": None}})
        )
        log.write_text("")
        p = subprocess.run(
            ["bash", str(BENCH), *args], env={**env, **extra}, capture_output=True, text=True, timeout=30
        )
        return p, log.read_text()

    run.build = build
    run.port = str(port)
    return run


def test_status_names_a_bond_to_the_bench_fake_unit(bench):
    p, _ = bench("status", bond=FAKE_UNIT)
    assert p.returncode == 0, p.stderr
    assert "fake unit:    active" in p.stdout
    assert f"board:        {BOARD} wifi setup -" in p.stdout
    assert "the bench's fake unit: run 'bench.sh release'" in p.stdout


def test_status_clean_board_and_other_unit(bench):
    assert "bond:         none (clean)" in bench("status", bond=None)[0].stdout
    assert "another unit" in bench("status", bond="02:00:00:00:00:99")[0].stdout
    assert "the bench's fake unit" in bench("status", bond="02:00:00:00:00:02")[0].stdout  # the controller


def test_release_without_confirmation_changes_nothing(bench):
    p, calls = bench("release", str(bench.build), bond=FAKE_UNIT, BENCH_YES="AA:BB:CC:DD:EE:02")
    assert p.returncode != 0 and "not confirmed" in p.stderr
    assert "esptool" not in calls and "flash.sh" not in calls


def test_release_needs_a_build_dir(bench, tmp_path):
    p, calls = bench("release", str(tmp_path / "nothing"), BENCH_YES=BOARD)
    assert p.returncode != 0 and "no flasher_args.json" in p.stderr
    assert "esptool" not in calls


def test_release_erases_then_flashes_the_confirmed_board(bench):
    p, calls = bench("release", str(bench.build), bond=None, BENCH_YES=BOARD)
    assert p.returncode == 0, p.stderr
    lines = calls.splitlines()
    erase = lines.index(f"esptool --chip esp32s3 -p {bench.port} erase-flash")
    assert lines.index(f"flash.sh {bench.port}") > erase
    assert f"{BOARD} is clean" in p.stdout


def test_release_fails_when_the_board_keeps_a_bond(bench):
    p, _ = bench("release", str(bench.build), bond=FAKE_UNIT, BENCH_YES=BOARD)
    assert p.returncode != 0 and "did not report a clean state" in p.stderr
