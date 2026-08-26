"""Generate docs/protocol/signal-matrix.md from protocol/signals.yaml (protocol doc view #5).

Read-only projection: never hand-edit the generated file, regenerate it instead —
`python3 -m tools.gen_signal_matrix --out docs/protocol/signal-matrix.md`. See
docs/protocol/README.md. tests/test_protocol_views.py asserts the committed file matches
a fresh regen (fails CI on drift).

Schema of protocol/signals.yaml (as actually observed, 2026-08-25 — not documented
elsewhere): a top-level map of ``<function>: {<state|control>: {<field>: <entry>}}``.
Each ``entry`` is a dict with:

- ``decision``: ``"surface"`` or ``"omit"`` (always present)
- ``name``: the surfaced signal name (only on ``surface`` entries)
- ``reason``: why it's omitted (only on ``omit`` entries)
- ``kind``: a semantic value-kind tag (e.g. ``battery``, ``percent``, ``level``,
  ``current``, ``flag``, ``source``, ``state``, ``raw`` — only on ``surface`` entries)
- ``scale``: the unit-scale note, e.g. ``"raw"``, ``"UNVERIFIED"``, or a free-text note
  such as ``"inverted-combined"`` flagging a non-trivial transform
- ``unit``: physical unit, or ``None``
- ``confidence``: ``"high"`` / ``"medium"`` (only on ``surface`` entries) — this is the
  closest thing signals.yaml has to an "evidence tier"
- ``sources``: ``{app, gui, vwdoc, live}`` — each ``None`` or a citation (getter name,
  GUI key, manual quote, or a live sample value); which keys are non-null is the
  provenance for the entry

There is **no persisted "semantic-review status" field** in signals.yaml — that check
(``SEMANTIC-REVIEW-NEEDED``) is computed on the fly by ``tools/audit_signals.py`` against
a decompile source tree, not stored here. As a best-effort stand-in we flag any entry
whose ``scale`` mentions "inverted" or "combined" (the only persisted marker of a
non-trivial semantic transform, per CLAUDE.md's camping-lights precedent) as
``needs-review``; everything else is ``—``. This is a proxy, not a real audit result —
said explicitly in the generated doc.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tools._view_common import load_catalog

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_FILE = ROOT / "protocol" / "signals.yaml"
OUT_FILE = ROOT / "docs" / "protocol" / "signal-matrix.md"

_SOURCE_KEYS = ("app", "gui", "vwdoc", "live")

_COLUMNS = (
    "function", "category", "field", "decision", "surfaced-name",
    "confidence", "provenance", "semantic-review", "omit-reason",
)


def _cell(value) -> str:
    if value is None or value == "":
        return "—"
    text = str(value)
    # escape pipes so a stray "|" in free-text reasons/scales doesn't break the table
    text = text.replace("|", "\\|")
    return text


def _provenance(entry) -> str:
    sources = entry.get("sources") or {}
    present = [k for k in _SOURCE_KEYS if sources.get(k) not in (None, "")]
    return ", ".join(present) if present else "—"


def _semantic_review(entry) -> str:
    scale = str(entry.get("scale") or "")
    if "inverted" in scale.lower() or "combined" in scale.lower():
        return "needs-review (%s)" % scale
    return "—"


def _rows(catalog):
    rows = []
    for fn in sorted(catalog):
        kinds = catalog.get(fn) or {}
        for category in sorted(kinds):
            fields = kinds.get(category) or {}
            for field in sorted(fields):
                entry = fields.get(field) or {}
                decision = entry.get("decision")
                rows.append((
                    fn,
                    category,
                    field,
                    _cell(decision),
                    _cell(entry.get("name")) if decision == "surface" else "—",
                    _cell(entry.get("confidence")),
                    _provenance(entry),
                    _semantic_review(entry),
                    _cell(entry.get("reason")) if decision == "omit" else "—",
                ))
    return rows


def build(signals_path=None) -> str:
    """Render the Markdown signal matrix from signals.yaml. Returns the file text."""

    catalog = load_catalog(signals_path)

    rows = _rows(catalog)
    total = len(rows)
    surfaced = sum(1 for r in rows if r[3] == "surface")
    omitted = sum(1 for r in rows if r[3] == "omit")
    flagged = sum(1 for r in rows if r[7] != "—")

    lines = [
        "# Signal provenance / coverage matrix",
        "",
        "GENERATED from `protocol/signals.yaml` — do not hand-edit; see "
        "[README.md](README.md) for how to regenerate.",
        "",
        "Every catalogued field, its surface/omit decision, and the evidence behind it. "
        "Sorted by function, then state/control, then field name for a stable diff.",
        "",
        "- **confidence**: signals.yaml's own evidence-tier tag (`high`/`medium`); "
        "absent on most `omit` entries.",
        "- **provenance**: which of `app` (decompiled getter/setter), `gui` (UI screen "
        "spec key), `vwdoc` (vendor manual), `live` (a captured live sample) back this "
        "entry — signals.yaml's `sources` map.",
        "- **semantic-review**: signals.yaml persists **no** dedicated review-status "
        "field — `tools/audit_signals.py --report` computes `SEMANTIC-REVIEW-NEEDED` "
        "live against a decompile source tree, it isn't stored here. This column is a "
        "best-effort proxy: entries whose `scale` mentions \"inverted\" or \"combined\" "
        "(the only persisted marker of a non-trivial transform, e.g. the camping-lights "
        "case) are flagged `needs-review`; run the real auditor for anything authoritative.",
        "",
        "Totals: %d fields catalogued, %d surfaced, %d omitted, %d flagged for review."
        % (total, surfaced, omitted, flagged),
        "",
        "| " + " | ".join(_COLUMNS) + " |",
        "|" + "---|" * len(_COLUMNS),
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="generate the Markdown signal provenance/coverage matrix")
    ap.add_argument("--out", default=str(OUT_FILE))
    ap.add_argument("--signals", default=str(SIGNALS_FILE), help="path to signals.yaml")
    args = ap.parse_args(argv)
    text = build(args.signals)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
