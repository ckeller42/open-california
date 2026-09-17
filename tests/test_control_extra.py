"""Control-frame round-trips for the extra actuatable functions
(roofaircondition / stairs / livingroomheater / roof).

Stdlib-only, NO BLE: every test builds a frame with `control.build`/`roof_frame`
and reads it back with `control.decode_control` (which uses the CONTROL-field
offsets, not the STATE offsets). These functions are NOT installed on this van, so
this only proves the frame layout + value packing, not on-device behaviour.

.. test:: Extra control builders encode targeted field, carry the rest
   :id: T_CONTROL_EXTRA
   :links: R_AIRHEATER_SET, R_ROOF_ACTUATE
"""
import pytest

from calictl import control, overrides
from calictl import protocol as P


def _funcs():
    f = P.load(); overrides.apply(f); return f


# --- roofaircondition (char 2001) --------------------------------------------

def test_roofac_power_toggles_state_and_carries_rest():
    f = _funcs()
    cur = {"State": 0, "Mode": 2, "Fanspeed": 3, "Temperature": 21}
    on = control.decode_control(f["roofaircondition"],
                                control.build(f, "roofaircondition", "power", "on", cur))
    off = control.decode_control(f["roofaircondition"],
                                 control.build(f, "roofaircondition", "power", "off", cur))
    assert (on["State"], off["State"]) == (1, 0)
    # untargeted fields carry current state (mirror _cooler)
    assert on["Mode"] == 2 and on["FanSpeed"] == 3 and on["Temperature"] == 21


def test_roofac_fanspeed_mode_temperature():
    f = _funcs()
    cur = {"State": 1, "Mode": 0, "Fanspeed": 0, "Temperature": 0}
    fan = control.decode_control(f["roofaircondition"],
                                 control.build(f, "roofaircondition", "fanspeed", "4", cur))
    mode = control.decode_control(f["roofaircondition"],
                                  control.build(f, "roofaircondition", "mode", "3", cur))
    temp = control.decode_control(f["roofaircondition"],
                                  control.build(f, "roofaircondition", "temperature", "200", cur))
    assert fan["FanSpeed"] == 4 and fan["State"] == 1
    assert mode["Mode"] == 3
    assert temp["Temperature"] == 200


@pytest.mark.parametrize("what,bad", [("fanspeed", "5"), ("mode", "4"), ("temperature", "256")])
def test_roofac_range_guards(what, bad):
    f = _funcs()
    with pytest.raises(ValueError):
        control.build(f, "roofaircondition", what, bad, {})


# --- stairs (char 1801) ------------------------------------------------------

def test_stairs_movement_maps():
    f = _funcs()
    ext = control.decode_control(f["stairs"], control.build(f, "stairs", "move", "extend", {}))
    ret = control.decode_control(f["stairs"], control.build(f, "stairs", "move", "retract", {}))
    stop = control.decode_control(f["stairs"], control.build(f, "stairs", "move", "stop", {}))
    assert ext["Movement"] == 2 and ret["Movement"] == 1 and stop["Movement"] == 0
    # OperationMode stays at the leave-unchanged sentinel when only Movement is targeted
    assert ext["OperationMode"] == control.SENTINEL


def test_stairs_mode_sets_operationmode_and_leaves_movement_stopped():
    f = _funcs()
    back = control.decode_control(f["stairs"], control.build(f, "stairs", "mode", "1", {}))
    assert back["OperationMode"] == 1 and back["Movement"] == 0


@pytest.mark.parametrize("what,bad", [("move", "sideways"), ("mode", "4")])
def test_stairs_guards(what, bad):
    f = _funcs()
    with pytest.raises(ValueError):
        control.build(f, "stairs", what, bad, {})


# --- livingroomheater (char 2101) --------------------------------------------

def test_lrheater_air_water_toggles_and_carries_rest():
    f = _funcs()
    cur = {"StateAir": 0, "StateWater": 0, "TemperatureWater": 1, "Mode": 5, "TemperatureAir": 40}
    air = control.decode_control(f["livingroomheater"],
                                 control.build(f, "livingroomheater", "air", "on", cur))
    water = control.decode_control(f["livingroomheater"],
                                   control.build(f, "livingroomheater", "water", "on", cur))
    assert air["StateAir"] == 1 and air["StateWater"] == 0        # only air targeted
    assert water["StateWater"] == 1 and water["StateAir"] == 0    # only water targeted
    # untargeted fields carry current state
    assert air["Mode"] == 5 and air["TemperatureAir"] == 40 and air["TemperatureWater"] == 1


