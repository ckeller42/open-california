"""Python ⇄ C parity for the portable decision-logic ports (issue #156).

Beyond the stateless frame codec, a few pure calictl decisions are shared with the
ESP32 port (#154) as C in ``csrc/ports.c``: the water stale-latch guard and its
ramp debounce (the ESP has no roof and no anchors, so those stay Python-only). Each has a
vector file under ``tests/vectors/`` that is first proven against the Python
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

    return "F %s %s %s %s" % (
        s(c["new"]["fresh"]),
        s(c["prev"]["fresh"]),
        s(c["new"]["waste"]),
        s(c["prev"]["waste"]),
    )


def test_freshness_c_port_parity(codec_cli):
    for c, out in zip(_fresh_cases(), codec_cli([_f_line(c) for c in _fresh_cases()])):
        assert out == "OK %d" % int(c["expect"]), (c["id"], out)


def _seq_cases():
    return json.loads((VDIR / "freshness.json").read_text())["sequences"]


def test_settle_vectors_pass_python_oracle():
    """The ramp-debounce sequences as ``freshness.settle_water`` decides them (ms clock, 5000 ms).

    .. test:: Freshness ramp-debounce sequences pass the Python original
       :id: T_PORT_FRESHNESS_SETTLE_PARITY
       :links: R_PORT_FRESHNESS, R_WATER_RAMP_DEBOUNCE
    """
    for seq in _seq_cases():
        good, pend, got = seq["good"], None, []
        for t, fresh, waste, _ in seq["steps"]:
            new = {"fresh": fresh, "waste": waste}
            adopt, pend = freshness.settle_water(
                _water(new), _water(good) if good else None, pend, t, freshness.WATER_SETTLE_S * 1000
            )
            good = new if adopt else good  # the baseline follows every adopt, as serve/ESP do
            got.append(int(adopt))
        assert got == [st[3] for st in seq["steps"]], seq["id"]


def test_settle_c_port_parity(codec_cli):
    def s(v):
        return "-" if v is None else str(v)

    for seq in _seq_cases():
        out = codec_cli(
            ["W reset"]
            + [
                "W %d %s %s %s %s" % (t, s(f), s(g["fresh"]) if g else "-", s(w), s(g["waste"]) if g else "-")
                for (t, f, w, _), g in zip(seq["steps"], _goods(seq))
            ]
        )
        assert out == ["OK"] + ["OK %d" % st[3] for st in seq["steps"]], seq["id"]


def _goods(seq):
    """The baseline in force at each step, given the vector's own adopt expectations."""
    good, goods = seq["good"], []
    for _, fresh, waste, expect in seq["steps"]:
        goods.append(good)
        if expect:
            good = {"fresh": fresh, "waste": waste}
    return goods
