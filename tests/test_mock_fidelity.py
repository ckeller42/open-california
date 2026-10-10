"""Mock-unit fidelity pinned against the REAL app's traffic (observed 2026-09-16 with the
CaliforniaOnTour app running in an Android emulator against ``tools/fake_unit_ble.py``, which
serves ``MockCamperUnit`` over BLE).

Three gaps the app exposed in one session:

* the app fills every UNTARGETED field of a full-packet frame with that field's
  leave-unchanged sentinel — for 4/8-bit fields that is the control field's dictionary DEFAULT
  (heater: HeatingLevel 11, RunningTime 127, TimerHour 31, TimerMin 63; cooler: Level 7, ...),
  not only the 2-bit ``3``. The mock applied 11/127 as values, so after the app's
  "turn continuous heating off" frame ``0f7b007f1f3f`` it showed Level 11/10 and 120 min;
* ``<X>Request`` control fields (NormalOperationRequest, PermanentOperationRequest) drive the
  ``<X>`` state bit — the mock left the state unchanged, so the app saw no effect and raised
  "Something went wrong";
* on the roof page the app streams ``Up/Down/SafetyCounter`` frames every ~500 ms and expects
  the unit to raise ``SafetyCounterValid`` (1402 bit 7) once the counter is seen incrementing —
  without it every frame fails.

.. test:: Mock honours per-field leave-unchanged sentinels
   :id: T_MOCK_SENTINELS
   :links: R_AIRHEATER_SET
"""

from calictl import control, overrides, protocol, semantics
from tools.mock_unit import MockCamperUnit


def _armed_unit(**seed):
    u = MockCamperUnit(seed=seed)
    u.armed = True
    return u


def _funcs():
    f = protocol.load()
    overrides.apply(f)
    return f


def test_app_frame_sentinels_leave_untargeted_fields_alone():
    """The app's real Dauerbetrieb-OFF frame: only PermanentOperationRequest=0 is a value; level 11,
    mode 7, run time 127, timer 31:63 are the leave-unchanged defaults and must not be applied."""
    f = _funcs()
    u = _armed_unit(
        airheater={
            "Installed": 1,
            "PermanentOperation": 1,
            "NormalOperation": 0,
            "HeatingLevel": 5,
            "RunningTime": 60,
            "TimerHour": 12,
            "TimerMin": 0,
        }
    )
    u.write(f["airheater"].control_char, bytes.fromhex("0f7b007f1f3f"))
    st = u.decoded("airheater")
    assert st["HeatingLevel"] == 5 and st["RunningTime"] == 60
    assert (st["TimerHour"], st["TimerMin"]) == (12, 0)
    assert st["PermanentOperation"] == 0  # the one targeted field took effect


def test_request_fields_drive_their_state_bits():
    f = _funcs()
    u = _armed_unit(
        airheater={
            "Installed": 1,
            "PermanentOperation": 0,
            "NormalOperation": 0,
            "HeatingLevel": 5,
            "RunningTime": 60,
        }
    )
    on = control.build(f, "airheater", "power", "on", u.state["airheater"])
    u.write(f["airheater"].control_char, on)
    assert u.decoded("airheater")["NormalOperation"] == 1
    off = control.build(f, "airheater", "power", "off", u.state["airheater"])
    u.write(f["airheater"].control_char, off)
    assert u.decoded("airheater")["NormalOperation"] == 0


def test_immediate_heating_starts_the_remaining_time_countdown():
    """The app's status bar reads RunningTimeinAction ("Active • N min remaining"); the unit loads
    it from RunningTime when immediate heating starts and clears it when it stops."""
    f = _funcs()
    u = _armed_unit(
        airheater={
            "Installed": 1,
            "NormalOperation": 0,
            "PermanentOperation": 0,
            "HeatingLevel": 5,
            "RunningTime": 60,
            "RunningTimeinAction": 0,
        }
    )
    u.write(f["airheater"].control_char, bytes.fromhex("3d7b007f1f3f"))  # the app's ON frame
    assert u.decoded("airheater")["RunningTimeinAction"] == 60
    u.write(f["airheater"].control_char, bytes.fromhex("3c7b007f1f3f"))  # the app's OFF frame
    assert u.decoded("airheater")["RunningTimeinAction"] == 0


def test_app_neutral_frame_with_all_2bit_sentinels_is_accepted():
    """500 ms after every write the app sends a neutral frame with every 2-bit field at 3 and every
    wider field at its default (cooler `ff771e3e1f1f`). The unit accepts it; so must the mock — the
    curated `State in {0,1}` constraint is for calictl's own commands, not for the sentinel."""
    f = _funcs()
    u = _armed_unit(
        cooler={
            "Installed": 1,
            "State": 0,
            "Level": 3,
            "Mode": 4,
            "NightTimerHourOn": 22,
            "NightTimerHourOff": 6,
        }
    )
    u.write(f["cooler"].control_char, bytes.fromhex("fc771e3e1f1f"))  # the app's OFF frame
    u.write(f["cooler"].control_char, bytes.fromhex("ff771e3e1f1f"))  # its neutral follow-up
    st = u.decoded("cooler")
    assert st["State"] == 0 and st["Level"] == 3 and st["Mode"] == 4
    assert (st["NightTimerHourOn"], st["NightTimerHourOff"]) == (22, 6)


def test_cooler_time_picker_frame_sets_the_start_time_fields():
    """The app's Time picker writes TimerHour/TimerMin alone (ff7704021f1f = 04:02, observed) and
    then displays the unit's TimerHourSet/TimerMinSet — the mock must map control→state names."""
    f = _funcs()
    u = _armed_unit(
        cooler={"Installed": 1, "State": 0, "Level": 3, "Mode": 4, "TimerHourSet": 0, "TimerMinSet": 0}
    )
    u.write(f["cooler"].control_char, bytes.fromhex("ff7704021f1f"))
    st = u.decoded("cooler")
    assert (st["TimerHourSet"], st["TimerMinSet"]) == (4, 2)
    assert st["State"] == 0 and st["Level"] == 3  # sentinels left everything else alone


def _fridge_on_unit():
    """The fake unit's baseline (``baseline-0410``): fridge ON, level 3, timer set 00:00."""
    return _armed_unit(
        cooler={"Installed": 1, "State": 1, "Level": 3, "Mode": 4, "TimerHourSet": 0, "TimerMinSet": 0}
    )


def test_app_level_and_power_writes_apply_while_the_fridge_is_on():
    """The app's level/power frames carry TimerHour/TimerMin at their dictionary defaults (30/62,
    the leave-unchanged sentinel), not the set time. They are not a timer change, so the unit
    applies them while the fridge is on — the real app shows "Something went wrong" otherwise
    (app lab 2026-10-05: level 3->5 reverted to 3, power-off reverted)."""
    f = _funcs()
    u = _fridge_on_unit()
    u.write(f["cooler"].control_char, bytes.fromhex("ff751e3e1f1f"))  # app: level 5, rest sentinel
    assert u.decoded("cooler")["Level"] == 5
    u.write(f["cooler"].control_char, bytes.fromhex("fc771e3e1f1f"))  # app: power off
    assert u.decoded("cooler")["State"] == 0
    assert u.refusals == []


