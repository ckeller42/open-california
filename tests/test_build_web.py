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
