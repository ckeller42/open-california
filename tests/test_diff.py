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