def test_a_real_timer_change_is_still_refused_while_the_fridge_is_on():
    """The DEVICE-confirmed rule stays: picking a start time (04:02, the app's `ff7704021f1f`) or
    arming the timer (`f7771e3e1f1f`) while the fridge is on is ACKed and ignored."""
    f = _funcs()
    u = _fridge_on_unit()
    u.write(f["cooler"].control_char, bytes.fromhex("ff7704021f1f"))
    st = u.decoded("cooler")
    assert (st["TimerHourSet"], st["TimerMinSet"]) == (0, 0)
    u.write(f["cooler"].control_char, bytes.fromhex("f7771e3e1f1f"))
    assert u.decoded("cooler")["TimerState"] == 0
    assert [why for _, why in u.refusals] == ["the cooling timer can only be set while the fridge is off"] * 2


def test_cooler_timer_action_bits_arm_and_clear_the_timer():
    """The app's "Timer" switch (box off) writes only TimerStart=1 with everything else at the
    sentinels (`f7771e3e1f1f`, observed); the unit reports TimerState=1. TimerCancel=1 clears it."""
    f = _funcs()
    u = _armed_unit(
        cooler={
            "Installed": 1,
            "State": 0,
            "Level": 3,
            "Mode": 0,
            "TimerState": 0,
            "TimerHourSet": 9,
            "TimerMinSet": 0,
        }
    )
    u.write(f["cooler"].control_char, bytes.fromhex("f7771e3e1f1f"))
    st = u.decoded("cooler")
    assert st["TimerState"] == 1 and (st["TimerHourSet"], st["TimerMinSet"]) == (9, 0)
    u.write(f["cooler"].control_char, bytes.fromhex("df771e3e1f1f"))  # TimerCancel=1 (bits 2-3)
    assert u.decoded("cooler")["TimerState"] == 0


def test_real_value_equal_to_default_is_not_mistaken_for_a_sentinel_on_2bit_fields():
    """2-bit fields keep the existing rule (3 = leave unchanged; 0/1 are values)."""
    f = _funcs()
    u = _armed_unit(campingmode={"Installed": 1, "State": 1, "UsbCharger": 1})
    u.write(
        f["campingmode"].control_char,
        control.build(f, "campingmode", "master", "off", u.state["campingmode"]),
    )
    assert u.decoded("campingmode")["State"] == 0


def _roof_frame(f, up, down, counter):
    return protocol.encode(
        f["roof"],
        {"Up": up, "Down": down, "SafetyCounter": counter},
        frame_bytes=overrides.CONTROL_FRAME_BYTES["roof"],
    )


def test_roof_counter_validates_only_when_incrementing():
    f = _funcs()
    u = _armed_unit(roof={"Installed": 1, "Position": 0, "InfoPopUp": 0, "SafetyCounterValid": 0})
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, 0x5024))
    assert u.decoded("roof")["SafetyCounterValid"] == 0  # first frame: nothing to compare
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, 0x5025))
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, 0x5026))
    assert u.decoded("roof")["SafetyCounterValid"] == 1  # seen incrementing -> valid
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, 0x5024))  # a restarted counter
    assert u.decoded("roof")["SafetyCounterValid"] == 0


def test_roof_counter_stays_valid_when_frames_repeat_a_value_at_high_rate():
    """During a press the app streams ~8 frames/s while its counter (seed + elapsed/500 ms) only
    increments every ~500 ms — four frames carry the same value. That is still a valid counter
    (observed 2026-09-16: an earlier per-frame +1 rule dropped validity and the app aborted the
    move). A frame with a SMALLER counter invalidates; so does a counter frozen for >1.5 s."""
    f = _funcs()
    u = _armed_unit(roof={"Installed": 1, "Position": 0, "InfoPopUp": 0, "SafetyCounterValid": 0})
    c = 500
    for _ in range(3):  # idle stream: +1 per 500 ms frame
        u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c))
        c += 1
        u.tick(0.5)
    assert u.decoded("roof")["SafetyCounterValid"] == 1
    for i in range(16):  # press: 8 Hz, counter +1 every 4 frames
        u.write(f["roof"].control_char, _roof_frame(f, 1, 0, c + i // 4))
        u.tick(0.125)
    assert u.decoded("roof")["SafetyCounterValid"] == 1
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c))  # counter went backwards
    assert u.decoded("roof")["SafetyCounterValid"] == 0
    for _ in range(3):
        c += 10
        u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c))
        u.tick(0.5)
    assert u.decoded("roof")["SafetyCounterValid"] == 1  # re-validated
    u.tick(2.0)
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c))  # same value after 2 s idle
    assert u.decoded("roof")["SafetyCounterValid"] == 0  # frozen counter -> invalid


def test_roof_validity_expires_when_the_stream_stops():
    """Leaving the roof page stops the counter stream; the unit must drop SafetyCounterValid, or
    the app's next visit sees a stale 1 and says "Function in use — another user…" (observed)."""
    f = _funcs()
    u = _armed_unit(roof={"Installed": 1, "Position": 0, "InfoPopUp": 0, "SafetyCounterValid": 0})
    for c in (10, 11, 12):
        u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c))
        u.tick(0.5)
    assert u.decoded("roof")["SafetyCounterValid"] == 1
    assert "roof" in u.tick(2.0)  # stream gone -> cleared, notified
    assert u.decoded("roof")["SafetyCounterValid"] == 0


class _RoofApp:
    """Drives the mock like the app's roof page (CAPTURE 2026-10-10): one frame per ``period`` (500 ms) with
    the counter +1 per frame, and a direction change sent at once with the CURRENT counter. Records
    every distinct 1402 frame (hex) with the unit's time, so tests compare against the wire."""

    def __init__(self, u, f, period=0.5, ctr=805000):
        self.u, self.f, self.period, self.ctr = u, f, period, ctr
        self.seen: list[tuple[float, str]] = []
        self.dir = (0, 0)
        u._subs.setdefault(f["roof"].state_char, []).append(self._on_push)

    def _on_push(self, _char, data):
        h = bytes(data).hex()
        if not self.seen or self.seen[-1][1] != h:
            self.seen.append((round(self.u.now, 2), h))

    def _send(self):
        up, down = self.dir
        self.u.write(self.f["roof"].control_char, _roof_frame(self.f, up, down, self.ctr))

    def hold(self, up, down, seconds):
        if (up, down) != self.dir:
            self.dir = (up, down)
            self._send()  # the change goes out at once, repeating the counter
        for _ in range(round(seconds / self.period)):
            self.u.tick(self.period)
            self.ctr += 1
            self._send()

    def codes(self):
        return [h for _t, h in self.seen]


def _roof_unit(position=0):
    return _armed_unit(roof={"Installed": 1, "Position": position, "InfoPopUp": 0, "SafetyCounterValid": 0})


