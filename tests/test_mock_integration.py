"""End-to-end integration tests: real calictl paths driven against tools.mock_unit.

Unlike test_device.py (which asserts the *shape* of the arming protocol against a dumb
recorder), these drive the whole runtime — cli.cmd_set / serve.on_command / device.actuate
— against the stateful MockCamperUnit and assert the observable OUTCOME:
  * cooler power on/off actually flips the mocked state (arm-gate honoured);
  * a write without the 1003 heartbeat changes nothing (the gate);
  * an out-of-range write drops the link (MockDisconnect);
  * lighting SET applies only with the commit frame (the physical-apply gap crack);
  * a set is reflected by a subsequent read within the same process.
All with no bleak/BLE — the fake `bleak` module is backed by the mock.
"""

import asyncio
import sys
import types

import pytest

from calictl import control, device, overrides, protocol
from tools.mock_unit import MockBleakClient, MockCamperUnit, MockDisconnect


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
    f = protocol.load()
    overrides.apply(f)
    return f


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
    assert wa["fresh"] == {"liters": 11, "capacity_l": 29, "percent": 37}
    assert wa["waste"] == {"liters": 0, "capacity_l": 22, "percent": 0}


# --- cli.cmd_set end-to-end -------------------------------------------------


def test_set_cooler_power_on_then_off(mock):
    from calictl import cli

    assert cli.main(["--addr", "11:22:33:44:55:66", "set", "cooler", "power", "on"]) == 0
    assert mock.decoded("cooler")["State"] == 1  # armed write applied
    assert cli.main(["--addr", "11:22:33:44:55:66", "set", "cooler", "power", "off"]) == 0
    assert mock.decoded("cooler")["State"] == 0  # same process -> reflected


def test_set_cooler_level(mock):
    from calictl import cli

    assert cli.main(["--addr", "11:22:33:44:55:66", "set", "cooler", "level", "5"]) == 0
    assert mock.decoded("cooler")["Level"] == 5


def test_set_campingmode_master(mock):
    from calictl import cli, semantics

    assert cli.main(["--addr", "11:22:33:44:55:66", "set", "campingmode", "master", "on"]) == 0
    interp = semantics.interpret("campingmode", mock.decoded("campingmode"))
    assert interp["master_on"] is True


def test_set_cooler_level_out_of_range_is_clean_error(mock, capsys):
    """The build-side guard (protocol.encode/CONTROL_RANGES) rejects before any write —
    the user gets a clean exit 2, not a traceback, and the state is untouched."""
    from calictl import cli

    assert cli.main(["--addr", "11:22:33:44:55:66", "set", "cooler", "level", "9"]) == 2
    assert "1-5" in capsys.readouterr().err
    assert mock.decoded("cooler")["Level"] == 3  # unchanged seed default


# --- lighting: SET frame cracked 2026-07-08; APPLY gap cracked 2026-07-13 (commit frame) -----


def test_set_lighting_applies_directly_no_activate_step(mock):
    """A lamp set applies straight away with the lights off (ProfileNumber 0) — like the app,
    which hardcodes ProfileNumber=9 in every SET_BRIGHTNESS instead of requiring a manual
    profile activation. The cli sends the 0e00… commit as the follow frame."""
    from calictl import cli

    assert mock.decoded("lighting")["ProfileNumber"] == 0  # lights off, no active profile
    assert (
        cli.main(["--addr", "11:22:33:44:55:66", "set", "lighting", "kitchen", "8"]) == 0
    )  # rc 0 = APPLIED, no activate needed
    assert mock.decoded("lighting")["BrightnessLSeven"] == 8
    assert mock.decoded("lighting")["ProfileNumber"] == 9  # the set made profile 9 active


def test_lighting_set_applies_and_the_commit_is_harmless(mock):
    """CAPTURE 2026-10-10 (the real app on the real unit): the unit notified the new levels ~230 ms
    after each SET, BEFORE the app's 0e00… commit — so the SET applies and the commit (which calictl
    still sends, as the app does: `device.actuate(..., follow=control.LIGHT_COMMIT)`) is ACKed as a
    no-op. The 2026-07-13 "commit applies" gate rested on the readback echo."""
    funcs = _funcs()
    dev = device.CamperDevice("11:22:33:44:55:66")
    setf = control.build(funcs, "lighting", "kitchen", 8, {})  # self-carries ProfileNumber=9
    asyncio.run(dev.actuate(funcs["lighting"], setf, verify=False))
    assert mock.decoded("lighting")["BrightnessLSeven"] == 8
    asyncio.run(dev.actuate(funcs["lighting"], setf, verify=False, follow=control.LIGHT_COMMIT))
    assert mock.decoded("lighting")["BrightnessLSeven"] == 8
    assert control.commit_for("lighting") == control.LIGHT_COMMIT
    assert control.commit_for("cooler") is None


