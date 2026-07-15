"""Bounded on-disk history of the leisure-battery readings (stdlib only, no Influx).

The web UI must stay **Influx-free** (an explicit product requirement: the GUI keeps working
when Influx/Grafana are down, and the daemon is the only thing that must be up). Influx remains
an optional *sink* for Grafana, never a source the GUI reads back. So the 24 h energy chart needs
its own history, and this module is it: a small append-only ring buffer the daemon fills from its
poll loop.

Storage is **JSONL, one compact array per sample** (``[ts, volts, amps]``), appended in place::

    [1752600000.0, 13.4, 3.4]

Appending ~40 bytes beats rewriting a whole JSON blob: at the 30 s poll interval, 24 h is ~2880
samples (~115 KB), so a rewrite-per-poll would push ~330 MB/day at buspi's **SD card** versus
~4 MB/day for appends. Old samples are dropped by an occasional :func:`trim` rewrite rather than
on every append, which bounds the file at ~230 KB for the 48 h retention.

Nothing here raises: telemetry must never take the poll loop down (same discipline as
``serve.Server._save_last``). A missing or corrupt file reads back as "no history", and a single
corrupt line is skipped rather than aborting the load.

.. req:: Influx-free energy history
   :id: R_ENERGY_HISTORY

   The daemon records ``(timestamp, leisure-battery volts, leisure-battery amps)`` for each
   successful poll in a bounded append-only file, and serves the last N hours of it to the web
   UI without reading InfluxDB. Samples with a missing volts/amps reading are not recorded;
   gaps (the unit deep-sleeps for days when parked) are represented by absent samples, never by
   interpolated or fabricated values.
"""
from __future__ import annotations

import json
import os
import time

#: Samples older than this are dropped by :func:`trim`. 2x the 24 h view, so a full-window read
#: always has slack rather than clipping at exactly the boundary.
RETENTION_S = 48 * 3600

#: Append this many samples before a :func:`trim` rewrite is worth the I/O (~4 h at a 30 s poll).
TRIM_EVERY = 500


def append(path: str, ts: float, volts, amps) -> bool:
    """Append one sample. Never raises.

    :param path: the JSONL history file (created, with parents, on first write).
    :param ts: epoch seconds of the reading.
    :param volts: leisure-battery voltage, or ``None`` to skip the sample.
    :param amps: leisure-battery current, or ``None`` to skip the sample.
    :returns: True if a sample was written; False if it was skipped or the write failed.
    """
    if volts is None or amps is None:
        return False                      # a half-read sample is not a data point; never write nulls
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps([round(float(ts), 3), volts, amps]) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def load(path: str, since: float = None) -> list:
    """Read samples, oldest first. Never raises.

    :param path: the JSONL history file.
    :param since: drop samples with ``ts`` older than this epoch, if given.
    :returns: a list of ``[ts, volts, amps]``; ``[]`` when the file is missing or unreadable.
    """
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue              # a torn/corrupt line loses one sample, not the history
                if not isinstance(row, list) or len(row) != 3:
                    continue
                ts = row[0]
                if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                    continue
                if since is not None and ts < since:
                    continue
                out.append([ts, row[1], row[2]])
    except OSError:
        return []
    return out


def trim(path: str, retention_s: float = RETENTION_S, now: float = None) -> int:
    """Rewrite the file, dropping samples older than ``retention_s``. Never raises.

    :param path: the JSONL history file. A missing file is left missing (not created).
    :param retention_s: maximum sample age to keep.
    :param now: epoch "now" (injectable for tests).
    :returns: the number of samples kept (0 if the file is missing).
    """
    if not os.path.isfile(path):
        return 0
    now = time.time() if now is None else now
    rows = load(path, since=now - retention_s)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, path)             # atomic: a reader never sees a half-rewritten history
    except OSError:
        return len(rows)
    return len(rows)
