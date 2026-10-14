#!/usr/bin/env python3
"""ESP soak report: 7 criteria over the last N hours of the ESP's diagnostics in InfluxDB (R_SOAK_REPORT).

Runs on buspi under calictl-esp-soak.timer (daily 08:00) with the serve env (INFLUXDB_TOKEN from
/etc/buspi/secrets.env). Prints one line per criterion, ``VERDICT PASS|FAIL``, writes the text to
``--out DIR/<date>.txt`` and one ``esp_soak`` point. Exit 0 PASS, 1 FAIL, 2 cannot run.

.. req:: Soak report
   :id: R_SOAK_REPORT
   :status: open

   Criteria: (1) no reboot with reset_reason in BAD_RESETS; (2) <= 10 link_down events per boot;
   (3) touch NACKs <= 5/h per boot; (4) esp-ingest lost = 0 in every summary, held = 0 in the last;
   (5) heap_min >= 32 KB in every diag sample; (6) screen: SKIP until an evt display record exists;
   (7) i2c.master timeout log lines <= 2 per day. A SKIP never fails the verdict.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

BAD_RESETS = {"panic", "int_wdt", "task_wdt", "wdt", "brownout", "cpu_lockup"}
LINK_DOWN_MAX = 10
NACKS_PER_H_MAX = 5.0
HEAP_MIN_B = 32768
I2C_PER_DAY_MAX = 2
H = 3_600_000.0


@dataclass
class SoakData:
    start_ms: int
    end_ms: int
    boots: list  # [{"t", "reset_reason", "fw"}] sorted by t
    link_down: list  # [{"t"}]
    diag: list  # [{"t", "heap_min", "link_up"}]
    touch: list  # [{"t", "errors"}]
    ingest: object  # [{"lost", "held"}] in journal order, or None = no journal
    i2c_lines: list  # [{"t", "msg"}]
    fw: str
    boot: int


def _intervals(d):
    """Boot intervals [(lo, hi)] from the boot points; the first starts at the window start."""
    edges = [d.start_ms] + [b["t"] for b in d.boots if b["t"] > d.start_ms] + [d.end_ms]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def evaluate(d):
    lines = []
    bad = [b for b in d.boots if b["reset_reason"] in BAD_RESETS]
    reasons = ",".join(b["reset_reason"] for b in d.boots) or "none"
    lines.append(("reboots", "FAIL" if bad else "PASS", "%d boots (%s)" % (len(d.boots), reasons)))

    worst = 0
    for lo, hi in _intervals(d):
        worst = max(worst, sum(1 for e in d.link_down if lo <= e["t"] < hi))
    up = [x["link_up"] for x in d.diag if x.get("link_up") is not None]
    frac = (sum(up) / len(up) * 100.0) if up else 0.0
    lines.append(
        (
            "link",
            "FAIL" if worst > LINK_DOWN_MAX else "PASS",
            "max %d link_down per boot (limit %d); link_up %.1f%% of %d diag samples"
            % (worst, LINK_DOWN_MAX, frac, len(up)),
        )
    )

    worst_rate = 0.0
    for lo, hi in _intervals(d):
        errs = [x["errors"] for x in d.touch if lo <= x["t"] < hi]
        hours = max((hi - lo) / H, 1 / 60.0)
        if errs:
            worst_rate = max(worst_rate, max(errs) / hours)
    lines.append(
        (
            "touch_nacks",
            "FAIL" if worst_rate > NACKS_PER_H_MAX else "PASS",
            "worst boot %.1f/h (limit %.0f/h)" % (worst_rate, NACKS_PER_H_MAX),
        )
    )

    if not d.ingest:
        lines.append(("ingest", "SKIP", "no esp-ingest summary in the journal"))
    else:
        lost = sum(s.get("lost", 0) for s in d.ingest)
        held = d.ingest[-1].get("held", 0)
        lines.append(
            (
                "ingest",
                "FAIL" if lost or held else "PASS",
                "lost %d held %d over %d runs" % (lost, held, len(d.ingest)),
            )
        )

    heaps = [x["heap_min"] for x in d.diag if x.get("heap_min") is not None]
    hmin = min(heaps) if heaps else None
    lines.append(
        (
            "heap_min",
            "SKIP" if hmin is None else ("FAIL" if hmin < HEAP_MIN_B else "PASS"),
            "min %s B (limit %d)" % (hmin, HEAP_MIN_B),
        )
    )

    lines.append(("screen", "SKIP", "no display record (evt display is reserved, firmware.md)"))

    days = max((d.end_ms - d.start_ms) / (24 * H), 1 / 24.0)
    per_day = len(d.i2c_lines) / days
    lines.append(
        (
            "i2c_timeouts",
            "FAIL" if per_day > I2C_PER_DAY_MAX else "PASS",
            "%d lines = %.2f/day (limit %d/day)" % (len(d.i2c_lines), per_day, I2C_PER_DAY_MAX),
        )
    )
    return lines


def verdict(lines):
    return "FAIL" if any(v == "FAIL" for _, v, _ in lines) else "PASS"


def render(d, lines):
    hours = (d.end_ms - d.start_ms) / H
    out = ["soak fw=%s boot=%s window=%.1fh %s..%s" % (d.fw, d.boot, hours, _iso(d.start_ms), _iso(d.end_ms))]
    out += ["%s %s %s" % (v, n, detail) for n, v, detail in lines]
    out.append("VERDICT " + verdict(lines))
    return "\n".join(out) + "\n"


def point(d, lines):
    return {
        "measurement": "esp_soak",
        "tags": {"fw": d.fw},
        "fields": {
            "verdict": verdict(lines),
            "fails": float(sum(v == "FAIL" for _, v, _ in lines)),
            "boot": float(d.boot),
        },
    }


def parse_journal(text):
    """The ``esp-ingest: {...}`` summaries in journalctl output, in order."""
    return [json.loads(m.group(1)) for m in re.finditer(r"esp-ingest: (\{.*\})\s*$", text, re.M)]


def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%MZ")


def _rfc(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _flux(bucket, lo, hi, flt, fields):
    return (
        'from(bucket:"%s") |> range(start:%s, stop:%s) |> filter(fn:(r) => %s) '
        "|> filter(fn:(r) => contains(value:r._field, set:[%s])) "
        '|> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'
        % (bucket, _rfc(lo), _rfc(hi), flt, ",".join('"%s"' % f for f in fields))
    )


def _pull(db, lo, hi):
    """SoakData's Influx halves via esp_ingest.Influx (lazy influxdb_client)."""

    def rows(flt, fields):
        out = []
        for r in db.rows(_flux(db.bucket, lo, hi, flt, fields)):
            v = {"t": int(r.values["_time"].timestamp() * 1000)}
            v.update({f: r.values.get(f) for f in fields})
            out.append(v)
        return sorted(out, key=lambda x: x["t"])

    return dict(
        boots=rows('r._measurement == "esp_evt" and r.kind == "boot"', ["reset_reason", "fw"]),
        link_down=rows('r._measurement == "esp_evt" and r.kind == "link_down"', ["reason"]),
        diag=rows('r._measurement == "esp_diag"', ["heap_min", "link_up"]),
        touch=rows('r._measurement == "esp_evt" and r.kind == "touch"', ["errors"]),
        i2c_lines=[
            x
            for x in rows('r._measurement == "esp_log"', ["msg"])
            if "i2c.master" in (x["msg"] or "") and "timeout" in (x["msg"] or "")
        ],
    )


