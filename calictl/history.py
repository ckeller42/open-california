"""Small append-only JSONL helpers for the daemon's own logs (stdlib only, no Influx).

Used for the per-poll outcome log (``serve.Server._record_outcome``, read back by
``tools/analyze_battery.py``). Nothing here raises: telemetry must never take the poll loop down.
(The leisure-battery history that fed the web UI's 24 h chart was removed 2026-10-10 with the chart,
owner decision.)
"""

from __future__ import annotations

import json
import os


def append_jsonl(path: str, record, max_lines: int = 300_000) -> bool:
    """Append one JSON record (a dict/list) as a compact line; trim to the last ``max_lines`` when
    the file grows large. Never raises. Used for the poll-outcome log (`serve._record_outcome`).
    """
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        if os.path.getsize(path) > max_lines * 96:  # cheap size gate before the O(n) rewrite
            with open(path) as f:
                lines = f.readlines()
            if len(lines) > max_lines:
                with open(path, "w") as f:
                    f.writelines(lines[-max_lines:])
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_jsonl(path: str, since: float | None = None) -> list:
    """Read JSONL records (dicts) oldest-first, dropping torn lines. ``since`` filters by ``ts``."""
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and (since is None or rec.get("ts", 0) >= since):
                    out.append(rec)
    except OSError:
        return []
    return out
