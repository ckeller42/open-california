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
from calictl import control, overrides, protocol
from tools.mock_unit import MockCamperUnit


def _armed_unit(**seed):
    u = MockCamperUnit(seed=seed)
    u.armed = True
    return u


def _funcs():
    f = protocol.load(); overrides.apply(f); return f


def test_app_frame_sentinels_leave_untargeted_fields_alone():
    """The app's real Dauerbetrieb-OFF frame: only PermanentOperationRequest=0 is a value; level 11,
    mode 7, run time 127, timer 31:63 are the leave-unchanged defaults and must not be applied."""
    f = _funcs()
    u = _armed_unit(airheater={"Installed": 1, "PermanentOperation": 1, "NormalOperation": 0,
                               "HeatingLevel": 5, "RunningTime": 60, "TimerHour": 12, "TimerMin": 0})
    u.write(f["airheater"].control_char, bytes.fromhex("0f7b007f1f3f"))
    st = u.decoded("airheater")
    assert st["HeatingLevel"] == 5 and st["RunningTime"] == 60
    assert (st["TimerHour"], st["TimerMin"]) == (12, 0)
    assert st["PermanentOperation"] == 0          # the one targeted field took effect


def test_request_fields_drive_their_state_bits():
    f = _funcs()
    u = _armed_unit(airheater={"Installed": 1, "PermanentOperation": 0, "NormalOperation": 0,
                               "HeatingLevel": 5, "RunningTime": 60})
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
    u = _armed_unit(airheater={"Installed": 1, "NormalOperation": 0, "PermanentOperation": 0,
                               "HeatingLevel": 5, "RunningTime": 60, "RunningTimeinAction": 0})
    u.write(f["airheater"].control_char, bytes.fromhex("3d7b007f1f3f"))   # the app's ON frame
    assert u.decoded("airheater")["RunningTimeinAction"] == 60
    u.write(f["airheater"].control_char, bytes.fromhex("3c7b007f1f3f"))   # the app's OFF frame
    assert u.decoded("airheater")["RunningTimeinAction"] == 0


def test_app_neutral_frame_with_all_2bit_sentinels_is_accepted():
    """500 ms after every write the app sends a neutral frame with every 2-bit field at 3 and every
    wider field at its default (cooler `ff771e3e1f1f`). The unit accepts it; so must the mock — the
    curated `State in {0,1}` constraint is for calictl's own commands, not for the sentinel."""
    f = _funcs()
    u = _armed_unit(cooler={"Installed": 1, "State": 0, "Level": 3, "Mode": 4,
                            "NightTimerHourOn": 22, "NightTimerHourOff": 6})
    u.write(f["cooler"].control_char, bytes.fromhex("fc771e3e1f1f"))   # the app's OFF frame
    u.write(f["cooler"].control_char, bytes.fromhex("ff771e3e1f1f"))   # its neutral follow-up
    st = u.decoded("cooler")
    assert st["State"] == 0 and st["Level"] == 3 and st["Mode"] == 4
    assert (st["NightTimerHourOn"], st["NightTimerHourOff"]) == (22, 6)


def test_cooler_time_picker_frame_sets_the_start_time_fields():
    """The app's Time picker writes TimerHour/TimerMin alone (ff7704021f1f = 04:02, observed) and
    then displays the unit's TimerHourSet/TimerMinSet — the mock must map control→state names."""
    f = _funcs()
    u = _armed_unit(cooler={"Installed": 1, "State": 0, "Level": 3, "Mode": 4,
                            "TimerHourSet": 0, "TimerMinSet": 0})
    u.write(f["cooler"].control_char, bytes.fromhex("ff7704021f1f"))
    st = u.decoded("cooler")
    assert (st["TimerHourSet"], st["TimerMinSet"]) == (4, 2)
    assert st["State"] == 0 and st["Level"] == 3           # sentinels left everything else alone


def test_cooler_timer_action_bits_arm_and_clear_the_timer():
    """The app's "Timer" switch (box off) writes only TimerStart=1 with everything else at the
    sentinels (`f7771e3e1f1f`, observed); the unit reports TimerState=1. TimerCancel=1 clears it."""
    f = _funcs()
    u = _armed_unit(cooler={"Installed": 1, "State": 0, "Level": 3, "Mode": 0, "TimerState": 0,
                            "TimerHourSet": 9, "TimerMinSet": 0})
    u.write(f["cooler"].control_char, bytes.fromhex("f7771e3e1f1f"))
    st = u.decoded("cooler")
    assert st["TimerState"] == 1 and (st["TimerHourSet"], st["TimerMinSet"]) == (9, 0)
    u.write(f["cooler"].control_char, bytes.fromhex("df771e3e1f1f"))   # TimerCancel=1 (bits 2-3)
    assert u.decoded("cooler")["TimerState"] == 0


