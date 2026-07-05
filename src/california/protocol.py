from __future__ import annotations
from collections import defaultdict
import yaml
from california.model import Capture
from california.diff import writes_of, WriteCandidate, _WRITE_OPS

def load_map(path: str) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {"actions": {}}
    except FileNotFoundError:
        return {"actions": {}}

def save_map(path: str, data: dict) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=True)

def merge_candidates(existing: dict, isolated: dict[str, list[WriteCandidate]]) -> dict:
    out = {"actions": dict(existing.get("actions", {}))}
    for action, cands in isolated.items():
        if out["actions"].get(action, {}).get("confirmed"):
            continue  # never overwrite a human-confirmed mapping
        if not cands:
            continue
        top = cands[0]
        out["actions"][action] = {
            "handle": top.handle,
            "uuid": top.uuid,
            "value": top.value.hex(),
            "confidence": top.confidence,
            "confirmed": False,
        }
    return out

def replay_safe(repeats: list[Capture]) -> bool:
    if len(repeats) < 2:
        return False  # cannot prove stability from a single capture

    def profile(cap: Capture) -> dict[int, set[bytes]]:
        d: dict[int, set[bytes]] = defaultdict(set)
        for e in cap.events:
            if e.op in _WRITE_OPS:
                d[e.handle].add(e.value)
        return d

    profiles = [profile(c) for c in repeats]
    common = set(profiles[0])
    for p in profiles[1:]:
        common &= set(p)
    if not common:
        return False
    for h in common:
        base = profiles[0][h]
        if any(p[h] != base for p in profiles[1:]):
            return False
    return True