def test_roof_open_press_raises_the_checklist_then_a_fresh_press_moves():
    """CAPTURE 2026-10-10 (att-3/att-5): an OPEN press on the roof page gets ``0302`` (InfoPopUp 2 =
    the pre-open checklist, no motion), cleared to ``0300`` after ~4 s; a fresh press within ~30 s
    moves (`030c` -> `230c`). Holding the first press longer never moves it.

    .. test:: Mock roof: open press -> checklist 0302, fresh press moves
       :id: T_MOCK_ROOF_CHECKLIST
       :links: R_ROOF_VIEW_STREAM
    """
    f = _funcs()
    u = _roof_unit()
    app = _RoofApp(u, f)
    app.hold(0, 0, 5.0)  # the page streams STOP frames: counter validated, withhold paid
    app.hold(1, 0, 6.0)  # first open press: the checklist, no motion even if held
    assert "0302" in app.codes() and u.decoded("roof")["Position"] == 0
    assert app.codes()[-1] == "0300", "the checklist clears to 0300 after ~4 s"
    t2 = [t for t, h in app.seen if h == "0302"][0]
    t0 = [t for t, h in app.seen if h == "0300" and t > t2][0]
    assert 3.5 <= t0 - t2 <= 4.5
    app.hold(0, 0, 1.0)  # release (the app sends STOP and shows the dialog), then OK
    app.hold(1, 0, 3.0)  # a fresh press moves
    assert app.codes()[-2:] == ["030c", "230c"]


def test_roof_checklist_is_raised_again_after_the_window_and_never_before_a_close():
    """CAPTURE 2026-10-10: presses 26 s after a ``0302`` moved, 40 s after got ``0302`` again; a close
    press from open moved at once (``130c``), with no checklist.

    .. test:: Mock roof: the checklist window and no checklist before a close
       :id: T_MOCK_ROOF_CHECKLIST_WINDOW
       :links: R_ROOF_VIEW_STREAM
    """
    f = _funcs()
    u = _roof_unit()
    app = _RoofApp(u, f)
    app.hold(0, 0, 5.0)
    app.hold(1, 0, 0.9)  # 0302
    app.hold(0, 0, 40.0)  # past the window
    n = len(app.seen)
    app.hold(1, 0, 0.9)
    assert app.codes()[n:][:1] == ["0302"], "outside the window the checklist comes again"
    u2 = _roof_unit(position=1)
    app2 = _RoofApp(u2, f)
    app2.hold(0, 0, 5.0)
    app2.hold(0, 1, 2.0)
    assert app2.codes()[-2:] == ["130c", "230c"], "a close never raises the checklist"


def test_roof_full_open_and_close_follow_the_captured_1402_sequence():
    """CAPTURE 2026-10-10 (att-5 13:32-13:34): open ``0300 -> 030c -> 230c -> 2308 -> 1300`` in ~28 s of
    hold; a mid-travel release ``2303 -> 2300`` (4 s); close ``... 230c -> 2308 -> 0300`` in ~23 s of
    total hold. The motor starts ~0.25 s after a press (InfoPopUp 12) and leaves the end position
    ~1.2 s after it (Position 2), with a counter already streamed (no withhold).

    .. test:: Mock roof: open / release / close follow the captured 1402 frames and timings
       :id: T_MOCK_ROOF_CAPTURE_SEQUENCE
       :links: R_ROOF_VIEW_STREAM
    """
    f = _funcs()
    u = _roof_unit()
    u.roof_checklist = False  # covered above
    app = _RoofApp(u, f)
    app.hold(0, 0, 5.0)
    t_press = u.now
    n = len(app.seen)
    app.hold(1, 0, 31.0)  # held past the end of travel
    opened = app.seen[n:]
    assert [h for _t, h in opened] == ["030c", "230c", "2308", "1300"]
    times = {h: t - t_press for t, h in opened}
    assert times["030c"] <= 0.5 and 0.9 <= times["230c"] <= 1.6
    assert 27.0 <= times["2308"] <= 29.5 and 0.8 <= times["1300"] - times["2308"] <= 1.5
    app.hold(0, 0, 2.0)  # release at the limit: no 2303
    n = len(app.seen)
    app.hold(0, 1, 4.5)  # close, released mid-travel
    app.hold(0, 0, 5.0)
    assert [h for _t, h in app.seen[n:]] == ["130c", "230c", "2303", "2300"]
    n = len(app.seen)
    app.hold(0, 1, 22.0)  # the rest of the close
    assert [h for _t, h in app.seen[n:]] == ["230c", "2308", "0300"]


def test_roof_fresh_counter_pays_the_withhold_a_streamed_one_does_not():
    """The motor withhold is keyed on counter VALIDATION: a counter the roof page streamed for a few
    seconds before the press starts the motor ~0.25 s after it (the real app, CAPTURE 2026-10-10),
    while a counter started AT the press validates first and then waits ``ROOF_WITHHOLD_S`` (~3 s).

    .. test:: Mock roof: a pre-streamed counter moves at once, a fresh one is withheld ~3 s
       :id: T_MOCK_ROOF_WITHHOLD
       :links: R_ROOF_VIEW_STREAM
    """
    f = _funcs()

    def first_motion(prestream_s):
        u = _roof_unit()
        u.roof_checklist = False
        app = _RoofApp(u, f)
        if prestream_s:
            app.hold(0, 0, prestream_s)
        t0 = u.now
        app.hold(1, 0, 8.0)
        return [t for t, h in app.seen if h == "030c"][0] - t0

    assert first_motion(4.5) <= 0.5
    assert first_motion(0) >= 3.0


def test_roof_stops_when_the_frames_stop():
    """No frame for ``ROOF_RELEASE_S`` = released (a dropped link): the motor stops mid-travel and the
    counter validity lapses."""
    f = _funcs()
    u = _roof_unit()
    u.roof_checklist = False
    app = _RoofApp(u, f)
    app.hold(0, 0, 5.0)
    app.hold(1, 0, 3.0)
    assert u.decoded("roof")["Position"] == 2
    u.tick(3.0)
    assert u.decoded("roof")["InfoPopUp"] == 3 and u.decoded("roof")["SafetyCounterValid"] == 0
    u.tick(10.0)
    assert u.decoded("roof")["Position"] == 2


def test_roof_reversal_mid_travel_keeps_the_moving_code():
    """Open, then close at once mid-travel: the release's ``2303`` (cleared 4 s later) must not
    overwrite the reversed move's ``InfoPopUp`` 12."""
    f = _funcs()
    u = _roof_unit()
    u.roof_checklist = False
    app = _RoofApp(u, f)
    app.hold(0, 0, 5.0)
    app.hold(1, 0, 10.0)
    app.hold(0, 1, 6.0)  # reversed while moving: 2303, then the close starts (past its 4 s clear)
    assert u.decoded("roof")["InfoPopUp"] == 12


def test_subscribe_pushes_the_current_value_once():
    """Real unit (buspi trace 2026-09-16): enabling notifications on a state char yields one
    notification with the current frame; nothing streams afterwards without a change."""
    import asyncio

    from tools.mock_unit import MockBleakClient

    u = _armed_unit(cooler={"Installed": 1, "State": 1, "Level": 3, "Mode": 4})
    client = MockBleakClient.bind(u)("MO:CK")
    got = []
    asyncio.run(client.start_notify(u.funcs["cooler"].state_char, lambda ch, data: got.append(bytes(data))))
    assert len(got) == 1 and got[0] == u.read(u.funcs["cooler"].state_char)


def _subscribe(unit, fn, sink):
    import asyncio

    from tools.mock_unit import MockBleakClient

    client = MockBleakClient.bind(unit)("MO:CK")
    asyncio.run(client.start_notify(unit.funcs[fn].state_char, lambda ch, data: sink.append(bytes(data))))
    return client


