#!/usr/bin/env python3
"""Dictionary-driven BLE control-frame builder.

Reads `protocol/dictionary.yaml` (as emitted by tools/extract_protocol.py) and
builds a control frame (bytes) for a given function's control characteristic
from a dict of field values, falling back to each field's documented default.

The dictionary is flow-style YAML but we deliberately avoid a PyYAML
dependency: it's a narrow, machine-generated format, so a small regex-based
line parser is enough (and keeps this tool stdlib-only).

Bit placement/packing reuses the primitives in tools/encode.py:
- bits_msb_first(value, n): value -> its low n bits, MSB-first
- pack_msb(frame_bits): bit list -> bytes, MSB-first

This module is our own code; no third-party/app source is included here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# encode.py lives next to this file; make sure it's importable regardless of
# the caller's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from encode import bits_msb_first, pack_msb  # noqa: E402

_FIELD_LINE_RE = re.compile(r"^\s*-\s*\{(?P<body>.*)\}\s*$")


def _parse_scalar(raw: str):
    """Parse one flow-mapping value: int, or a bare sentinel/string token."""
    raw = raw.strip()
    if raw and (raw[0] in "'\"") and raw[-1] == raw[0]:
        raw = raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        return raw


def _parse_field_line(line: str) -> dict:
    """Parse a `- {name: X, offset: 6, width: 2, default: 3, ...}` line.

    Values are comma-separated `key: value` pairs; values never contain commas
    in this dictionary format, so a plain split is sufficient.
    """
    m = _FIELD_LINE_RE.match(line)
    if not m:
        raise ValueError(f"could not parse control_fields line: {line!r}")
    body = m.group("body")
    field = {}
    for part in body.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        # Strip trailing inline comments (e.g. `default: 30, ...  # note`).
        value = value.split("#", 1)[0].strip()
        field[key] = _parse_scalar(value)
    return field


def load_fields(dict_path: str, function: str) -> list[dict]:
    """Return the `control_fields` list (as dicts) for `function`.

    Each returned dict has at least: name, offset (int or "MERGED_AMBIGUOUS"),
    width (int or "UNKNOWN"), default (int or "UNKNOWN").
    """
    text = Path(dict_path).read_text()
    lines = text.splitlines()

    func_header_re = re.compile(r"^  (\S+):\s*$")
    control_fields_re = re.compile(r"^\s*control_fields:\s*$")
    state_fields_re = re.compile(r"^\s*state_fields:")

    # Find the requested function's block.
    func_start = None
    for i, line in enumerate(lines):
        m = func_header_re.match(line)
        if m and m.group(1) == function:
            func_start = i
            break
    if func_start is None:
        raise ValueError(f"function {function!r} not found in {dict_path}")

    # Find the end of this function's block: next top-level (2-space-indent)
    # function header, or end of file.
    func_end = len(lines)
    for i in range(func_start + 1, len(lines)):
        if func_header_re.match(lines[i]):
            func_end = i
            break

    block = lines[func_start:func_end]

    # Within the block, find control_fields: ... up to state_fields: (or end).
    cf_start = None
    for i, line in enumerate(block):
        if control_fields_re.match(line):
            cf_start = i + 1
            break
    if cf_start is None:
        raise ValueError(f"function {function!r} has no control_fields in {dict_path}")

    cf_end = len(block)
    for i in range(cf_start, len(block)):
        if state_fields_re.match(block[i]):
            cf_end = i
            break

    fields = []
    for line in block[cf_start:cf_end]:
        if not line.strip():
            continue
        fields.append(_parse_field_line(line))
    return fields


def build(dict_path: str, function: str, values: dict[str, int]) -> bytes:
    """Build a control frame for `function`, using `values` overrides where
    given and each field's dictionary default otherwise.

    Raises ValueError (refusing to build) if any field that must be placed
    has an unresolved offset ("MERGED_AMBIGUOUS") or width ("UNKNOWN"), or an
    unresolved default ("UNKNOWN") with no explicit override -- the frame
    layout or fill value isn't knowable, so emitting bytes would be a guess.
    """
    fields = load_fields(dict_path, function)

    unresolved = []
    resolved = []
    for field in fields:
        name = field["name"]
        has_override = name in values
        offset = field.get("offset")
        width = field.get("width")
        default = field.get("default")

        problems = []
        if offset == "MERGED_AMBIGUOUS":
            problems.append("offset=MERGED_AMBIGUOUS")
        if width == "UNKNOWN":
            problems.append("width=UNKNOWN")
        if not has_override and default == "UNKNOWN":
            problems.append("default=UNKNOWN (and no explicit value given)")

        if problems:
            unresolved.append(f"{name} ({', '.join(problems)})")
            continue

        value = values[name] if has_override else default
        resolved.append((name, offset, width, value))

    if unresolved:
        raise ValueError(
            f"cannot build {function!r} frame: unresolved field(s) prevent "
            f"layout: {'; '.join(unresolved)}"
        )

    total_bits = max((offset + width for _, offset, width, _ in resolved), default=0)
    frame_bits = [0] * total_bits

    for name, offset, width, value in resolved:
        frame_bits[offset:offset + width] = bits_msb_first(value, width)

    return pack_msb(frame_bits)


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            f"usage: {argv[0] if argv else 'build_frame.py'} "
            "<dict.yaml> <function> [field=value ...]",
            file=sys.stderr,
        )
        return 2

    dict_path, function, *field_args = argv[1:]
    values = {}
    for arg in field_args:
        if "=" not in arg:
            print(f"bad field=value argument: {arg!r}", file=sys.stderr)
            return 2
        name, _, raw_value = arg.partition("=")
        values[name] = int(raw_value)

    try:
        frame = build(dict_path, function, values)
    except ValueError as exc:
        print(f"ValueError: {exc}", file=sys.stderr)
        return 1

    print(frame.hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
