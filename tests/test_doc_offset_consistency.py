"""Guardrail: tie the PROSE to the CODE.

Docs and code-comments cite bit offsets in a ``Field@offset`` / ``Field@offset/wWidth`` shorthand
(e.g. ``NightTimerHourOn@48``, ``ProfileNumber@4/w4``). Nothing used to check those citations against
the dictionary, so prose drifted from the wire layout (this is how the roof-cadence and several
offset claims went stale). This test parses every such citation out of ``docs/business-logic/*.md``,
``CLAUDE.md``, and ``calictl/*.py`` and asserts the dictionary (``protocol/dictionary.yaml`` +
``overrides.CONTROL_OFFSETS``) actually has a field of that name at that offset (and width, if cited).

A failure means a document claims a bit layout the code does not implement — fix the prose (or the
dictionary). Add genuinely non-layout ``X@N`` tokens to ``_IGNORE`` if a false positive ever appears.
"""
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent

# `Word@12` or `Word@12/w4`; the name must start uppercase (dictionary field convention).
_CITE = re.compile(r"\b([A-Z][A-Za-z0-9]+)@(\d+)(?:/w(\d+))?\b")

# Tokens that look like citations but aren't dictionary fields (add sparingly, with a reason).
_IGNORE = {
    ("L1", ), ("L8", ), ("L9", ), ("L16", ),   # lamp-zone shorthand, not dictionary field names
}

_SCAN = ["CLAUDE.md"] + \
    [str(p.relative_to(ROOT)) for p in (ROOT / "docs" / "business-logic").glob("*.md")] + \
    [str(p.relative_to(ROOT)) for p in (ROOT / "calictl").glob("*.py")]


def _dictionary_layout():
    """(name, offset) pairs and (name, offset, width) triples the dictionary + overrides define."""
    doc = yaml.safe_load((ROOT / "protocol" / "dictionary.yaml").read_text())
    funcs = doc.get("functions", doc)
    pairs, triples = set(), set()
    for spec in funcs.values():
        if not isinstance(spec, dict):
            continue
        for section in ("state_fields", "control_fields"):
            for f in spec.get(section) or []:
                name, off, w = f.get("name"), f.get("offset"), f.get("width")
                if isinstance(off, int):
                    pairs.add((name, off))
                    if isinstance(w, int):
                        triples.add((name, off, w))
    # manually-resolved control offsets live in overrides, not the dictionary
    from calictl import overrides
    for fields in overrides.CONTROL_OFFSETS.values():
        for name, (off, w) in fields.items():
            pairs.add((name, off))
            triples.add((name, off, w))
    return pairs, triples


def test_doc_offset_citations_match_the_dictionary():
    pairs, triples = _dictionary_layout()
    bad = []
    for rel in _SCAN:
        path = ROOT / rel
        for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for name, off, width in _CITE.findall(line):
                if (name,) in _IGNORE:
                    continue
                off = int(off)
                if (name, off) not in pairs:
                    bad.append(f"{rel}:{lineno}  {name}@{off}  — no dictionary field '{name}' at offset {off}")
                elif width and (name, off, int(width)) not in triples:
                    bad.append(f"{rel}:{lineno}  {name}@{off}/w{width}  — '{name}'@{off} exists but not with width {width}")
    assert not bad, "doc/comment offset citations disagree with the dictionary:\n  " + "\n  ".join(bad)