def _commit_brightness(unit, zone_field, value):
    """Drive a real SET_BRIGHTNESS + commit pair through the write path."""
    f = _funcs()["lighting"]
    frame_bytes = overrides.CONTROL_FRAME_BYTES["lighting"]
    zones = {
        c.name: 14
        for c in f.control_fields  # 14 = per-zone leave-unchanged
        if c.placed and c.name.startswith("BrightnessL")
    }
    zones[zone_field] = value
    unit.write(
        f.control_char, protocol.encode(f, {"Mode": 4, "ProfileNumber": 9, **zones}, frame_bytes=frame_bytes)
    )
    unit.write(
        f.control_char,
        protocol.encode(
            f, {"Mode": 0, "ProfileNumber": 0, **{k: 14 for k in zones}}, frame_bytes=frame_bytes
        ),
    )


def test_lighting_readback_is_an_echo_while_the_lamps_ramp():
    """The unit's lighting state char is a write-through ECHO: it reports the WRITTEN value at
    once, whether or not the lamps moved. The truthful channel is the 1502 Mode-4 ramp
    notification, which carries the REAL brightness stepping toward the target
    (control-and-actuation.md; owner-checked 2026-07 when a "confirmed" readback hid dark lamps).

    .. test:: Lighting readback echoes immediately while the real brightness ramps in pushes
       :id: T_MOCK_LIGHT_ECHO_VS_RAMP
    """
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 9, "BrightnessLOne": 1})
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()  # drop the one-shot push at subscribe time

    _commit_brightness(u, "BrightnessLOne", 4)
    # the ECHO is instant — this is exactly what must NOT be treated as proof of actuation
    assert u.decoded("lighting")["BrightnessLOne"] == 4
    assert u.light_actual["BrightnessLOne"] == 1  # the lamp is still where it was
    assert pushes == []  # and nothing was notified yet

    seen = []
    for _ in range(4):  # ramp 1 -> 2 -> 3 -> 4, one step per tick
        u.tick(0.5)
        seen.append(protocol.decode(_funcs()["lighting"], pushes[-1])["BrightnessLOne"])
    assert seen == [2, 3, 4, 4]  # steps, then holds at the target
    assert u.light_actual["BrightnessLOne"] == 4
    # the ramp frames are Mode 4 — the SET_BRIGHTNESS notification the app confirms on
    assert protocol.decode(_funcs()["lighting"], pushes[-1])["Mode"] == 4


def test_lighting_echo_still_confirms_when_the_lamps_never_move():
    """The bug this models cost a month in 2026-07: the readback said "applied" while the lamps
    stayed dark. With the unit not actuating, the echo STILL reports the written value — and the
    only honest signal, the Mode-4 push, never arrives. Anything that confirms lighting from the
    readback rather than the notification passes here and lies on the van.

    .. test:: A non-actuating unit still echoes the write but never pushes a ramp
       :id: T_MOCK_LIGHT_ECHO_LIES
    """
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 9, "BrightnessLOne": 1})
    u.light_applies = False  # ACK + echo, but the lamps do not move
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()

    _commit_brightness(u, "BrightnessLOne", 9)
    assert u.decoded("lighting")["BrightnessLOne"] == 9  # the echo lies
    for _ in range(5):
        u.tick(0.5)
    assert pushes == []  # no ramp notification, ever
    assert u.light_actual == {}  # and the lamp really never moved


def test_lighting_actuates_on_an_awake_unit_without_the_heartbeat():
    """The real unit actuates lighting on an AWAKE unit with a bare SET_BRIGHTNESS + ``0e00…``
    commit and NO 1003 heartbeat (photon-verified 2026-08-16; AGENTS.md Known state). The mock used
    to apply the generic arm gate to lighting too, so it was stricter than the van. Every other
    control write stays heartbeat-gated, and lighting keeps its own gates (commit, non-zero PN).

    .. test:: Lighting applies without the 1003 heartbeat; other writes stay arm-gated
       :id: T_MOCK_LIGHT_NO_HEARTBEAT
    """
    f = _funcs()
    u = MockCamperUnit(
        seed={
            "lighting": {"Installed": 1, "ProfileNumber": 9, "BrightnessLOne": 1},
            "cooler": {"Installed": 1, "State": 0, "Level": 3, "Mode": 0},
        }
    )
    assert u.armed is False  # no heartbeat ever written
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()

    _commit_brightness(u, "BrightnessLOne", 3)
    assert u.decoded("lighting")["BrightnessLOne"] == 3  # applied (echo) ...
    u.tick(0.5)
    assert pushes and u.light_actual["BrightnessLOne"] == 2  # ... and the lamp really ramps

    # the lighting gates themselves are untouched: a PN=0 brightness frame is still ignored
    frame_bytes = overrides.CONTROL_FRAME_BYTES["lighting"]
    zones = {
        c.name: 14 for c in f["lighting"].control_fields if c.placed and c.name.startswith("BrightnessL")
    }
    u.write(
        f["lighting"].control_char,
        protocol.encode(
            f["lighting"],
            {"Mode": 4, "ProfileNumber": 0, **zones, "BrightnessLOne": 8},
            frame_bytes=frame_bytes,
        ),
    )
    u.write(
        f["lighting"].control_char,
        protocol.encode(f["lighting"], {"Mode": 0, "ProfileNumber": 0, **zones}, frame_bytes=frame_bytes),
    )
    assert u.decoded("lighting")["BrightnessLOne"] == 3

    # ... and every other control write still needs the heartbeat: ACKed and ignored unarmed
    u.write(f["cooler"].control_char, control.build(f, "cooler", "power", "on", u.decoded("cooler")))
    assert u.decoded("cooler")["State"] == 0


def test_change_pushes_only_for_the_chars_the_unit_really_pushes():
    """campingmode (1202) and ignition (1004) are confirmed change-push channels on real hardware;
    the 2026-09-16 buspi trace saw NO change-driven push on the other subscribed chars in 150 s.
    So a state change must notify for those two and stay silent elsewhere.

    .. test:: Change-driven pushes are modelled only for the confirmed channels
       :id: T_MOCK_CHANGE_PUSH_SCOPE
    """
    u = _armed_unit(
        vehicle={
            "TerminalOneFive": 0,
            "CarTimeYear": 126,
            "CarTimeMonth": 8,
            "CarTimeDay": 16,
            "CarTimeHour": 12,
            "CarTimeMinute": 0,
            "CarTimeSecond": 0,
        },
        campingmode={"Installed": 1, "State": 1, "Enable": 0},
        airheater={
            "Installed": 1,
            "NormalOperation": 1,
            "RunningTime": 2,
            "RunningTimeinAction": 2,
            "HeatingLevel": 5,
        },
    )
    camping, heater = [], []
    _subscribe(u, "campingmode", camping)
    _subscribe(u, "airheater", heater)
    u.tick(1.0)  # establish the ignition-edge baseline
    camping.clear()
    heater.clear()

    u.state["vehicle"]["TerminalOneFive"] = 1  # key turned -> camping couples + pushes
    changed = u.tick(1.0)
    assert "campingmode" in changed and camping, "camping must push on the confirmed 1202 channel"

    # the heater DOES change on the clock (its countdown) but must not push: not a confirmed channel
    u.tick(120)
    assert u.decoded("airheater")["RunningTimeinAction"] < 2  # it really did change
    assert heater == []


