"""End-to-end chain test: diff.isolate -> protocol.merge_candidates ->
protocol.save_map/load_map -> codegen.generate.

This closes the untested cross-module seam: WriteCandidate -> YAML schema
-> codegen.
"""
from __future__ import annotations
import ast
from california.model import AttEvent, Capture
from california.diff import isolate
from california.protocol import merge_candidates, save_map, load_map
from california.codegen import generate


def _cap(action, index, writes):
    events = [
        AttEvent(frame=i, t=0.0, op="write-cmd", handle=h, uuid=uuid, value=v, direction="sent")
        for i, (h, uuid, v) in enumerate(writes)
    ]
    return Capture(action=action, index=index, date="2026-07-05", events=events)


def test_diff_merge_codegen_end_to_end(tmp_path):
    caps = [
        _cap("light-on", 0, [(0x2a, "fff1", b"\x01")]),
        _cap("light-on", 1, [(0x2a, "fff1", b"\x01")]),
        _cap("light-off", 0, [(0x2b, "fff2", b"\x00")]),
        _cap("light-off", 1, [(0x2b, "fff2", b"\x00")]),
    ]

    isolated = isolate(caps)
    merged = merge_candidates({"actions": {}}, isolated)

    assert "light-on" in merged["actions"]
    entry = merged["actions"]["light-on"]
    assert entry["handle"] == 0x2a
    assert entry["value"] == b"\x01".hex()
    assert entry["uuid"] == "fff1"
    assert entry["confirmed"] is False

    # Human confirms the mapping.
    merged["actions"]["light-on"]["confirmed"] = True

    p = tmp_path / "protocol.yaml"
    save_map(str(p), merged)
    loaded = load_map(str(p))

    src = generate(loaded, "AA:BB:CC:DD:EE:FF")
    assert "light-on" in src
    assert "AA:BB:CC:DD:EE:FF" in src
    ast.parse(src)  # must be valid Python
