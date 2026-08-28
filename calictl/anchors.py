"""Plausibility anchors — cheap invariants that catch a SILENTLY-WRONG decode.

A firmware update that shifts a field offset (or changes a scale) usually pushes some value out of
its physically-possible range — the way the DC-DC current went implausible when its scaling drifted.
These anchors assert a handful of such ranges so a decode regression trips an alarm even when nobody
noticed the firmware changed. A violation is a strong hint to look at :mod:`firmware`'s drift capture.

.. req:: Flag implausible decoded values (decode-drift alarm)
   :id: R_PLAUSIBILITY_ANCHORS
   :status: implemented
   :tags: firmware, diagnostics

   The daemon shall check a set of physical-range invariants over the interpreted state and surface
   any violation, so a decode drift (e.g. after a firmware change) is caught rather than published
   as truth.

Stdlib-only at import.
"""
from __future__ import annotations


def check(states: dict) -> list:
    """Return a list of human-readable plausibility violations (empty = everything in range).

    Only anchors values that are actually present (a None/missing read is not a violation) and only
    while the relevant subsystem is meaningfully active, to avoid false alarms on parked/sentinel
    reads.
    """
    out = []
    en = states.get("energy") or {}
    v = en.get("batt2_v")
    if isinstance(v, (int, float)) and not (8.0 <= v <= 16.0):
        out.append("leisure battery %.1f V outside 8-16 V" % v)
    soc = en.get("soc2_level")
    if isinstance(soc, (int, float)) and not (0 <= soc <= 15):
        out.append("leisure SoC level %s outside 0-15" % soc)

    c = states.get("cooler") or {}
    if c.get("installed"):
        lvl = c.get("level")
        if isinstance(lvl, (int, float)) and not (1 <= lvl <= 5):
            out.append("cooler level %s outside 1-5" % lvl)
        for k in ("quiet_from", "quiet_to"):
            hr = c.get(k)
            if isinstance(hr, (int, float)) and not (0 <= hr <= 23):
                out.append("cooler %s %s outside 0-23 h" % (k, hr))

    r = states.get("roof") or {}
    if r.get("installed"):
        pos = r.get("position")
        if isinstance(pos, (int, float)) and not (0 <= pos <= 15):
            out.append("roof position %s outside 0-15" % pos)

    veh = states.get("vehicle") or {}
    for k in ("level_roll", "level_pitch"):
        deg = veh.get(k)
        if isinstance(deg, (int, float)) and abs(deg) > 90:
            out.append("vehicle %s %s° outside ±90°" % (k, deg))
    return out
