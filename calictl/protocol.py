"""Dictionary-driven frame decode/encode for the Camper Unit BLE protocol.

Loads `protocol/dictionary.yaml` (machine-generated flow YAML) with a small
stdlib parser — no PyYAML dependency, so this runs on the Pi with only `bleak`.

Frame codec (from the app's `u8`): a value is a bit array, MSB-first per byte
(bit 0 -> byte0 0x80). An 8-bit field on a byte boundary therefore equals the
raw byte. `decode` slices named fields out; `encode` packs them back.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

_FIELD_RE = re.compile(r"-\s*\{(?P<body>[^}]*)\}")
_FUNC_RE = re.compile(r"^  (\w+):\s*$")
_CTRL_CHAR_RE = re.compile(r"control char (\w+)")


def _default_dict_path() -> Path:
    # repo_root/protocol/dictionary.yaml (this file is repo_root/calictl/protocol.py)
    return Path(__file__).resolve().parent.parent / "protocol" / "dictionary.yaml"


def _scalar(raw: str):
    raw = raw.strip()
    if raw and raw[0] in "'\"" and raw[-1] == raw[0]:
        raw = raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        return raw


def _parse_field(body: str) -> dict:
    out = {}
    for part in body.split(","):
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        out[k.strip()] = _scalar(v.split("#", 1)[0].strip())
    return out


@dataclass
class Field:
    name: str
    offset: object          # int, or "MERGED_AMBIGUOUS"
    width: object           # int, or "UNKNOWN"
    default: object = None  # control fields only
    valid: object = None    # optional semantic constraint: (lo, hi) inclusive range,
                            # or a set/frozenset of allowed values. Set via overrides.

    @property
    def placed(self) -> bool:
        return isinstance(self.offset, int) and isinstance(self.width, int)


@dataclass
class Function:
    name: str
    state_char: str | None = None
    control_char: str | None = None
    state_fields: list[Field] = dc_field(default_factory=list)
    control_fields: list[Field] = dc_field(default_factory=list)

    def state_field(self, name: str) -> Field | None:
        return next((f for f in self.state_fields if f.name == name), None)


def _char_uuid(short: str) -> str:
    return "0000%s-6c77-4b7d-bbf6-a5e587701f3d" % short.lower()


def load(dict_path: str | Path | None = None) -> dict[str, Function]:
    """Parse dictionary.yaml -> {function_name: Function}."""
    path = Path(dict_path) if dict_path else _default_dict_path()
    lines = path.read_text().splitlines()

    funcs: dict[str, Function] = {}
    cur: Function | None = None
    section = None  # "control" | "state"
    for line in lines:
        if line.startswith("functions:") or line.startswith("#"):
            continue
        m = _FUNC_RE.match(line)
        if m and not line.strip().startswith("-"):
            # a 2-space-indented `name:` starts a function block (but not
            # top-level keys like command_enums: which have no leading spaces)
            cur = Function(name=m.group(1))
            funcs[cur.name] = cur
            section = None
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("state_services:"):
            shorts = re.findall(r"[0-9a-fA-F]{3,4}", s.split(":", 1)[1])
            state = next((u for u in shorts if u.endswith("02")),
                         shorts[1] if len(shorts) > 1 else None)
            cur.state_char = _char_uuid(state) if state else None
            continue
        if s.startswith("control_source:"):
            cm = _CTRL_CHAR_RE.search(s)
            if cm:
                cur.control_char = _char_uuid(cm.group(1))
            continue
        if s.startswith("control_fields:"):
            section = "control"; continue
        if s.startswith("state_fields:"):
            section = "state"; continue
        fm = _FIELD_RE.search(s)
        if fm and section:
            d = _parse_field(fm.group("body"))
            f = Field(name=d.get("name"), offset=d.get("offset"),
                      width=d.get("width"), default=d.get("default"))
            (cur.control_fields if section == "control" else cur.state_fields).append(f)
    return funcs


# ---- bit codec (mirrors the app's u8.d / u8.e / u8.f) --------------------

def to_bits(data: bytes) -> list[int]:
    """Bytes -> MSB-first bit list (bit i = byte[i//8] >> (7 - i%8) & 1)."""
    out = []
    for b in data:
        for k in range(8):
            out.append((b >> (7 - k)) & 1)
    return out


def get_field(bits: list[int], offset: int, width: int) -> int:
    v = 0
    for x in bits[offset:offset + width]:
        v = (v << 1) | x
    return v


def _bits_of(value: int, width: int) -> list[int]:
    return [(value >> k) & 1 for k in range(width)][::-1]


def _in_valid(val: int, valid) -> bool:
    """`valid` is either an (lo, hi) inclusive range or a set of allowed values."""
    if isinstance(valid, (set, frozenset)):
        return val in valid
    lo, hi = valid
    return lo <= val <= hi


def check_value(func: Function, name: str, width: int, val: int, valid=None) -> None:
    """Raise ValueError if `val` won't fit `width` bits, or violates a curated
    semantic `valid` constraint. Guards against two failure modes: a value wider
    than its field silently corrupting the frame (``_bits_of`` masks low bits), and
    a within-width-but-semantically-invalid value that the unit rejects at ATT with
    ``0x0E`` and drops the link (observed: cooler ``State=3``, lighting
    ``ProfileNumber=14``)."""
    lo, hi = 0, (1 << width) - 1
    if not (lo <= val <= hi):
        raise ValueError("%s.%s=%s out of range %d..%d for a %d-bit field"
                         % (func.name, name, val, lo, hi, width))
    if valid is not None and not _in_valid(val, valid):
        allowed = sorted(valid) if isinstance(valid, (set, frozenset)) else "%d..%d" % valid
        raise ValueError("%s.%s=%s not an allowed value (%s)" % (func.name, name, val, allowed))


def pack(frame_bits: list[int]) -> bytes:
    out = bytearray((len(frame_bits) + 7) // 8)
    for i, b in enumerate(frame_bits):
        if b:
            out[i // 8] |= 1 << (7 - (i % 8))
    return bytes(out)


def decode(func: Function, raw: bytes) -> dict[str, int]:
    """Decode a state frame into {field_name: raw_int} for all placed fields."""
    bits = to_bits(raw)
    out = {}
    for f in func.state_fields:
        if f.placed and f.offset + f.width <= len(bits):
            out[f.name] = get_field(bits, f.offset, f.width)
    return out


def encode(func: Function, values: dict[str, int], *,
           frame_bytes: int | None = None) -> bytes:
    """Encode a control frame. Every placed control field is written from
    `values` (falling back to its dictionary default). Raises if a required
    field is unplaced (MERGED_AMBIGUOUS) and not supplied via `values` — we do
    not guess frame layout. `frame_bytes` pins the length (else max field end).
    """
    placed = [f for f in func.control_fields if f.placed]
    missing = [f.name for f in func.control_fields
               if not f.placed and f.name not in values]
    if missing:
        raise ValueError(
            "cannot encode %s: unresolved offset for %s (supply explicitly or "
            "disambiguate)" % (func.name, ", ".join(missing)))
    total = (frame_bytes * 8) if frame_bytes else \
        max((f.offset + f.width for f in placed), default=0)
    frame = [0] * total
    for f in placed:
        if f.offset + f.width > total:
            # a too-small frame_bytes would let list-slice assignment GROW the frame
            # and silently misplace bits -> a corrupt write the firmware may reject by
            # dropping the ATT link. Fail loudly instead.
            raise ValueError("%s.%s (offset %d width %d) exceeds the %d-bit frame"
                             % (func.name, f.name, f.offset, f.width, total))
        val = values.get(f.name, f.default)
        if val is None or val == "UNKNOWN":
            raise ValueError("no value/default for %s.%s" % (func.name, f.name))
        check_value(func, f.name, f.width, int(val), f.valid)
        frame[f.offset:f.offset + f.width] = _bits_of(int(val), f.width)
    return pack(frame)