def test_lighting_applies_without_preamble(mock):
    """A bare SET + commit actuates — the app's REQUEST_CONFIG screen-open pull is NOT an
    actuation gate (photon-verified on-device 2026-08-16), and calictl no longer sends it.

    .. test:: Lighting set actuates with the bare commit
       :id: T_LIGHT_COMMIT
       :links: R_LIGHT_COMMIT
    """
    funcs = _funcs()
    dev = device.CamperDevice("11:22:33:44:55:66")
    setf = control.build(funcs, "lighting", "kitchen", 8, {})
    asyncio.run(dev.actuate(funcs["lighting"], setf, verify=False, follow=control.LIGHT_COMMIT))
    assert mock.decoded("lighting")["BrightnessLSeven"] == 8


def test_connect_timeout_is_tunable_for_a_flaky_link(monkeypatch):
    """The per-attempt connect timeout must be tunable without a code change.

    A unit that advertises but ignores connection requests (seen at the van 2026-09-18) burns the
    FULL timeout on every one of the three attempts, so a 30 s default means the daemon only
    probes the unit every ~90 s and keeps missing its brief connectable windows. Lowering the
    timeout samples far more often. An explicit constructor argument still wins, so callers that
    know better are unaffected.
    """
    from calictl.device import CamperDevice

    monkeypatch.delenv("CALICTL_CONNECT_TIMEOUT_S", raising=False)
    assert CamperDevice().connect_timeout == 30.0  # unchanged default
    monkeypatch.setenv("CALICTL_CONNECT_TIMEOUT_S", "8")
    assert CamperDevice().connect_timeout == 8.0  # env tunes it
    assert CamperDevice(connect_timeout=12.0).connect_timeout == 12.0  # explicit arg still wins


def test_read_all_heartbeat_refreshes_stale_read(mock):
    """The MOCK unit latches a stale value (1 L) until the 1003 heartbeat ticks, then serves the
    fresh 11 L — so this pins that read_all runs the heartbeat while reading (link keepalive +
    re-read refresh). NB on the real unit water is measurement-gated, not heartbeat-driven
    (value-freshness.md, CORRECTION 2026-07-14); the 1 L / 11 L numbers here are the mock's
    model only. The `mock` fixture patches device sleeps to no-ops, so warm-up is instant.

    .. test:: read_all heartbeat refreshes a stale latched read
       :id: T_READ_HEARTBEAT_REFRESH
       :links: R_READ_HEARTBEAT_REFRESH
       :status: passing
    """
    import asyncio

    from calictl import device, protocol

    mock.state["water"]["FreshWaterLevel"] = 11  # the truth (revealed once armed)
    mock.read_latch["water"] = {"FreshWaterLevel": 1}  # a bare read returns the stale latch
    funcs = mock.funcs

    # a plain read (no heartbeat -> not armed) sees the stale latch...
    assert protocol.decode(funcs["water"], mock.read(funcs["water"].state_char))["FreshWaterLevel"] == 1
    # ...but read_all runs the heartbeat, which arms the session, so it surfaces the truth
    raw = asyncio.run(device.CamperDevice("11:22:33:44:55:66").read_all(funcs))
    assert mock.armed is True
    assert protocol.decode(funcs["water"], raw["water"])["FreshWaterLevel"] == 11


# --- read_all: subscribe, then read; the last frame wins (app order) ---------


def test_read_all_read_after_subscribe_wins_over_stale_push(mock):
    """App order (decompile 2026-10-07, ``jb/b``): subscribe 1302, THEN read 1302; both land in the
    one decoder ``qg/b.e`` and the LAST frame wins. The subscribe-time push carries a stale 1 L,
    the read that follows carries the correct 17 L -> calictl must surface the read.

    .. test:: read_all: a read after the subscribe push wins (app order, last frame wins)
       :id: T_READ_ALL_LAST_FRAME_WINS
       :links: R_READ_LAST_FRAME_WINS
    """
    import asyncio

    from calictl import device, protocol

    mock.state["water"]["FreshWaterLevel"] = 17  # what a read returns
    mock.notify_push["water"] = {"FreshWaterLevel": 1}  # the subscribe-time push: stale
    funcs = mock.funcs
    raw = asyncio.run(device.CamperDevice("11:22:33:44:55:66").read_all(funcs))
    assert protocol.decode(funcs["water"], raw["water"])["FreshWaterLevel"] == 17


def test_read_all_later_notify_overrides_the_read(mock):
    """Last frame wins both ways: a 1302 notification arriving AFTER the read replaces it.

    .. test:: read_all: a notify arriving after the read overrides it
       :id: T_READ_ALL_LATER_NOTIFY_WINS
       :links: R_READ_LAST_FRAME_WINS
    """
    import asyncio

    from calictl import device, protocol

    mock.state["water"]["FreshWaterLevel"] = 17
    funcs = {"water": mock.funcs["water"], "cooler": mock.funcs["cooler"]}
    real_read = mock.read
    cooler_char = str(funcs["cooler"].state_char)

    def read(uuid):  # while the cooler is read (after water), the unit pushes a new water level
        if str(uuid) == cooler_char:
            mock.push("water", {"FreshWaterLevel": 9})
        return real_read(uuid)

    mock.read = read
    raw = asyncio.run(device.CamperDevice("11:22:33:44:55:66").read_all(funcs))
    assert protocol.decode(funcs["water"], raw["water"])["FreshWaterLevel"] == 9


