"""The BLE power experiment's schedule (``ble_exp.c``, R_FW_BLE_EXP): hold -> quiet -> off, a probe at each
phase end, interrupted by activity. Pure C99, driven through ``test/ble_exp_cli.c``. R_FW_BLE_EXP is in
docs/firmware.md.
"""

import re
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
    """The scheduler: window.

    .. test:: BLE experiment: window
       :id: T_FW_BLE_EXP_WINDOW
       :links: R_FW_BLE_EXP
    """
    out = run(cli, "win 22 22 6", "win 5 22 6", "win 6 22 6", "win 12 22 6", "win 3 0 0", "win -1 22 6")
    assert out == ["WIN 1", "WIN 1", "WIN 0", "WIN 0", "WIN 1", "WIN 0"]


def test_cycle_hold_quiet_off_with_a_probe_at_each_phase_end(cli):
    """The scheduler: phase cycle.

    .. test:: BLE experiment: phase cycle
       :id: T_FW_BLE_EXP_CYCLE
       :links: R_FW_BLE_EXP
    """
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
    """The scheduler: abort + resume.

    .. test:: BLE experiment: abort + resume
       :id: T_FW_BLE_EXP_ABORT
       :links: R_FW_BLE_EXP
    """
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
    """The scheduler: external mode change.

    .. test:: BLE experiment: external mode change
       :id: T_FW_BLE_EXP_EXTERNAL
       :links: R_FW_BLE_EXP
    """
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
        f"step {2 * MIN + 5 + 10 * MIN - 1} 12 {H} 0",  # the outside change holds 10 min ...
        f"step {2 * MIN + 5 + 10 * MIN} 12 {H} 0",  # ... then the next phase (hold)
    )
    assert o[-3] == "OUT set=-1 probe=0 done=2 aborted=1 started=-1"
    assert o[-2] == "OUT set=-1 probe=0 done=-1 aborted=0 started=-1"
    assert o[-1] == "OUT set=-1 probe=0 done=-1 aborted=0 started=0"


def test_window_close_ends_the_phase_and_returns_to_hold(cli):
    """The scheduler: window close.

    .. test:: BLE experiment: window close
       :id: T_FW_BLE_EXP_CLOSE
       :links: R_FW_BLE_EXP
    """
    o = steps(cli, "start 60 22 6", f"step 0 23 {H} 0", f"step 1 23 {H} 0", f"step 2 6 {Q} 0")
    assert o[-1] == "OUT set=0 probe=0 done=0 aborted=1 started=-1"


def test_mode_forced_during_the_probe_counts_as_abort(cli):
    """A command forces ``hold`` while the off phase's probe runs: the phase ends aborted, not done.

    .. test:: BLE experiment: a mode forced during the probe aborts the phase
       :id: T_FW_BLE_EXP_PROBE_FORCED
       :links: R_FW_BLE_EXP
    """
    o = steps(
        cli,
        "start 1 0 0",
        f"step 0 12 {H} 0",  # hold starts
        f"step {MIN} 12 {H} 0",  # hold ends -> probe
        f"step {MIN + 1} 12 {H} 0",  # probe done
        f"step {MIN + 2} 12 {H} 0",  # quiet starts
        f"step {2 * MIN + 2} 12 {Q} 0",  # quiet ends -> probe
        f"step {2 * MIN + 3} 12 {Q} 0",  # probe done
        f"step {2 * MIN + 4} 12 {Q} 0",  # off starts
        f"step {3 * MIN + 4} 12 {O} 0",  # off ends -> probe
        f"step {3 * MIN + 5} 12 {O} 1",  # probe runs
        f"step {3 * MIN + 6} 12 {H} 0",  # probe ended, but a command forced hold meanwhile
        f"step {3 * MIN + 6 + 10 * MIN - 1} 12 {H} 0",  # held 10 min
        f"step {3 * MIN + 6 + 10 * MIN} 12 {H} 0",
    )
    assert o[-5] == "OUT set=-1 probe=1 done=-1 aborted=0 started=-1"
    assert o[-3] == "OUT set=-1 probe=0 done=2 aborted=1 started=-1"
    assert o[-2] == "OUT set=-1 probe=0 done=-1 aborted=0 started=-1"
    assert o[-1] == "OUT set=-1 probe=0 done=-1 aborted=0 started=0"