def test_lrheater_temperature():
    f = _funcs()
    cur = {"StateAir": 1, "StateWater": 0, "TemperatureWater": 0, "Mode": 5, "TemperatureAir": 0}
    back = control.decode_control(f["livingroomheater"],
                                  control.build(f, "livingroomheater", "temperature", "255", cur))
    assert back["TemperatureAir"] == 255 and back["StateAir"] == 1


def test_lrheater_temperature_guard():
    f = _funcs()
    with pytest.raises(ValueError):
        control.build(f, "livingroomheater", "temperature", "256", {})


# --- roof (char 1401) — SAFETY-SENSITIVE, NOT-LIVE-VERIFIED ------------------

def test_roof_open_close_stop_set_up_down():
    f = _funcs()
    up = control.decode_control(f["roof"], control.roof_frame(f, "open"))
    down = control.decode_control(f["roof"], control.roof_frame(f, "close"))
    stop = control.decode_control(f["roof"], control.roof_frame(f, "stop"))
    assert (up["Up"], up["Down"]) == (1, 0)      # OPEN
    assert (down["Up"], down["Down"]) == (0, 1)  # CLOSE
    assert (stop["Up"], stop["Down"]) == (0, 0)  # STOP


def test_roof_build_matches_roof_frame():
    f = _funcs()
    # the generic builder (used by control.build / BUILDERS) routes `what`->direction
    assert control.build(f, "roof", "open", None, {}) == control.roof_frame(f, "open")
    assert control.build(f, "roof", "close", None, {}) == control.roof_frame(f, "close")


def test_roof_safety_counter_default_zero_and_settable():
    f = _funcs()
    zero = control.decode_control(f["roof"], control.roof_frame(f, "open"))
    assert zero["SafetyCounter"] == 0
    beat = control.decode_control(f["roof"], control.roof_frame(f, "open", counter=0x1234))
    assert beat["SafetyCounter"] == 0x1234


def test_roof_frame_is_five_bytes():
    f = _funcs()
    assert len(control.roof_frame(f, "open")) == 5


def test_roof_bad_direction_raises():
    f = _funcs()
    with pytest.raises(ValueError):
        control.roof_frame(f, "sideways")
    assert control.build(f, "roof", "sideways", None, {}) is None


def test_int_range_helper_validates_and_traces():
    """The shared control int-range validator: coerces valid values, rejects out-of-range/garbage.

    .. test:: _int_range coerces valid ints and rejects out-of-range/non-numeric
       :id: T_CONTROL_INT_RANGE
       :links: R_CONTROL_INT_RANGE
    """
    import pytest

    from calictl import control
    assert control._int_range("3", 1, 5, "cooler level") == 3     # str coerced
    assert control._int_range(0, 0, 23, "night hour") == 0        # inclusive bounds
    assert control._int_range(23, 0, 23, "night hour") == 23
    for bad in (0, 6, -1):                                          # out of 1..5
        with pytest.raises(ValueError):
            control._int_range(bad, 1, 5, "cooler level")
    with pytest.raises(ValueError):                                # non-numeric
        control._int_range("nope", 1, 5, "cooler level")


def test_command_precondition_roof_reading_needs_raised_roof():
    from calictl import control
    # roof closed (0/14) -> ON write refused; open/middle/unknown -> allowed; OFF always allowed.
    assert control.command_precondition("lighting", "roof-reading", 8, {"roof": {"Position": 0}})
    assert control.command_precondition("lighting", "roof-reading", 8, {"roof": {"Position": 14}})
    assert control.command_precondition("lighting", "roof-reading", 8, {"roof": {"Position": 1}}) is None
    assert control.command_precondition("lighting", "roof-reading", 8, {"roof": {"Position": 2}}) is None
    assert control.command_precondition("lighting", "roof-reading", 8, {}) is None          # unknown -> allow
    assert control.command_precondition("lighting", "roof-reading", 0, {"roof": {"Position": 0}}) is None  # OFF ok
    assert control.command_precondition("lighting", "kitchen", 8, {"roof": {"Position": 0}}) is None       # other zone


