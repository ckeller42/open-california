"""Golden codec vectors — the language-neutral oracle for the C port (issue #156).

``tests/vectors/camper_codec.json`` is the SPECIFICATION of the frame codec: generated
by ``tools/gen_codec_vectors.py`` with its own independent MSB-first bit slicer (NOT
calictl.protocol's), then verified here against the Python reference implementation.
Passing means two independent implementations of the bit convention agree — a verified
oracle the C codec must then match too (``tests/test_codec_parity.py``).

On a mismatch, fix the VECTOR (regenerate), not the code: ``calictl.protocol`` is the
reference.

.. test:: Golden codec vectors pass the Python reference codec
   :id: T_CODEC_VECTORS_ORACLE
   :links: R_CODEC_XLANG_PARITY
"""
import json
from pathlib import Path

import pytest

from calictl import overrides, protocol

VECTORS = Path(__file__).parent / "vectors" / "camper_codec.json"


def _funcs():
    f = protocol.load()
    overrides.apply(f)
    return f


def _vectors():
    return json.loads(VECTORS.read_text())


def test_vector_file_exists_and_versioned():
    v = _vectors()
    assert v["version"] == 1 and v["decode"] and v["encode"]


def test_decode_vectors_pass_python_codec():
    funcs, v = _funcs(), _vectors()
    for vec in v["decode"]:
        got = protocol.decode(funcs[vec["function"]], bytes.fromhex(vec["raw_hex"]))
        assert got == vec["expect"], vec["id"]


def test_encode_vectors_pass_python_codec():
    funcs, v = _funcs(), _vectors()
    for vec in v["encode"]:
        f = funcs[vec["function"]]
        if vec.get("error"):
            with pytest.raises(ValueError):
                protocol.encode(f, vec["values"], frame_bytes=vec["frame_bytes"])
        else:
            out = protocol.encode(f, vec["values"], frame_bytes=vec["frame_bytes"])
            assert out.hex() == vec["expect_hex"], vec["id"]


def test_every_function_covered():
    """Coverage floor: every function with placed state fields has decode vectors;
    every control function with a pinned frame length has encode vectors."""
    funcs, v = _funcs(), _vectors()
    decode_fns = {x["function"] for x in v["decode"]}
    want = {n for n, f in funcs.items() if any(fl.placed for fl in f.state_fields)}
    assert want <= decode_fns, want - decode_fns
    encode_fns = {x["function"] for x in v["encode"]}
    assert set(overrides.CONTROL_FRAME_BYTES) <= encode_fns


def test_onehot_vectors_expect_all_ones():
    """Non-tautological guard: a one-hot decode vector's hot field must expect
    (1<<width)-1 — derived from the dictionary, not from either codec."""
    funcs, v = _funcs(), _vectors()
    seen = 0
    for vec in v["decode"]:
        parts = vec["id"].split("/")
        if parts[-1] != "onehot":
            continue
        fn, field = parts[0], parts[2]
        w = next(fl.width for fl in funcs[fn].state_fields if fl.name == field)
        assert vec["expect"][field] == (1 << w) - 1, vec["id"]
        seen += 1
    assert seen > 20  # every placed state field across all functions gets one
