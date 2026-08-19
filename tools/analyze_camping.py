"""Characterise how CAMPING MODE behaves around an engine start — the evidence needed before
enabling auto-camper mode.

Pulls the ignition / DC-DC-charging / camping-substate series from InfluxDB and, for each engine
start, reports how camping mode moved and whether it FLIP-FLOPPED. The daemon's observer
(:meth:`calictl.serve.Server._observe_transitions`) bursts the poll rate to ~3 s around an engine
edge, so the InfluxDB series is fine-grained exactly here — this tool reads that back.

Two "engine on" signals, because they differ:

* ``ignition_on`` — Terminal-15 (the ignition switch; can be on with the engine NOT running);
* ``dcdc_charging`` — DC-DC converter active = alternator feeding the leisure battery = the engine
  actually RUNNING.

An engine start = a rising edge of EITHER. For each, the tool prints the camping master/USB/lights
transitions in a window around it, tagged with Δ-since-edge, and FLAGS a flip-flop (≥2 camping
``master_on`` transitions inside the post-edge window) — the behaviour that would make auto-camper
fight the unit.

Run::

    python3 -m tools.analyze_camping [--days 3] [--every 10s] [--pre 60] [--post 300] [--flip-window 300]

InfluxDB connection comes from the same env as :mod:`calictl.influx` (``INFLUXDB_TOKEN`` /
``INFLUX_URL`` / ``INFLUX_ORG`` / ``INFLUX_BUCKET``); with no token it prints a clear "no data"
message instead of failing. On buspi, source ``/etc/buspi/*.env`` first.

The analysis functions are pure (they take ``{field: [(epoch_s, value)]}``) so they are unit-tested
without a database — see ``tests/test_analyze_camping.py``.
"""
from __future__ import annotations

import argparse
import time

# (influx field key, function tag). ignition + dcdc_charging are the two engine-on signals; the
# camping substates are what we watch move; the rest is context printed on each transition.
SERIES = [
    ("ignition_on", "vehicle"),
    ("dcdc_charging", "energy"),
    ("master_on", "campingmode"),
    ("usb_charger", "campingmode"),
    ("lights_on", "campingmode"),
    ("soc2_pct", "energy"),
    ("dcdc_current", "energy"),
    ("batt2_current", "energy"),
]
ENGINE_SIGNALS = ("ignition_on", "dcdc_charging")   # a rising edge of either = an engine start
CAMPING_SUBSTATES = ("master_on", "usb_charger", "lights_on")
_MISSING = object()


def merge(named: dict) -> list:
    """Flatten ``{field: [(ts, val)]}`` into one ``(ts, field, val)`` list, ascending by (ts, field)."""
    evs = [(ts, f, v) for f, series in named.items() for ts, v in series]
    evs.sort(key=lambda e: (e[0], e[1]))
    return evs


def transitions(named: dict, track) -> list:
    """``(ts, field, old, new)`` for each change in a tracked field, on the merged clock.

    ``old`` is ``None`` for a field's first-ever sample (no prior value to compare). Only fields in
    ``track`` are reported; the others still advance the clock but never emit a transition.
    """
    state = {}
    out = []
    for ts, f, v in merge(named):
        if f in track:
            old = state.get(f, _MISSING)
            if old is not _MISSING and old != v:
                out.append((ts, f, old, v))
        state[f] = v
    return out


def engine_starts(named: dict) -> list:
    """Timestamps of engine-start edges: a 0->1 (falsy->truthy) transition of ignition OR dcdc_charging.

    Returns ``[(ts, signal)]`` ascending; simultaneous edges of both signals yield one entry per
    signal (deduped to the earliest per timestamp is left to the caller — usually harmless)."""
    edges = []
    for ts, f, old, new in transitions(named, ENGINE_SIGNALS):
        if not _truthy(old) and _truthy(new):
            edges.append((ts, f))
    return edges


