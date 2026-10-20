#!/usr/bin/env python3
"""BLE power experiment report: one row per phase from the ESP's SD log (``R_BLE_EXP_REPORT``).

Usage: ``python3 tools/esplab/ble_exp_report.py --url http://calictl-esp.local [--since SEQ]
[--volt-alias ALIAS]``.

.. req:: BLE experiment report
   :id: R_BLE_EXP_REPORT
   :status: open

   Per ``ble_phase`` record: mode, aborted, minutes, minutes the unit's advert was seen
   (``unit_adv``; at phase start the last earlier record's ``seen``, none = unknown = not counted —
   the firmware starts each experiment unknown and logs the first sighting), the last energy
   reading after the last ok ``probe`` (``None`` + ``probe`` ``fail`` when it failed), the
   battery-sense voltage slope (least squares, mV/h) and the last cooler / air-heater state.
   Voltage comes from the first ``ext`` record of type ``victron_battery`` (its ``battery_voltage``),
   or the alias given with ``--volt-alias``.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

EVT_KINDS = ("ble_mode", "ble_phase", "probe", "unit_adv")


def _slope_mv_h(pts):
    """Least-squares slope of ``[(ts_ms, volts)]`` in mV/h; None with < 2 distinct times."""
    n = len(pts)
    if n < 2:
        return None
    mt = sum(t for t, _ in pts) / n
    mv = sum(v for _, v in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    if den == 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in pts) / den * 3_600_000 * 1000


def _last(evs, kind, key):
    return next((x.get(key) for _, k, x in reversed(evs) if k == kind), None)


def _mean(rs, key):
    xs = [r[key] for r in rs if r[key] is not None]
    return sum(xs) / len(xs) if xs else None


def phases(events):
    """One dict per ``ble_phase`` event.

    :param events: ``(ts_ms, kind, data)`` tuples; kinds ``ble_mode``, ``ble_phase``, ``probe``,
        ``unit_adv``, ``energy``, ``cooler``, ``airheater``, ``volt`` (float V).
    :returns: rows with ``mode``, ``aborted``, ``minutes``, ``adv_minutes``, ``probe`` (``ok`` /
        ``fail`` / None), ``batt2_current``, ``soc2_pct``, ``batt2_v``, ``mv_per_h``,
        ``cooler_on``, ``heater_on``.
    """
    ev = sorted(events, key=lambda e: e[0])  # stable: same-ts records keep log order
    rows, prev_end = [], None
    for i, (end, kind, d) in enumerate(ev):
        if kind != "ble_phase":
            continue
        before = ev[:i]
        start = next(
            (
                t
                for t, k, x in reversed(before)
                if k == "ble_mode" and x.get("mode") == d.get("mode") and (prev_end is None or t >= prev_end)
            ),
            prev_end,
        )
        if start is None:
            start = ev[0][0]
        if d.get("dur_ms") is not None:  # the firmware's own span (a hold start logs no ble_mode)
            start = end - int(d["dur_ms"])
        prev_end = end
        seen = next((x.get("seen") for t, k, x in reversed(before) if k == "unit_adv" and t < start), False)
        adv, cur = 0, start
        for t, k, x in ev[:i]:
            if k == "unit_adv" and start <= t:
                adv += (t - cur) if seen else 0
                cur, seen = t, bool(x.get("seen"))
        adv += (end - cur) if seen else 0
        inph = [e for e in before if e[0] >= start]
        probes = [e for e in inph if e[1] == "probe"]
        probe = None if not probes else ("ok" if probes[-1][2].get("ok") else "fail")
        floor = next((t for t, k, x in reversed(probes) if x.get("ok")), start)
        en = {}
        if probe != "fail":
            en = next((x for t, k, x in reversed(inph) if k == "energy" and t >= floor), {})

        rows.append(
            {
                "mode": d.get("mode"),
                "aborted": bool(d.get("aborted")),
                "minutes": round((end - start) / 60_000),
                "adv_minutes": round(adv / 60_000),
                "probe": probe,
                "batt2_current": en.get("batt2_current"),
                "soc2_pct": en.get("soc2_pct"),
                "batt2_v": en.get("batt2_v"),
                "mv_per_h": _slope_mv_h([(t, x) for t, k, x in inph if k == "volt"]),
                "cooler_on": _last(inph, "cooler", "power"),
                "heater_on": _last(inph, "airheater", "running"),
            }
        )
    return rows


def _fmt(v):
    return "-" if v is None else ("%.2f" % v if isinstance(v, float) else str(v))


def table(rows):
    """Fixed-width table of ``rows`` plus a per-mode mean of current and slope."""
    cols = (
        "mode",
        "aborted",
        "minutes",
        "adv_minutes",
        "probe",
        "batt2_current",
        "soc2_pct",
        "batt2_v",
        "mv_per_h",
        "cooler_on",
        "heater_on",
    )
    cells = [cols] + [tuple(_fmt(r[c]) for c in cols) for r in rows]
    w = [max(len(c[i]) for c in cells) for i in range(len(cols))]
    out = ["  ".join(c.ljust(n) for c, n in zip(line, w)) for line in cells]
    for m in sorted({r["mode"] for r in rows}):
        rs = [r for r in rows if r["mode"] == m]
        out.append(
            "mean %s (n=%d): batt2_current %s  mv_per_h %s"
            % (m, len(rs), _fmt(_mean(rs, "batt2_current")), _fmt(_mean(rs, "mv_per_h")))
        )
    return "\n".join(out)


def normalize(done, replay, volt_alias=None):
    """Timed log records ``[(rec, ts_ms | None)]`` -> ``(ts_ms, kind, data)`` events.

    The ``cooler`` interp's ``on`` is reported as ``power``; the voltage alias is ``volt_alias`` or,
    by default, the first ``ext`` alias of type ``victron_battery``.
    """
    out = []
    for rec, ts in done:
        if ts is None:
            continue
        if rec.get("ev") == "evt" and rec.get("kind") in EVT_KINDS:
            out.append((ts, rec["kind"], rec))
        elif rec.get("ev") == "ext" and isinstance(rec.get("battery_voltage"), (int, float)):
            if (rec.get("dev") == volt_alias) if volt_alias else (rec.get("type") == "victron_battery"):
                volt_alias = rec["dev"]
                out.append((ts, "volt", float(rec["battery_voltage"])))
        else:
            for fn, i in replay.process(rec, ts).items():
                if fn == "energy":
                    out.append((ts, fn, i))
                elif fn == "cooler":
                    out.append((ts, fn, {"power": i.get("on")}))
                elif fn == "airheater":
                    out.append((ts, fn, {"running": i.get("running")}))
    return out


def main(argv=None):
    from calictl import esp_ingest as ei

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="the ESP's base URL")
    ap.add_argument("--since", type=int, default=0, help="first seq to read after (default 0)")
    ap.add_argument("--volt-alias", help="ext alias carrying the battery voltage")
    a = ap.parse_args(argv)
    fetch, since, buf, trailer = ei.http_fetch(a.url), a.since, [], {"more": True}
    while trailer["more"]:
        recs, trailer = fetch(since, 2000)
        buf += recs
        since = trailer["next"]
    done, _held = ei.resolve(buf, trailer["boot"], True)
    events = normalize(done, ei.Replay(ei._funcs()), a.volt_alias)
    print(table(phases(events)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
