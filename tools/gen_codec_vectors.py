#!/usr/bin/env python3
"""Generate the golden codec vectors (``tests/vectors/camper_codec.json``) — issue #156.

The vectors are the language-neutral SPECIFICATION of the frame codec, shared by the
Python reference implementation (:mod:`calictl.protocol`) and the C port
(``csrc/codec.c``). This generator deliberately does **not** use protocol's
``to_bits``/``get_field``/``pack`` to compute expectations — the tiny independent
MSB-first slicer below implements the bit convention a second time, so the oracle
test (``tests/test_codec_vectors.py``) is a genuine cross-check of two
implementations, and the C port must then agree with both.

Coverage per function: a one-hot decode vector for every placed state field, a
mixed-pattern decode vector, a truncated-frame decode vector (field past the end is
absent); per control function with a pinned frame length: explicit- and
implicit-defaults encode vectors (the latter exercises the default-fallback path,
and — the 2-bit defaults being ``3`` — doubles as the leave-unchanged sentinel
case), a max-valid one-hot per field, valid-set-rejection and width-overflow error
vectors, plus the real captured cooler power-on frame ``3d4300000000`` with values
derived independently from its bytes.

Deterministic output; PyYAML-free. Regenerate after any dictionary/overrides change:

    python3 -m tools.gen_codec_vectors

.. req:: One dictionary drives byte-identical Python and C codecs
   :id: R_CODEC_XLANG_PARITY
   :status: implemented
   :tags: protocol, codec, esp32

   ``calictl`` shall express the BLE frame codec as golden vectors derived from
   ``protocol/dictionary.yaml`` (+ overrides) that both the Python reference
   implementation and the generated/ported C codec satisfy byte-identically, so the
   ESP32 port (#154) reuses the reverse-engineered protocol instead of forking a
   divergent twin.
"""
from __future__ import annotations

import json
from pathlib import Path

from calictl import overrides, protocol

OUT = Path(__file__).resolve().parent.parent / "tests" / "vectors" / "camper_codec.json"
# Real captured app frame (cooler power-on; asserted byte-identical in
# tests/test_capture_diff.py) — the one non-synthetic encode vector.
COOLER_POWER_ON_HEX = "3d4300000000"


# --- independent MSB-first bit helpers (NOT protocol.py's — see module docstring) ---