def test_real_value_equal_to_default_is_not_mistaken_for_a_sentinel_on_2bit_fields():
    """2-bit fields keep the existing rule (3 = leave unchanged; 0/1 are values)."""
    f = _funcs()
    u = _armed_unit(campingmode={"Installed": 1, "State": 1, "UsbCharger": 1})
    u.write(f["campingmode"].control_char, control.build(f, "campingmode", "master", "off",
                                                          u.state["campingmode"]))
    assert u.decoded("campingmode")["State"] == 0


def _roof_frame(f, up, down, counter):
    return protocol.encode(f["roof"], {"Up": up, "Down": down, "SafetyCounter": counter},
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["roof"])


def test_roof_counter_validates_only_when_incrementing():
    f = _funcs()
    u = _armed_unit(roof={"Installed": 1, "Position": 0, "InfoPopUp": 0, "SafetyCounterValid": 0})
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, 0x5024))
    assert u.decoded("roof")["SafetyCounterValid"] == 0     # first frame: nothing to compare
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, 0x5025))
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, 0x5026))
    assert u.decoded("roof")["SafetyCounterValid"] == 1     # seen incrementing -> valid
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
    for _ in range(3):                                       # idle stream: +1 per 500 ms frame
        u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c)); c += 1; u.tick(0.5)
    assert u.decoded("roof")["SafetyCounterValid"] == 1
    for i in range(16):                                      # press: 8 Hz, counter +1 every 4 frames
        u.write(f["roof"].control_char, _roof_frame(f, 1, 0, c + i // 4)); u.tick(0.125)
    assert u.decoded("roof")["SafetyCounterValid"] == 1
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c))            # counter went backwards
    assert u.decoded("roof")["SafetyCounterValid"] == 0
    for _ in range(3):
        c += 10; u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c)); u.tick(0.5)
    assert u.decoded("roof")["SafetyCounterValid"] == 1                # re-validated
    u.tick(2.0)
    u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c))            # same value after 2 s idle
    assert u.decoded("roof")["SafetyCounterValid"] == 0                # frozen counter -> invalid


def test_roof_validity_expires_when_the_stream_stops():
    """Leaving the roof page stops the counter stream; the unit must drop SafetyCounterValid, or
    the app's next visit sees a stale 1 and says "Function in use — another user…" (observed)."""
    f = _funcs()
    u = _armed_unit(roof={"Installed": 1, "Position": 0, "InfoPopUp": 0, "SafetyCounterValid": 0})
    for c in (10, 11, 12):
        u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c)); u.tick(0.5)
    assert u.decoded("roof")["SafetyCounterValid"] == 1
    assert "roof" in u.tick(2.0)                                # stream gone -> cleared, notified
    assert u.decoded("roof")["SafetyCounterValid"] == 0


def test_roof_travels_on_the_clock_while_a_valid_move_is_held():
    """Motion is time-based (real unit: ~3 s counter validation, then ~10 s per half travel):
    with Up held and the counter valid, Position steps closed -> middle -> open every
    ROOF_STEP_S of tick(); releasing (no frames for >1.5 s) stops it; Down reverses."""
    f = _funcs()
    u = _armed_unit(roof={"Installed": 1, "Position": 0, "InfoPopUp": 0, "SafetyCounterValid": 0})
    c = 100

    def hold(up, down, seconds):
        nonlocal c
        for _ in range(int(seconds * 2)):                    # a frame every 500 ms, like the app
            u.write(f["roof"].control_char, _roof_frame(f, up, down, c)); c += 1
            u.tick(0.5)

    hold(0, 0, 1.5)                                          # counter validates (stop frames)
    assert u.decoded("roof")["SafetyCounterValid"] == 1
    hold(1, 0, 3.0)
    assert u.decoded("roof")["Position"] == 0                # still within the first step time
    hold(1, 0, 2.0)                                          # 5 s of Up held -> one step
    assert u.decoded("roof")["Position"] == 2
    u.tick(3.0)                                              # released: no frames -> motion stops,
    assert u.decoded("roof")["Position"] == 2                # and the counter validity expires
    assert u.decoded("roof")["SafetyCounterValid"] == 0
    hold(1, 0, 6.5)                                          # ~1 s to re-validate + 5 s of travel
    assert u.decoded("roof")["Position"] == 1                # middle -> open
    hold(1, 0, 5.0)
    assert u.decoded("roof")["Position"] == 1                # open: stays
    hold(0, 1, 5.0)
    assert u.decoded("roof")["Position"] == 2                # open -> middle
    hold(0, 1, 5.0)
    assert u.decoded("roof")["Position"] == 0


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


