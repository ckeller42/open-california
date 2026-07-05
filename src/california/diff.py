from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from california.model import Capture

_WRITE_OPS = {"write-req", "write-cmd"}

@dataclass(frozen=True)
class WriteCandidate:
    handle: int
    uuid: str | None
    value: bytes
    confidence: float

def writes_of(cap: Capture) -> set[tuple[int, bytes]]:
    return {(e.handle, e.value) for e in cap.events if e.op in _WRITE_OPS}

def _uuid_for(cap: Capture, handle: int) -> str | None:
    for e in cap.events:
        if e.handle == handle and e.uuid:
            return e.uuid
    return None

def diff_pair(action_cap: Capture, baseline_cap: Capture) -> list[WriteCandidate]:
    unique = writes_of(action_cap) - writes_of(baseline_cap)
    return [WriteCandidate(h, _uuid_for(action_cap, h), v, 1.0)
            for (h, v) in sorted(unique)]

def isolate(captures: list[Capture]) -> dict[str, list[WriteCandidate]]:
    by_action: dict[str, list[Capture]] = defaultdict(list)
    for cap in captures:
        by_action[cap.action].append(cap)

    def baseline_for(action: str) -> set[tuple[int, bytes]]:
        # prefer the paired inverse (on<->off); else union of all other actions
        inverse = action.replace("-on", "-off") if action.endswith("-on") else action.replace("-off", "-on")
        if inverse in by_action and inverse != action:
            base: set[tuple[int, bytes]] = set()
            for c in by_action[inverse]:
                base |= writes_of(c)
            return base
        base = set()
        for other, caps in by_action.items():
            if other == action:
                continue
            for c in caps:
                base |= writes_of(c)
        return base

    result: dict[str, list[WriteCandidate]] = {}
    for action, caps in by_action.items():
        base = baseline_for(action)
        # count in how many repeats each unique write appears
        counts: dict[tuple[int, bytes], int] = defaultdict(int)
        for c in caps:
            for w in writes_of(c) - base:
                counts[w] += 1
        n = len(caps)
        cands = [WriteCandidate(h, _uuid_for(caps[0], h), v, cnt / n)
                 for (h, v), cnt in counts.items()]
        cands.sort(key=lambda c: (-c.confidence, c.handle))
        result[action] = cands
    return result
