"""Assess whether the camper's battery readings can be trusted over a long period.

Pulls the leisure + starter battery series (and the unit's own ``AgeOneBattValuesMinutes``) from
InfluxDB and reports, over N days:

* how often each reading actually CHANGES vs stays frozen (a latched/stale value never moves);
* the longest frozen run per field (the smoking gun for an unreliable, stuck reading);
* polling GAPS — stretches with no sample, i.e. the unit deep-slept and every value latched;
* how often the unit itself reports its battery data stale (``age_min`` at the 255 sentinel).

It ends with a per-field VERDICT (trust / caution / stale) so you can decide what to rely on and
how to flag outdated values.

Run::

    python3 -m tools.analyze_battery [--days 7] [--gap-min 10] [--every 1m]

InfluxDB connection is read from the same env as :mod:`calictl.influx`
(``INFLUXDB_TOKEN`` / ``INFLUX_URL`` / ``INFLUX_ORG`` / ``INFLUX_BUCKET``). With no token / no
Influx it prints a clear "no data" message instead of failing.

The metric functions below are pure (``series`` = ``[(epoch_s, value)]`` ascending) so they are
unit-tested without a database.
"""
from __future__ import annotations

import argparse

# The leisure battery is the one you live off; the starter is engine-on-only. age_min is the unit's
# OWN staleness signal for the starter values (255 = subsystem asleep, data is last-known).
FIELDS = [
    ("soc2_pct", "Leisure SoC %"),
    ("soc2_level", "Leisure SoC level (0-15)"),
    ("batt2_v", "Leisure voltage V"),
    ("batt2_current", "Leisure current A"),
    ("soc1_pct", "Starter SoC %"),
    ("batt1_v", "Starter voltage V"),
    ("age_min", "Unit's battery-values age (min)"),
]
AGE_STALE_SENTINEL = 255


def frozen_runs(series):
    """``(start_ts, end_ts, value, duration_s)`` for each maximal run of an unchanged value.

    A single isolated sample is a zero-duration run. Consecutive equal values form one run spanning
    first→last sample, so ``duration_s`` is how long the reading sat frozen at that value.
    """
    if not series:
        return []
    runs = []
    start_ts, val, last_ts = series[0][0], series[0][1], series[0][0]
    for ts, v in series[1:]:
        if v == val:
            last_ts = ts
        else:
            runs.append((start_ts, last_ts, val, last_ts - start_ts))
            start_ts, val, last_ts = ts, v, ts
    runs.append((start_ts, last_ts, val, last_ts - start_ts))
    return runs


def summarize_field(series):
    """Reliability metrics for one field's series."""
    if not series:
        return {"n": 0}
    vals = [v for _, v in series]
    changes = sum(1 for i in range(1, len(series)) if series[i][1] != series[i - 1][1])
    runs = frozen_runs(series)
    longest = max((d for *_, d in runs), default=0)
    span = series[-1][0] - series[0][0]
    return {
        "n": len(series),
        "span_s": span,
        "changes": changes,
        "change_frac": changes / (len(series) - 1) if len(series) > 1 else 0.0,
        "longest_frozen_s": longest,
        "min": min(vals),
        "max": max(vals),
    }


def polling_gaps(timestamps, gap_s):
    """``(start_ts, end_ts, duration_s)`` for each inter-sample gap longer than ``gap_s``."""
    gaps = []
    for a, b in zip(timestamps, timestamps[1:]):
        if b - a > gap_s:
            gaps.append((a, b, b - a))
    return gaps


def age_summary(age_series, stale_at=AGE_STALE_SENTINEL):
    """How often the unit itself reported its battery data stale (age at the 255 sentinel)."""
    if not age_series:
        return {"n": 0}
    stale = [1 if v >= stale_at else 0 for _, v in age_series]
    fresh_vals = [v for _, v in age_series if v < stale_at]
    # longest consecutive stale run (in seconds)
    longest, cur_start = 0, None
    for (ts, v) in age_series:
        if v >= stale_at:
            cur_start = ts if cur_start is None else cur_start
            longest = max(longest, ts - cur_start)
        else:
            cur_start = None
    return {
        "n": len(age_series),
        "stale_frac": sum(stale) / len(stale),
        "longest_stale_s": longest,
        "max_fresh_age_min": max(fresh_vals) if fresh_vals else None,
    }


