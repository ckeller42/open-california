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

import asyncio

import pytest

from calictl import control, overrides
from calictl import protocol as P


def _funcs():
    f = P.load()
    overrides.apply(f)
    return f


# --- roofaircondition (char 2001) --------------------------------------------


def test_roofac_power_toggles_state_and_carries_rest():
    f = _funcs()
    cur = {"State": 0, "Mode": 2, "Fanspeed": 3, "Temperature": 21}
    on = control.decode_control(
        f["roofaircondition"], control.build(f, "roofaircondition", "power", "on", cur)
    )
    off = control.decode_control(
        f["roofaircondition"], control.build(f, "roofaircondition", "power", "off", cur)
    )
    assert (on["State"], off["State"]) == (1, 0)
    # untargeted fields carry current state (mirror _cooler)
    assert on["Mode"] == 2 and on["FanSpeed"] == 3 and on["Temperature"] == 21


def test_roofac_fanspeed_mode_temperature():
    f = _funcs()
    cur = {"State": 1, "Mode": 0, "Fanspeed": 0, "Temperature": 0}
    fan = control.decode_control(
        f["roofaircondition"], control.build(f, "roofaircondition", "fanspeed", "4", cur)
    )
    mode = control.decode_control(
        f["roofaircondition"], control.build(f, "roofaircondition", "mode", "3", cur)
    )
    temp = control.decode_control(
        f["roofaircondition"], control.build(f, "roofaircondition", "temperature", "200", cur)
    )
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
    air = control.decode_control(
        f["livingroomheater"], control.build(f, "livingroomheater", "air", "on", cur)
    )
    water = control.decode_control(
        f["livingroomheater"], control.build(f, "livingroomheater", "water", "on", cur)
    )
    assert air["StateAir"] == 1 and air["StateWater"] == 0  # only air targeted
    assert water["StateWater"] == 1 and water["StateAir"] == 0  # only water targeted
    # untargeted fields carry current state
    assert air["Mode"] == 5 and air["TemperatureAir"] == 40 and air["TemperatureWater"] == 1


def test_lrheater_temperature():
    f = _funcs()
    cur = {"StateAir": 1, "StateWater": 0, "TemperatureWater": 0, "Mode": 5, "TemperatureAir": 0}
    back = control.decode_control(
        f["livingroomheater"], control.build(f, "livingroomheater", "temperature", "255", cur)
    )
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
    assert (up["Up"], up["Down"]) == (1, 0)  # OPEN
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

    assert control._int_range("3", 1, 5, "cooler level") == 3  # str coerced
    assert control._int_range(0, 0, 23, "night hour") == 0  # inclusive bounds
    assert control._int_range(23, 0, 23, "night hour") == 23
    for bad in (0, 6, -1):  # out of 1..5
        with pytest.raises(ValueError):
            control._int_range(bad, 1, 5, "cooler level")
    with pytest.raises(ValueError):  # non-numeric
        control._int_range("nope", 1, 5, "cooler level")


def test_command_precondition_roof_reading_needs_raised_roof():
    from calictl import control

    # roof closed (0/14) -> ON write refused; open/middle/unknown -> allowed; OFF always allowed.
    assert control.command_precondition("lighting", "roof-reading", 8, {"roof": {"Position": 0}})
    assert control.command_precondition("lighting", "roof-reading", 8, {"roof": {"Position": 14}})
    assert control.command_precondition("lighting", "roof-reading", 8, {"roof": {"Position": 1}}) is None
    assert control.command_precondition("lighting", "roof-reading", 8, {"roof": {"Position": 2}}) is None
    assert control.command_precondition("lighting", "roof-reading", 8, {}) is None  # unknown -> allow
    assert (
        control.command_precondition("lighting", "roof-reading", 0, {"roof": {"Position": 0}}) is None
    )  # OFF ok
    assert (
        control.command_precondition("lighting", "kitchen", 8, {"roof": {"Position": 0}}) is None
    )  # other zone


