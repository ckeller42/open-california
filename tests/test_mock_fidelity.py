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


def test_roof_moves_one_step_per_valid_move_frame():
    f = _funcs()
    u = _armed_unit(roof={"Installed": 1, "Position": 0, "InfoPopUp": 0, "SafetyCounterValid": 0})
    c = 100
    for _ in range(3):                                      # stop frames: validate the counter
        u.write(f["roof"].control_char, _roof_frame(f, 0, 0, c)); c += 1
    u.write(f["roof"].control_char, _roof_frame(f, 1, 0, c)); c += 1
    assert u.decoded("roof")["Position"] == 2              # closed -> middle
    u.write(f["roof"].control_char, _roof_frame(f, 1, 0, c)); c += 1
    assert u.decoded("roof")["Position"] == 1              # middle -> open
    u.write(f["roof"].control_char, _roof_frame(f, 1, 0, c)); c += 1
    assert u.decoded("roof")["Position"] == 1              # already open: stays
    u.write(f["roof"].control_char, _roof_frame(f, 0, 1, c)); c += 1
    u.write(f["roof"].control_char, _roof_frame(f, 0, 1, c)); c += 1
    assert u.decoded("roof")["Position"] == 0              # open -> middle -> closed
