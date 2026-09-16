#!/usr/bin/env python3
"""Replay a REAL unit's BLE trace (``CALICTL_BLE_TRACE`` JSONL, see :mod:`calictl.trace`) through
the mock and report where ``tools/mock_unit.py`` differs from the hardware.

    python3 -m tools.trace_compare ~/ble.jsonl            # human report
    python3 -m tools.trace_compare ~/ble.jsonl --json     # machine-readable

Checks (each a section of the report):

* **round-trip** — every state frame the unit pushed/read must re-pack byte-for-byte from its
  dictionary decode (otherwise the dictionary misses bits the unit uses, and the mock would serve
  a different frame than the van);
* **cadence** — per state char: notifications per minute + median interval, against the mock's
  push model (energy every tick; others on change);
* **dynamics** — rates the mock's clock model assumes vs what the unit did: heater
  ``RunningTimeinAction`` per minute while ``NormalOperation``, energy ``AgeOneBattValuesMinutes``
  per minute while the ignition is off, roof ``Position`` transitions, the ignition→camping
  coupling (``TerminalOneFive`` edge followed by ``campingmode.Enable``/``State``);
* **writes** — calictl's own frames seen in the trace, decoded, for eyeballing against the app
  frames tabled in ``docs/business-logic/protocol-crosscheck-applab.md``.

The trace is evidence, the mock is a model: a difference here is a mock bug or a new protocol fact,
never a reason to edit the trace.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict

from calictl import overrides, protocol, trace
from tools.mock_unit import _pack_state


def load(path: str) -> list[dict]:
    return [e for e in trace.read_events(path)]


def round_trip(events, funcs) -> dict:
    """{fn: {"frames": n, "mismatch": [(hex, repacked_hex)]}} for state frames."""
    out: dict[str, dict] = {}
    for e in events:
        if e.get("ev") not in ("notify", "read") or not e.get("fn") or e["fn"] not in funcs:
            continue
        f = funcs[e["fn"]]
        if trace.char_short(f.state_char) != e["char"]:
            continue
        raw = bytes.fromhex(e["hex"])
        rep = _pack_state(f, protocol.decode(f, raw))
        d = out.setdefault(e["fn"], {"frames": 0, "mismatch": []})
        d["frames"] += 1
        if rep != raw and (raw.hex(), rep.hex()) not in d["mismatch"]:
            d["mismatch"].append((raw.hex(), rep.hex()))
    return out


def cadence(events) -> dict:
    """{char: {"n": notifications, "per_min": …, "median_s": …}} over the trace's span."""
    times: dict[str, list[float]] = defaultdict(list)
    for e in events:
        if e.get("ev") == "notify":
            times[e["char"]].append(e["t"])
    out = {}
    for ch, ts in times.items():
        ts.sort()
        span = (ts[-1] - ts[0]) if len(ts) > 1 else 0.0
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        out[ch] = {"n": len(ts), "per_min": round(60 * (len(ts) - 1) / span, 2) if span else None,
                   "median_s": round(statistics.median(gaps), 3) if gaps else None}
    return out


def _series(events, funcs, fn, field):
    """[(t, value)] of a decoded state field from every notify/read of that function."""
    f = funcs[fn]
    short = trace.char_short(f.state_char)
    out = []
    for e in events:
        if e.get("ev") in ("notify", "read") and e.get("char") == short:
            v = protocol.decode(f, bytes.fromhex(e["hex"])).get(field)
            if v is not None:
                out.append((e["t"], v))
    return out


def _rate_per_minute(series, only_while=None):
    """Average change per minute over the stretches where ``only_while(value_dict)`` holds."""
    pts = [(t, v) for t, v in series if v is not None]
    if len(pts) < 2:
        return None
    total_dv, total_dt = 0.0, 0.0
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if v1 != v0 and t1 > t0:
            total_dv += v1 - v0
            total_dt += t1 - t0
    return round(60.0 * total_dv / total_dt, 3) if total_dt else None


def dynamics(events, funcs) -> dict:
    out: dict[str, object] = {}
    if "airheater" in funcs:
        rem = _series(events, funcs, "airheater", "RunningTimeinAction")
        out["heater_remaining_per_min"] = {"observed": _rate_per_minute(rem), "mock": -1.0}
    if "energy" in funcs:
        age = _series(events, funcs, "energy", "AgeOneBattValuesMinutes")
        out["battery_age_per_min"] = {"observed": _rate_per_minute(age), "mock": 1.0}
    if "roof" in funcs:
        pos = _series(events, funcs, "roof", "Position")
        trans = [(round(t1 - t0, 1), a, b) for (t0, a), (t1, b) in zip(pos, pos[1:]) if a != b]
        out["roof_position_transitions"] = trans[:50]
    if "vehicle" in funcs and "campingmode" in funcs:
        ign = _series(events, funcs, "vehicle", "TerminalOneFive")
        en = _series(events, funcs, "campingmode", "Enable")
        edges = [(t, v) for (t0, v0), (t, v) in zip(ign, ign[1:]) if v != v0]
        coupling = []
        for t, v in edges:
            after = [(t2 - t, e2) for t2, e2 in en if t2 >= t]
            coupling.append({"ignition": v, "enable_after": after[:2]})
        out["ignition_to_camping_enable"] = coupling
    return out


def writes(events, funcs) -> list[dict]:
    out = []
    for e in events:
        if e.get("ev") == "write" and e.get("fn") in funcs and e["char"] != "1003":
            f = funcs[e["fn"]]
            try:
                from calictl import control
                dec = control.decode_control(f, bytes.fromhex(e["hex"]))
            except Exception:  # noqa: BLE001
                dec = None
            out.append({"t": e["t"], "fn": e["fn"], "hex": e["hex"], "decoded": dec})
    return out


def report(path: str) -> dict:
    funcs = protocol.load()
    overrides.apply(funcs)
    events = load(path)
    return {"events": len(events),
            "span_s": round(events[-1]["t"] - events[0]["t"], 1) if len(events) > 1 else 0,
            "round_trip": round_trip(events, funcs),
            "cadence": cadence(events),
            "dynamics": dynamics(events, funcs),
            "writes": writes(events, funcs)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trace")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = report(a.trace)
    if a.json:
        print(json.dumps(r, indent=1, default=str))
        return 0
    print(f"{r['events']} events over {r['span_s']} s")
    print("\nround-trip (unit frame -> dictionary decode -> repack):")
    for fn, d in sorted(r["round_trip"].items()):
        flag = "OK " if not d["mismatch"] else "MISMATCH"
        print(f"  {flag:9s} {fn:22s} {d['frames']} frames" + (f"  e.g. {d['mismatch'][0][0]} -> {d['mismatch'][0][1]}" if d["mismatch"] else ""))
    print("\nnotification cadence:")
    for ch, c in sorted(r["cadence"].items()):
        print(f"  {ch}  n={c['n']:5d}  per_min={c['per_min']}  median_gap={c['median_s']} s")
    print("\ndynamics (observed vs mock model):")
    for k, v in r["dynamics"].items():
        print(f"  {k}: {v}")
    print(f"\ncalictl writes in trace: {len(r['writes'])}")
    for w in r["writes"][:20]:
        print(f"  {w['fn']:12s} {w['hex']}  {w['decoded']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