def test_command_precondition_cooler_timer_needs_fridge_off():
    from calictl import control

    on = {"cooler": {"State": 1}}
    off = {"cooler": {"State": 0}}
    for what in ("timer_set", "timer_start"):
        assert control.command_precondition("cooler", what, 9, on)  # fridge on -> refuse
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
        assert control.command_precondition("cooler", what, 2, fridge_off)  # fridge off -> refuse
        assert control.command_precondition("cooler", what, 2, fridge_on) is None
        # unknown State -> allow the quiet gate; but night_* then hit R4 (no schedule to carry)
        exp = control.REASON_COOLER_STATE_UNKNOWN if what != "mode" else None
        assert control.command_precondition("cooler", what, 2, {}) == exp

    master_on, master_off = {"campingmode": {"State": 1}}, {"campingmode": {"State": 0}}
    for what in ("lights", "usb"):
        assert control.command_precondition("campingmode", what, "on", master_off)  # refuse
        assert control.command_precondition("campingmode", what, "on", master_on) is None
        assert control.command_precondition("campingmode", what, "on", {}) is None
    # the master switch itself is never gated here (the firmware refuses it while driving)
    assert control.command_precondition("campingmode", "master", "on", master_off) is None

    assert control.command_precondition("energy", "mode", "eco", {"energy": {"EnergyModeNotSelectable": 1}})
    assert (
        control.command_precondition("energy", "mode", "eco", {"energy": {"EnergyModeNotSelectable": 0}})
        is None
    )
    assert control.command_precondition("energy", "mode", "eco", {}) is None


def test_command_precondition_roof_move_blocked_but_stop_never_is():
    """Roof open/close are refused under the same alert set the GUI greys (`ROOF_MOVE_BLOCK` in
    webui/app.js) and on a Position the unit reports as `error`. STOP is the safety action — it is
    never gated, under any alert. The unit enforces this itself (it withholds the motor); this is a
    courtesy refusal so the off-UI paths behave like the app."""
    from calictl import control

    def state(popup, pos=0, installed=1):
        return {"roof": {"Installed": installed, "InfoPopUp": popup, "Position": pos}}

    # InfoPopUp -> alert (semantics._ROOF_ALERT): 1 child_lock, 4 error, 5 driving,
    # 7 emergency_locked, 9 not_stationary, 10 not_possible, 11 low_battery
    for popup in (1, 4, 5, 7, 9, 10, 11):
        for what in ("open", "close"):
            assert control.command_precondition("roof", what, None, state(popup)), popup
        assert control.command_precondition("roof", "stop", None, state(popup)) is None, popup
    # 6 = sensor_error is deliberately NOT in the block set (the app still allows the move)
    assert control.command_precondition("roof", "open", None, state(6)) is None
    # CAPTURE 2026-10-10 (real app, real unit): 2 = pre-open checklist prompt (press -> checklist ->
    # press again), 12 moving, 8 end of travel, 3 stopped mid-travel — none of them blocks anything
    for popup in (2, 3, 8, 12):
        for what in ("open", "close", "stop"):
            assert control.command_precondition("roof", what, None, state(popup, pos=2)) is None, popup
    assert control.command_precondition("roof", "open", None, state(0)) is None  # no alert
    # Position 15 = error blocks a move even with no alert
    assert control.command_precondition("roof", "open", None, state(0, pos=15))
    assert control.command_precondition("roof", "stop", None, state(0, pos=15)) is None
    # unknown / not-installed state allows (can't prove it's blocked), per the doctrine
    assert control.command_precondition("roof", "open", None, {}) is None
    assert control.command_precondition("roof", "open", None, state(1, installed=0)) is None


def test_favourite_gate_refuses_only_a_known_empty_slot():
    """
    .. test:: Activating a favourite is refused only when the unit reported it empty
       :id: T_LIGHT_FAVOURITE_GATE
       :links: R_LIGHT_FAVOURITE
    """
    known = {"lighting": {"Mode": 4, "FavouritesStored": 0b001}}
    assert control.command_precondition("lighting", "profile", 1, known) is None
    assert control.command_precondition("lighting", "profile", 2, known) == (
        "this favourite is empty on the unit — save it first"
    )
    assert (
        control.command_precondition("lighting", "profile", 2, {"lighting": {"Mode": 4}}) is None
    )  # unknown
    assert control.command_precondition("lighting", "profile", 12, known) is None  # not a favourite