def _slice(frame: bytes, offset: int, width: int) -> int:
    v = 0
    for i in range(offset, offset + width):
        v = (v << 1) | ((frame[i // 8] >> (7 - i % 8)) & 1)
    return v


def _place(buf: bytearray, offset: int, width: int, value: int) -> None:
    for k in range(width):
        if (value >> (width - 1 - k)) & 1:
            i = offset + k
            buf[i // 8] |= 1 << (7 - i % 8)


def _valid_ok(val, valid) -> bool:
    if valid is None:
        return True
    if isinstance(valid, (set, frozenset)):
        return val in valid
    lo, hi = valid
    return lo <= val <= hi


def _min_valid(valid) -> int:
    return min(valid) if isinstance(valid, (set, frozenset)) else valid[0]


def _max_valid(field) -> int:
    if field.valid is None:
        return (1 << field.width) - 1
    return max(field.valid) if isinstance(field.valid, (set, frozenset)) else field.valid[1]


# --- vector construction ---------------------------------------------------------

def _decode_vectors(name, fn) -> list[dict]:
    placed = [f for f in fn.state_fields if f.placed]
    if not placed:
        return []
    vecs = []
    for f in placed:
        nbytes = (f.offset + f.width + 7) // 8
        buf = bytearray(nbytes)
        _place(buf, f.offset, f.width, (1 << f.width) - 1)
        raw = bytes(buf)
        expect = {g.name: _slice(raw, g.offset, g.width)
                  for g in placed if g.offset + g.width <= nbytes * 8}
        vecs.append({"id": "%s/state/%s/onehot" % (name, f.name), "function": name,
                     "raw_hex": raw.hex(), "expect": expect})
    # mixed pattern: every field carries bits, catches cross-field slicing errors
    maxend = max(f.offset + f.width for f in placed)
    nbytes = (maxend + 7) // 8
    raw = bytes((0xA5 + 7 * i) & 0xFF for i in range(nbytes))
    vecs.append({"id": "%s/state/mixed" % name, "function": name, "raw_hex": raw.hex(),
                 "expect": {f.name: _slice(raw, f.offset, f.width) for f in placed}})
    # truncated: frame ends before the last field completes -> that field is absent
    last = max(placed, key=lambda f: f.offset + f.width)
    cut = last.offset // 8
    if 0 < cut * 8 < last.offset + last.width:
        raw = bytes(cut)
        vecs.append({"id": "%s/state/truncated" % name, "function": name,
                     "raw_hex": raw.hex(),
                     "expect": {f.name: 0 for f in placed
                                if f.offset + f.width <= cut * 8}})
    return vecs


def _base_values(ctrl) -> dict[str, int]:
    """Explicit value for every placed control field: the dictionary default when it
    is encodable, else the lowest allowed value (e.g. cooler State default 3 is the
    firmware-rejected sentinel outside valid {0,1})."""
    vals = {}
    for f in ctrl:
        d = f.default
        if isinstance(d, int) and 0 <= d < (1 << f.width) and _valid_ok(d, f.valid):
            vals[f.name] = d
        else:
            vals[f.name] = _min_valid(f.valid) if f.valid is not None else 0
    return vals


def _encode_hex(ctrl, values, frame_bytes) -> str:
    buf = bytearray(frame_bytes)
    for f in ctrl:
        _place(buf, f.offset, f.width, values[f.name])
    return bytes(buf).hex()


def _encode_vectors(name, fn, frame_bytes) -> list[dict]:
    ctrl = [f for f in fn.control_fields if f.placed]
    unplaced = [f.name for f in fn.control_fields if not f.placed]
    # A pinned-length control function must be fully placed, or the C table would be a
    # half-frame. Fail generation loudly (mirrors gen_c_dict's assert).
    assert not unplaced, "%s has unplaced control fields: %s" % (name, unplaced)
    base = _base_values(ctrl)
    vecs = [
        # explicit defaults (2-bit defaults are 3 -> doubles as the sentinel case)
        {"id": "%s/encode/defaults-explicit-sentinel" % name, "function": name,
         "values": dict(base), "frame_bytes": frame_bytes,
         "expect_hex": _encode_hex(ctrl, base, frame_bytes)},
        # implicit defaults: only the non-encodable defaults supplied; the rest must
        # come from the dictionary defaults inside encode (exercises HAS_DEFAULT)
        {"id": "%s/encode/defaults-implicit" % name, "function": name,
         "values": {f.name: base[f.name] for f in ctrl if base[f.name] != f.default},
         "frame_bytes": frame_bytes,
         "expect_hex": _encode_hex(ctrl, base, frame_bytes)},
    ]
    for f in ctrl:
        vals = dict(base)
        vals[f.name] = _max_valid(f)
        vecs.append({"id": "%s/encode/%s/max" % (name, f.name), "function": name,
                     "values": vals, "frame_bytes": frame_bytes,
                     "expect_hex": _encode_hex(ctrl, vals, frame_bytes)})
    # error: width overflow on the widest field
    wide = max(ctrl, key=lambda f: f.width)
    vals = dict(base)
    vals[wide.name] = 1 << wide.width
    vecs.append({"id": "%s/encode/err-width-overflow" % name, "function": name,
                 "values": vals, "frame_bytes": frame_bytes, "error": True})
    # error: valid-set rejection (first in-width value outside the allowed set)
    for f in ctrl:
        if f.valid is None:
            continue
        bad = next((v for v in range(1 << f.width) if not _valid_ok(v, f.valid)), None)
        if bad is None:
            continue
        vals = dict(base)
        vals[f.name] = bad
        vecs.append({"id": "%s/encode/err-invalid-%s-%d" % (name, f.name, bad),
                     "function": name, "values": vals, "frame_bytes": frame_bytes,
                     "error": True})
    return vecs


def generate() -> dict:
    funcs = protocol.load()
    overrides.apply(funcs)
    decode, encode = [], []
    for name in sorted(funcs):
        decode.extend(_decode_vectors(name, funcs[name]))
    for name in sorted(overrides.CONTROL_FRAME_BYTES):
        encode.extend(_encode_vectors(name, funcs[name],
                                      overrides.CONTROL_FRAME_BYTES[name]))
    # the real captured frame: values sliced independently from its bytes, encode
    # must reproduce it byte-identically
    cooler = [f for f in funcs["cooler"].control_fields if f.placed]
    raw = bytes.fromhex(COOLER_POWER_ON_HEX)
    encode.append({"id": "cooler/encode/real-power-on", "function": "cooler",
                   "values": {f.name: _slice(raw, f.offset, f.width) for f in cooler},
                   "frame_bytes": len(raw), "expect_hex": COOLER_POWER_ON_HEX})
    return {"version": 1, "generated_by": "tools/gen_codec_vectors.py",
            "decode": decode, "encode": encode}


def render() -> str:
    """The exact checked-in file content for the current dictionary/overrides."""
    return json.dumps(generate(), indent=1, sort_keys=False) + "\n"


def main() -> int:
    import sys
    text = render()
    if "--check" in sys.argv:
        if not OUT.is_file() or OUT.read_text() != text:
            print("STALE: %s does not match a fresh regeneration — run "
                  "python3 -m tools.gen_codec_vectors" % OUT, file=sys.stderr)
            return 1
        print("fresh: %s" % OUT)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
