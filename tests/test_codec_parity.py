"""Python ⇄ C codec parity — the differential harness for issue #156.

Compiles ``csrc/`` once per test session (skipped cleanly when no C compiler is on
the machine — CI is the enforcement point) and drives the batched ``codec_cli``
via one subprocess per test: all input lines written, stdin closed, all output
read. Every golden vector is asserted THREE ways: vector expect == Python codec ==
C codec. A later task adds the seeded differential fuzz pass on top.

.. test:: Golden vectors pass the C codec identically to Python
   :id: T_CODEC_PARITY_GOLDEN
   :links: R_CODEC_XLANG_PARITY
"""
import json
from pathlib import Path

import pytest

from calictl import overrides, protocol

VECTORS = Path(__file__).resolve().parent / "vectors" / "camper_codec.json"


def _parse_ok_fields(line):
    """'OK Name=1 Other=2' -> {'Name': 1, 'Other': 2}; None when not OK."""
    if not line.startswith("OK"):
        return None
    return {k: int(v) for k, v in
            (part.split("=", 1) for part in line.split()[1:])}


def _funcs():
    f = protocol.load()
    overrides.apply(f)
    return f


def test_decode_vectors_triple_parity(codec_cli):
    funcs = _funcs()
    vecs = json.loads(VECTORS.read_text())["decode"]
    lines = ["D %s %s" % (v["function"], v["raw_hex"] or "-") for v in vecs]
    for vec, out in zip(vecs, codec_cli(lines)):
        c_fields = _parse_ok_fields(out)
        assert c_fields is not None, "%s: C said %r" % (vec["id"], out)
        py = protocol.decode(funcs[vec["function"]], bytes.fromhex(vec["raw_hex"]))
        assert c_fields == py == vec["expect"], vec["id"]


def _encode_line(vec):
    kv = " ".join("%s=%d" % (k, v) for k, v in vec["values"].items())
    return ("E %s %d %s" % (vec["function"], vec["frame_bytes"], kv)).rstrip()


def test_encode_vectors_triple_parity(codec_cli):
    """Byte-identical encode, and error parity: Python raises ⇔ C answers ERR."""
    funcs = _funcs()
    vecs = json.loads(VECTORS.read_text())["encode"]
    for vec, out in zip(vecs, codec_cli([_encode_line(v) for v in vecs])):
        f = funcs[vec["function"]]
        if vec.get("error"):
            with pytest.raises(ValueError):
                protocol.encode(f, vec["values"], frame_bytes=vec["frame_bytes"])
            assert out.startswith("ERR "), "%s: C accepted an invalid encode: %r" % (
                vec["id"], out)
        else:
            py_hex = protocol.encode(f, vec["values"],
                                     frame_bytes=vec["frame_bytes"]).hex()
            assert out == "OK " + py_hex and py_hex == vec["expect_hex"], vec["id"]


def test_seeded_differential_fuzz(codec_cli):
    """Random frames/values through BOTH codecs — covers the input space the fixed
    vectors miss. Seeded (deterministic CI, repo convention); one batched CLI exec.

    .. test:: Seeded differential fuzz, Python vs C codec
       :id: T_CODEC_PARITY_FUZZ
       :links: R_CODEC_XLANG_PARITY
    """
    import random
    rng = random.Random(0xCA11)
    funcs = _funcs()
    names, enc_fns = sorted(funcs), sorted(overrides.CONTROL_FRAME_BYTES)
    ops, lines = [], []
    for _ in range(500):                       # decode: random function/length/bytes
        fn = rng.choice(names)
        raw = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 24)))
        ops.append(("D", fn, raw))
        lines.append("D %s %s" % (fn, raw.hex() or "-"))
    for _ in range(500):                       # encode: valid-biased, ~10% deliberately bad
        fn = rng.choice(enc_fns)
        fb, vals = overrides.CONTROL_FRAME_BYTES[fn], {}
        for fl in funcs[fn].control_fields:
            if rng.random() < 0.2:
                continue                       # exercise the default-fallback path
            if fl.valid is not None and rng.random() >= 0.15:
                vals[fl.name] = rng.choice(sorted(fl.valid))
            elif rng.random() < 0.1:
                vals[fl.name] = (1 << fl.width) + rng.randrange(7)   # too wide
            else:
                vals[fl.name] = rng.randrange(1 << min(fl.width, 32))
        ops.append(("E", fn, (vals, fb)))
        kv = " ".join("%s=%d" % (k, v) for k, v in vals.items())
        lines.append(("E %s %d %s" % (fn, fb, kv)).rstrip())
    for (kind, fn, payload), got in zip(ops, codec_cli(lines)):
        if kind == "D":
            assert _parse_ok_fields(got) == protocol.decode(funcs[fn], payload), (fn, payload.hex())
        else:
            vals, fb = payload
            try:
                py_hex = protocol.encode(funcs[fn], vals, frame_bytes=fb).hex()
            except ValueError:
                assert got.startswith("ERR "), (fn, vals, got)
            else:
                assert got == "OK " + py_hex, (fn, vals, got)


def test_malformed_lines_never_crash(codec_cli):
    junk = ["", "X", "D", "E cooler", "D nosuch 00", "D cooler zz",
            "E cooler 6 State=abc", "E cooler 999 State=1", "E nosuch 6 A=1"]
    for line, got in zip(junk, codec_cli(junk)):
        assert got.startswith("ERR "), (line, got)
