# 24 h energy history chart — design

**Status:** approved 2026-07-15, implemented.
**Requirement:** `R_ENERGY_HISTORY` (`calictl/history.py`) ← `T_ENERGY_HISTORY` (`tests/test_history.py`).

## Problem

The web UI's Energy screen shows instantaneous leisure-battery voltage and current. The owner
wants the **last 24 h** of both, on the phone.

The constraint that shapes everything: **the GUI must not depend on InfluxDB**. That was an
explicit product decision (2026-07-14) — the PWA must keep working when Influx/Grafana are down,
so the daemon is the only thing that has to be up. Influx stays a write-only *sink* for Grafana.
But 24 h of history is, by definition, historical data, and Influx was the only place it lived.

## Decision

The daemon keeps its **own bounded history** and serves it. Rejected alternatives:

| Option | Why not |
|---|---|
| Point the user at Grafana | Both panels already exist (`Leisure battery (V)`, `Leisure battery current (A)`) — but it's a separate app with a login, not the PWA, and it dies with Influx. |
| `/api/history` backed by Influx | Least code, but re-couples the GUI to Influx — reverses the requirement above. |

Accepted cost: this **duplicates Grafana** for this one window. Justified by "works on the phone,
works offline, no login". Grafana remains the tool for deep analysis and arbitrary ranges.

## Architecture

| Unit | Purpose | Depends on |
|---|---|---|
| `calictl/history.py` | `append` / `load` / `trim` over a file path | stdlib only |
| `serve.Server._record_history` | one append per successful poll | `history` |
| `serve.ServeBackend.history` + `GET /api/history` | serve the last N hours | `history` |
| `webui/app.js` `energyChart` | inline-SVG sparkline | `/api/history` |

`history.py` knows nothing about BLE, MQTT, Influx, or the web — it is a file and two numbers,
so it is testable standalone and the daemon does not grow another tangled responsibility.

### Storage: append-only JSONL

`~/.cache/calictl/history.jsonl` (env `CALICTL_HISTORY_CACHE`), one compact array per sample:

```
[1752600000.0, 13.4, 3.4]
```

**Why not extend `last_state.json`:** that blob is rewritten every poll. At the 30 s interval, 24 h
is ~2880 samples (~115 KB), so rewriting it per poll would push **~330 MB/day** at buspi's SD card
versus **~4 MB/day** for ~40-byte appends. Retention is **48 h** (2× the window, for slack), applied
by a `trim` rewrite every `TRIM_EVERY` (500) appends — roughly every 4 h — not per append. That
bounds the file at ~230 KB.

Timestamps are rounded to milliseconds; sub-ms precision is meaningless at a 30 s poll.

### Series: leisure battery only

`batt2_v` and `batt2_current`. These are always measured, and their scales were **verified against
the app's view-model** (`xf/d.java`, 2026-07-08; see `semantics.py:61`), so the chart can carry real
V/A units without violating the project's "don't label unverified values with a unit" rule.

The **starter** battery is deliberately excluded: it is measured only with terminal-15 on and reads
sentinels otherwise (`I=0x81`, `U=48`, nulled by `semantics.energy`), so it would be a permanently
empty series on a parked van.

### Gaps are gaps

The unit deep-sleeps for days when parked, so a 24 h window is often mostly empty — the common case,
not an edge case. Therefore:

- The x-axis is **always the true last 24 h**. Sparse data renders sparse.
- The line **breaks** wherever consecutive samples are more than `gap_s` apart. `gap_s` is
  `interval * 3 + 30` — deliberately the same threshold `_meta.online` uses, so "we lost the van"
  means one thing across the codebase.
- Gaps are **never interpolated**. Joining across a sleep would draw a voltage the battery never
  held; the chart would be inventing data.
- An empty window renders `No data in the last 24 h — van asleep since <time>`.

Rejected: "last 24 h of *available* data" (splices out gaps — the time axis becomes fiction) and
auto-ranging to the last activity (honest, but the range silently shifts).

## Error handling

Telemetry must never take the poll loop down (the `_save_last` discipline):

- `append` returns False on any `OSError`; a missing volts/amps **skips the sample** rather than
  writing a null the chart would draw as 0.
- A corrupt line loses **one sample**, not the history.
- Missing/unreadable file → `[]` → the empty state.
- `api()` resolves the body even on a 500, so the UI distinguishes `{"error": ...}` (→ *History
  unavailable*) from an empty window (→ *van asleep*). Otherwise a bug on our side would be
  reported to the user as the van's fault.

## Testing

- `tests/test_history.py` — round-trip, `since` filter, trim retention, corrupt-line tolerance,
  missing file, "trim does not conjure a file".
- `tests/test_energy_history.py` — poll records a sample; no-energy/half-read poll records nothing;
  trim is amortised; `hours` clamped to 1–48; unwritable path doesn't raise; **asserts the path
  never calls `influx.field_series`**.
- `tests/e2e/test_gui.py::test_energy_chart_draws_from_daemon_history_not_influx` — the daemon runs
  `--no-influx`, so a rendered line proves the Influx-free property end-to-end.

## Out of scope (YAGNI)

No zoom, pan, range picker, starter series, or downsampling. Fixed 24 h. Grafana covers the rest.

## Known limits

- History accrues **only from deploy onward**, and only while the van is awake — the chart starts
  empty and stays empty until the next trip.
- The client caches the window for 60 s (`HISTORY_TTL_MS`); `render()` runs every 2 s and refetching
  ~2880 samples per tick would be pointless traffic.
