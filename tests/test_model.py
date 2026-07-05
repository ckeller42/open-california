import pytest
from california.model import AttEvent, parse_label

def test_parse_label_ok():
    assert parse_label("2026-07-05_light-on_001.pklg") == ("2026-07-05", "light-on", 1)

def test_parse_label_rejects_bad_action():
    with pytest.raises(ValueError):
        parse_label("2026-07-05_Light_On_001.pklg")  # uppercase/underscore in action

def test_att_event_value_is_bytes():
    e = AttEvent(frame=1, t=0.0, op="write-cmd", handle=0x2a, uuid="fff1", value=b"\x01", direction="sent")
    assert e.value == b"\x01" and e.handle == 42
