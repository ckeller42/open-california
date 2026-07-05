from california.model import Capture, AttEvent
from california.diff import writes_of, diff_pair, isolate

def _cap(action, idx, writes):
    events = [AttEvent(frame=i, t=float(i), op="write-cmd", handle=h,
                       uuid=None, value=v, direction="sent")
              for i, (h, v) in enumerate(writes)]
    return Capture(action=action, index=idx, date="2026-07-05", events=events)

def test_writes_of_filters_writes():
    cap = _cap("light-on", 1, [(0x2a, b"\x01")])
    assert writes_of(cap) == {(0x2a, b"\x01")}

def test_diff_pair_isolates_unique_write():
    on = _cap("light-on", 1, [(0x2a, b"\x01"), (0x10, b"\x00")])   # 0x10 is noise/keepalive
    off = _cap("light-off", 1, [(0x2a, b"\x00"), (0x10, b"\x00")]) # shared noise cancels
    cands = diff_pair(on, off)
    assert [(c.handle, c.value) for c in cands] == [(0x2a, b"\x01")]

def test_isolate_confidence_from_repeats():
    caps = [
        _cap("light-on", 1, [(0x2a, b"\x01")]),
        _cap("light-on", 2, [(0x2a, b"\x01")]),
        _cap("light-off", 1, [(0x2a, b"\x00")]),
    ]
    result = isolate(caps)
    on = result["light-on"]
    assert on[0].handle == 0x2a and on[0].value == b"\x01"
    assert on[0].confidence == 1.0   # appears in both light-on repeats

def test_isolate_uuid_from_later_repeat():
    # Regression for Fix 1: uuid resolved from only first repeat
    # Two repeats of light-on; uuid appears only in second repeat
    cap1 = _cap("light-on", 1, [(0x2a, b"\x01")])  # no uuid
    cap2_events = [AttEvent(frame=0, t=0.0, op="write-cmd", handle=0x2a,
                            uuid="uuid-abc123", value=b"\x01", direction="sent")]
    cap2 = Capture(action="light-on", index=2, date="2026-07-05", events=cap2_events)
    cap_off = _cap("light-off", 1, [(0x2a, b"\x00")])

    result = isolate([cap1, cap2, cap_off])
    on = result["light-on"]
    assert len(on) == 1
    assert on[0].handle == 0x2a
    assert on[0].uuid == "uuid-abc123"  # Must find uuid from second repeat

def test_isolate_union_fallback_with_partial_confidence():
    # Regression for Fix 2: no -on/-off inverse uses union-of-others
    # Action with no -on/-off suffix uses union of all other actions as baseline
    cap_temp_1 = _cap("temp-4c", 1, [(0x20, b"\x42"), (0x30, b"\x99")])
    cap_temp_2 = _cap("temp-4c", 2, [(0x20, b"\x42")])  # Missing 0x30
    cap_other = _cap("other-action", 1, [(0x20, b"\x42")])  # Shared write

    result = isolate([cap_temp_1, cap_temp_2, cap_other])
    temp_cands = result["temp-4c"]

    # 0x20 is in baseline (from other-action), so excluded
    # 0x30 appears in only 1/2 repeats, not in baseline, confidence 0.5
    assert len(temp_cands) == 1
    assert temp_cands[0].handle == 0x30
    assert temp_cands[0].confidence == 0.5