def test_quiet_forced_off_during_the_probe_aborts_back_to_hold(cli):
    """The quiet phase's probe ends with the mode forced to ``off`` from outside: aborted, back to hold.

    .. test:: BLE experiment: a non-hold mode forced during the probe returns to hold
       :id: T_FW_BLE_EXP_PROBE_FORCED_OFF
       :links: R_FW_BLE_EXP
    """
    o = steps(
        cli,
        "start 1 0 0",
        f"step 0 12 {H} 0",
        f"step {MIN} 12 {H} 0",
        f"step {MIN + 1} 12 {H} 0",
        f"step {MIN + 2} 12 {H} 0",  # quiet starts
        f"step {2 * MIN + 2} 12 {Q} 0",  # -> probe
        f"step {2 * MIN + 3} 12 {Q} 1",
        f"step {2 * MIN + 4} 12 {O} 0",  # forced off meanwhile
        f"step {2 * MIN + 5} 12 {H} 0",  # still within the 10-min hold
    )
    assert o[-2] == "OUT set=0 probe=0 done=1 aborted=1 started=-1"
    assert o[-1] == "OUT set=-1 probe=0 done=-1 aborted=0 started=-1"


def _gone_ms():
    """CALI_BX_GONE_MS from the header; it must stay 3 sensor scan periods + one window (sensors.c)."""
    gone = int(re.search(r"#define CALI_BX_GONE_MS (\d+)u", (CORE / "include/cali_ble_exp.h").read_text())[1])
    sens = (CORE / "sensors.c").read_text()
    period = int(re.search(r"#define SENS_PERIOD_MS (\d+)u", sens)[1])
    window = int(re.search(r"#define SENS_WINDOW_MS (\d+)u", sens)[1])
    assert gone == 3 * period + window
    return gone


def test_unit_adv_transitions_only(cli):
    """The unit's advert sighting: only the transitions report (1 seen, -1 gone after CALI_BX_GONE_MS).

    .. test:: BLE experiment: unit advert sighting transitions
       :id: T_FW_BLE_EXP_ADV
       :links: R_FW_BLE_EXP
    """
    G = _gone_ms()
    out = run(
        cli,
        "start 60 0 0",
        "seen 0 1",
        "seen 1000 1",
        f"seen {G + 999} 0",
        f"seen {G + 1000} 0",
        f"seen {G + 2000} 1",
    )
    assert [ln for ln in out if ln.startswith("SEEN")] == ["SEEN 1", "SEEN 0", "SEEN 0", "SEEN -1", "SEEN 1"]


def test_unit_adv_unknown_at_start_goes_gone_without_a_sighting(cli):
    """At the experiment's start the state is unknown: no sighting for CALI_BX_GONE_MS from the first
    check is a gone (so a later sighting is a reappearance); off, nothing is reported.

    .. test:: BLE experiment: unit advert unknown at start, gone without a sighting
       :id: T_FW_BLE_EXP_ADV_UNKNOWN
       :links: R_FW_BLE_EXP
    """
    G = _gone_ms()
    out = run(
        cli,
        "seen 0 1",
        "start 60 0 0",
        "seen 5 0",
        f"seen {G + 4} 0",
        f"seen {G + 5} 0",
        f"seen {G + 6} 0",
        "stop",
        f"seen {G + 7} 1",
    )
    assert [ln for ln in out if ln.startswith("SEEN")] == [
        "SEEN 0",
        "SEEN 0",
        "SEEN 0",
        "SEEN -1",
        "SEEN 0",
        "SEEN 0",
    ]


# ---- the idle policy (R_FW_BLE_IDLE): hold while active, else off + a probe every probe_min ----
IDLE = 10 * MIN  # CALI_BX_IDLE_MS


def isteps(cli, *lines):
    return [x for x in run(cli, *lines) if x.startswith("IOUT")]


