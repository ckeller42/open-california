"""Generate docs/protocol/reference.md from protocol/dictionary.yaml (protocol doc view #1).

Read-only projection: never hand-edit the generated file, regenerate it instead —
`python3 -m tools.gen_protocol_reference --out docs/protocol/reference.md`. See
docs/protocol/README.md. tests/test_protocol_views.py asserts the committed file matches
a fresh regen (fails CI on drift).
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = ROOT / "protocol" / "dictionary.yaml"
OUT_FILE = ROOT / "docs" / "protocol" / "reference.md"

# field table columns, in order (first column is always the field name)
_COLUMNS = ("offset", "width", "default", "raw_range", "value_semantics")


def _char_uuid(short) -> str:
    # Mirrors calictl.protocol._char_uuid: a BLE characteristic short id -> full 128-bit UUID.
    return "0000%s-6c77-4b7d-bbf6-a5e587701f3d" % str(short).lower()


def _cell(col, value) -> str:
    if value is None:
        return "—"
    text = str(value)
    if col == "offset" and text == "MERGED_AMBIGUOUS":
        return "**MERGED_AMBIGUOUS**"   # explicitly flag: offset could not be positionally resolved
    return text


def _field_table(fields) -> list[str]:
    if not fields:
        return ["_(none)_"]
    lines = ["| field | " + " | ".join(_COLUMNS) + " |",
             "|" + "---|" * (len(_COLUMNS) + 1)]
    for f in fields:
        row = [_cell(c, f.get(c)) for c in _COLUMNS]
        lines.append("| " + " | ".join([f.get("name", "—")] + row) + " |")
    return lines


def build(dict_path=None) -> str:
    """Render the Markdown reference from dictionary.yaml. Returns the file text."""
    import yaml  # lazy: tooling only, not the calictl runtime

    doc = yaml.safe_load(Path(dict_path or DICT_FILE).read_text())
    funcs = doc.get("functions", doc)

    lines = [
        "# Protocol reference",
        "",
        "GENERATED from `protocol/dictionary.yaml` — do not hand-edit; see "
        "[README.md](README.md) for how to regenerate.",
        "",
    ]
    for name in sorted(funcs):
        spec = funcs[name]
        if not isinstance(spec, dict):
            continue
        services = spec.get("state_services") or []
        uuids = ", ".join(_char_uuid(s) for s in services) if services else "—"
        control_source = spec.get("control_source") or "—"
        lines.append("## %s" % name)
        lines.append("")
        lines.append("- **State char UUID(s):** %s" % uuids)
        lines.append("- **Control source:** %s" % control_source)
        lines.append("")
        lines.append("### State fields")
        lines.append("")
        lines.extend(_field_table(spec.get("state_fields") or []))
        lines.append("")
        lines.append("### Control fields")
        lines.append("")
        lines.extend(_field_table(spec.get("control_fields") or []))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="generate the Markdown protocol reference")
    ap.add_argument("--out", default=str(OUT_FILE))
    ap.add_argument("--dict", default=str(DICT_FILE), help="path to dictionary.yaml")
    args = ap.parse_args(argv)
    text = build(args.dict)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