def test_read_all_lighting_config_push_never_replaces_the_read(mock):
    """Lighting is excluded from the later-notify override: a 1502 config frame (Mode 20, the
    wake-up config) that lands after the lighting read must not become the polled lighting state
    (``serve`` latches config frames itself; decoded as state it would garble the zones).

    .. test:: read_all keeps the lighting read when a later 1502 config push lands
       :id: T_READ_ALL_LIGHTING_EXCLUDED
       :links: R_READ_LAST_FRAME_WINS
    """
    import asyncio

    from calictl import device, protocol

    funcs = {"lighting": mock.funcs["lighting"], "cooler": mock.funcs["cooler"]}
    want = mock.read(funcs["lighting"].state_char)
    real_read = mock.read
    cooler_char = str(funcs["cooler"].state_char)

    def read(uuid):  # while the cooler is read (after lighting), the unit pushes a Mode-20 frame
        if str(uuid) == cooler_char:
            mock.push("lighting", {"Mode": 20, "ProfileNumber": 14})
        return real_read(uuid)

    mock.read = read
    raw = asyncio.run(device.CamperDevice("11:22:33:44:55:66").read_all(funcs))
    assert raw["lighting"] == want
    assert protocol.decode(funcs["lighting"], raw["lighting"])["Mode"] != 20


# --- the 1003 arm-gate, at the unit level -----------------------------------


def test_write_ignored_without_heartbeat_then_applied_with_it():
    """Directly exercise the gate: an unarmed control write is ACKed but ignored;
    once the 1003 heartbeat ticks, the same write applies."""
    unit = MockCamperUnit()
    funcs = unit.funcs
    cur = protocol.decode(funcs["cooler"], unit.read(funcs["cooler"].state_char))
    frame = control.build(funcs, "cooler", "power", "on", cur)

    unit.write(funcs["cooler"].control_char, frame)  # not armed
    assert unit.decoded("cooler")["State"] == 0  # ignored

    unit.beat((0x00100000).to_bytes(4, "big"))  # heartbeat -> arm
    unit.write(funcs["cooler"].control_char, frame)
    assert unit.decoded("cooler")["State"] == 1  # now applied


# --- firmware range validation -> link drop ---------------------------------


def _out_of_range_frame():
    """A lighting frame carrying Mode=5 — outside the firmware's Mode enum (dg/n.java), the
    class of value the unit rejects with 0x0E and a link drop. Built with the range constraint
    relaxed so encode will emit it; the unit re-validates. (The old example, cooler State=3, is
    the 2-bit leave-unchanged sentinel: the app's own neutral frame carries it and the unit
    accepts it — see tests/test_mock_fidelity.py — so it no longer counts as out-of-range.)"""
    relaxed = _funcs()
    for cf in relaxed["lighting"].control_fields:
        if cf.name == "Mode":
            cf.valid = None
    vals = {cf.name: (cf.default or 0) for cf in relaxed["lighting"].control_fields if cf.placed}
    vals["Mode"] = 5
    return protocol.encode(relaxed["lighting"], vals, frame_bytes=overrides.CONTROL_FRAME_BYTES["lighting"])


def test_out_of_range_write_drops_link_at_unit():
    unit = MockCamperUnit()
    unit.beat((0x00100000).to_bytes(4, "big"))
    with pytest.raises(MockDisconnect):
        unit.write(unit.funcs["lighting"].control_char, _out_of_range_frame())


def test_out_of_range_surfaces_through_actuate(mock):
    """The MockDisconnect propagates out of device.actuate (the firmware dropped us)."""
    funcs = _funcs()
    bad = _out_of_range_frame()
    with pytest.raises(MockDisconnect):
        asyncio.run(device.CamperDevice("11:22:33:44:55:66").actuate(funcs["lighting"], bad, verify=True))


# --- serve.on_command path --------------------------------------------------


def test_poll_writes_only_installed_functions_to_influx(mock, monkeypatch):
    """Influx should store only INSTALLED functions (same set MQTT publishes). The uninstalled
    ones (satellite / living-room heater / roof-A/C / stairs / generalpurposesignals) emit only
    raw pass-through fields (WordZeroFour, System, ...) — noise that must not reach the dashboard."""
    from calictl import influx, serve

    captured = {}
    monkeypatch.setattr(influx, "points_for", lambda states: captured.__setitem__("fns", set(states)) or [])

    class _Rec:
        def write(self, **_k):
            pass

    s = serve.Server("11:22:33:44:55:66", influx_enabled=True)
    s._iw = _Rec()

    async def _run():
        s._ble = asyncio.Lock()
        await s.poll()

    asyncio.run(_run())

    fns = captured["fns"]
    assert {"water", "cooler", "campingmode", "vehicle"} <= fns  # installed -> stored
    assert "generalpurposesignals" not in fns and "satelliteantenna" not in fns  # noise dropped


