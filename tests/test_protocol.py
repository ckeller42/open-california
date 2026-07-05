from california.model import Capture, AttEvent
from california.protocol import save_map, load_map, replay_safe

def _cap(action, writes):
    ev = [AttEvent(frame=i, t=0.0, op="write-cmd", handle=h, uuid=None, value=v, direction="sent")
          for i, (h, v) in enumerate(writes)]
    return Capture(action=action, index=0, date="2026-07-05", events=ev)

def test_map_roundtrip(tmp_path):
    p = tmp_path / "protocol.yaml"
    data = {"actions": {"light-on": {"handle": 42, "uuid": "fff1",
            "value": "01ff", "confidence": 1.0, "confirmed": True}}}
    save_map(str(p), data)
    assert load_map(str(p)) == data

def test_replay_safe_true_when_stable():
    reps = [_cap("light-on", [(0x2a, b"\x01")]), _cap("light-on", [(0x2a, b"\x01")])]
    assert replay_safe(reps) is True

def test_replay_safe_false_when_payload_varies():
    # same action, differing payloads => nonce/counter/crypto present
    reps = [_cap("light-on", [(0x2a, b"\x01\xAA")]), _cap("light-on", [(0x2a, b"\x01\xBB")])]
    assert replay_safe(reps) is False

def test_load_map_missing_file(tmp_path):
    # Regression: load_map should return {"actions": {}} for nonexistent path
    p = str(tmp_path / "nope.yaml")
    assert load_map(p) == {"actions": {}}