def test_wakeup_gate_refuses_enabling_with_no_area():
    no_area = {"lighting": {"WakeupTimestamp": 25200, "WakeupLightValue": 0x1000}}  # 07:00, no areas, off
    assert control.command_precondition("lighting", "wakeup", "on", no_area) == (
        "the wake-up light needs at least one vehicle area"
    )
    assert control.command_precondition("lighting", "wakeup", "07:30", no_area) is None  # stays off
    assert control.command_precondition("lighting", "wakeup", "07:30 2 on", no_area) is None
    assert control.command_precondition("lighting", "wakeup", "bogus", {}) is None  # the builder reports it


def test_door_contact_is_not_gated_on_car_variant():
    for variant in (0, 1, 2, 4, None):
        assert (
            control.command_precondition(
                "lighting", "door_contact", "on", {"vehicle": {"CarVariant": variant}}
            )
            is None
        )


def test_postcheck_handles_the_new_lighting_values():
    # _lighting_check used to int() every value: "07:00 on" / "1 red" / "on" raised inside
    # serve._confirm_lighting and failed the command after the frame was already written.
    from calictl.postcheck import set_check

    assert set_check("lighting", "wakeup", "07:00 on", {}, {})[1:] == (None, None)  # no applied-check
    assert set_check("lighting", "save_profile", "1 red", {}, {})[1:] == (None, None)
    assert set_check("lighting", "door_contact", "on", {"door_contact": True}, {})[1:] == (True, True)
    assert set_check("lighting", "door_contact", "off", {"door_contact": True}, {})[1:] == (True, False)


def _cli_run(argv, state_hex):
    """Parse a real argv with the CLI parser and run cmd_set against a fake device; returns the
    (preface, frame) of every actuate call."""
    from calictl import cli

    funcs = P.load()
    overrides.apply(funcs)
    calls = []

    class FakeDev:
        async def read(self, func):
            return bytes.fromhex(state_hex)

        async def actuate(self, func, frame, *, verify=True, follow=None, preface=None):
            calls.append((preface.hex() if preface else None, frame.hex()))
            return None

    args = cli.build_parser().parse_args(argv)
    asyncio.run(cli.cmd_set(funcs, FakeDev(), args))
    return calls


def test_cli_save_profile_with_colour_writes_the_set_color_preface_first():
    """The CLI sends the SET_COLOR preface with the save in ONE actuate (one link, one arm)."""
    calls = _cli_run(["set", "lighting", "save_profile", "1", "red"], "091000000000000000000005d00ddddd")
    assert calls == [("010600000000000900000005e00eeeee", "010400000000000000000005e00eeeee")]


def test_cli_parser_takes_multi_word_values():
    from calictl import cli

    p = cli.build_parser()
    assert p.parse_args(["set", "lighting", "wakeup", "07:00", "1,2", "5", "10", "on"]).value == [
        "07:00",
        "1,2",
        "5",
        "10",
        "on",
    ]
    assert p.parse_args(["set", "roof", "open"]).value == []


def test_cli_wakeup_time_edit_without_a_known_config_is_refused(capsys):
    """Ruling R5: the CLI has no latch, so a time edit with no on/off is refused (never a silent
    disarm); an explicit switch token still builds the app's recorded time-picker frame."""
    assert (
        _cli_run(["set", "lighting", "wakeup", "07:00"], "0c1000000000000000000000d00ddddd") == []
    )  # nothing written
    assert "not known yet" in capsys.readouterr().err


def test_cli_wakeup_time_with_explicit_off_is_the_app_time_picker_frame(monkeypatch):
    import datetime

    monkeypatch.setattr(control, "local_now", lambda: datetime.datetime(2026, 10, 5, 17, 25, 6))
    calls = _cli_run(["set", "lighting", "wakeup", "07:00", "off"], "0c1000000000000000000000d00ddddd")
    assert calls == [(None, "0e146ac49c701100eeeeeeeeeeeeeeee")]


def test_wakeup_edit_with_no_unit_reported_config_is_refused():
    """Ruling R5: never silently disarm. Edits (no on/off) need a unit-reported config; an explicit
    on/off carries its own enabled state and passes."""
    for v in ("07:00", "07:00 1 5 10"):  # (no-time edits: the builder refuses "not known yet")
        assert "not known yet" in control.command_precondition("lighting", "wakeup", v, {"lighting": {}})
    assert control.command_precondition("lighting", "wakeup", "07:00 off", {"lighting": {}}) is None
    known = {"lighting": {"WakeupTimestamp": 6 * 3600, "WakeupLightValue": 0x1301}}
    assert control.command_precondition("lighting", "wakeup", "07:00", known) is None