def test_water_freezes_both_tanks_while_the_system_is_unpowered():
    """Water is measurement-gated on the van's WATER SYSTEM, not on the 1003 heartbeat — the
    2026-07-09 "heartbeat refreshed water" reading was correlation and was disproven at the van
    (value-freshness.md). Unpowered, the unit stops measuring and freezes BOTH tanks, so a read
    returns the latch however the true level moves, and an armed heartbeat does NOT thaw it.

    .. test:: Unpowered water freezes both tanks regardless of the heartbeat
       :id: T_MOCK_WATER_MEASUREMENT_GATE
    """
    u = _armed_unit(
        water={
            "Installed": 1,
            "FreshWaterUnit": 1,
            "FreshWaterVolume": 22,
            "FreshWaterLevel": 19,
            "WasteWaterUnit": 1,
            "WasteWaterVolume": 22,
            "WasteWaterLevel": 3,
        }
    )
    assert u.armed is True  # heartbeat running the whole time
    u.set_water_power(False)  # van parked/locked
    # the true levels move on (someone drains grey at a dump station) but the unit isn't measuring
    u.state["water"].update(FreshWaterLevel=11, WasteWaterLevel=0)
    w = u.decoded("water")
    assert (w["FreshWaterLevel"], w["WasteWaterLevel"]) == (19, 3)  # BOTH frozen at the latch
    u.set_water_power(True)  # water system on -> it measures again
    w = u.decoded("water")
    assert (w["FreshWaterLevel"], w["WasteWaterLevel"]) == (11, 0)


def test_unpowered_water_latch_is_exactly_what_the_stale_guard_rejects():
    """The end-to-end payoff of the gate: what the mock serves while parked is precisely the
    signature `freshness.implausible_water_drop` exists to catch — fresh dropping while grey is
    EXACTLY frozen — and what it serves while powered is accepted as a live measurement.

    .. test:: The mock's parked water reading trips R_WATER_STALE_GUARD, the live one does not
       :id: T_MOCK_WATER_TRIPS_STALE_GUARD
       :links: R_WATER_STALE_GUARD
    """
    from calictl import freshness, semantics

    u = _armed_unit(
        water={
            "Installed": 1,
            "FreshWaterUnit": 1,
            "FreshWaterVolume": 22,
            "FreshWaterLevel": 19,
            "WasteWaterUnit": 1,
            "WasteWaterVolume": 22,
            "WasteWaterLevel": 3,
        }
    )
    plausible = semantics.water(u.decoded("water"))

    # Parked: the unit latches. A later poll sees a LOWER fresh level with grey exactly frozen —
    # the guard must reject it and the daemon keeps showing the last plausible reading.
    u.set_water_power(False)
    u.state["water"]["FreshWaterLevel"] = 1  # the classic parked-decay reading
    u._water_latch["FreshWaterLevel"] = 1  # unit re-latches lower (the ratchet)
    parked = semantics.water(u.decoded("water"))
    assert parked["waste"]["liters"] == plausible["waste"]["liters"]  # grey exactly frozen
    assert freshness.implausible_water_drop(parked, plausible) is True

    # Powered: real usage moves grey too, so the same fresh drop is accepted as live.
    u.set_water_power(True)
    u.state["water"].update(FreshWaterLevel=17, WasteWaterLevel=5)
    live = semantics.water(u.decoded("water"))
    assert freshness.implausible_water_drop(live, plausible) is False


def test_water_pushes_1302_only_on_a_measured_change():
    """The unit notifies 1302 on an actual measured change (what `device._await_water_push` waits
    for) — so a powered system that moves pushes, a powered system at rest does not, and a parked
    one never does however the true level drifts. That last case is why the buspi trace saw no
    water push at all.

    .. test:: Water notifies only on a measured change, never while unpowered
       :id: T_MOCK_WATER_PUSH_ON_CHANGE
    """
    u = _armed_unit(
        water={
            "Installed": 1,
            "FreshWaterUnit": 1,
            "FreshWaterVolume": 22,
            "FreshWaterLevel": 19,
            "WasteWaterUnit": 1,
            "WasteWaterVolume": 22,
            "WasteWaterLevel": 3,
        }
    )
    pushes = []
    _subscribe(u, "water", pushes)
    u.tick(1.0)  # settle the baseline
    pushes.clear()

    u.tick(1.0)
    assert pushes == []  # powered but nothing moved -> no push

    u.state["water"]["FreshWaterLevel"] = 18  # a tap runs: a real measured change
    u.tick(1.0)
    assert len(pushes) == 1
    assert protocol.decode(_funcs()["water"], pushes[-1])["FreshWaterLevel"] == 18

    pushes.clear()
    u.set_water_power(False)  # parked: measurement stops
    u.state["water"]["FreshWaterLevel"] = 2  # the true level drifts / the unit latches
    u.tick(60)
    assert pushes == []  # nothing measured -> nothing notified


def test_camping_master_is_acked_but_silently_refused_while_driving():
    """The firmware refuses camping master ON while the vehicle is being driven — live-verified
    2026-08-19: the write did not take, `applied:false`, and the unit's own console said "Diese
    Funktion ist während der Fahrt nicht verfügbar". Nothing errors: the write is ACKed and the
    state simply never moves, which is the trap. Until the mock could express this, every write
    that passed range-validation applied, so no test could tell ACK-and-apply from ACK-and-ignore.

    .. test:: Camping master ON is ACKed and ignored while driving
       :id: T_MOCK_REFUSES_CAMPING_WHILE_DRIVING
    """
    from calictl import control

    f = _funcs()
    u = _armed_unit(campingmode={"Installed": 1, "State": 0, "UsbCharger": 1})
    frame = control.build(f, "campingmode", "master", "on", u.decoded("campingmode"))

    u.driving = True
    u.write(f["campingmode"].control_char, frame)
    assert u.writes[-1][0] == "campingmode"  # the unit ACKed it (it was received)
    assert u.decoded("campingmode")["State"] == 0  # ...and silently did not apply it
    assert u.refusals and "driving" in u.refusals[-1][1]

    u.driving = False  # stationary: the same frame applies
    u.write(f["campingmode"].control_char, frame)
    assert u.decoded("campingmode")["State"] == 1


def test_roof_reading_light_is_refused_while_the_roof_is_down():
    """The pop-top reading light (L9) is unpowered with the roof down, so the write lands and
    nothing lights (DEVICE-confirmed). calictl mirrors this in `control.command_precondition`;
    with a permissive mock that mirror could be deleted and no test would notice.

    .. test:: L9 brightness is ACKed and ignored with the roof closed
       :id: T_MOCK_REFUSES_ROOF_LAMP_WHEN_DOWN
    """
    u = _armed_unit(
        lighting={"Installed": 1, "ProfileNumber": 9, "BrightnessLNine": 0},
        roof={"Installed": 1, "Position": 0},
    )  # 0 = closed
    _commit_brightness(u, "BrightnessLNine", 8)
    assert u.decoded("lighting")["BrightnessLNine"] == 0  # refused, echo included
    assert u.refusals and "roof raised" in u.refusals[-1][1]

    u.state["roof"]["Position"] = 1  # roof open -> the lamp has power
    _commit_brightness(u, "BrightnessLNine", 8)
    assert u.decoded("lighting")["BrightnessLNine"] == 8


