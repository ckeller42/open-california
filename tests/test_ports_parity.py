"""Python ⇄ C parity for the portable decision-logic ports (issue #156).

Beyond the stateless frame codec, a few pure calictl decisions are shared with the
ESP32 port (#154) as C in ``csrc/ports.c``: the water stale-latch guard, the
plausibility anchors, and the roof SafetyCounter formula. Each has a sequence /
sweep vector file under ``tests/vectors/`` that is first proven against the Python
original (the oracle), then replayed through the batched ``codec_cli`` and asserted
identical.
"""
import json
from pathlib import Path

from calictl import freshness

VDIR = Path(__file__).resolve().parent / "vectors"


def _water(d):
    """Flat vector water {'fresh': x|None, 'waste': y|None} -> the interpreted-dict
    shape freshness.implausible_water_drop consumes."""
    return {"fresh": {"liters": d["fresh"]}, "waste": {"liters": d["waste"]}}


def _fresh_cases():
    return json.loads((VDIR / "freshness.json").read_text())["cases"]


def test_freshness_vectors_pass_python_oracle():
    """Vectors must encode the documented ladder exactly as the Python original
    decides it — on a mismatch fix the vector, not freshness.py.

    .. test:: Freshness stale-latch vectors pass the Python original
       :id: T_PORT_FRESHNESS_PARITY
       :links: R_PORT_FRESHNESS
    """
    for c in _fresh_cases():
        got = freshness.implausible_water_drop(_water(c["new"]), _water(c["prev"]))
        assert got is c["expect"], c["id"]


def _f_line(c):
    def s(v):
        return "-" if v is None else str(v)
    return "F %s %s %s %s" % (s(c["new"]["fresh"]), s(c["prev"]["fresh"]),
                              s(c["new"]["waste"]), s(c["prev"]["waste"]))


def test_freshness_c_port_parity(codec_cli):
    for c, out in zip(_fresh_cases(), codec_cli([_f_line(c) for c in _fresh_cases()])):
        assert out == "OK %d" % int(c["expect"]), (c["id"], out)
