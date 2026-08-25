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
