import json
import pytest
from tools import build_web


def test_humanize_strips_affixes_and_splits_camelcase():
    assert build_web.humanize("coolboxPage_coolingLevelSlider_coolingLevel_text") == "Cooling level"
    assert build_web.humanize("campingModePage_usbWidget_usbCharger_text") == "Usb charger"
    assert build_web.humanize(None) is None


def test_resolve_label_prefers_strings_then_labels_then_humanize():
    labels = {"coolboxPage_x_text": "Refrigerator box"}
    # local overlay (real VW text) wins when present
    assert build_web.resolve_label("coolboxPage_x_text", labels, {"coolboxPage_x_text": "Kühlbox"}) == "Kühlbox"
    # committed neutral label next
    assert build_web.resolve_label("coolboxPage_x_text", labels, None) == "Refrigerator box"
    # humanized fallback last
    assert build_web.resolve_label("aPage_y_fooBar_text", {}, None) == "Foo bar"


def test_build_covers_control_scope_and_carries_controls():
    data = build_web.build()
    assert set(build_web.CONTROL_SCREENS) <= set(data["screens"])
    cooler = data["screens"]["coolbox"]
    assert cooler["function"] == "cooler"
    power = next(w for w in cooler["widgets"] if w["id"] == "refrigeratorBoxWidget")
    assert power["controls"] == {"field": "State", "action": "U0"}
    assert isinstance(power["label"], str) and power["label"]


def test_no_raw_label_keys_leak_into_output():
    data = build_web.build()
    for scr in data["screens"].values():
        assert "_text" not in (scr["title"] or "")
        for w in scr["widgets"]:
            assert "_text" not in (w["label"] or "")


def test_humanize_strips_variant_suffix_not_just_bare_suffix():
    # A bare "_text_gc"/"_text_t6" variant tag must not survive as the label
    # (this used to yield "Gc"/"T6" — the suffix regex only matched "_text$").
    result = build_web.humanize(
        "lighting_interiorLighting_sectionTitle_interiorLighting_text_gc"
    )
    assert result == "Interior lighting"
    assert result not in ("Gc", "T6")


def test_humanize_prefers_meaningful_segment_over_generic_titlebar():
    # Real title_key from vehicle-info.yaml. The naive "last underscore
    # segment" rule picks "pageTitle" (then "titleBar") for several distinct
    # widgets, colliding them all on "Page title".
    result = build_web.humanize("howToAndInfo_titleBar_pageTitle_text")
    assert result == "How to and info"
    assert result != "Page title"


def test_labels_yaml_keys_exist_in_screens():
    """Every ui/labels.yaml key must be a real title_key or widget label_key
    in one of the in-scope ui/screens/*.yaml files — otherwise it silently
    never applies and resolve_label falls through to humanize()."""
    import yaml

    labels = yaml.safe_load(build_web.LABELS_FILE.read_text()) or {}
    real_keys = set()
    for name in build_web.CONTROL_SCREENS:
        spec = yaml.safe_load((build_web.SCREENS_DIR / (name + ".yaml")).read_text())
        if spec.get("title_key"):
            real_keys.add(spec["title_key"])
        for w in (spec.get("widgets") or []):
            if w.get("label_key"):
                real_keys.add(w["label_key"])

    dead = set(labels) - real_keys
    assert not dead, "ui/labels.yaml has keys that match no real title_key/label_key: %s" % dead