def test_wakeup_edit_carries_the_unit_reported_enabled_state():
    """Controller ruling (Task 6 fix round 1): the decompile ``m0(ef.m config, ...)`` receives the
    whole current config, so an edit (time/areas/brightness/ramp) keeps the enabled state AS LAST
    REPORTED BY THE UNIT; only on/off changes it. Unknown config + time only = the app's recorded
    frame (enabled=0, nothing reported to carry).
    """
    last = {"WakeupTimestamp": 6 * 3600, "WakeupLightValue": 0x1301}  # latched: ON, areas 1+2
    c = control.wakeup_request("07:30", last)
    assert c["enabled"] is True and c["areas"] == [1, 2]
    assert control.wakeup_request("off", last)["enabled"] is False
    off = {"WakeupTimestamp": 6 * 3600, "WakeupLightValue": 0x1300}
    assert control.wakeup_request("07:30", off)["enabled"] is False
    assert control.wakeup_request("on", off)["enabled"] is True
    assert control.wakeup_request("07:30", None)["enabled"] is False
    with pytest.raises(ValueError, match="not known yet"):
        control.wakeup_request("1,2", None)  # areas edit with no unit-reported config


def test_build_input_errors_are_command_errors():
    funcs = P.load()
    overrides.apply(funcs)
    for what, value in (("color", "red"), ("wakeup", "25:00"), ("wakeup", "bogus"), ("kitchen", "x")):
        with pytest.raises(control.CommandError):
            control.build(funcs, "lighting", what, value, {})


# --- rulings R3 / R4 (ESP control path, 2026-10-06): unknown -> refuse, never default -----------


# one parametrize: docs/conf.py mocks pytest, and a second stacked decorator would turn the test into
# a Mock that autodoc drops (its need would vanish from needs.json)
@pytest.mark.parametrize(
    "fn,what,value",
    [
        (fn, what, value)
        for fn, what in (
            ("cooler", "power"),
            ("campingmode", "master"),
            ("campingmode", "lights"),
            ("campingmode", "usb"),
            ("airheater", "power"),
            ("lighting", "power"),
            ("lighting", "door_contact"),
        )
        for value in (None, "", "x", "maybe", "3", " ")
    ],
)
def test_a_value_that_is_not_on_or_off_is_refused_not_off(fn, what, value):
    """Ruling R3: `set cooler power null` must refuse, never switch the fridge off.

    .. test:: An on/off command with any other value is refused
       :id: T_ONOFF_REFUSED
       :links: R_ONOFF_STRICT
    """
    f = _funcs()
    assert control.command_precondition(fn, what, value, {}) == control.REASON_NOT_ONOFF
    with pytest.raises(control.CommandError, match="on or off"):
        control.build(f, fn, what, value, {})


@pytest.mark.parametrize("value", ["on", "OFF", " true ", "false", 1, 0, "1", "0"])
def test_on_off_spellings_still_pass(value):
    f = _funcs()
    assert control.command_precondition("cooler", "power", value, {}) is None
    assert control.build(f, "cooler", "power", value, {}) is not None


def test_night_hours_with_no_cooler_state_are_refused():
    """Ruling R4: a night_on/night_off edit carries the current schedule; with none known it would
    send the default-filled frame whose literal 0 clobbered a set schedule (2026-08-26, #99).

    .. test:: Cooler night hours need a known cooler state
       :id: T_COOLER_NIGHT_NEEDS_STATE
       :links: R_ONOFF_STRICT
    """
    f = _funcs()
    for what in ("night_on", "night_off"):
        assert control.command_precondition("cooler", what, 22, {}) == control.REASON_COOLER_STATE_UNKNOWN
        assert (
            control.command_precondition("cooler", what, 22, {"cooler": {}})
            == control.REASON_COOLER_STATE_UNKNOWN
        )
        with pytest.raises(control.CommandError, match="not known"):
            control.build(f, "cooler", what, 22, {})
    known = {"cooler": {"State": 1, "NightTimerHourOn": 22, "NightTimerHourOff": 6}}
    assert control.command_precondition("cooler", "night_on", 22, known) is None
