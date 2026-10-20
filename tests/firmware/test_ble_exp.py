"""The BLE power experiment's schedule (``ble_exp.c``, R_FW_BLE_EXP): hold -> quiet -> off, a probe at each
phase end, interrupted by activity. Pure C99, driven through ``test/ble_exp_cli.c``.

.. req:: BLE experiment scheduler
   :id: R_FW_BLE_EXP
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/components/cali_core"
H, Q, O = 0, 1, 2
MIN = 60_000


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("bx") / "ble_exp_cli"
    subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include"), "-I", str(ROOT / "csrc")]
        + [str(CORE / "ble_exp.c"), str(CORE / "test/ble_exp_cli.c"), "-o", str(out)],
        check=True,
    )
    return out


def run(cli, *lines):
    out = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def steps(cli, *lines):
    return [x for x in run(cli, *lines) if x.startswith("OUT")]


def test_window_wraps_midnight_and_all_day(cli):
    """.. test:: window
    :id: T_FW_BLE_EXP_WINDOW
    :links: R_FW_BLE_EXP"""
    out = run(cli, "win 22 22 6", "win 5 22 6", "win 6 22 6", "win 12 22 6", "win 3 0 0", "win -1 22 6")
    assert out == ["WIN 1", "WIN 1", "WIN 0", "WIN 0", "WIN 1", "WIN 0"]


def test_cycle_hold_quiet_off_with_a_probe_at_each_phase_end(cli):
    """.. test:: phase cycle
    :id: T_FW_BLE_EXP_CYCLE
    :links: R_FW_BLE_EXP"""
    o = steps(
        cli,
        "start 1 22 6",
        f"step 0 23 {H} 0",  # starts hold (already hold: no set)
        f"step {MIN} 23 {H} 0",  # phase end -> probe
        f"step {MIN + 100} 23 {H} 1",  # probe busy -> nothing
        f"step {MIN + 200} 23 {H} 0",  # probe done -> phase done hold
        f"step {MIN + 300} 23 {H} 0",  # next: quiet
        f"step {2 * MIN + 300} 23 {Q} 0",
        f"step {2 * MIN + 400} 23 {Q} 0",
        f"step {2 * MIN + 500} 23 {Q} 0",  # next: off
    )
    assert o == [
        "OUT set=-1 probe=0 done=-1 aborted=0 started=0",
        "OUT set=-1 probe=1 done=-1 aborted=0 started=-1",
        "OUT set=-1 probe=0 done=-1 aborted=0 started=-1",
        "OUT set=-1 probe=0 done=0 aborted=0 started=-1",
        "OUT set=1 probe=0 done=-1 aborted=0 started=1",
        "OUT set=-1 probe=1 done=-1 aborted=0 started=-1",
        "OUT set=-1 probe=0 done=1 aborted=0 started=-1",
        "OUT set=2 probe=0 done=-1 aborted=0 started=2",
    ]


def test_activity_aborts_holds_ten_minutes_then_moves_to_the_next_phase(cli):
    """.. test:: abort + resume
    :id: T_FW_BLE_EXP_ABORT
    :links: R_FW_BLE_EXP"""
    o = steps(
        cli,
        "start 60 22 6",
        f"step 0 23 {H} 0",
        f"step 1000 23 {H} 0",
        "act 2000",
        f"step 2100 23 {H} 0",
        f"step {2000 + 10 * MIN - 1} 23 {H} 0",
        f"step {2000 + 10 * MIN} 23 {H} 0",
    )
    assert o[2] == "OUT set=-1 probe=0 done=0 aborted=1 started=-1"
    assert o[3] == "OUT set=-1 probe=0 done=-1 aborted=0 started=-1"  # still idle
    assert o[4] == "OUT set=1 probe=0 done=-1 aborted=0 started=1"  # next phase = quiet


def test_mode_changed_underneath_counts_as_abort(cli):
    """.. test:: external mode change
    :id: T_FW_BLE_EXP_EXTERNAL
    :links: R_FW_BLE_EXP"""
    o = steps(
        cli,
        "start 1 0 0",
        f"step 0 12 {H} 0",
        f"step {MIN} 12 {H} 0",
        f"step {MIN + 1} 12 {H} 0",
        f"step {MIN + 2} 12 {H} 0",
        f"step {2 * MIN + 2} 12 {Q} 0",
        f"step {2 * MIN + 3} 12 {Q} 0",
        f"step {2 * MIN + 4} 12 {Q} 0",
        f"step {2 * MIN + 5} 12 {H} 0",
    )
    assert o[-1] == "OUT set=-1 probe=0 done=2 aborted=1 started=-1"


def test_window_close_ends_the_phase_and_returns_to_hold(cli):
    """.. test:: window close
    :id: T_FW_BLE_EXP_CLOSE
    :links: R_FW_BLE_EXP"""
    o = steps(cli, "start 60 22 6", f"step 0 23 {H} 0", f"step 1 23 {H} 0", f"step 2 6 {Q} 0")
    assert o[-1] == "OUT set=0 probe=0 done=0 aborted=1 started=-1"