def test_tick_advances_the_vehicle_clock():
    """CarTimeMonth is 0-based on the wire (the app shows month+1 — app lab 2026-09-16); the
    month rollover below is 8 (= September) -> 9 (= October) at 23:59:30 + 45 s on the 30th."""
    u = _armed_unit(vehicle={"CarTimeYear": 126, "CarTimeMonth": 8, "CarTimeDay": 30,
                             "CarTimeHour": 23, "CarTimeMinute": 59, "CarTimeSecond": 30,
                             "TerminalOneFive": 0})
    u.tick(45)
    v = u.decoded("vehicle")
    assert (v["CarTimeMonth"], v["CarTimeDay"], v["CarTimeHour"], v["CarTimeMinute"], v["CarTimeSecond"]) == (9, 1, 0, 0, 15)


def test_tick_counts_down_immediate_heating_and_stops_it():
    u = _armed_unit(airheater={"Installed": 1, "NormalOperation": 1, "PermanentOperation": 0,
                               "HeatingLevel": 5, "RunningTime": 2, "RunningTimeinAction": 2})
    u.tick(59)
    assert u.decoded("airheater")["RunningTimeinAction"] == 2
    u.tick(1)
    assert u.decoded("airheater")["RunningTimeinAction"] == 1
    u.tick(60)
    a = u.decoded("airheater")
    assert a["RunningTimeinAction"] == 0 and a["NormalOperation"] == 0   # run time over -> off


def test_tick_fires_the_cooler_timer_at_its_start_time():
    u = _armed_unit(vehicle={"CarTimeYear": 126, "CarTimeMonth": 9, "CarTimeDay": 16,
                             "CarTimeHour": 8, "CarTimeMinute": 59, "CarTimeSecond": 0},
                    cooler={"Installed": 1, "State": 0, "TimerState": 1, "TimerHourSet": 9,
                            "TimerMinSet": 0, "Level": 3, "Mode": 0})
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
    u = _armed_unit(vehicle={"CarTimeYear": 126, "CarTimeMonth": 9, "CarTimeDay": 16,
                             "CarTimeHour": 11, "CarTimeMinute": 59, "CarTimeSecond": 0},
                    airheater={"Installed": 1, "NormalOperation": 0, "PermanentOperation": 0,
                               "HeatingLevel": 5, "RunningTime": 60, "RunningTimeinAction": 0,
                               "OperationModeAirHeater": 0, "OperationModeCombined": 0,
                               "TimerHour": 12, "TimerMin": 0})
    ch = _funcs()["airheater"].control_char
    u.write(ch, bytes.fromhex("3f3b017f1f3f"))
    a = u.decoded("airheater")
    assert (a["OperationModeAirHeater"], a["OperationModeCombined"], a["NormalOperation"]) == (3, 1, 0)
    u.write(ch, bytes.fromhex("3f0b007f1f3f"))
    assert u.decoded("airheater")["OperationModeAirHeater"] == 0
    u.write(ch, bytes.fromhex("3f3b017f1f3f"))
    u.tick(30)
    assert u.decoded("airheater")["NormalOperation"] == 0            # 11:59:30 — not yet
    u.tick(31)
    a = u.decoded("airheater")
    assert a["NormalOperation"] == 1 and a["RunningTimeinAction"] == 60 and a["OperationModeAirHeater"] == 0


def test_ignition_couples_into_camping_and_battery_age():
    u = _armed_unit(vehicle={"TerminalOneFive": 0, "CarTimeYear": 126, "CarTimeMonth": 9,
                             "CarTimeDay": 16, "CarTimeHour": 8, "CarTimeMinute": 0, "CarTimeSecond": 0},
                    campingmode={"Installed": 1, "State": 1, "Enable": 0, "UsbCharger": 1},
                    energy={"AgeOneBattValuesMinutes": 3})
    u.tick(120)
    assert u.decoded("energy")["AgeOneBattValuesMinutes"] == 5       # starter data ages while parked
    u.state["vehicle"]["TerminalOneFive"] = 1                       # key turned (e.g. via the console)
    u.tick(1)
    cm = u.decoded("campingmode")
    assert cm["Enable"] == 1 and cm["State"] == 0                     # unit sheds camping master
    assert u.decoded("energy")["AgeOneBattValuesMinutes"] == 0       # starter subsystem awake
    u.state["vehicle"]["TerminalOneFive"] = 0
    u.tick(1)
    assert u.decoded("campingmode")["Enable"] == 0
