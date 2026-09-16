"""BLE trace recorder: every notification / read / write / link event as one JSONL line, so the
REAL unit's traffic can be replayed through the mock and compared (tools/trace_compare.py).

.. test:: BLE trace recorder writes decodable JSONL and stays off by default
   :id: T_BLE_TRACE
   :links: R_BLE_TRACE
"""
import json
import os

from calictl import trace


def test_trace_off_by_default_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("CALICTL_BLE_TRACE", raising=False)
    t = trace.Tracer.from_env()
    assert not t.enabled
    t.notify("00001602-6c77-4b7d-bbf6-a5e587701f3d", b"\x01\x02")   # must not raise or write
    assert list(tmp_path.iterdir()) == []


def test_trace_records_events_as_jsonl(tmp_path, monkeypatch):
    p = tmp_path / "ble.jsonl"
    monkeypatch.setenv("CALICTL_BLE_TRACE", str(p))
    t = trace.Tracer.from_env()
    assert t.enabled
    t.link("connect", "AA:BB:CC:DD:EE:FF")
    t.notify("00001602-6C77-4B7D-BBF6-A5E587701F3D", b"\x00\xd0")
    t.read("00001102-6c77-4b7d-bbf6-a5e587701f3d", b"\x09\x43")
    t.write("00001101-6c77-4b7d-bbf6-a5e587701f3d", b"\x3c\x43")
    t.link("disconnect", None, reason="idle")
    lines = [json.loads(line) for line in p.read_text().splitlines()]
    assert [e["ev"] for e in lines] == ["connect", "notify", "read", "write", "disconnect"]
    n = lines[1]
    assert n["char"] == "1602" and n["fn"] == "energy" and n["hex"] == "00d0"
    assert isinstance(n["t"], float) and n["t"] > 1.7e9          # epoch seconds
    assert lines[3]["char"] == "1101" and lines[3]["fn"] == "cooler"
    assert lines[4]["reason"] == "idle"


def test_trace_skips_heartbeat_unless_asked(tmp_path, monkeypatch):
    """1003 beats come every ~0.6 s for hours; only record them when explicitly enabled."""
    p = tmp_path / "ble.jsonl"
    monkeypatch.setenv("CALICTL_BLE_TRACE", str(p))
    monkeypatch.delenv("CALICTL_BLE_TRACE_HEARTBEAT", raising=False)
    t = trace.Tracer.from_env()
    t.write("00001003-6c77-4b7d-bbf6-a5e587701f3d", b"\x00\x00\x00\x07")
    assert not p.exists() or p.read_text() == ""
    monkeypatch.setenv("CALICTL_BLE_TRACE_HEARTBEAT", "1")
    t2 = trace.Tracer.from_env()
    t2.write("00001003-6c77-4b7d-bbf6-a5e587701f3d", b"\x00\x00\x00\x08")
    assert json.loads(p.read_text().splitlines()[-1])["char"] == "1003"


def test_trace_rotates_when_too_large(tmp_path, monkeypatch):
    p = tmp_path / "ble.jsonl"
    monkeypatch.setenv("CALICTL_BLE_TRACE", str(p))
    t = trace.Tracer.from_env(max_bytes=400)
    for i in range(40):
        t.notify("00001602-6c77-4b7d-bbf6-a5e587701f3d", bytes([i]) * 8)
    assert p.exists() and os.path.getsize(p) <= 400 + 200            # current file stays small
    assert (tmp_path / "ble.jsonl.1").exists()                        # one rotated predecessor


def test_trace_reader_yields_events_in_order(tmp_path, monkeypatch):
    p = tmp_path / "ble.jsonl"
    monkeypatch.setenv("CALICTL_BLE_TRACE", str(p))
    t = trace.Tracer.from_env()
    t.notify("00001602-6c77-4b7d-bbf6-a5e587701f3d", b"\x01")
    t.notify("00001602-6c77-4b7d-bbf6-a5e587701f3d", b"\x02")
    evs = list(trace.read_events(p))
    assert [e["hex"] for e in evs] == ["01", "02"]