def test_serve_on_command_actuates(mock):
    from calictl import serve

    s = serve.Server("11:22:33:44:55:66", influx_enabled=False)
    s._read_only = False  # writes explicitly enabled

    async def _run():
        s._ble = asyncio.Lock()  # normally created inside run()'s loop
        await s.on_command("cooler", "power", "on")

    asyncio.run(_run())
    assert mock.decoded("cooler")["State"] == 1


def test_serve_refuses_a_roof_move_under_a_blocking_alert_but_never_a_stop(mock, monkeypatch):
    """The roof branch of on_command short-circuits to _roof_move, so it used to skip the
    precondition check at the bottom entirely — /api/command, the CLI and HA could drive the roof
    under a blocking InfoPopUp that the GUI greys. The gate now runs BEFORE the session nudge, so a
    blocked move never reaches _roof_move (and never wakes the unit). STOP is the safety action and
    must still get through untouched."""
    from calictl import serve

    s = serve.Server(influx_enabled=False)
    s._read_only = False
    moved, stopped = [], []

    async def _fake_move(what, *_a):
        moved.append(what)

    async def _fake_stop():
        stopped.append(True)

    monkeypatch.setattr(s, "_roof_move", _fake_move)
    monkeypatch.setattr(s, "_roof_stop_command", _fake_stop)

    async def _run():
        s._ble = asyncio.Lock()  # normally created inside run()'s loop
        # child_lock (InfoPopUp 1) is in ROOF_MOVE_BLOCK -> refuse; _roof_move is never called
        s._last["roof"] = {"Installed": 1, "InfoPopUp": 1, "Position": 0}
        await s.on_command("roof", "open", None)
        assert moved == [] and stopped == []
        # a STOP under the very same blocking alert still goes through
        await s.on_command("roof", "stop", None)
        assert stopped == [True]
        # with the alert cleared the move reaches _roof_move again
        s._last["roof"] = {"Installed": 1, "InfoPopUp": 0, "Position": 0}
        await s.on_command("roof", "open", None)
        assert moved == ["open"]

    asyncio.run(_run())


async def _real_wait(seconds):
    """A real-time wait: the ``mock`` fixture turns ``asyncio.sleep`` into a no-op."""
    try:
        await asyncio.wait_for(asyncio.Event().wait(), seconds)
    except TimeoutError:
        pass


def _log_unit_writes(unit):
    """Record the ORDER of 1003 beats and roof frames reaching the unit:
    ``[("beat", counter) | ("roof", byte0)]``; ``unit.write_times`` holds the matching monotonic times."""
    import time

    events = []
    unit.write_times = []
    real_write = unit.write

    def _write(uuid, data):
        if uuid == device.HEARTBEAT_CHAR:
            events.append(("beat", int.from_bytes(bytes(data), "big")))
            unit.write_times.append(time.monotonic())
        elif uuid == unit.funcs["roof"].control_char:
            events.append(("roof", bytes(data)[0]))
            unit.write_times.append(time.monotonic())
        return real_write(uuid, data)

    unit.write = _write
    return events


def test_roof_move_runs_inside_the_live_session_with_the_heartbeat_ticking(mock):
    """A roof move with a live persistent session runs INSIDE it: the 1003 heartbeat keeps ticking
    between the move frames, the single slot is never released or re-taken (the mock's ``one_slot``
    stays happy), and a release STOP still interrupts the move at once, lock-free.

    Replaces the #198 handover test. That handover existed only because the roof contract was read
    as "no 1003 heartbeat during a roof move"; the decompile shows the app keeps its session-global
    heartbeat ticking during roof moves (#235), so calictl now follows the app and reuses the
    session instead of dropping it.

    .. test:: Roof move inside the live session: heartbeat interleaved, one slot, immediate STOP
       :id: T_ROOF_IN_SESSION_MOCK
       :links: R_ROOF_ACTUATE, R_PERSISTENT_SESSION
    """
    import time

    from calictl import serve

    mock.one_slot = True  # model the unit's single slot
    s = serve.Server("11:22:33:44:55:66", influx_enabled=False)
    s._read_only = False
    s._last["roof"] = {"Installed": 1, "InfoPopUp": 0, "Position": 0}
    stop_latency = []
    released_at = []

    async def _release():
        # hold for >= 2 beats into the move (bounded), then release -> STOP
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and sum(e[0] == "beat" for e in events[n0:]) < 2:
            await _real_wait(0.05)
        t0 = time.monotonic()
        released_at.append(t0)
        await s.on_command("roof", "stop", None)
        stop_latency.append(time.monotonic() - t0)

    async def _run():
        nonlocal n0
        s._ble = asyncio.Lock()
        s._persistent = True
        # No supervisor task in a bare Server, so bring the session up the way it would.
        async with s._ble:
            await s._sessions._connect_once()
        sess = s._live_session()
        assert sess is not None, "persistent session did not come up"
        holder = mock.holder
        n0 = len(events)
        rel = asyncio.ensure_future(_release())
        await asyncio.wait_for(s.on_command("roof", "open", None), timeout=10.0)
        await rel
        survived = s._live_session() is sess  # checked while the loop (and its heartbeat) is alive
        return holder, survived

    events = _log_unit_writes(mock)
    n0 = 0
    holder, survived = asyncio.run(_run())

    move = events[n0:]
    roof_idx = [i for i, e in enumerate(move) if e[0] == "roof"]
    assert roof_idx, "the roof move never reached the unit"
    beats_in_move = [i for i, e in enumerate(move) if e[0] == "beat" and roof_idx[0] < i < roof_idx[-1]]
    assert beats_in_move, "the 1003 heartbeat must tick between the roof frames"
    assert move[roof_idx[-1]] == ("roof", 0x00), "the move ends in a STOP"
    assert mock.holder is holder, "the single slot was released/re-taken (handover) instead of reused"
    assert survived, "the persistent session must survive the roof move"
    assert stop_latency and stop_latency[0] < 0.5, "release STOP must be immediate (lock-free)"
    # the STOP frame itself reaches the unit promptly after the release (not a whole frame period)
    t_stop = mock.write_times[n0 + roof_idx[-1]]
    assert t_stop - released_at[0] < 0.3, "STOP frame must follow the release at once"
    # ONE heartbeat writer across the whole move: the session's counter, strictly +1 (a second
    # writer — a re-arm inside the session — would restart at HEARTBEAT_START and jump back)
    beats = [c for k, c in events if k == "beat"]
    assert beats == list(range(beats[0], beats[0] + len(beats))), "1003 must be strictly +1"


