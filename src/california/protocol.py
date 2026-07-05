from __future__ import annotations
import yaml
from california.model import Capture
from california.diff import writes_of, WriteCandidate

def load_map(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {"actions": {}}

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
    common = writes_of(repeats[0])
    for cap in repeats[1:]:
        common &= writes_of(cap)
    return len(common) > 0