def test_command_precondition_cooler_timer_needs_fridge_off():
    from calictl import control
    on = {"cooler": {"State": 1}}
    off = {"cooler": {"State": 0}}
    for what in ("timer_set", "timer_start"):
        assert control.command_precondition("cooler", what, 9, on)        # fridge on -> refuse
        assert control.command_precondition("cooler", what, 9, off) is None
        assert control.command_precondition("cooler", what, 9, {}) is None  # unknown -> allow
    assert control.command_precondition("cooler", "power", "on", on) is None  # non-timer unaffected


def test_command_precondition_mirrors_the_remaining_web_ui_gates():
    """The web UI greys state-forbidden controls, but those gates are client-side: the CLI/API/HA
    paths bypass them. These three families are the ones the UI grays and the server must refuse too
    — quiet mode needs the fridge ON (the mirror of the timer gate above), camping lights/USB need
    the camping master ON (the rear USB is physically dead without it, #111), and the energy-mode
    selector is refused while the unit reports it locked. Unknown state always allows the write."""
    from calictl import control
    fridge_on, fridge_off = {"cooler": {"State": 1}}, {"cooler": {"State": 0}}
    for what in ("mode", "night_on", "night_off"):
        assert control.command_precondition("cooler", what, 2, fridge_off)        # fridge off -> refuse
        assert control.command_precondition("cooler", what, 2, fridge_on) is None
        assert control.command_precondition("cooler", what, 2, {}) is None        # unknown -> allow

    master_on, master_off = {"campingmode": {"State": 1}}, {"campingmode": {"State": 0}}
    for what in ("lights", "usb"):
        assert control.command_precondition("campingmode", what, "on", master_off)   # refuse
        assert control.command_precondition("campingmode", what, "on", master_on) is None
        assert control.command_precondition("campingmode", what, "on", {}) is None
    # the master switch itself is never gated here (the firmware refuses it while driving)
    assert control.command_precondition("campingmode", "master", "on", master_off) is None

    assert control.command_precondition("energy", "mode", "eco", {"energy": {"EnergyModeNotSelectable": 1}})
    assert control.command_precondition("energy", "mode", "eco", {"energy": {"EnergyModeNotSelectable": 0}}) is None
    assert control.command_precondition("energy", "mode", "eco", {}) is None


def test_command_precondition_roof_move_blocked_but_stop_never_is():
    """Roof open/close are refused under the same alert set the GUI greys (`ROOF_MOVE_BLOCK` in
    webui/app.js) and on a Position the unit reports as `error`. STOP is the safety action — it is
    never gated, under any alert. The unit enforces this itself (it withholds the motor); this is a
    courtesy refusal so the off-UI paths behave like the app."""
    from calictl import control

    def state(popup, pos=0, installed=1):
        return {"roof": {"Installed": installed, "InfoPopUp": popup, "Position": pos}}

    # InfoPopUp -> alert (semantics._ROOF_ALERT): 1 child_lock, 2/3/12 in_use, 4 error, 5 driving,
    # 7 emergency_locked, 9 not_stationary, 10 not_possible, 11 low_battery
    for popup in (1, 2, 3, 4, 5, 7, 9, 10, 11, 12):
        for what in ("open", "close"):
            assert control.command_precondition("roof", what, None, state(popup)), popup
        assert control.command_precondition("roof", "stop", None, state(popup)) is None, popup
    # 6 = sensor_error is deliberately NOT in the block set (the app still allows the move)
    assert control.command_precondition("roof", "open", None, state(6)) is None
    assert control.command_precondition("roof", "open", None, state(0)) is None      # no alert
    # Position 15 = error blocks a move even with no alert
    assert control.command_precondition("roof", "open", None, state(0, pos=15))
    assert control.command_precondition("roof", "stop", None, state(0, pos=15)) is None
    # unknown / not-installed state allows (can't prove it's blocked), per the doctrine
    assert control.command_precondition("roof", "open", None, {}) is None
    assert control.command_precondition("roof", "open", None, state(1, installed=0)) is None