def _fast_roof(monkeypatch, unit):
    """Scale the mock roof's real-unit timings (CAPTURE 2026-10-10) down to a fraction of a second
    and skip the pre-open checklist (its own tests cover it), so a full travel fits in a test."""
    from tools import mock_unit

    for name, val in (
        ("ROOF_WITHHOLD_S", 0.2),
        ("ROOF_OPEN_S", 0.6),
        ("ROOF_CLOSE_S", 0.6),
        ("ROOF_START_S", 0.05),
        ("ROOF_LEAVE_S", 0.1),
        ("ROOF_SETTLE_S", 0.1),
    ):
        monkeypatch.setattr(mock_unit, name, val)
    monkeypatch.setattr(device, "ROOF_LIMIT_POLL_S", 0.05)
    unit.roof_checklist = False


def _session_server(mock):
    from calictl import serve

    mock.one_slot = True
    s = serve.Server("11:22:33:44:55:66", influx_enabled=False)
    s._read_only = False
    s._last["roof"] = {"Installed": 1, "InfoPopUp": 0, "Position": 0}
    return s


async def _bring_up_session(s):
    s._ble = asyncio.Lock()
    s._persistent = True
    async with s._ble:
        await s._sessions._connect_once()
    assert s._live_session() is not None, "persistent session did not come up"


def test_roof_release_while_the_press_waits_on_the_lock_never_moves(mock):
    """SAFETY (pre-existing bug, review C1 on #238): a quick tap whose release arrives while the press
    still waits for the ``_ble`` lock (a poll, a cooler/lighting write or a supervisor connect holds
    it) must not start the move. The stop token used to be created/cleared only once the press held
    the lock, so the early release set a stale event, the press then cleared it and the roof drove to
    the limit or the 30 s cap.

    .. test:: A release that arrives before the move starts cancels the move
       :id: T_ROOF_EARLY_RELEASE
       :links: R_ROOF_ACTUATE
    """
    s = _session_server(mock)
    events = _log_unit_writes(mock)
    out = {}

    async def _run():
        await _bring_up_session(s)
        await s._ble.acquire()  # a poll holds the lock
        press = asyncio.ensure_future(s.on_command("roof", "open", None))
        await _real_wait(0.05)
        rel = asyncio.ensure_future(s.on_command("roof", "stop", None))  # the tap is released
        await _real_wait(0.05)
        n_rel = len(events)
        s._ble.release()  # the poll is done
        await _real_wait(1.0)
        out["move_frames_after_release"] = sum(1 for e in events[n_rel:] if e == ("roof", 0x01))
        out["press_done"], out["stop_done"] = press.done(), rel.done()
        if s._roof_stop is not None:
            s._roof_stop.set()  # end a runaway move so the test terminates
        await press
        await rel

    asyncio.run(_run())
    assert out["move_frames_after_release"] == 0, out
    assert out["press_done"] and out["stop_done"], out