def test_cooling_timer_is_refused_while_the_fridge_is_on_but_power_still_works():
    """The cooling timer is settable only while the fridge is OFF (DEVICE-confirmed). The refusal
    must key on an actual timer CHANGE, not on the timer fields being present: calictl's writes are
    full-packet and carry the current timer values back on every unrelated command, and the unit
    plainly does not refuse those.

    .. test:: The cooling timer is refused while the fridge runs; unrelated writes are not
       :id: T_MOCK_REFUSES_COOLER_TIMER_WHEN_ON
    """
    from calictl import control

    f = _funcs()
    u = _armed_unit(
        cooler={"Installed": 1, "State": 1, "Level": 3, "Mode": 0, "TimerHourSet": 7, "TimerMinSet": 0}
    )
    cur = u.decoded("cooler")

    u.write(f["cooler"].control_char, control.build(f, "cooler", "timer_set", "09:30", cur))
    assert u.decoded("cooler")["TimerHourSet"] == 7  # refused while the fridge runs
    assert u.refusals and "fridge is off" in u.refusals[-1][1]

    n = len(u.refusals)  # an unrelated write must pass
    u.write(f["cooler"].control_char, control.build(f, "cooler", "level", 5, cur))
    assert u.decoded("cooler")["Level"] == 5 and len(u.refusals) == n

    u.state["cooler"]["State"] = 0  # fridge off -> the timer sets
    u.write(f["cooler"].control_char, control.build(f, "cooler", "timer_set", "09:30", u.decoded("cooler")))
    assert (u.decoded("cooler")["TimerHourSet"], u.decoded("cooler")["TimerMinSet"]) == (9, 30)


def test_the_arm_lapses_when_the_heartbeat_stops():
    """The arm is a latch, but not a permanent one: the unit drops a link that carries no 1003 beat
    for ~15 s (on-device 2026-07-09) and the arm goes with it. The mock previously stayed armed
    forever after a single beat, so a daemon whose heartbeat task died mid-sequence would still
    have passed every offline test.

    .. test:: A link that stops beating lapses the arm and drops
       :id: T_MOCK_ARM_LAPSES
    """
    from calictl import device

    u = MockCamperUnit(seed={"cooler": {"Installed": 1, "State": 0, "Level": 3, "Mode": 0}})
    u.write(device.HEARTBEAT_CHAR, (1).to_bytes(4, "big"))
    assert u.armed is True
    u.tick(10)
    assert u.armed is True  # still inside the window
    u.tick(6)  # 16 s since the last beat
    assert u.armed is False and u.online is False

    # a hand-armed fixture (no beat seen) must NOT expire underneath itself
    v = _armed_unit(cooler={"Installed": 1, "State": 0})
    v.tick(120)
    assert v.armed is True


def test_only_one_client_holds_the_connection_slot():
    """The unit has ONE connection slot — while the phone app holds it the unit stops advertising
    and a second connect fails. calictl's failure taxonomy distinguishes that from a sleeping van
    (value-freshness.md); with a mock that let everyone in, the two were indistinguishable and a
    mis-classification was invisible.

    .. test:: A second client is refused while the slot is held
       :id: T_MOCK_SINGLE_CONNECTION_SLOT
    """
    import asyncio

    from tools.mock_unit import MockBleakClient, MockDisconnect

    u = _armed_unit(cooler={"Installed": 1, "State": 1})
    u.one_slot = True  # opt-in: see the note on MockCamperUnit.one_slot
    phone, daemon = MockBleakClient.bind(u)("PH:ON:E"), MockBleakClient.bind(u)("DA:EM:ON")

    asyncio.run(phone.connect())
    try:
        asyncio.run(daemon.connect())
    except MockDisconnect as e:
        assert "slot" in str(e)
    else:
        raise AssertionError("second client connected while the slot was held")

    asyncio.run(phone.disconnect())  # phone hangs up -> the slot frees
    asyncio.run(daemon.connect())
    assert daemon.is_connected is True


def test_the_link_drops_for_a_minute_at_the_engine_crank():
    """Observed at the van: the BLE link drops for ~1 min when the engine cranks — the one moment
    calictl both loses the link and has state changes to reconcile. The camping-shed notification
    still goes out first (field data pairs every ignition 0->1 with master_on 1->0, so the daemon
    did observe the shed), and the unit comes back by itself.

    .. test:: The engine crank drops the link for ~1 min after notifying the shed
       :id: T_MOCK_CRANK_DROP
    """
    u = _armed_unit(
        vehicle={
            "TerminalOneFive": 0,
            "CarTimeYear": 126,
            "CarTimeMonth": 8,
            "CarTimeDay": 17,
            "CarTimeHour": 9,
            "CarTimeMinute": 0,
            "CarTimeSecond": 0,
        },
        campingmode={"Installed": 1, "State": 1, "Enable": 0},
    )
    camping = []
    _subscribe(u, "campingmode", camping)
    u.tick(1.0)  # ignition-edge baseline
    camping.clear()

    u.state["vehicle"]["TerminalOneFive"] = 1  # crank
    u.tick(1.0)
    assert camping, "the camping shed must be notified before the link goes"
    assert u.decoded("campingmode")["State"] == 0
    assert u.online is False  # ...and then the link drops

    u.tick(30)
    assert u.online is False  # still down
    u.tick(31)  # ~61 s after the crank
    assert u.online is True  # the unit comes back by itself


def test_tick_advances_the_vehicle_clock():
    """CarTimeMonth is 0-based on the wire (the app shows month+1 — app lab 2026-09-16); the
    month rollover below is 8 (= September) -> 9 (= October) at 23:59:30 + 45 s on the 30th."""
    u = _armed_unit(
        vehicle={
            "CarTimeYear": 126,
            "CarTimeMonth": 8,
            "CarTimeDay": 30,
            "CarTimeHour": 23,
            "CarTimeMinute": 59,
            "CarTimeSecond": 30,
            "TerminalOneFive": 0,
        }
    )
    u.tick(45)
    v = u.decoded("vehicle")
    assert (v["CarTimeMonth"], v["CarTimeDay"], v["CarTimeHour"], v["CarTimeMinute"], v["CarTimeSecond"]) == (
        9,
        1,
        0,
        0,
        15,
    )


def test_tick_counts_down_immediate_heating_and_stops_it():
    u = _armed_unit(
        airheater={
            "Installed": 1,
            "NormalOperation": 1,
            "PermanentOperation": 0,
            "HeatingLevel": 5,
            "RunningTime": 2,
            "RunningTimeinAction": 2,
        }
    )
    u.tick(59)
    assert u.decoded("airheater")["RunningTimeinAction"] == 2
    u.tick(1)
    assert u.decoded("airheater")["RunningTimeinAction"] == 1
    u.tick(60)
    a = u.decoded("airheater")
    assert a["RunningTimeinAction"] == 0 and a["NormalOperation"] == 0  # run time over -> off


def test_tick_fires_the_cooler_timer_at_its_start_time():
    u = _armed_unit(
        vehicle={
            "CarTimeYear": 126,
            "CarTimeMonth": 9,
            "CarTimeDay": 16,
            "CarTimeHour": 8,
            "CarTimeMinute": 59,
            "CarTimeSecond": 0,
        },
        cooler={
            "Installed": 1,
            "State": 0,
            "TimerState": 1,
            "TimerHourSet": 9,
            "TimerMinSet": 0,
            "Level": 3,
            "Mode": 0,
        },
    )
    u.tick(30)
    c = u.decoded("cooler")
    assert c["State"] == 0 and (c["TimerCounterHour"], c["TimerCounterMin"]) == (0, 1)
    u.tick(31)
    c = u.decoded("cooler")
    assert c["State"] == 1 and c["TimerState"] == 0 and c["TimerElapsed"] == 1


