"""Lighting commands the app has and calictl now builds byte-exact: wake-up light (Mode 20),
door contact (Mode 16 / PN 8), favourite activate / save (+ colour). Frames come from the app
recordings (tests/vectors/app/) or, where the app cannot be recorded, from the decompile.

.. test:: Wake-up frame reproduces the app recording and the decompile packing
   :id: T_LIGHT_WAKEUP
   :links: R_LIGHT_WAKEUP
"""

import calendar
import datetime

import pytest

from calictl import control, overrides, protocol, semantics

DT = datetime.datetime
# 2026-10-05 17:25:06 — the recorded wake-up write's event time (lighting-wakeup.jsonl), as UTC.
REC_NOW = DT(2026, 10, 5, 17, 25, 6)
# the 1502 notify the app had seen before the wake-up write (lighting-wakeup.jsonl)
REC_STATE_HEX = "0c1000000000000000000000d00ddddd"


def _funcs():
    f = protocol.load()
    overrides.apply(f)
    return f


def _st(hexframe):
    return protocol.decode(_funcs()["lighting"], bytes.fromhex(hexframe))


@pytest.fixture
def at_rec_time(monkeypatch):
    monkeypatch.setattr(control, "local_now", lambda: REC_NOW)


def test_wakeup_time_reproduces_the_recorded_frame(at_rec_time):
    frame = control.build(_funcs(), "lighting", "wakeup", "07:00", _st(REC_STATE_HEX))
    assert frame.hex() == "0e146ac49c701100eeeeeeeeeeeeeeee"


def test_wakeup_switch_reproduces_the_hand_observed_frame(at_rec_time):
    # app-rec2 report: with no wake-up config seen (readback 00:00) the app's switch wrote
    # 0e146ac43a001101... = next 00:00 (2026-10-06), mode 1 (ON, no ramp).
    frame = control.build(_funcs(), "lighting", "wakeup", "00:00 on", _st(REC_STATE_HEX))
    assert frame.hex() == "0e146ac43a001101eeeeeeeeeeeeeeee"


def test_wakeup_light_value_matches_the_decompile_example():
    # lighting-energy-water-sat-roof.md action 10: ON + 10 min ramp, brightness 5, warm white, area 1
    c = {"colour": 1, "areas": [1], "brightness": 5, "ramp": 10, "enabled": True}
    assert control._wakeup_light_value(c) == 0x1153
    c = {"colour": 1, "areas": [1, 2, 3, 4], "brightness": 10, "ramp": 30, "enabled": False}
    assert control._wakeup_light_value(c) == 0x1FA6


def test_next_wakeup_rolls_past_midnight_dst_and_year_end():
    def ts(*a):
        return calendar.timegm(DT(*a).timetuple())

    assert control.next_wakeup_epoch(7, 0, DT(2026, 10, 5, 6, 59)) == ts(2026, 10, 5, 7, 0)  # later today
    assert control.next_wakeup_epoch(7, 0, DT(2026, 10, 5, 7, 0)) == ts(2026, 10, 6, 7, 0)  # exactly now
    assert control.next_wakeup_epoch(0, 0, DT(2026, 10, 5, 23, 59, 30)) == ts(2026, 10, 6, 0, 0)
    # spring-forward night (Europe: 2026-03-29 02:00 -> 03:00): the app's LocalDateTime is naive,
    # so 02:30 stays 02:30 wall-clock, packed as UTC
    assert control.next_wakeup_epoch(2, 30, DT(2026, 3, 28, 23, 30)) == ts(2026, 3, 29, 2, 30)
    assert control.next_wakeup_epoch(7, 0, DT(2026, 12, 31, 23, 0)) == ts(2027, 1, 1, 7, 0)