def test_standalone_roof_stop_without_a_session_has_no_arm_delay(mock, monkeypatch):
    """A STOP with no move in flight and no live session goes through the roof path: handshake,
    1003 heartbeat with NO ``ARM_DELAY_S``, one STOP frame with a live counter (review I1 on #238).
    A STOP is never delayed by an arm delay.

    .. test:: A standalone roof STOP has no arm delay and carries a live counter
       :id: T_ROOF_STOP_NO_ARM_DELAY
       :links: R_ROOF_ACTUATE
    """
    from calictl import serve

    slept = []
    real_sleep = asyncio.sleep

    async def _rec(sec=0, *_a, **_k):
        slept.append(sec)
        await real_sleep(0)

    monkeypatch.setattr(device.asyncio, "sleep", _rec)
    s = serve.Server("11:22:33:44:55:66", influx_enabled=False)
    s._read_only = False
    writes = []
    real_write = mock.write

    def _w(uuid, data):
        writes.append((uuid, bytes(data)))
        return real_write(uuid, data)

    mock.write = _w

    async def _run():
        s._ble = asyncio.Lock()
        await s.on_command("roof", "stop", None)

    asyncio.run(_run())
    roof = [d for u, d in writes if u == mock.funcs["roof"].control_char]
    assert device.ARM_DELAY_S not in slept, "a STOP must never wait the arm delay"
    assert any(u == device.HEARTBEAT_CHAR for u, _d in writes), "the heartbeat runs for the STOP"
    assert len(roof) == 1 and roof[0][0] == 0x00, "exactly one STOP frame"
    assert int.from_bytes(roof[0][1:5], "big") != 0, "the STOP carries a live (seeded) counter"


def test_roof_auto_stops_at_the_limit_inside_the_live_session(mock, monkeypatch):
    """Auto-stop at the limit works on the daemon's main roof path (the move inside the live
    session, review I2 on #238): with no release, the move ceases at Position 1 (open) and sends
    STOP. Afterwards the stop token is set (the move is over), so a later standalone STOP really
    sends a frame instead of only flipping an event (review minor 2).

    .. test:: The in-session roof move auto-stops at the limit
       :id: T_ROOF_IN_SESSION_LIMIT
       :links: R_ROOF_ACTUATE, R_PERSISTENT_SESSION
    """
    import time

    _fast_roof(monkeypatch, mock)
    s = _session_server(mock)
    events = _log_unit_writes(mock)

    async def _ticker(done):
        last = time.monotonic()
        while not done.is_set():
            await _real_wait(0.05)
            now = time.monotonic()
            mock.tick(now - last)
            last = now

    async def _run():
        await _bring_up_session(s)
        done = asyncio.Event()
        tick = asyncio.ensure_future(_ticker(done))
        try:
            await asyncio.wait_for(s.on_command("roof", "open", None), timeout=10.0)
            await _real_wait(0.4)  # end of travel (InfoPopUp 8) -> the final Position settles
        finally:
            done.set()
            await tick
        token_set = s._roof_stop is not None and s._roof_stop.is_set()
        n = len(events)
        await s.on_command("roof", "stop", None)  # a later standalone STOP
        return token_set, events[n:]

    token_set, after = asyncio.run(_run())
    assert mock.decoded("roof")["Position"] == 1, "the move must stop at the open limit"
    roof = [e for e in events if e[0] == "roof"]
    assert roof[-1] == ("roof", 0x00)
    assert token_set, "the stop token must be set once the move has ended"
    assert ("roof", 0x00) in after, "a later STOP must send a real STOP frame"


def test_roof_opens_and_closes_on_the_mock_with_the_heartbeat_ticking(mock, monkeypatch):
    """calictl's own roof drive moves the mock roof open and closed from a COLD unit (never armed,
    no session), with the 1003 heartbeat ticking alongside the SafetyCounter stream. The mock
    honours a roof frame only while the heartbeat has armed the unit, so a heartbeat-less drive
    (the pre-#235 contract) leaves the roof where it was.

    The unit's clock follows real time here; the withhold and the travel are shortened so the
    full open + close takes a few seconds.

    .. test:: calictl opens and closes the mock roof with the heartbeat ticking
       :id: T_ROOF_MOCK_OPEN_CLOSE_HEARTBEAT
       :links: R_ROOF_ACTUATE
    """
    import time

    _fast_roof(monkeypatch, mock)
    funcs = _funcs()
    f = funcs["roof"]
    stop = control.roof_frame(funcs, "stop")
    assert mock.armed is False and mock.decoded("roof")["Position"] == 0

    async def _ticker(done):
        last = time.monotonic()
        while not done.is_set():
            await _real_wait(0.05)
            now = time.monotonic()
            mock.tick(now - last)
            last = now

    async def _drive(direction):
        done = asyncio.Event()
        tick = asyncio.ensure_future(_ticker(done))
        try:
            await device.CamperDevice("MO:CK").actuate_roof(
                f,
                control.roof_frame(funcs, direction),
                stop,
                max_duration_s=8.0,
                validate_s=2.0,
                limit_positions=control.roof_limit_positions(direction),
                verify=False,
            )
            await _real_wait(0.4)  # end of travel (InfoPopUp 8) -> the final Position settles
        finally:
            done.set()
            await tick
        return mock.decoded("roof")["Position"]

    assert asyncio.run(_drive("open")) == 1  # closed -> middle -> open
    assert asyncio.run(_drive("close")) == 0  # open -> middle -> closed


