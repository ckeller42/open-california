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

from calictl import protocol as P, overrides, control


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