def test_wakeup_keeps_the_latched_config_and_changes_only_what_is_given(at_rec_time):
    # a Mode-20 frame (the mock's echo) = the latched config: 06:30, areas 1+3, brightness 4, 20 min, ON
    lv = control._wakeup_light_value(
        {"colour": 1, "areas": [1, 3], "brightness": 4, "ramp": 20, "enabled": True}
    )
    last = {"Mode": 20, "ProfileNumber": 14, "Timestamp": 6 * 3600 + 30 * 60, "LightValue": lv}
    d = control.decode_control(
        _funcs()["lighting"], control.build(_funcs(), "lighting", "wakeup", "off", last)
    )
    assert d["LightValue"] == lv & ~1  # only the enabled bit moved
    assert d["Timestamp"] == calendar.timegm(DT(2026, 10, 6, 6, 30).timetuple())  # 06:30 already passed today
    d = control.decode_control(
        _funcs()["lighting"], control.build(_funcs(), "lighting", "wakeup", "07:15 2,4 7 30 on", last)
    )
    assert semantics.wakeup_config(
        {"WakeupTimestamp": d["Timestamp"], "WakeupLightValue": d["LightValue"]}
    ) == {
        "time": "07:15",
        "hour": 7,
        "minute": 15,
        "enabled": True,
        "ramp": 30,
        "brightness": 7,
        "areas": [2, 4],
        "colour": 1,
    }


@pytest.mark.parametrize(
    "value, msg",
    [
        ("on", "time not known"),  # no latched config + no time: would arm a midnight wake-up
        ("", "needs"),
        ("25:00", "out of range"),
        ("07:00 5", "wake-up area"),
        ("07:00 1 11", "wake-up brightness"),
        ("07:00 1 5 15", "ramp"),
        ("07:00 1 5 10 9", "takes"),
    ],
)
def test_wakeup_rejects_bad_values(at_rec_time, value, msg):
    with pytest.raises(ValueError, match=msg):
        control.build(_funcs(), "lighting", "wakeup", value, _st(REC_STATE_HEX))


def test_lighting_config_latches_and_carries():
    wake = {"Mode": 20, "ProfileNumber": 14, "Timestamp": 25200, "LightValue": 0x1101}
    cfg = semantics.lighting_config(None, wake)
    assert cfg == {"WakeupTimestamp": 25200, "WakeupLightValue": 0x1101}
    later = semantics.lighting_config(cfg, {"Mode": 4, "ProfileNumber": 9, "LightValue": 0})
    assert later == cfg  # a brightness ramp frame does not wipe the wake-up config
    door = semantics.lighting_config(later, {"Mode": 16, "ProfileNumber": 8, "LightValue": 1})
    assert door["DoorContact"] == 1 and door["WakeupLightValue"] == 0x1101
    assert "FavouritesStored" not in semantics.lighting_config(door, {"Mode": 4, "ProfileNumber": 1})
    favs = semantics.lighting_config(door, {"Mode": 12, "ProfileNumber": 12, "LightValue": 0b0000101})
    assert favs["FavouritesStored"] == 0b101
    assert semantics.lighting_config(favs, {"Mode": 4, "ProfileNumber": 2})["FavouritesStored"] == 0b111


@pytest.mark.parametrize(
    "colour, areas, brightness, ramp, enabled",
    [
        (1, [1], 0, 0, False),
        (2, [1, 2, 3, 4], 10, 30, True),
        (5, [2, 4], 7, 20, False),
        (15, [3], 1, 10, True),
        (0, [4], 5, 0, True),
    ],
)
def test_wakeup_light_value_round_trips_through_semantics(colour, areas, brightness, ramp, enabled):
    c = {"colour": colour, "areas": areas, "brightness": brightness, "ramp": ramp, "enabled": enabled}
    lv = control._wakeup_light_value(c)
    got = semantics.wakeup_config({"WakeupTimestamp": 7 * 3600 + 5 * 60, "WakeupLightValue": lv})
    assert {k: got[k] for k in c} == c and got["time"] == "07:05"


def test_wakeup_keeps_a_non_default_colour(at_rec_time):
    lv = control._wakeup_light_value(
        {"colour": 5, "areas": [2], "brightness": 3, "ramp": 10, "enabled": True}
    )
    last = {"Mode": 20, "ProfileNumber": 14, "Timestamp": 3600, "LightValue": lv}
    d = control.decode_control(
        _funcs()["lighting"], control.build(_funcs(), "lighting", "wakeup", "off", last)
    )
    assert d["LightValue"] >> 12 == 5


# The notify the app had seen right before its save (lighting-profile.jsonl): Cooking (L7) = 5.
SAVE_STATE_HEX = "091000000000000000000005d00ddddd"
SAVE_FRAME_HEX = "010400000000000000000005e00eeeee"  # recorded: press-and-hold tile A


