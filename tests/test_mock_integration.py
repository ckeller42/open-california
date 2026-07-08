"""End-to-end integration tests: real calictl paths driven against tools.mock_unit.

Unlike test_device.py (which asserts the *shape* of the arming protocol against a dumb
recorder), these drive the whole runtime — cli.cmd_set / serve.on_command / device.actuate
— against the stateful MockCamperUnit and assert the observable OUTCOME:
  * cooler power on/off actually flips the mocked state (arm-gate honoured);
  * a write without the 1003 heartbeat changes nothing (the gate);
  * an out-of-range write drops the link (MockDisconnect);
  * lighting SET is ACKed but never applied (the still-unsolved gate stays honest);
  * a set is reflected by a subsequent read within the same process.
All with no bleak/BLE — the fake `bleak` module is backed by the mock.
"""
import asyncio
import sys
import types

import pytest

from calictl import protocol, control, overrides, device
from tools.mock_unit import MockCamperUnit, MockBleakClient, MockDisconnect


@pytest.fixture
def mock(monkeypatch):
    """Install a fake `bleak` backed by one shared MockCamperUnit; make sleeps instant."""
    unit = MockCamperUnit()
    mod = types.ModuleType("bleak")
    mod.BleakClient = MockBleakClient.bind(unit)
    monkeypatch.setitem(sys.modules, "bleak", mod)
    real_sleep = asyncio.sleep
    async def _fast(*_a, **_k):
        await real_sleep(0)
    monkeypatch.setattr(device.asyncio, "sleep", _fast)
    return unit


def _funcs():
    f = protocol.load(); overrides.apply(f); return f


# --- cli.cmd_set end-to-end -------------------------------------------------

def test_set_cooler_power_on_then_off(mock):
    from calictl import cli
    assert cli.main(["set", "cooler", "power", "on"]) == 0
    assert mock.decoded("cooler")["State"] == 1          # armed write applied
    assert cli.main(["set", "cooler", "power", "off"]) == 0
    assert mock.decoded("cooler")["State"] == 0          # same process -> reflected


def test_set_cooler_level(mock):
    from calictl import cli
    assert cli.main(["set", "cooler", "level", "5"]) == 0
    assert mock.decoded("cooler")["Level"] == 5


def test_set_campingmode_master(mock):
    from calictl import cli
    from calictl import semantics
    assert cli.main(["set", "campingmode", "master", "on"]) == 0
    interp = semantics.interpret("campingmode", mock.decoded("campingmode"))
    assert interp["master_on"] is True


def test_set_cooler_level_out_of_range_is_clean_error(mock, capsys):
    """The build-side guard (protocol.encode/CONTROL_RANGES) rejects before any write —
    the user gets a clean exit 2, not a traceback, and the state is untouched."""
    from calictl import cli
    assert cli.main(["set", "cooler", "level", "9"]) == 2
    assert "1-5" in capsys.readouterr().err
    assert mock.decoded("cooler")["Level"] == 3          # unchanged seed default


# --- lighting now actuates (cracked via HCI capture 2026-07-08) --------------

def test_set_lighting_needs_active_profile_then_applies(mock):
    from calictl import cli
    # live-verified precondition: with no active profile a zone set is ACKed but NOT applied
    assert cli.main(["set", "lighting", "kitchen", "8"]) == 1    # rc 1 = NOT APPLIED
    assert mock.decoded("lighting")["BrightnessLSeven"] == 0
    # activate a profile, then the zone applies
    assert cli.main(["set", "lighting", "profile", "9"]) == 0
    assert cli.main(["set", "lighting", "kitchen", "8"]) == 0
    assert mock.decoded("lighting")["BrightnessLSeven"] == 8


# --- the 1003 arm-gate, at the unit level -----------------------------------

def test_write_ignored_without_heartbeat_then_applied_with_it():
    """Directly exercise the gate: an unarmed control write is ACKed but ignored;
    once the 1003 heartbeat ticks, the same write applies."""
    unit = MockCamperUnit()
    funcs = unit.funcs
    cur = protocol.decode(funcs["cooler"], unit.read(funcs["cooler"].state_char))
    frame = control.build(funcs, "cooler", "power", "on", cur)

    unit.write(funcs["cooler"].control_char, frame)      # not armed
    assert unit.decoded("cooler")["State"] == 0          # ignored

    unit.beat((0x00100000).to_bytes(4, "big"))           # heartbeat -> arm
    unit.write(funcs["cooler"].control_char, frame)
    assert unit.decoded("cooler")["State"] == 1          # now applied


# --- firmware range validation -> link drop ---------------------------------

def _out_of_range_cooler_frame():
    """A cooler frame carrying State=3 (rejected 0x0E on-device). Built with the
    range constraint relaxed so encode will emit it; the unit re-validates."""
    relaxed = _funcs()
    for cf in relaxed["cooler"].control_fields:
        if cf.name == "State":
            cf.valid = None
    vals = control._cooler_values({}, State=3)
    return protocol.encode(relaxed["cooler"], vals, frame_bytes=6)


def test_out_of_range_write_drops_link_at_unit():
    unit = MockCamperUnit()
    unit.beat((0x00100000).to_bytes(4, "big"))
    with pytest.raises(MockDisconnect):
        unit.write(unit.funcs["cooler"].control_char, _out_of_range_cooler_frame())


def test_out_of_range_surfaces_through_actuate(mock):
    """The MockDisconnect propagates out of device.actuate (the firmware dropped us)."""
    funcs = _funcs()
    bad = _out_of_range_cooler_frame()
    with pytest.raises(MockDisconnect):
        asyncio.run(device.CamperDevice().actuate(funcs["cooler"], bad, verify=True))


# --- serve.on_command path --------------------------------------------------

def test_serve_on_command_actuates(mock):
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    async def _run():
        s._ble = asyncio.Lock()                          # normally created inside run()'s loop
        await s.on_command("cooler", "power", "on")
    asyncio.run(_run())
    assert mock.decoded("cooler")["State"] == 1
