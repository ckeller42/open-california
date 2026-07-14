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


# --- mock seed fidelity: a coherent, realistic snapshot of the real van ------

def test_mock_seed_is_coherent_and_realistic():
    """The DEFAULT_SEED must mirror a real engine-off/parked read, so the GUI tests exercise
    the states the hardware actually emits (this is how the 'null V' starter-voltage bug slipped
    through — the old seed made the starter battery look measured when the real van doesn't)."""
    from calictl import semantics
    u = MockCamperUnit()
    en = semantics.interpret("energy", u.decoded("energy"))
    ve = semantics.interpret("vehicle", u.decoded("vehicle"))
    wa = semantics.interpret("water", u.decoded("water"))
    # engine-off coherence: terminal-15 off <=> starter battery unmeasured (batt1_v None)
    assert ve["ignition_on"] is False and en["stale"] is True and en["batt1_v"] is None
    # leisure battery is always live (never null) — 100% / 14.1 V / 58 h
    assert en["batt2_v"] == 14.1 and en["soc2_pct"] == 100 and en["batt2_remaining_h"] == 58
    # this van's source profile: DC-DC + shore fitted (idle), solar absent
    assert en["dcdc_installed"] and en["shore_installed"] and not en["solar_installed"]
    assert en["dcdc_state"] == "inactive" and en["shore_state"] == "inactive"
    # water in the van's absolute-litre encoding: fresh 11/29 (38%), waste 0/22
    assert wa["fresh"] == {"liters": 11, "capacity_l": 29, "percent": 38}
    assert wa["waste"] == {"liters": 0, "capacity_l": 22, "percent": 0}


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


# --- lighting: SET frame cracked 2026-07-08; APPLY gap cracked 2026-07-13 (commit frame) -----

def test_set_lighting_needs_active_profile_then_applies(mock):
    from calictl import cli
    # live-verified precondition: with no active profile a zone set is ACKed but NOT applied
    assert cli.main(["set", "lighting", "kitchen", "8"]) == 1    # rc 1 = NOT APPLIED
    assert mock.decoded("lighting")["BrightnessLSeven"] == 0
    # activate a profile, then the zone applies (cli sends the 0e00… commit as the follow frame)
    assert cli.main(["set", "lighting", "profile", "9"]) == 0
    assert cli.main(["set", "lighting", "kitchen", "8"]) == 0
    assert mock.decoded("lighting")["BrightnessLSeven"] == 8


def test_lighting_requires_the_commit_frame_to_apply(mock):
    """The APPLY gap crack (HCI-verified on-device 2026-07-13): the unit applies a SET_BRIGHTNESS
    only after the 0e00… commit frame lands. The SET alone is ACKed but never applied — which is
    exactly why lighting looked broken for so long. `device.actuate(..., follow=control.LIGHT_COMMIT)`
    sends it; `control.commit_for('lighting')` returns it."""
    funcs = _funcs()
    dev = device.CamperDevice()
    # activate a profile so brightness can apply (SET_PROFILE + its commit)
    asyncio.run(dev.actuate(funcs["lighting"], control.build(funcs, "lighting", "profile", 9, {}),
                            verify=False, follow=control.LIGHT_COMMIT))
    setf = control.build(funcs, "lighting", "kitchen", 8, {"ProfileNumber": 9})
    # SET alone (no commit) -> ACKed, NOT applied
    asyncio.run(dev.actuate(funcs["lighting"], setf, verify=False))
    assert mock.decoded("lighting")["BrightnessLSeven"] == 0
    # SET + commit -> applied
    asyncio.run(dev.actuate(funcs["lighting"], setf, verify=False, follow=control.LIGHT_COMMIT))
    assert mock.decoded("lighting")["BrightnessLSeven"] == 8
    assert control.commit_for("lighting") == control.LIGHT_COMMIT
    assert control.commit_for("cooler") is None


# --- read_all prefers pushed notifications over a stale latched read ---------