def _log_roof_frames(unit):
    """Every roof frame reaching the unit as ``(monotonic time, direction byte, counter)``."""
    import time

    frames = []
    real_write = unit.write

    def _write(uuid, data):
        if uuid == unit.funcs["roof"].control_char:
            d = bytes(data)
            frames.append((time.monotonic(), d[0], int.from_bytes(d[1:5], "big")))
        return real_write(uuid, data)

    unit.write = _write
    return frames


async def _unit_clock(unit, done):
    """Advance the mock's clock in real time until ``done`` is set."""
    import time

    last = time.monotonic()
    while not done.is_set():
        await _real_wait(0.05)
        now = time.monotonic()
        unit.tick(now - last)
        last = now


def test_roof_view_streams_stop_frames_and_a_press_continues_the_counter(mock, monkeypatch):
    """The app's roof screen, CAPTURE 2026-10-10: while the roof page is open (``roof_view``) the live
    session streams STOP frames ``00 <counter>`` every ~500 ms with the counter advancing; a press
    switches the SAME stream to the move byte — its first move frame carries the last STOP frame's
    counter — and a release goes back to STOP frames, the counter never restarting. The mock unit
    therefore has the counter validated before the press and starts the motor without the fresh-
    counter withhold.

    .. test:: Roof view: STOP stream, press continues the counter, release back to STOP
       :id: T_ROOF_VIEW_STREAM
       :links: R_ROOF_VIEW_STREAM, R_ROOF_ACTUATE
    """
    from tools import mock_unit

    monkeypatch.setattr(device, "ROOF_LIMIT_POLL_S", 0.05)
    mock.roof_checklist = False  # the checklist has its own mock tests
    s = _session_server(mock)
    frames = _log_roof_frames(mock)
    seen = []
    mock._subs.setdefault(mock.funcs["roof"].state_char, []).append(
        lambda _c, d: seen.append((mock.now, bytes(d).hex()))
    )
    out = {}

    async def _run():
        await _bring_up_session(s)
        done = asyncio.Event()
        clock = asyncio.ensure_future(_unit_clock(mock, done))
        try:
            await s.roof_view("view")
            await _real_wait(mock_unit.ROOF_WITHHOLD_S + 1.5)  # the page is open: validated + withhold paid
            out["n_view"] = len(frames)
            out["t_press"] = mock.now
            press = asyncio.ensure_future(s.on_command("roof", "open", None))
            await _real_wait(1.0)
            await s.on_command("roof", "stop", None)  # release
            await asyncio.wait_for(press, 5.0)
            await _real_wait(1.0)
            out["streaming"] = s._live_session().roof_streaming
            await s.roof_view("leave")
            await asyncio.wait_for(s._roof_view_task, 5.0)
            out["after_leave"] = s._live_session().roof_streaming
        finally:
            done.set()
            await clock

    asyncio.run(_run())
    view = frames[: out["n_view"]]
    assert {d for _t, d, _c in view} == {0x00}, "the open page streams STOP frames only"
    gaps = [b[0] - a[0] for a, b in zip(view, view[1:])]
    assert len(view) >= 8 and max(gaps) < 0.7 and 0.3 < sum(gaps) / len(gaps) < 0.6, gaps
    ctrs = [c for _t, _d, c in frames]
    assert ctrs == sorted(ctrs) and ctrs[-1] > ctrs[0], "one counter, never restarting"
    first_move = next(i for i, (_t, d, _c) in enumerate(frames) if d == 0x01)
    assert frames[first_move][2] == frames[first_move - 1][2], (
        "first move frame repeats the last STOP counter"
    )
    last_move = max(i for i, (_t, d, _c) in enumerate(frames) if d == 0x01)
    assert frames[last_move + 1][1] == 0x00 and len(frames) > last_move + 2, (
        "release -> the STOP stream goes on"
    )
    moving = [t for t, h in seen if h.endswith("0c") and t >= out["t_press"]]
    assert moving and moving[0] - out["t_press"] < 1.0, "a pre-validated counter: no ~3 s withhold"
    assert out["streaming"] is True and out["after_leave"] is False


def test_roof_checklist_then_a_fresh_press_moves_through_serve(mock, monkeypatch):
    """The real unit's open flow end to end (CAPTURE 2026-10-10): with the roof view streaming, the
    first open press gets ``0302`` (the pre-open checklist, no motion); after the release a fresh
    press is NOT refused (InfoPopUp 2 is no move block, #276) and the roof moves.

    .. test:: Roof view: the checklist press, then a fresh press moves (serve on the mock)
       :id: T_ROOF_VIEW_CHECKLIST_FLOW
       :links: R_ROOF_VIEW_STREAM, R_ROOF_ALERT
    """
    _fast_roof(monkeypatch, mock)
    mock.roof_checklist = True
    s = _session_server(mock)
    seen = []
    mock._subs.setdefault(mock.funcs["roof"].state_char, []).append(lambda _c, d: seen.append(bytes(d).hex()))

    async def _hold(what, seconds):
        press = asyncio.ensure_future(s.on_command("roof", what, None))
        await _real_wait(seconds)
        await s.on_command("roof", "stop", None)
        await asyncio.wait_for(press, 5.0)

    async def _run():
        await _bring_up_session(s)
        done = asyncio.Event()
        clock = asyncio.ensure_future(_unit_clock(mock, done))
        try:
            await s.roof_view("view")
            await _real_wait(1.5)
            await _hold("open", 0.5)  # the checklist press
            first = list(seen)
            await _real_wait(0.3)
            s._last["roof"] = mock.decoded("roof")  # the poll would have cached 0302 by now
            await _hold("open", 0.5)  # a fresh press within the window
            await s.roof_view("leave")
            return first
        finally:
            done.set()
            await clock

    first = asyncio.run(_run())
    assert "0302" in first and not any(h.endswith("0c") for h in first), "checklist, no motion"
    assert any(h.endswith("0c") for h in seen[len(first) :]), "the fresh press moves"