def _journal(lo):
    try:
        out = subprocess.run(
            [
                "journalctl",
                "-u",
                "calictl-esp-ingest",
                "--since",
                "@%d" % (lo // 1000),
                "-o",
                "short",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_journal(out) or None


def _esp(url):
    try:
        dev = json.load(urllib.request.urlopen(url.rstrip("/") + "/api/state", timeout=10))["device"]
        return dev["fw"], dev["time"]["boot"]
    except Exception as e:  # unreachable ESP: the report still runs on Influx alone
        print("soak: ESP unreachable (%s)" % e, file=sys.stderr)
        return "?", -1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="the ESP, e.g. http://192.168.8.183 (omit with --no-esp)")
    ap.add_argument("--no-esp", action="store_true")
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--since", help="ISO start (UTC), overrides --hours")
    ap.add_argument("--out", help="directory for <date>.txt")
    ap.add_argument("--no-influx-write", action="store_true")
    a = ap.parse_args(argv)
    if not a.url and not a.no_esp:
        ap.error("--url or --no-esp")
    from calictl.esp_ingest import Influx  # lazy: influxdb_client lives in solix-env

    try:
        db = Influx()
    except SystemExit as e:  # no INFLUXDB_TOKEN etc.: cannot run
        print(e, file=sys.stderr)
        return 2
    end = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    start = int(dt.datetime.fromisoformat(a.since).timestamp() * 1000) if a.since else end - int(a.hours * H)
    fw, boot = _esp(a.url) if a.url else ("?", -1)
    d = SoakData(
        start_ms=start, end_ms=end, ingest=_journal(start), fw=fw, boot=boot, **_pull(db, start, end)
    )
    lines = evaluate(d)
    text = render(d, lines)
    sys.stdout.write(text)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, dt.date.today().isoformat() + ".txt"), "w") as f:
            f.write(text)
    if not a.no_influx_write:
        from influxdb_client import Point, WritePrecision

        p = point(d, lines)
        pt = Point(p["measurement"]).time(end, WritePrecision.MS)
        for k, v in p["tags"].items():
            pt = pt.tag(k, v)
        for k, v in p["fields"].items():
            pt = pt.field(k, v)
        db.write([pt])
    return 0 if verdict(lines) == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
