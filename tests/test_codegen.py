import pytest
from california.codegen import generate

MAP = {"actions": {
    "light-on":  {"handle": 42, "uuid": "fff1", "value": "01", "confidence": 1.0, "confirmed": True},
    "roof-open": {"handle": 99, "uuid": "aaaa", "value": "ff", "confidence": 1.0, "confirmed": True},
}}

def test_generate_includes_allowed_action():
    src = generate(MAP, "AA:BB:CC:DD:EE:FF")
    assert "light-on" in src
    assert 'bytes.fromhex("01")' in src
    assert "AA:BB:CC:DD:EE:FF" in src

def test_generate_excludes_safety_action():
    src = generate(MAP, "AA:BB:CC:DD:EE:FF")
    assert "roof-open" not in src   # safety function must be filtered out

def test_generate_raises_when_nothing_allowed():
    with pytest.raises(ValueError):
        generate({"actions": {"roof-open": MAP["actions"]["roof-open"]}}, "AA:BB:CC:DD:EE:FF")

def test_generate_rejects_injected_uuid():
    """Regression: uuid with quote should raise ValueError, not produce injected output."""
    malicious_map = {"actions": {
        "light-evil": {"handle": 42, "uuid": 'fff1"); import os; ("', "value": "01", "confirmed": True},
    }}
    with pytest.raises(ValueError):
        generate(malicious_map, "AA:BB:CC:DD:EE:FF")

def test_generate_rejects_invalid_hex_value():
    """Regression: non-hex value should raise ValueError."""
    bad_map = {"actions": {
        "light-bad": {"handle": 42, "uuid": "fff1", "value": "zz", "confirmed": True},
    }}
    with pytest.raises(ValueError):
        generate(bad_map, "AA:BB:CC:DD:EE:FF")