def test_roof_view_lapses_without_a_refresh(mock, monkeypatch):
    """A view that is not refreshed ends: the STOP stream stops after ``CALICTL_ROOF_VIEW_LAPSE_S`` —
    it never streams forever (a closed browser tab sends no ``leave``).

    .. test:: Roof view lapses without a refresh
       :id: T_ROOF_VIEW_LAPSE
       :links: R_ROOF_VIEW_STREAM
    """
    import time

    from calictl import serve

    monkeypatch.setattr(serve, "_ROOF_VIEW_LAPSE_S", 0.6)
    s = _session_server(mock)
    frames = _log_roof_frames(mock)
    out = {}

    async def _run():
        await _bring_up_session(s)
        await s.roof_view("view")
        await asyncio.wait_for(s._roof_view_task, 5.0)  # lapses by itself
        out["t_end"] = time.monotonic()
        out["streaming"] = s._live_session().roof_streaming
        await _real_wait(1.0)

    asyncio.run(_run())
    assert frames, "the view streamed"
    assert out["streaming"] is False
    assert all(t <= out["t_end"] + 0.05 for t, _d, _c in frames), "no frame after the lapse"


def test_roof_view_leave_ends_a_held_move_with_stop(mock):
    """Leaving the roof page while a move is held ends the move: STOP, then the stream ends.

    .. test:: Leaving the roof view ends a held move with STOP
       :id: T_ROOF_VIEW_LEAVE_STOPS
       :links: R_ROOF_VIEW_STREAM
    """
    s = _session_server(mock)
    frames = _log_roof_frames(mock)

    async def _run():
        await _bring_up_session(s)
        await s.roof_view("view")
        await _real_wait(0.6)
        press = asyncio.ensure_future(s.on_command("roof", "close", None))
        await _real_wait(0.6)
        await s.roof_view("leave")
        await asyncio.wait_for(press, 5.0)
        await asyncio.wait_for(s._roof_view_task, 5.0)
        return s._live_session().roof_streaming

    assert asyncio.run(_run()) is False
    assert frames[-1][1] == 0x00 and any(d == 0x04 for _t, d, _c in frames)


def test_roof_view_is_refused_read_only(mock):
    """A read-only daemon writes nothing for a roof view."""
    from calictl import serve

    s = serve.Server("11:22:33:44:55:66", influx_enabled=False)
    frames = _log_roof_frames(mock)

    async def _run():
        s._ble = asyncio.Lock()
        return await s.roof_view("view")

    assert asyncio.run(_run()) == {"ok": False, "viewing": False}
    assert frames == []


def test_read_only_is_default_and_refuses_writes(mock):
    """SAFE DEFAULT: a fresh Server is read-only, so on_command refuses to actuate (and _meta
    reports it). Writes only happen once explicitly enabled (--enable-writes / CALICTL_ENABLE_WRITES)."""
    from calictl import serve

    s = serve.Server(influx_enabled=False)
    assert s._read_only is True  # default
    assert serve.ServeBackend(s, loop=None, read_only=s._read_only).state()["_meta"]["read_only"] is True

    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("cooler", "power", "on")

    assert asyncio.run(_run()) is None  # refused
    assert mock.decoded("cooler")["State"] == 0  # unchanged


def test_mock_drop_and_wake_models_deep_sleep():
    import asyncio

    from tools.mock_unit import MockBleakClient, MockCamperUnit, MockDisconnect

    unit = MockCamperUnit()
    client = MockBleakClient.bind(unit)("MO:CK", timeout=1)

    async def _run():
        await client.connect()
        assert client.is_connected
        unit.drop()  # van parks -> deep sleep
        raised = False
        try:
            await client.read_gatt_char(unit.funcs["cooler"].state_char)
        except MockDisconnect:
            raised = True
        assert raised and client.is_connected is False
        # a fresh connect while asleep fails (not advertising)
        c2 = MockBleakClient.bind(unit)("MO:CK", timeout=1)
        try:
            await c2.connect()
            assert False, "connect should fail while asleep"
        except MockDisconnect:
            pass
        unit.wake()
        await c2.connect()
        assert c2.is_connected  # wakes on physical use

    asyncio.run(_run())