def value_at(series, ts):
    """Last sampled value at or before ``ts`` (forward-fill), or ``None`` if none precede it."""
    out = None
    for t, v in series:
        if t <= ts:
            out = v
        else:
            break
    return out


def flip_flops(named: dict, edge_ts, window_s) -> int:
    """Count camping ``master_on`` transitions in ``[edge_ts, edge_ts + window_s]``.

    ``>= 2`` means camping went off→on→… (or on→off→…) more than once after the engine start — a
    flip-flop, the behaviour that would make an auto-re-enable fight the unit."""
    return sum(1 for ts, f, _o, _n in transitions(named, ("master_on",))
               if f == "master_on" and edge_ts <= ts <= edge_ts + window_s)


def _truthy(v):
    return bool(v) and v != _MISSING


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def analyze(days=3.0, every="10s", pre=60.0, post=300.0, flip_window=300.0):
    """Query InfluxDB and print the engine-start / camping report. Returns the list of events."""
    from calictl import influx
    named = {field: influx.field_series(field, fn_tag, days=days, every=every, fn="last")
             for field, fn_tag in SERIES}
    print(f"=== Camping-vs-engine-start over {days:g} days (every {every}, aggregate=last) ===")
    if not any(named.values()):
        print("No InfluxDB data (token unset, Influx unreachable, or empty bucket). "
              "Run on buspi with /etc/buspi/*.env sourced.")
        return []
    edges = engine_starts(named)
    if not edges:
        print("No engine-start edges (ignition/dcdc_charging never rose 0->1) in the window.")
        return []
    trans = transitions(named, CAMPING_SUBSTATES + ENGINE_SIGNALS)
    events = []
    print(f"{len(edges)} engine-start edge(s):\n")
    for edge_ts, signal in edges:
        flips = flip_flops(named, edge_ts, flip_window)
        verdict = "FLIP-FLOP" if flips >= 2 else ("clean" if flips <= 1 else "check")
        when = time.strftime("%a %m-%d %H:%M:%S", time.localtime(edge_ts))
        soc = value_at(named.get("soc2_pct", []), edge_ts)
        print(f"● {when}  engine-start via {signal}  soc={_fmt(soc)}  "
              f"-> master_on transitions in +{flip_window:g}s: {flips} [{verdict}]")
        # every camping/engine transition in the [-pre, +post] window, tagged Δ-since-edge
        for ts, f, old, new in trans:
            if edge_ts - pre <= ts <= edge_ts + post:
                dt = ts - edge_ts
                print(f"    {dt:+6.0f}s  {f}: {_fmt(old)}->{_fmt(new)}")
        events.append({"ts": edge_ts, "signal": signal, "flips": flips, "verdict": verdict})
        print()
    ff = [e for e in events if e["flips"] >= 2]
    print("SUMMARY: %d engine start(s); %d showed a camping flip-flop (>=2 master transitions)."
          % (len(events), len(ff)))
    if not ff:
        print("No flip-flop observed: camping mode settled after each engine start. "
              "Auto-camper's guards (window + max-fails) would not thrash here.")
    else:
        print("Flip-flop(s) observed — the unit toggled camping repeatedly after an engine start. "
              "Review before enabling auto-camper; its max-fails cap bounds it but it would still fight.")
    return events


def main(argv=None):
    p = argparse.ArgumentParser(description="Characterise camping mode around engine starts from InfluxDB.")
    p.add_argument("--days", type=float, default=3.0, help="look-back window (days)")
    p.add_argument("--every", default="10s", help="aggregate window (Flux duration, e.g. 5s/10s/30s)")
    p.add_argument("--pre", type=float, default=60.0, help="seconds before each edge to print")
    p.add_argument("--post", type=float, default=300.0, help="seconds after each edge to print")
    p.add_argument("--flip-window", type=float, default=300.0,
                   help="seconds after an edge within which >=2 master transitions = flip-flop")
    a = p.parse_args(argv)
    analyze(days=a.days, every=a.every, pre=a.pre, post=a.post, flip_window=a.flip_window)


if __name__ == "__main__":
    main()
