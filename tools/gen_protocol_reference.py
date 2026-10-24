"""Generate docs/protocol/reference.md from protocol/dictionary.yaml (protocol doc view #1).

Read-only projection: never hand-edit the generated file, regenerate it instead —
`python3 -m tools.gen_protocol_reference --out docs/protocol/reference.md`. See
docs/protocol/README.md. tests/test_protocol_views.py asserts the committed file matches
a fresh regen (fails CI on drift).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tools._view_common import char_uuid, load_catalog, load_functions

ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = ROOT / "protocol" / "dictionary.yaml"
OUT_FILE = ROOT / "docs" / "protocol" / "reference.md"

_HEADER = """\
# Protocol reference

GENERATED from `protocol/dictionary.yaml`, `calictl/overrides.py` and `protocol/signals.yaml` —
do not hand-edit; see [README.md](README.md) for how to regenerate.

How to read the tables:

- **offset** — the field's first bit (MSB-first). "(override)": the extractor could not place it
  (two functions share one control class in the app) and `calictl/overrides.py` places it from the
  app's frame builder. A bare `MERGED_AMBIGUOUS` would still be unplaced; none is used by calictl.
- **calictl** — what the [signal catalog](signal-matrix.md) decided: `name` (unit) = surfaced
  under that name, with its verified unit if it has one (a value without a unit is a raw level or
  enum — verified as such, see `docs/business-logic/signal-scales.md`); "omitted" = not surfaced
  (the reason is in `protocol/signals.yaml`). Control fields are written, never surfaced: their
  column is "—" (the commands that write them: `calictl/control.py`).
- **meaning** — the value meaning the decompile pins down, where the extractor recorded one.
"""


def _offset(f) -> str:
    off = f.get("offset")
    return "%s (override)" % off if f.get("override") else "—" if off is None else str(off)


def _calictl(entry) -> str:
    if not isinstance(entry, dict):
        return "—"
    if entry.get("decision") != "surface":
        return "omitted"
    unit = entry.get("unit")
    return "`%s`%s" % (entry.get("name"), " (%s)" % unit if unit else "")


def _cell(value) -> str:
    return "—" if value in (None, "UNVERIFIED", "UNKNOWN") else str(value).replace("|", "\\|")


def _field_table(fn, side, fields, catalog) -> list[str]:
    if not fields:
        return ["_(none)_"]
    lines = ["| field | offset | width | default | raw_range | calictl | meaning |", "|" + "---|" * 7]
    for f in fields:
        row = [
            _offset(f),
            _cell(f.get("width")),
            _cell(f.get("default")),
            _cell(f.get("raw_range")),
            _calictl(catalog.get(f.get("name"))) if side == "state" else "—",
            _cell(f.get("value_semantics")),
        ]
        lines.append("| " + " | ".join([f.get("name", "—")] + row) + " |")
    return lines


def build(dict_path=None, signals_path=None) -> str:
    """Render the Markdown reference from dictionary.yaml + overrides + the signal catalog.

    :param dict_path: dictionary.yaml (default: the repo's)
    :param signals_path: signals.yaml (default: the repo's)
    :returns: the file text
    """

    funcs = load_functions(dict_path)
    catalog = load_catalog(signals_path)

    lines = _HEADER.splitlines() + [""]
    for name in sorted(funcs):
        spec = funcs[name]
        if not isinstance(spec, dict):
            continue
        services = spec.get("state_services") or []
        uuids = ", ".join(char_uuid(s) for s in services) if services else "—"
        control_source = spec.get("control_source") or "—"
        lines.append("## %s" % name)
        lines.append("")
        lines.append("- **State char UUID(s):** %s" % uuids)
        lines.append("- **Control source:** %s" % control_source)
        lines.append("")
        cat = catalog.get(name) or {}
        lines.append("### State fields")
        lines.append("")
        lines.extend(_field_table(name, "state", spec.get("state_fields") or [], cat.get("state") or {}))
        lines.append("")
        lines.append("### Control fields")
        lines.append("")
        lines.extend(
            _field_table(name, "control", spec.get("control_fields") or [], cat.get("control") or {})
        )
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