def test_read_all_heartbeat_refreshes_stale_read(mock):
    """A bare read of the fresh-water char returns the stale latch (1 L), but read_all runs the
    1003 liveness heartbeat, which arms the unit's measurement loop, so it surfaces the true 11 L.
    Live-verified on-device 2026-07-09 (bare poll read 1 L; with a continuous heartbeat the same
    read returned 11 L). The `mock` fixture patches device sleeps to no-ops, so warm-up is instant.

    .. test:: read_all heartbeat refreshes a stale latched read
       :id: T_READ_HEARTBEAT_REFRESH
       :links: R_READ_HEARTBEAT_REFRESH
       :status: passing
    """
    import asyncio
    from calictl import protocol, device
    mock.state["water"]["FreshWaterLevel"] = 11             # the truth (revealed once armed)
    mock.read_latch["water"] = {"FreshWaterLevel": 1}       # a bare read returns the stale latch
    funcs = mock.funcs

    # a plain read (no heartbeat -> not armed) sees the stale latch...
    assert protocol.decode(funcs["water"], mock.read(funcs["water"].state_char))["FreshWaterLevel"] == 1
    # ...but read_all runs the heartbeat, which arms the session, so it surfaces the truth
    raw = asyncio.run(device.CamperDevice().read_all(funcs))
    assert mock.armed is True
    assert protocol.decode(funcs["water"], raw["water"])["FreshWaterLevel"] == 11


def test_read_all_prefers_water_notification_over_stale_read(mock):
    """Water (1302) is PUSH-ONLY for freshness (decompile-confirmed 2026-07-14: the app's water VM
    ``qg/b`` reads the level from the 1302 notification, not a bare read). A bare read returns the
    stale latch; ``read_all`` now subscribes and prefers the pushed value."""
    import asyncio
    from calictl import protocol, device
    mock.state["water"]["FreshWaterLevel"] = 1               # bare read = stale latch
    mock.notify_push["water"] = {"FreshWaterLevel": 11}      # the unit pushes the truth
    funcs = mock.funcs
    # a bare read still sees the stale 1...
    assert protocol.decode(funcs["water"], mock.read(funcs["water"].state_char))["FreshWaterLevel"] == 1
    # ...but read_all consumes the 1302 notification and surfaces 11
    raw = asyncio.run(device.CamperDevice().read_all(funcs))
    assert protocol.decode(funcs["water"], raw["water"])["FreshWaterLevel"] == 11


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

def test_poll_writes_only_installed_functions_to_influx(mock, monkeypatch):
    """Influx should store only INSTALLED functions (same set MQTT publishes). The uninstalled
    ones (satellite / living-room heater / roof-A/C / stairs / generalpurposesignals) emit only
    raw pass-through fields (WordZeroFour, System, ...) — noise that must not reach the dashboard."""
    from calictl import serve, influx
    captured = {}
    monkeypatch.setattr(influx, "points_for",
                        lambda states: (captured.__setitem__("fns", set(states)) or []))

    class _Rec:
        def write(self, **_k):
            pass

    s = serve.Server(influx_enabled=True)
    s._iw = _Rec()

    async def _run():
        s._ble = asyncio.Lock()
        await s.poll()
    asyncio.run(_run())

    fns = captured["fns"]
    assert {"water", "cooler", "campingmode", "vehicle"} <= fns          # installed -> stored
    assert "generalpurposesignals" not in fns and "satelliteantenna" not in fns   # noise dropped


def test_serve_on_command_actuates(mock):
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._read_only = False                                 # writes explicitly enabled
    async def _run():
        s._ble = asyncio.Lock()                          # normally created inside run()'s loop
        await s.on_command("cooler", "power", "on")
    asyncio.run(_run())
    assert mock.decoded("cooler")["State"] == 1


def test_read_only_is_default_and_refuses_writes(mock):
    """SAFE DEFAULT: a fresh Server is read-only, so on_command refuses to actuate (and _meta
    reports it). Writes only happen once explicitly enabled (--enable-writes / CALICTL_ENABLE_WRITES)."""
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    assert s._read_only is True                          # default
    assert serve.ServeBackend(s, loop=None, read_only=s._read_only).state()["_meta"]["read_only"] is True
    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("cooler", "power", "on")
    assert asyncio.run(_run()) is None                   # refused
    assert mock.decoded("cooler")["State"] == 0          # unchanged
