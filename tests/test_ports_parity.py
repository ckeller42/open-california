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


# --- anchors ---------------------------------------------------------------------

# Python violation-message prefix -> the C port's stable bitmask ID (csrc/ports.h)
_ANCHOR_ID = {"leisure battery": 1 << 0, "leisure SoC": 1 << 1,
              "cooler level": 1 << 2, "cooler quiet_from": 1 << 3,
              "cooler quiet_to": 1 << 4, "roof position": 1 << 5,
              "vehicle level_roll": 1 << 6, "vehicle level_pitch": 1 << 7}

# flat sweep key -> (states path, needs-installed gate)
_ANCHOR_KEYS = ("batt2_v", "soc2_level", "cooler_installed", "cooler_level",
                "quiet_from", "quiet_to", "roof_installed", "roof_position",
                "level_roll", "level_pitch")


def _anchor_states(case):
    """Flat sweep dict -> the interpreted-states shape anchors.check consumes."""
    st = {"energy": {}, "cooler": {}, "roof": {}, "vehicle": {}}
    for k, v in case.items():
        if k in ("batt2_v", "soc2_level"):
            st["energy"][k] = v
        elif k == "cooler_installed":
            st["cooler"]["installed"] = bool(v)
        elif k in ("cooler_level", "quiet_from", "quiet_to"):
            st["cooler"]["level" if k == "cooler_level" else k] = v
        elif k == "roof_installed":
            st["roof"]["installed"] = bool(v)
        elif k == "roof_position":
            st["roof"]["position"] = v
        else:
            st["vehicle"][k] = v
    return st


def _py_anchor_mask(case):
    from calictl import anchors
    mask = 0
    for msg in anchors.check(_anchor_states(case)):
        mask |= next(v for k, v in _ANCHOR_ID.items() if msg.startswith(k))
    return mask


def _anchor_sweep():
    """Deterministic below/at/mid/at/above sweep per anchor + installed gates +
    combined and all-absent cases. Boundary floats use exactly-representable
    .0/.5 values so float(C) and double(Python) agree on every verdict."""
    cases = []
    for k, lo, hi, gate in (("batt2_v", 8.0, 16.0, None),
                            ("soc2_level", 0, 15, None),
                            ("cooler_level", 1, 5, {"cooler_installed": 1}),
                            ("quiet_from", 0, 23, {"cooler_installed": 1}),
                            ("quiet_to", 0, 23, {"cooler_installed": 1}),
                            ("roof_position", 0, 15, {"roof_installed": 1})):
        step = 0.5 if isinstance(lo, float) else 1
        for v in (lo - step, lo, (lo + hi) / 2 if isinstance(lo, float) else (lo + hi) // 2,
                  hi, hi + step):
            cases.append({k: v, **(gate or {})})
    for k in ("level_roll", "level_pitch"):
        for v in (-95.5, -90.0, 0.0, 90.0, 90.5):
            cases.append({k: v})
    # installed gates OFF: out-of-range values must NOT fire
    cases.append({"cooler_installed": 0, "cooler_level": 9, "quiet_from": 25})
    cases.append({"roof_installed": 0, "roof_position": 20})
    # combined multi-violation + all-absent
    cases.append({"batt2_v": 3.0, "soc2_level": 99, "cooler_installed": 1,
                  "cooler_level": 0, "quiet_to": 30, "roof_installed": 1,
                  "roof_position": 16, "level_roll": 180.0, "level_pitch": -180.0})
    cases.append({})
    return cases


def _a_line(case):
    kv = " ".join("%s=%s" % (k, case[k]) for k in _ANCHOR_KEYS if k in case)
    return ("A " + kv).rstrip()


def test_anchors_c_port_parity(codec_cli):
    """Differential sweep: Python anchors.check (the oracle, mapped to stable IDs)
    must equal the C port's violation bitmask on every case.

    .. test:: Plausibility-anchor C port matches the Python original
       :id: T_PORT_ANCHORS_PARITY
       :links: R_PORT_ANCHORS
    """
    cases = _anchor_sweep()
    fired = 0
    for case, out in zip(cases, codec_cli([_a_line(c) for c in cases])):
        py = _py_anchor_mask(case)
        assert out == "OK %d" % py, (case, out, py)
        fired += bool(py)
    assert fired >= 15  # the sweep genuinely exercises violations (17 today)


# --- roof SafetyCounter ----------------------------------------------------------

def _counter_cases():
    return json.loads((VDIR / "safety_counter.json").read_text())["cases"]


def test_safety_counter_vectors_pass_python_oracle():
    """Hand-derived tick timelines (incl. the 2^32 wrap and a 49-day elapsed) must
    match the Python original exactly.

    .. test:: SafetyCounter formula vectors pass the Python original
       :id: T_PORT_SAFETY_COUNTER_PARITY
       :links: R_PORT_SAFETY_COUNTER
    """
    from calictl import device
    for c in _counter_cases():
        for t, exp, beat in zip(c["timeline_ms"], c["expect"], c["expect_beat_hex"]):
            got = device._roof_safety_counter(c["seed"], float(t), c["tick_ms"])
            assert got == exp, (c["id"], t)
            assert device._beat_bytes(got).hex() == beat, (c["id"], t)


def test_safety_counter_c_port_parity(codec_cli):
    lines, flat = [], []
    for c in _counter_cases():
        for t, exp, beat in zip(c["timeline_ms"], c["expect"], c["expect_beat_hex"]):
            lines.append("C %d %d %d" % (c["seed"], c["tick_ms"], t))
            flat.append((c["id"], t, exp, beat))
    for (cid, t, exp, beat), out in zip(flat, codec_cli(lines)):
        assert out == "OK %d %s" % (exp, beat), (cid, t, out)


def test_safety_counter_delta_invariant_both_sides(codec_cli):
    """App invariant: at the 500 ms pump cadence consecutive counters step exactly
    +1; at a faster cadence deltas are only 0 or +1, never +2 — asserted on the
    Python and C sequences alike (and the two sequences are identical)."""
    from calictl import device
    seed, tick = 424242, 500
    for period, allowed in ((500, {1}), (250, {0, 1})):
        ts = [i * period for i in range(60)]
        py = [device._roof_safety_counter(seed, float(t), tick) for t in ts]
        c_out = codec_cli(["C %d %d %d" % (seed, tick, t) for t in ts])
        c = [int(line.split()[1]) for line in c_out]
        assert c == py
        deltas = {b - a for a, b in zip(py, py[1:])}
        assert deltas <= allowed, (period, deltas)
