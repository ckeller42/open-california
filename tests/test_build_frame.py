"""Tests for tools/build_frame.py: dictionary-driven BLE control-frame builder."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from build_frame import build, load_fields  # noqa: E402
from encode import bits_msb_first, pack_msb  # noqa: E402

DEMO_DICT = """\
functions:
  demo:
    control_fields:
      - {name: Power, offset: 6, width: 2, default: 0, value_semantics: UNVERIFIED}
      - {name: Level, offset: 0, width: 4, default: 0, value_semantics: UNVERIFIED}
    state_fields: []
"""

AMBIGUOUS_DICT = """\
functions:
  broken:
    control_fields:
      - {name: Power, offset: 6, width: 2, default: 0, value_semantics: UNVERIFIED}
      - {name: Mystery, offset: MERGED_AMBIGUOUS, width: 8, default: 30, value_semantics: UNVERIFIED}
    state_fields: []
"""

UNKNOWN_DICT = """\
functions:
  alsobroken:
    control_fields:
      - {name: Power, offset: 6, width: 2, default: 0, value_semantics: UNVERIFIED}
      - {name: Ghost, offset: MERGED_AMBIGUOUS, width: UNKNOWN, default: UNKNOWN, value_semantics: UNVERIFIED}
    state_fields: []
"""


@pytest.fixture
def demo_dict_path(tmp_path) -> Path:
    p = tmp_path / "dictionary.yaml"
    p.write_text(DEMO_DICT)
    return p


@pytest.fixture
def ambiguous_dict_path(tmp_path) -> Path:
    p = tmp_path / "ambiguous.yaml"
    p.write_text(AMBIGUOUS_DICT)
    return p


@pytest.fixture
def unknown_dict_path(tmp_path) -> Path:
    p = tmp_path / "unknown.yaml"
    p.write_text(UNKNOWN_DICT)
    return p


def test_load_fields_parses_names_offsets_widths_defaults(demo_dict_path):
    fields = load_fields(str(demo_dict_path), "demo")
    by_name = {f["name"]: f for f in fields}
    assert by_name["Power"]["offset"] == 6
    assert by_name["Power"]["width"] == 2
    assert by_name["Power"]["default"] == 0
    assert by_name["Level"]["offset"] == 0
    assert by_name["Level"]["width"] == 4


def test_build_sets_explicit_value_bit(demo_dict_path):
    frame = build(str(demo_dict_path), "demo", {"Power": 1})

    # Total frame is 8 bits (Level occupies 0-3, Power occupies 6-7).
    expected_bits = [0] * 8
    expected_bits[6:8] = bits_msb_first(1, 2)  # Power=1
    expected_bits[0:4] = bits_msb_first(0, 4)  # Level default=0
    expected = pack_msb(expected_bits)

    assert frame == expected
    assert frame == bytes([0b00000001])


def test_build_falls_back_to_default_when_field_omitted(demo_dict_path):
    frame = build(str(demo_dict_path), "demo", {})
    expected_bits = [0] * 8
    expected_bits[6:8] = bits_msb_first(0, 2)  # Power default=0
    expected_bits[0:4] = bits_msb_first(0, 4)  # Level default=0
    assert frame == pack_msb(expected_bits)
    assert frame == bytes([0x00])


def test_build_explicit_value_overrides_default(demo_dict_path):
    frame = build(str(demo_dict_path), "demo", {"Level": 5})
    expected_bits = [0] * 8
    expected_bits[6:8] = bits_msb_first(0, 2)
    expected_bits[0:4] = bits_msb_first(5, 4)
    assert frame == pack_msb(expected_bits)


def test_build_raises_on_merged_ambiguous_offset(ambiguous_dict_path):
    with pytest.raises(ValueError, match="Mystery"):
        build(str(ambiguous_dict_path), "broken", {})


def test_build_raises_on_unknown_width_and_default(unknown_dict_path):
    with pytest.raises(ValueError, match="Ghost"):
        build(str(unknown_dict_path), "alsobroken", {})


def test_load_fields_missing_function_raises(demo_dict_path):
    with pytest.raises(ValueError, match="not found"):
        load_fields(str(demo_dict_path), "nosuch")