def test_idle_policy_off_after_idle_then_a_probe_every_interval(cli):
    """Hold for CALI_BX_IDLE_MS after the setting, then off; a probe every probe_min while off; a probe in
    flight is never cut (no mode change while busy).

    .. test:: BLE idle policy: off after idle, periodic probe
       :id: T_FW_BLE_IDLE_PROBE
       :links: R_FW_BLE_IDLE
    """
    o = isteps(
        cli,
        "iset 5 0",
        f"istep 0 {H} 0 0",  # active: hold (already hold)
        f"istep {IDLE} {H} 0 0",  # idle -> off
        f"istep {IDLE + 1} {O} 0 0",  # off: no probe yet
        f"istep {IDLE + 5 * MIN} {O} 0 0",  # 5 min -> probe
        f"istep {IDLE + 5 * MIN + 100} {O} 1 0",  # probe busy -> nothing
        f"istep {IDLE + 5 * MIN + 6000} {O} 0 0",  # probe done -> nothing
        f"istep {IDLE + 10 * MIN} {O} 0 0",  # next probe
    )
    assert o == [
        "IOUT set=-1 probe=0",
        f"IOUT set={O} probe=0",
        "IOUT set=-1 probe=0",
        "IOUT set=-1 probe=1",
        "IOUT set=-1 probe=0",
        "IOUT set=-1 probe=0",
        "IOUT set=-1 probe=1",
    ]


def test_idle_policy_activity_and_outside_mode_hold_ten_minutes(cli):
    """A viewer / the ignition (activity) or a mode the policy did not set (a command forces hold) holds
    for CALI_BX_IDLE_MS from then; activity during a probe holds once the probe ends.

    .. test:: BLE idle policy: activity and commands hold
       :id: T_FW_BLE_IDLE_ACTIVITY
       :links: R_FW_BLE_IDLE
    """
    t = IDLE
    o = isteps(
        cli,
        "iset 5 0",
        f"istep {t} {H} 0 0",  # -> off
        "iact",
        f"istep {t + 1000} {O} 0 0",  # viewer -> hold
        f"istep {t + 1000 + IDLE - 1} {H} 0 0",  # still active
        f"istep {t + 1000 + IDLE} {H} 0 0",  # idle -> off
        f"istep {t + 2 * IDLE} {H} 0 0",  # a command set hold -> outside change: stays hold
        f"istep {t + 3 * IDLE - 1} {H} 0 0",
        f"istep {t + 3 * IDLE} {H} 0 0",  # idle again -> off
        "iact",
        f"istep {t + 3 * IDLE + 100} {O} 1 0",  # activity during a probe: wait
        f"istep {t + 3 * IDLE + 200} {O} 0 0",  # probe ended -> hold
    )
    assert o == [
        f"IOUT set={O} probe=0",
        f"IOUT set={H} probe=0",
        "IOUT set=-1 probe=0",
        f"IOUT set={O} probe=0",
        "IOUT set=-1 probe=0",
        "IOUT set=-1 probe=0",
        f"IOUT set={O} probe=0",
        "IOUT set=-1 probe=0",
        f"IOUT set={H} probe=0",
    ]


def test_idle_policy_hold_setting_and_experiment_take_precedence(cli):
    """probe_min 0 (the default) never changes the mode; a running experiment owns the session and the
    policy holds CALI_BX_IDLE_MS after it ends.

    .. test:: BLE idle policy: hold setting, experiment precedence
       :id: T_FW_BLE_IDLE_OFF
       :links: R_FW_BLE_IDLE
    """
    assert isteps(cli, "iset 0 0", f"istep {IDLE} {H} 0 0", f"istep {5 * IDLE} {O} 0 0") == [
        "IOUT set=-1 probe=0",
        "IOUT set=-1 probe=0",
    ]
    o = isteps(
        cli,
        "iset 5 0",
        f"istep {IDLE} {Q} 0 1",  # experiment runs (quiet phase): hands off
        f"istep {3 * IDLE} {O} 0 1",
        f"istep {3 * IDLE + 1000} {H} 0 0",  # experiment ended (hold): active 10 min
        f"istep {4 * IDLE} {H} 0 0",  # -> off
    )
    assert o == ["IOUT set=-1 probe=0", "IOUT set=-1 probe=0", "IOUT set=-1 probe=0", f"IOUT set={O} probe=0"]
