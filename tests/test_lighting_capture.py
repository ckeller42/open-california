"""Lighting control, verified against the real app's HCI-captured frames (2026-07-08).

The capture (idevicebtlogger on the owner's phone driving the van) proved the two things our
earlier static guesses got wrong: unchanged zones are ``14`` (0xe), not 0, and the profile
switch is Mode 16. These tests pin ``control._lighting`` to the exact bytes the app sends, so
the crack can never silently regress.
"""
from calictl import protocol, overrides, control


def _f():
    f = protocol.load(); overrides.apply(f); return f


def test_set_brightness_one_zone_matches_app_frame():
    # app: Kitchen (BrightnessLSeven) -> 5 under active profile 9
    f = _f()
    frame = control.build(f, "lighting", "kitchen", 5, {"ProfileNumber": 9})
    assert frame.hex() == "0904000000000000eeeeeee5eeeeeeee"


def test_raw_field_name_also_works():
    f = _f()
    frame = control.build(f, "lighting", "BrightnessLSeven", 5, {"ProfileNumber": 9})
    assert frame.hex() == "0904000000000000eeeeeee5eeeeeeee"


def test_unchanged_zones_are_14_not_zero():
    f = _f()
    frame = control.build(f, "lighting", "kitchen", 5, {"ProfileNumber": 9})
    d = control.decode_control(f["lighting"], frame)
    others = [v for k, v in d.items() if k.startswith("Brightness") and k != "BrightnessLSeven"]
    assert set(others) == {14}                       # every other zone left unchanged
    assert d["BrightnessLSeven"] == 5


def test_set_profile_is_mode_16():
    # app profile switch: Mode 16 + target ProfileNumber, all zones unchanged
    f = _f()
    frame = control.build(f, "lighting", "profile", 0, {"ProfileNumber": 9})
    assert frame.hex() == "0010000000000000eeeeeeeeeeeeeeee"
    assert control.decode_control(f["lighting"], frame)["Mode"] == 16


def test_power_off_zeroes_all_zones():
    f = _f()
    frame = control.build(f, "lighting", "power", "off", {"ProfileNumber": 9})
    d = control.decode_control(f["lighting"], frame)
    assert all(v == 0 for k, v in d.items() if k.startswith("Brightness"))
