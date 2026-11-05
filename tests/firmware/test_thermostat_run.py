"""thermostat_run.c against scripted stubs (session, control sequencer, sensors): the session hold
while a stop is pending, the reading plausibility, and a batch dropped after a switch-off or OFF.
``test/thermostat_run_cli.c``; macOS OK. End to end over NimBLE: ``test_thermostat_e2e.py``."""

import json
import pathlib
import subprocess

import pytest

from calictl import overrides, protocol
from tools.mock_unit import MockCamperUnit

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
OK, BUSY = 0, 6  # enum cali_ctl_rc_e


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    out = tmp_path_factory.mktemp("b") / "thermostat_run_cli"
    subprocess.run(
        ["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include"),
         "-I", str(ROOT / "firmware/components/platform/include"), "-I", str(ROOT / "csrc"),
         str(CORE / "thermostat_run.c"), str(CORE / "thermostat.c"), str(CORE / "json.c"), str(CORE / "diag.c"),
         str(ROOT / "csrc/codec.c"), str(ROOT / "firmware/components/platform/host/platform_host.c"),
         str(CORE / "test/thermostat_run_cli.c"), "-o", str(out)],
        check=True,
    )  # fmt: skip
    return out


def run(cli, tmp_path, lines):
    p = subprocess.run(
        [str(cli), str(tmp_path)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return p.stdout.splitlines()


_FUNCS = protocol.load()
overrides.apply(_FUNCS)


def frame(fn, **vals):
    m = MockCamperUnit()
    m.state[fn].update(vals)
    return "frame %s %s" % (fn, m.read(_FUNCS[fn].state_char).hex())


OFF_HEATER = frame("airheater", Installed=1, NormalOperation=0, PermanentOperation=0, HeatingLevel=5)
RUNNING = frame("airheater", Installed=1, NormalOperation=1, PermanentOperation=0, HeatingLevel=6)
SETUP = ["init", "on 1", "ref cabin", "target 2100", "set period_ms 1000", "ready 1", "ign 0", "now 1000",
         frame("campingmode", State=1), frame("energy", UTwoBattBemAfs=136), OFF_HEATER, "sensor cabin 1700"]  # fmt: skip
STARTED = [*SETUP, "auto", "tick", f"done {OK}", f"done {OK}", f"done {OK}", "now 1500", RUNNING, "tick"]


def submits(out):
    return [line[7:] for line in out if line.startswith("SUBMIT ")]


def state(out):
    return json.loads([line for line in out if line.startswith("THERMO ")][-1][7:])["thermostat"]


def test_start_batch_goes_out_one_by_one(cli, tmp_path):
    out = run(cli, tmp_path, [*STARTED, "state"])
    assert submits(out) == ["runtime 120", "level 6", "power on"]
    assert state(out)["mode"] == "AUTO" and "HOLD 1" in out


def test_hold_stays_while_a_stop_is_pending(cli, tmp_path):
    """A BUSY stop leaves mode OFF with the stop pending: the hold stays (a hang-up reconnects,
    test_session_fake's hold test), the next period sends power off again, and the steps of a
    disabled stop are logged; the hold drops once the readback shows the heater off.

    .. test:: Thermostat runner: the session hold covers a pending stop
       :id: T_FW_THERMOSTAT_RUN_PENDING_STOP
       :links: R_FW_THERMOSTAT_RUN
    """
    mark = len(run(cli, tmp_path, STARTED))
    out = run(cli, tmp_path, [*STARTED, "on 0", f"rc {BUSY}", "now 2000", "tick", "now 3000", "tick",
                              f"done {OK}", "now 3500", OFF_HEATER, "tick", "state"])  # fmt: skip
    tail = out[mark:]
    assert submits(tail) == ["power off", "power off"]
    assert tail.index("HOLD 0") > max(i for i, s in enumerate(tail) if s == "SUBMIT power off")
    recs = [json.loads("{" + s[4:] + "}") for s in tail if s.startswith('REC "ev":"thermo"')]
    assert len(recs) == 2 and all(
        r["m"] == "OFF" for r in recs
    )  # the pending DISABLED stop's steps are logged
    assert state(out)["mode"] == "OFF"


@pytest.mark.parametrize(
    "script, valid",
    [
        (["sensor cabin -"], False),  # no reading (NaN/inf: cali_sensors_value says -1, test_sensors.py)
        (["sensor cabin 6100"], False),  # out of range (60.0 C max)
        (["sensor cabin -4100"], False),  # out of range (-40.0 C min)
        (["tick", "now 31000", "sensor cabin 2700"], False),  # 10 C in 30 s
        (["tick", "now 61000", "sensor cabin 1950"], True),  # 2.5 C in a minute
        (
            ["tick", "now 31000", "sensor cabin 2700", "tick", "now 91000", "sensor cabin 1800"],
            True,
        ),  # vs 1700
        # two adverts 100 ms apart in one scan window (the step runs later, at the period)
        (["tick", "now 1100", "sensor cabin 1710", "now 2000"], True),  # a new 0.1 C measurement
        (["tick", "now 1100", "sensor cabin 2700", "now 2000"], False),  # 10 C
    ],
)
def test_implausible_reading_is_invalid(cli, tmp_path, script, valid):
    """A reading outside -40..60 C, or more than 3 C per minute from the last accepted one of the
    same alias, reaches the step as invalid (t_ref null); a slow change stays valid. The time
    difference is floored at a minute, so two adverts 100 ms apart may differ by up to 3 C.

    .. test:: Thermostat runner: implausible thermometer readings are invalid
       :id: T_FW_THERMOSTAT_RUN_PLAUSIBLE
       :links: R_FW_THERMOSTAT_RUN
    """
    out = run(cli, tmp_path, [*SETUP, "on 0", *script, "tick", "state"])
    st = state(out)
    assert (st["t_ref"] is not None) == valid, st


@pytest.mark.parametrize("cut", ["on 0", "stop"])
def test_batch_rest_dropped_after_switch_off_or_off_request(cli, tmp_path, cut):
    """The feature switched off, or an OFF request, while the START batch goes out: the rest of the
    batch stays home (no level, no power on).

    .. test:: Thermostat runner: a switch-off or OFF drops the rest of a batch
       :id: T_FW_THERMOSTAT_RUN_BATCH_DROP
       :links: R_FW_THERMOSTAT_RUN
    """
    out = run(
        cli, tmp_path, [*SETUP, "auto", "tick", cut, f"done {OK}", "now 1200", "tick", "now 2200", "tick"]
    )
    s = submits(out)
    assert s[0] == "runtime 120" and "level 6" not in s and "power on" not in s, s


def test_auto_tap_wakes_the_link_and_waits_for_it(cli, tmp_path):
    """An AUTO tap with the link down (the idle policy's quiet/off) asks the session to connect now and
    waits for the ready link up to CALI_THERMO_LINK_WAIT_MS instead of answering AUTO_REFUSED_NO_LINK;
    past that wait it is refused.

    .. test:: Thermostat runner: an AUTO tap wakes the link
       :id: T_FW_THERMOSTAT_RUN_WAKE
       :links: R_FW_THERMOSTAT_RUN
    """
    out = run(cli, tmp_path, [*SETUP, "ready 0", "auto", "tick", "now 6000", "tick", "state",
                              "ready 1", "now 7000", "tick", "state"])  # fmt: skip
    assert "CONNECT_NOW" in out
    states = [json.loads(s[7:])["thermostat"] for s in out if s.startswith("THERMO ")]
    assert states[0]["reason"] is None and states[0]["mode"] == "OFF"  # waiting, nothing answered yet
    assert states[1]["mode"] == "AUTO" and submits(out) == ["runtime 120"]  # the start batch begins
    out = run(cli, tmp_path, [*SETUP, "ready 0", "auto", "tick", "now 31000", "tick", "state"])
    assert state(out)["reason"] == "AUTO_REFUSED_NO_LINK" and submits(out) == []


def test_auto_tap_waits_for_a_fresh_heater_read(cli, tmp_path):
    """With the thermostat OFF the session does not re-read 1702, so an idle heater's frame ages past
    heater_stale_ms. An AUTO tap then holds the session (the hold reads 1702 at once) and waits up to
    CALI_THERMO_LINK_WAIT_MS for a fresh frame instead of answering AUTO_REFUSED_STALE_HEATER; past that
    wait it is refused and the hold drops.

    .. test:: Thermostat runner: an AUTO tap waits for a fresh heater read
       :id: T_FW_THERMOSTAT_RUN_FRESH_HEATER
       :links: R_FW_THERMOSTAT_RUN
    """
    stale = [*SETUP, "now 200000", "auto", "tick", "state"]  # the 1702 frame from 1000 is 199 s old
    out = run(cli, tmp_path, [*stale, "now 201000", OFF_HEATER, "tick", "state"])
    states = [json.loads(s[7:])["thermostat"] for s in out if s.startswith("THERMO ")]
    assert "HOLD 1" in out and states[0]["reason"] is None and states[0]["mode"] == "OFF"  # waiting
    assert states[1]["mode"] == "AUTO" and submits(out) == ["runtime 120"]  # the fresh read starts it
    out = run(cli, tmp_path, [*stale, "now 231000", "tick", "state"])
    assert state(out)["reason"] == "AUTO_REFUSED_STALE_HEATER" and submits(out) == []
    assert out.index("HOLD 0") > out.index("HOLD 1")


def test_night_wait_holds_the_session(cli, tmp_path):
    """A night AUTO request with the heater off pre-sets the level alone and enters WAITING; the wait holds
    the session like AUTO (no rest: a WAITING that loses fresh 1702 data never sees the panel's Dauerbetrieb).

    .. test:: Thermostat runner: the night wait holds the session
       :id: T_FW_THERMOSTAT_RUN_NIGHT_HOLD
       :links: R_FW_THERMOSTAT_RUN
    """
    out = run(cli, tmp_path, [*SETUP, "night 1", "auto", "tick", f"done {OK}", "state"])
    st = state(out)
    assert submits(out) == ["level 6"] and "HOLD 1" in out
    assert (st["mode"], st["reason"], st["night"], st["wait_left_s"]) == (
        "WAITING",
        "WAITING_FOR_CONTINUOUS",
        True,
        300,
    )


def test_blocked_uses_the_last_known_frames(cli, tmp_path):
    """``status.blocked`` reads camping mode and the ignition (1004 TerminalOneFive or 1202 Enable, the rule of
    cali_ac_ignition) from the last-known frames, the entry check from this link's only: with the stored
    frames not live (a rest), nothing blocks on the card, and an AUTO request is refused.

    .. test:: Thermostat runner: blocked from the last-known frames
       :id: T_FW_THERMOSTAT_RUN_BLOCKED
       :links: R_FW_THERMOSTAT_RUN
    """
    out = run(cli, tmp_path, [*SETUP, frame("vehicle", TerminalOneFive=0), "live 0", "ign -1", "tick", "state",
                              "auto", "tick", "state"])  # fmt: skip
    states = [json.loads(s[7:])["thermostat"] for s in out if s.startswith("THERMO ")]
    assert states[0]["blocked"] is None and states[0]["wait_left_s"] is None
    assert states[1]["reason"] == "AUTO_REFUSED_NO_CAMPING"
    out = run(
        cli, tmp_path, [*SETUP, frame("vehicle", TerminalOneFive=1), "live 0", "ign -1", "tick", "state"]
    )
    assert state(out)["blocked"] == "IGNITION"
    out = run(cli, tmp_path, [*SETUP, frame("campingmode", State=1, Enable=1), "live 0", "ign -1", "state"])
    assert state(out)["blocked"] == "IGNITION"  # the 1202 mirror of terminal 15


def test_blocked_follows_the_inputs_between_steps(cli, tmp_path):
    """``status.blocked`` is computed at each read of the status, not only at a step: the card polls every
    2 s and undims as soon as camping mode is on.

    .. test:: Thermostat runner: blocked is current at each GET
       :id: T_FW_THERMOSTAT_RUN_BLOCKED_GET
       :links: R_FW_THERMOSTAT_API
    """
    out = run(cli, tmp_path, [*SETUP, frame("campingmode", State=0), "tick", "state",
                              frame("campingmode", State=1), "state"])  # fmt: skip
    states = [json.loads(s[7:])["thermostat"] for s in out if s.startswith("THERMO ")]
    assert [st["blocked"] for st in states] == ["NO_CAMPING", None]


def test_night_in_off_is_the_stored_setting_at_each_get(cli, tmp_path):
    """``status.night`` in mode OFF is the stored night setting, current at each GET (not the last step's).

    .. test:: Thermostat runner: the night in OFF is current at each GET
       :id: T_FW_THERMOSTAT_RUN_NIGHT_OFF_GET
       :links: R_FW_THERMOSTAT_API
    """
    out = run(cli, tmp_path, [*SETUP, "tick", "state", "night 1", "state"])
    states = [json.loads(s[7:])["thermostat"] for s in out if s.startswith("THERMO ")]
    assert [st["night"] for st in states] == [False, True]


CONT = frame("airheater", Installed=1, NormalOperation=1, PermanentOperation=1, HeatingLevel=6)
NIGHT_60S = [*SETUP, "set period_ms 60000", "night 1", "auto", "tick", f"done {OK}", "tick"]


def test_night_countdown_counts_at_each_get(cli, tmp_path):
    """``status.wait_left_s`` is computed at each GET from the end of the wait, not only at a step: with
    ``period_ms`` 60 s the card's countdown still falls between two steps.

    .. test:: Thermostat runner: the night countdown is current at each GET
       :id: T_FW_THERMOSTAT_RUN_WAIT_GET
       :links: R_FW_THERMOSTAT_API
    """
    out = run(cli, tmp_path, [*NIGHT_60S, "state", "now 3000", "tick", "state", "now 3500", "tick", "state"])
    states = [json.loads(s[7:])["thermostat"] for s in out if s.startswith("THERMO ")]
    assert [s["wait_left_s"] for s in states] == [300, 298, 298]  # rounded up: 297.5 s -> 298
    assert all(s["mode"] == "WAITING" for s in states)


def test_night_confirm_sampled_within_seconds(cli, tmp_path):
    """While WAITING the runner steps every ``min(period_ms, 5000)``: Dauerbetrieb that holds
    ``night_confirm_ms`` (30 s) is taken over within 35 s, not at the next 60 s step.

    .. test:: Thermostat runner: the night confirm window is sampled every 5 s
       :id: T_FW_THERMOSTAT_RUN_WAIT_STEP
       :links: R_FW_THERMOSTAT_RUN
    """
    lines = list(NIGHT_60S)
    for t in range(2000, 64000, 1000):
        lines += [f"now {t}", CONT, "tick", "state"]
    out = run(cli, tmp_path, lines)
    states = [json.loads(s[7:])["thermostat"] for s in out if s.startswith("THERMO ")]
    first = next(i for i, s in enumerate(states) if s["mode"] == "AUTO")
    assert 2000 + 1000 * first <= 2000 + 35000, first
    assert states[first]["reason"] == "ADOPT_CONTINUOUS" and submits(out) == ["level 6"]