def test_heater_timer_arm_frame_arms_and_fires_at_the_start_time():
    """The app's "Start timer" frame (3f3b017f1f3f: Mode=3, Combined=1) must land as
    OperationModeAirHeater=3 in the state (the app re-reads it as "Timer: On"); "Stop"
    (3f0b007f1f3f) clears it. Armed, the clock fires the heater at TimerHour:TimerMin
    (modelled: NormalOperation=1, countdown loaded, Mode back to 0 — the unit's own
    post-fire Mode value is UNVERIFIED)."""
    u = _armed_unit(
        vehicle={
            "CarTimeYear": 126,
            "CarTimeMonth": 9,
            "CarTimeDay": 16,
            "CarTimeHour": 11,
            "CarTimeMinute": 59,
            "CarTimeSecond": 0,
        },
        airheater={
            "Installed": 1,
            "NormalOperation": 0,
            "PermanentOperation": 0,
            "HeatingLevel": 5,
            "RunningTime": 60,
            "RunningTimeinAction": 0,
            "OperationModeAirHeater": 0,
            "OperationModeCombined": 0,
            "TimerHour": 12,
            "TimerMin": 0,
        },
    )
    ch = _funcs()["airheater"].control_char
    u.write(ch, bytes.fromhex("3f3b017f1f3f"))
    a = u.decoded("airheater")
    assert (a["OperationModeAirHeater"], a["OperationModeCombined"], a["NormalOperation"]) == (3, 1, 0)
    u.write(ch, bytes.fromhex("3f0b007f1f3f"))
    assert u.decoded("airheater")["OperationModeAirHeater"] == 0
    u.write(ch, bytes.fromhex("3f3b017f1f3f"))
    u.tick(30)
    assert u.decoded("airheater")["NormalOperation"] == 0  # 11:59:30 — not yet
    u.tick(31)
    a = u.decoded("airheater")
    assert a["NormalOperation"] == 1 and a["RunningTimeinAction"] == 60 and a["OperationModeAirHeater"] == 0


def test_ignition_couples_into_camping_and_battery_age():
    u = _armed_unit(
        vehicle={
            "TerminalOneFive": 0,
            "CarTimeYear": 126,
            "CarTimeMonth": 9,
            "CarTimeDay": 16,
            "CarTimeHour": 8,
            "CarTimeMinute": 0,
            "CarTimeSecond": 0,
        },
        campingmode={"Installed": 1, "State": 1, "Enable": 0, "UsbCharger": 1},
        energy={"AgeOneBattValuesMinutes": 3},
    )
    u.tick(120)
    assert u.decoded("energy")["AgeOneBattValuesMinutes"] == 5  # starter data ages while parked
    u.state["vehicle"]["TerminalOneFive"] = 1  # key turned (e.g. via the console)
    u.tick(1)
    cm = u.decoded("campingmode")
    assert cm["Enable"] == 1 and cm["State"] == 0  # unit sheds camping master
    assert u.decoded("energy")["AgeOneBattValuesMinutes"] == 0  # starter subsystem awake
    u.state["vehicle"]["TerminalOneFive"] = 0
    u.tick(1)
    assert u.decoded("campingmode")["Enable"] == 0


WAKE_0700 = "0e146ac49c701100eeeeeeeeeeeeeeee"  # lighting-wakeup.jsonl
SAVE_A = "010400000000000000000005e00eeeee"  # lighting-profile.jsonl (L7 = 5)


def _w(u, hexframe):
    f = _funcs()["lighting"]
    u.write(f.control_char, bytes.fromhex(hexframe))


def test_wakeup_is_stored_and_echoed_on_1502_through_the_commit():
    """The app's wake-up page reads its time from the 1502 Mode-20 frame (app-rec2: without an echo
    the switch resent 00:00). The echo must survive the app's 0e00 flush.

    .. test:: Mock stores the wake-up config and echoes it on 1502
       :id: T_MOCK_LIGHT_WAKEUP
       :links: R_LIGHT_WAKEUP
    """
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 12, "Mode": 16})
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()
    _w(u, WAKE_0700)
    assert u.wakeup == {"Timestamp": 0x6AC49C70, "LightValue": 0x1100}
    assert protocol.decode(_funcs()["lighting"], pushes[-1])["Mode"] == 20
    _w(u, control.LIGHT_COMMIT.hex())
    assert u.decoded("lighting")["Mode"] == 16  # the echo is a one-off frame, never stored state
    # builder -> mock -> semantics round trip: the 1502 echo decodes to the wake-up the CLI asked for
    pushes.clear()
    u.write(_funcs()["lighting"].control_char, control.build(_funcs(), "lighting", "wakeup", "07:00 on", {}))
    cfg = semantics.wakeup_config(
        semantics.lighting_config(None, protocol.decode(_funcs()["lighting"], pushes[-1]))
    )
    assert cfg["time"] == "07:00" and cfg["enabled"]


def test_door_contact_flag_is_reported_on_1502_and_never_becomes_the_active_profile():
    """Door contact = SET_PROFILE PN 8 with LightValue 1/0 (dg/h n4; lighting-door-contact recording).

    .. test:: Mock reports the door-contact flag as Mode 16 / PN 8 / LightValue
       :id: T_MOCK_LIGHT_DOOR
       :links: R_LIGHT_DOOR_CONTACT
    """
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 12, "Mode": 16})
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()
    _w(u, "0810000000000001eeeeeeeeeeeeeeee")
    _w(u, control.LIGHT_COMMIT.hex())
    d = u.decoded("lighting")
    assert u.door_contact == 1 and (d["Mode"], d["ProfileNumber"]) == (16, 12)  # real state untouched
    echo = protocol.decode(_funcs()["lighting"], pushes[0])
    assert (echo["Mode"], echo["ProfileNumber"], echo["LightValue"]) == (16, 8, 1)
    _w(u, "0810000000000000eeeeeeeeeeeeeeee")
    assert u.door_contact == 0
    # builder -> mock -> semantics round trip (polarity: on = True)
    pushes.clear()
    u.write(_funcs()["lighting"].control_char, control.build(_funcs(), "lighting", "door_contact", "on", {}))
    got = semantics.lighting(protocol.decode(_funcs()["lighting"], pushes[-1]))
    assert got["door_contact"] is True


def test_activating_an_empty_favourite_is_acked_and_ignored():
    """Evidence: decompile only (dg/h u0 is offered by the app on a stored tile); not device-confirmed.

    .. test:: Mock refuses (ACK-and-ignore) an empty favourite
       :id: T_MOCK_LIGHT_FAV_EMPTY
       :links: R_LIGHT_FAVOURITE
    """
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 12, "Mode": 16})
    _w(u, "0210000000000000eeeeeeeeeeeeeeee")
    _w(u, control.LIGHT_COMMIT.hex())
    assert ("lighting", "favourite 2 is empty") in u.refusals
    assert u.decoded("lighting")["ProfileNumber"] == 12


