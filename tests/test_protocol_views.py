"""Freshness guardrails for docs/protocol/* — generated views of protocol/dictionary.yaml.

Each generator (`tools/gen_*.py`) writes a deterministic Markdown file into `docs/protocol/`.
Nothing re-derives those files automatically, so they can silently drift from the dictionary
they're supposed to describe. Each test here regenerates a view to a tmp path and asserts it's
byte-identical to the committed copy — a stale view fails CI instead of lying quietly.

Add one test per generator/view here (this file is the single home for all of them).
"""
import pathlib

import pytest

pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_reference_md_matches_dictionary(tmp_path):
    from tools import gen_protocol_reference

    committed = ROOT / "docs" / "protocol" / "reference.md"
    assert committed.exists(), "run: python3 -m tools.gen_protocol_reference --out docs/protocol/reference.md"

    fresh = gen_protocol_reference.build()
    out = tmp_path / "reference.md"
    out.write_text(fresh)

    assert out.read_text() == committed.read_text(), (
        "docs/protocol/reference.md is stale — regenerate with "
        "`python3 -m tools.gen_protocol_reference --out docs/protocol/reference.md`"
    )


def test_frame_layouts_md_matches_dictionary(tmp_path):
    from tools import gen_frame_layouts

    committed = ROOT / "docs" / "protocol" / "frame-layouts.md"
    assert committed.exists(), (
        "run: python3 -m tools.gen_frame_layouts --out docs/protocol/frame-layouts.md"
    )

    fresh = gen_frame_layouts.build()
    out = tmp_path / "frame-layouts.md"
    out.write_text(fresh)

    assert out.read_text() == committed.read_text(), (
        "docs/protocol/frame-layouts.md is stale — regenerate with "
        "`python3 -m tools.gen_frame_layouts --out docs/protocol/frame-layouts.md`"
    )


def test_wireshark_dissector_matches_dictionary(tmp_path):
    from tools import gen_wireshark_dissector

    committed = ROOT / "docs" / "protocol" / "vwcamper.lua"
    assert committed.exists(), (
        "run: python3 -m tools.gen_wireshark_dissector --out docs/protocol/vwcamper.lua"
    )

    fresh = gen_wireshark_dissector.build()
    out = tmp_path / "vwcamper.lua"
    out.write_text(fresh)

    assert out.read_text() == committed.read_text(), (
        "docs/protocol/vwcamper.lua is stale — regenerate with "
        "`python3 -m tools.gen_wireshark_dissector --out docs/protocol/vwcamper.lua`"
    )


def test_wireshark_dissector_is_non_trivial():
    text = (ROOT / "docs" / "protocol" / "vwcamper.lua").read_text()
    assert "Proto(" in text
    proto_field_count = sum(1 for line in text.splitlines() if "ProtoField." in line)
    assert proto_field_count >= 20, (
        "expected many ProtoField definitions (one per surfaced state field), got %d"
        % proto_field_count
    )


def test_signal_matrix_md_matches_signals_yaml(tmp_path):
    from tools import gen_signal_matrix

    committed = ROOT / "docs" / "protocol" / "signal-matrix.md"
    assert committed.exists(), (
        "run: python3 -m tools.gen_signal_matrix --out docs/protocol/signal-matrix.md"
    )

    fresh = gen_signal_matrix.build()
    out = tmp_path / "signal-matrix.md"
    out.write_text(fresh)

    assert out.read_text() == committed.read_text(), (
        "docs/protocol/signal-matrix.md is stale — regenerate with "
        "`python3 -m tools.gen_signal_matrix --out docs/protocol/signal-matrix.md`"
    )