def test_door_contact_on_off_is_set_profile_8_with_light_value():
    """.. test:: Door contact builds dg/h.n4 (Mode 16, PN 8, LightValue 1/0)
    :id: T_LIGHT_DOOR_CONTACT
    :links: R_LIGHT_DOOR_CONTACT
    """
    f = _funcs()
    assert control.build(f, "lighting", "door_contact", "on", {}).hex() == "0810000000000001eeeeeeeeeeeeeeee"
    assert control.build(f, "lighting", "door_contact", "off", {}).hex() == "0810000000000000eeeeeeeeeeeeeeee"
    with pytest.raises(ValueError, match="on or off"):
        control.build(f, "lighting", "door_contact", "maybe", {})


def test_profile_activate_is_one_set_profile_frame():
    """dg/h.u0: v(16, PENDING) then w(N, DIRECT) — one frame, everything else at the reset values.

    .. test:: Favourite activate is one SET_PROFILE frame
       :id: T_LIGHT_FAVOURITE_ACTIVATE
       :links: R_LIGHT_FAVOURITE
    """
    f = _funcs()
    assert control.build(f, "lighting", "profile", 1, {}).hex() == "0110000000000000eeeeeeeeeeeeeeee"
    assert control.build(f, "lighting", "profile", "7", {}).hex() == "0710000000000000eeeeeeeeeeeeeeee"
    with pytest.raises(ValueError, match="door_contact"):
        control.build(f, "lighting", "profile", 8, {})  # PN 8 = the door-contact frame
    with pytest.raises(ValueError):
        control.build(f, "lighting", "profile", 14, {})


def test_save_profile_stays_byte_exact_and_colour_adds_a_set_color_preface():
    """.. test:: save_profile N [colour] = optional SET_COLOR then the recorded SET_BRIGHTNESS
    :id: T_LIGHT_FAVOURITE_SAVE
    :links: R_LIGHT_FAVOURITE
    """
    f = _funcs()
    st = _st(SAVE_STATE_HEX)
    assert control.build(f, "lighting", "save_profile", 1, st).hex() == SAVE_FRAME_HEX
    assert control.build(f, "lighting", "save_profile", "1 red", st).hex() == SAVE_FRAME_HEX
    assert control.preface_for(f, "lighting", "save_profile", 1, st) is None
    assert (
        control.preface_for(f, "lighting", "save_profile", "1 red", st).hex()
        == "010600000000000900000005e00eeeee"
    )
    assert control.preface_for(f, "cooler", "power", "on", {}) is None
    with pytest.raises(ValueError, match="unknown light colour"):
        control.build(f, "lighting", "save_profile", "1 chartreuse", st)
    with pytest.raises(ValueError, match="N \\[colour\\]"):
        control.build(f, "lighting", "save_profile", "1 red extra", st)


def test_retired_color_raises_with_a_pointer():
    with pytest.raises(ValueError, match="retired.*save_profile"):
        control.build(_funcs(), "lighting", "color", "red", {"ProfileNumber": 9})


def test_unknown_lighting_target_raises_not_none():
    with pytest.raises(ValueError, match="unknown lighting control"):
        control.build(_funcs(), "lighting", "disco", 5, {})


def test_lighting_state_surfaces_wakeup_door_contact_and_favourites():
    """.. test:: The lighting state shows the latched wake-up, door contact and stored favourites
    :id: T_LIGHT_CONFIG_STATE
    :links: R_LIGHT_CONFIG_LATCH
    """
    plain = semantics.lighting(_st(REC_STATE_HEX))
    assert plain["wakeup"] is None and plain["door_contact"] is None and plain["favourites_stored"] is None
    wake = semantics.lighting(_st("0e146ac49c701100eeeeeeeeeeeeeeee"))  # a Mode-20 frame (mock echo)
    assert wake["wakeup"]["time"] == "07:00" and wake["wakeup"]["enabled"] is False
    assert wake["wakeup"]["areas"] == [1]
    carried = semantics.lighting({**_st(REC_STATE_HEX), "DoorContact": 1, "FavouritesStored": 0b11})
    assert carried["door_contact"] is True and carried["favourites_stored"] == [1, 2]
