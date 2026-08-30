"""Lighting control, verified against the real app's HCI-captured frames (2026-07-08).

The capture (idevicebtlogger on the owner's phone driving the van) proved the two things our
earlier static guesses got wrong: unchanged zones are ``14`` (0xe), not 0, and the profile
switch is Mode 16. These tests pin ``control._lighting`` to the exact bytes the app sends, so
the crack can never silently regress.
"""
from calictl import control, overrides, protocol


def _f():
    f = protocol.load(); overrides.apply(f); return f


def test_set_brightness_one_zone_matches_app_frame():
    # app: Kitchen (BrightnessLSeven) -> 5 under active profile 9
    f = _f()
    frame = control.build(f, "lighting", "kitchen", 5, {"ProfileNumber": 9})
    assert frame.hex() == "0904000000000000eeeeeee5eeeeeeee"


def test_inferred_zones_l5_l6_target_the_right_fields():
    # Full lamp map DEVICE-confirmed 2026-08-30 (single-light isolation + owner-watched writes):
    # kitchen-ambient=L5, kitchen-cabinet=L6, roof-reading=L9 (was mislabelled L6), entrance=L12.
    # The friendly-key -> field wiring must be exact; every other zone left at the 14 sentinel.
    f = _f()
    for what, field in (("kitchen-ambient", "BrightnessLFive"), ("kitchen-cabinet", "BrightnessLSix"),
                        ("roof-reading", "BrightnessLNine"), ("entrance", "BrightnessLOneTwo")):
        d = control.decode_control(f["lighting"], control.build(f, "lighting", what, 6, {"ProfileNumber": 9}))
        assert d[field] == 6
        others = [v for k, v in d.items() if k.startswith("Brightness") and k != field]
        assert set(others) == {14}, "%s must leave every other zone unchanged" % what


def test_gui_lamp_keys_all_resolve_to_control_zones():
    # Guard the GUI<->control contract: every `what` the Lighting screen can send must be a real
    # LIGHT_ZONES key, or the slider would 400. (app.js LIGHT_LAMPS is the source; mirrored here.)
    gui_keys = {"reading-1", "reading-2", "reading-3", "kitchen-ambient", "kitchen-cabinet",
                "kitchen", "roof-ambient", "roof-reading", "outside-rear", "entrance"}
    assert gui_keys <= set(control.LIGHT_ZONES), gui_keys - set(control.LIGHT_ZONES)


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


def test_set_color_is_mode_6_palette_index():
    # SET_COLOR (dg/n Mode 6): recolour the active profile — LightValue = palette index (1-10),
    # ProfileNumber echoes active, all zones unchanged (14). Byte layout from the decompile.
    f = _f()
    frame = control.build(f, "lighting", "color", "red", {"ProfileNumber": 9})  # red = index 9
    assert frame.hex() == "0906000000000009eeeeeeeeeeeeeeee"
    d = control.decode_control(f["lighting"], frame)
    assert d["Mode"] == 6 and d["LightValue"] == 9 and d["ProfileNumber"] == 9
    # unknown colour is a clean error, not a bad frame
    try:
        control.build(f, "lighting", "color", "chartreuse", {"ProfileNumber": 9})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_power_off_selects_lights_off_profile():
    f = _f()
    # power off == SET_PROFILE LIGHTS_OFF (0), the app's master toggle — not per-zone zeroing
    d = control.decode_control(f["lighting"], control.build(f, "lighting", "power", "off", {"ProfileNumber": 9}))
    assert d["Mode"] == control.LIGHT_MODE_SET_PROFILE and d["ProfileNumber"] == 0
    assert all(v == control.LIGHT_UNCHANGED for k, v in d.items() if k.startswith("Brightness"))


def test_any_on_ignores_phantom_zones():
    from calictl import semantics
    # L9-L16 read constant not-installed defaults (13) — must NOT count as "on"
    d = {"BrightnessL" + s: (13 if n >= 9 else 0) for s, n in semantics._LZONES.items()}
    assert semantics.interpret("lighting", d)["any_on"] is False
    d["BrightnessLSeven"] = 5   # a real lamp (L7 = Kitchen) lit
    assert semantics.interpret("lighting", d)["any_on"] is True