def test_save_then_activate_restores_the_saved_levels():
    """dg/h l3: SET_COLOR then SET_BRIGHTNESS under the favourite's PN = save, not a live change
    (lighting-profile.jsonl); activation = SET_PROFILE PN n.

    .. test:: Mock saves a favourite without a live change and applies it on activate
       :id: T_MOCK_LIGHT_FAV_SAVE
       :links: R_LIGHT_FAVOURITE
    """
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 9, "BrightnessLSeven": 5})
    _w(u, "010600000000000900000005e00eeeee")  # SET_COLOR red for favourite 1 (preface)
    _w(u, control.LIGHT_COMMIT.hex())
    _w(u, SAVE_A)
    _w(u, control.LIGHT_COMMIT.hex())
    assert u.favourites[1]["zones"]["BrightnessLSeven"] == 5 and u.favourites[1]["colour"] == 9
    assert u.decoded("lighting")["ProfileNumber"] == 9  # a save is not an activation
    _commit_brightness(u, "BrightnessLSeven", 0)  # lamp off
    _w(u, "0110000000000000eeeeeeeeeeeeeeee")
    _w(u, control.LIGHT_COMMIT.hex())
    d = u.decoded("lighting")
    assert d["ProfileNumber"] == 1 and d["BrightnessLSeven"] == 5


def test_all_lights_master_switches_every_equipped_zone_and_acks_on_1502():
    """The app's All-lights master (dg/h Q, ``lighting-zone.jsonl``: ``0c10…`` + ``0e00…``) is a
    SET_PROFILE PN 12 (LIGHTS_ON) / PN 0 (LIGHTS_OFF). The mock used to only store the profile
    number, so the lamps stayed dark and the next read showed the master off again.

    MOCK-MODELLED, not van-verified (#230): ON lights every equipped zone that is off at
    ``control.LIGHT_ON_BRIGHTNESS`` (zones already lit keep their level; NOT_EQUIPPED 13 and the
    pop-top light with the roof down stay as they are); OFF darkens every equipped zone.

    .. test:: Mock applies the All-lights master to every equipped zone and acks on 1502
       :id: T_MOCK_LIGHT_ALL_LIGHTS
    """
    f = _funcs()["lighting"]
    u = _armed_unit(
        lighting={"Installed": 1, "ProfileNumber": 0, "BrightnessLSeven": 4, "BrightnessLOneZero": 13}
    )
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()
    u.write(f.control_char, control.build(_funcs(), "lighting", "power", "on", u.decoded("lighting")))
    u.write(f.control_char, control.LIGHT_COMMIT)
    d = u.decoded("lighting")
    assert d["ProfileNumber"] == control.LIGHT_PROFILE_ALL_ON
    assert d["BrightnessLOne"] == control.LIGHT_ON_BRIGHTNESS  # was off -> on
    assert d["BrightnessLSeven"] == 4  # already lit: kept
    assert d["BrightnessLOneZero"] == 13  # not equipped: untouched
    assert d["BrightnessLNine"] == 0  # pop-top light: roof down (seed) -> unpowered
    assert semantics.lighting(d)["any_on"] is True
    ack = protocol.decode(f, pushes[0])  # the app awaits a 1502 frame with PN == 12
    assert (ack["Mode"], ack["ProfileNumber"]) == (16, control.LIGHT_PROFILE_ALL_ON)
    for _ in range(12):  # the lamps ramp to the target and the Mode-4 pushes carry it
        u.tick(0.5)
    assert u.light_actual["BrightnessLOne"] == control.LIGHT_ON_BRIGHTNESS

    u.write(f.control_char, control.build(_funcs(), "lighting", "power", "off", u.decoded("lighting")))
    u.write(f.control_char, control.LIGHT_COMMIT)
    d = u.decoded("lighting")
    assert d["ProfileNumber"] == control.LIGHT_PROFILE_ALL_OFF
    assert semantics.lighting(d)["any_on"] is False
    assert d["BrightnessLOneZero"] == 13


def test_request_config_reply_comes_after_the_save_ack():
    """dg/h.l3 sends d0() REQUEST_CONFIG right after the save ack and awaits the Mode-12 reply whose
    LightValue bits 0-6 are FavoriteProfileModifiedState (vineflower dg/a.java:286-290). Back to back,
    the reply must be the LAST 1502 frame, with favourite 1's bit set.

    .. test:: REQUEST_CONFIG reply follows the save ack and carries the favourite bit
       :id: T_MOCK_LIGHT_REQUEST_CONFIG
       :links: R_LIGHT_FAVOURITE
    """
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 9, "BrightnessLSeven": 5})
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()
    _w(u, SAVE_A)
    _w(u, control.LIGHT_COMMIT.hex())
    _w(u, "0d0c000000000000eeeeeeeeeeeeeeee")  # the app's REQUEST_CONFIG
    modes = [protocol.decode(_funcs()["lighting"], p)["Mode"] for p in pushes]
    assert modes == [4, 12]
    ack = protocol.decode(_funcs()["lighting"], pushes[0])
    assert ack["ProfileNumber"] == 1  # the save ack names the saved favourite
    assert protocol.decode(_funcs()["lighting"], pushes[-1])["LightValue"] & 1 == 1
    d = u.decoded("lighting")  # nothing sticky: reads still return the real lighting state
    assert d["ProfileNumber"] == 9 and d["Mode"] not in (4, 12, 20)


def test_request_config_reply_also_reports_the_wakeup_and_door_frames():
    """The app awaits 6 frames after REQUEST_CONFIG and fills its wake-up/door state from them
    (decompile d0 / F0): after the Mode-12 favourites frame the mock re-reports the stored Mode-20
    wake-up and the Mode-16/PN-8 door frame (only when set).

    .. test:: REQUEST_CONFIG reply re-reports wake-up and door
       :id: T_MOCK_LIGHT_REQUEST_CONFIG_FULL
       :links: R_LIGHT_FAVOURITE
    """
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 9})
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()
    _w(u, "0d0c000000000000eeeeeeeeeeeeeeee")
    assert [protocol.decode(_funcs()["lighting"], p)["Mode"] for p in pushes] == [12]  # nothing stored yet
    u.write(_funcs()["lighting"].control_char, control.build(_funcs(), "lighting", "wakeup", "07:00 on", {}))
    u.write(_funcs()["lighting"].control_char, control.build(_funcs(), "lighting", "door_contact", "on", {}))
    pushes.clear()
    _w(u, "0d0c000000000000eeeeeeeeeeeeeeee")
    got = [protocol.decode(_funcs()["lighting"], p) for p in pushes]
    assert [(g["Mode"], g.get("ProfileNumber")) for g in got][:1] == [(12, 9)]
    assert [g["Mode"] for g in got] == [12, 20, 16]
    assert got[1]["LightValue"] & 1 == 1 and got[2]["ProfileNumber"] == 8 and got[2]["LightValue"] == 1


def test_set_color_is_acked_on_1502():
    """dg/h.l3 step (a) awaits an ack for SET_COLOR (Mode 6, PN N)."""
    u = _armed_unit(lighting={"Installed": 1, "ProfileNumber": 9})
    pushes = []
    _subscribe(u, "lighting", pushes)
    pushes.clear()
    _w(u, "010600000000000900000005e00eeeee")  # SET_COLOR red for favourite 1
    _w(u, control.LIGHT_COMMIT.hex())
    got = [protocol.decode(_funcs()["lighting"], p) for p in pushes]
    assert [(g["Mode"], g["ProfileNumber"]) for g in got] == [(6, 1)]
