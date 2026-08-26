"""Generate docs/protocol/frame-layouts.md from protocol/dictionary.yaml (protocol doc view #2).

Read-only projection: never hand-edit the generated file, regenerate it instead —
`python3 -m tools.gen_frame_layouts --out docs/protocol/frame-layouts.md`. See
docs/protocol/README.md. tests/test_protocol_views.py asserts the committed file matches
a fresh regen (fails CI on drift).

Bit convention (verified against calictl/protocol.py, `to_bits`/`get_field`): the codec is
MSB-first — bit index 0 of a frame is bit 7 (the MSB) of byte 0
(``bits[i] = data[i // 8] >> (7 - i % 8) & 1``). A field with a given ``offset``/``width``
therefore occupies frame-bit indices ``[offset .. offset + width - 1]`` in that scheme, which
this generator maps onto a per-byte grid of bit positions 7..0.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tools._view_common import char_uuid, is_placed, load_functions

ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = ROOT / "protocol" / "dictionary.yaml"
OUT_FILE = ROOT / "docs" / "protocol" / "frame-layouts.md"

_BIT_COLS = tuple(range(7, -1, -1))  # bit 7 (MSB) .. bit 0 (LSB), per byte






def _byte_grid(fields):
    """Place `fields` (with resolvable offset/width) into a list-of-bytes bit grid.

    Returns (grid, ambiguous) where grid[byte][7 - bitpos] holds the occupying field name
    (or None for an unused/gap bit), and `ambiguous` lists the fields that could not be
    positionally resolved.
    """
    placed = [f for f in fields if is_placed(f)]
    ambiguous = [f for f in fields if not is_placed(f)]
    if not placed:
        return [], ambiguous
    total_bits = max(f["offset"] + f["width"] for f in placed)
    total_bytes = (total_bits + 7) // 8
    grid = [[None] * 8 for _ in range(total_bytes)]
    for f in placed:
        name = f.get("name", "—")
        for i in range(f["offset"], f["offset"] + f["width"]):
            byte, pos = i // 8, 7 - (i % 8)
            grid[byte][pos] = name
    return grid, ambiguous


def _grid_table(grid) -> list[str]:
    header = "| byte | " + " | ".join("bit %d" % b for b in _BIT_COLS) + " |"
    sep = "|" + "---|" * (len(_BIT_COLS) + 1)
    lines = [header, sep]
    for byte_idx, row in enumerate(grid):
        cells = [row[b] if row[b] else "·" for b in _BIT_COLS]
        lines.append("| %d | " % byte_idx + " | ".join(cells) + " |")
    return lines


def _frame_section(title, fields) -> list[str]:
    lines = ["### %s" % title, ""]
    grid, ambiguous = _byte_grid(fields)
    if grid:
        lines.extend(_grid_table(grid))
    else:
        lines.append("_(no positionally-resolved fields)_")
    lines.append("")
    if ambiguous:
        names = ", ".join(f.get("name", "—") for f in ambiguous)
        lines.append("**Ambiguous offset (not shown in map):** %s" % names)
        lines.append("")
    return lines


def build(dict_path=None) -> str:
    """Render the Markdown frame bit-layout doc from dictionary.yaml. Returns the file text."""

    funcs = load_functions(dict_path)

    lines = [
        "# Protocol frame bit-layouts",
        "",
        "GENERATED from `protocol/dictionary.yaml` — do not hand-edit; see "
        "[README.md](README.md) for how to regenerate.",
        "",
        "Bit numbering is **MSB-first** (`calictl/protocol.py:to_bits`): frame-bit index 0 is "
        "bit 7 (the MSB) of byte 0. A field at `offset`/`width` occupies frame-bit indices "
        "`[offset .. offset + width - 1]`; the grids below place those bits per byte (bit 7 "
        "= MSB .. bit 0 = LSB). `·` marks an unused/gap bit. Fields with a "
        "`MERGED_AMBIGUOUS` offset can't be positioned and are listed separately.",
        "",
    ]
    for name in sorted(funcs):
        spec = funcs[name]
        if not isinstance(spec, dict):
            continue
        services = spec.get("state_services") or []
        uuids = ", ".join(char_uuid(s) for s in services) if services else "—"
        control_source = spec.get("control_source") or "—"
        lines.append("## %s" % name)
        lines.append("")
        lines.extend(_frame_section(
            "State frame (char(s): %s)" % uuids, spec.get("state_fields") or []))
        lines.extend(_frame_section(
            "Control frame (control_source: %s)" % control_source, spec.get("control_fields") or []))
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="generate the Markdown frame bit-layout diagrams")
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