def verdict(name, s, age_stale_frac=None):
    """A one-line trust judgement for a field."""
    if not s or s.get("n", 0) < 3:
        return f"{name}: insufficient data ({s.get('n', 0)} samples)"
    hrs = s["longest_frozen_s"] / 3600
    tag = ("STALE-RISK" if s["change_frac"] < 0.02 or hrs > 24 else
           "CAUTION" if s["change_frac"] < 0.1 or hrs > 8 else "TRUSTWORTHY")
    extra = ""
    if name.startswith("age_min") and age_stale_frac is not None:
        extra = f"; unit-reported stale {age_stale_frac * 100:.0f}% of the time"
    return (f"{name}: {tag} — {s['changes']} changes / {s['n']} samples over "
            f"{s['span_s'] / 86400:.1f}d, longest frozen {hrs:.1f}h, range {s['min']}..{s['max']}{extra}")


def _fmt_dur(sec):
    h = sec / 3600
    return f"{h:.1f}h" if h >= 1 else f"{sec / 60:.0f}m"


def analyze(days=7.0, every="1m", gap_min=10.0):
    """Query InfluxDB and print the reliability report. Returns the per-field summary dict."""
    from calictl import influx
    # A reference series (leisure %) drives the polling-gap analysis (shared clock for all fields).
    ref = influx.field_series("soc2_pct", "energy", days=days, every=every, fn="last")
    print(f"=== Battery reliability over {days:g} days (every {every}, aggregate=last) ===")
    if not ref:
        print("No InfluxDB data (token unset, Influx unreachable, or empty bucket). "
              "Run on buspi with /etc/buspi/*.env sourced.")
        return {}
    gaps = polling_gaps([t for t, _ in ref], gap_min * 60)
    total_gap = sum(d for *_, d in gaps)
    print(f"Polling: {len(ref)} windows; {len(gaps)} deep-sleep gaps >{gap_min:g}m "
          f"totalling {_fmt_dur(total_gap)} ({total_gap / (days * 864):.0f}% of the window).")
    if gaps:
        longest = max(gaps, key=lambda g: g[2])
        print(f"  longest gap: {_fmt_dur(longest[2])}")
    print()

    age = influx.field_series("age_min", "energy", days=days, every=every, fn="last")
    a = age_summary(age)
    summaries = {}
    for field, label in FIELDS:
        series = age if field == "age_min" else influx.field_series(field, "energy", days=days,
                                                                    every=every, fn="last")
        s = summarize_field(series)
        summaries[field] = s
        print(verdict(f"{label} [{field}]", s,
                      age_stale_frac=a.get("stale_frac") if field == "age_min" else None))
    if a.get("n"):
        print(f"\nUnit's own staleness (age_min): stale {a['stale_frac'] * 100:.0f}% of samples; "
              f"longest stale stretch {_fmt_dur(a['longest_stale_s'])}; "
              f"max fresh age reported {a['max_fresh_age_min']} min.")
    return summaries


def main(argv=None):
    p = argparse.ArgumentParser(description="Assess camper battery-reading reliability from InfluxDB.")
    p.add_argument("--days", type=float, default=7.0, help="look-back window (days)")
    p.add_argument("--every", default="1m", help="aggregate window (Flux duration, e.g. 1m/5m)")
    p.add_argument("--gap-min", type=float, default=10.0, help="minutes with no sample = a sleep gap")
    args = p.parse_args(argv)
    analyze(days=args.days, every=args.every, gap_min=args.gap_min)


if __name__ == "__main__":
    main()
