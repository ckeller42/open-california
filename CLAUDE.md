# open-california — project guide for Claude

Reverse-engineered control + monitoring for the **VW California T7 Camper Unit** over
BLE (own-vehicle interoperability). Reads all vehicle telemetry; control *writes* are
blocked by a firmware gate (see below). Runs on a Raspberry Pi (`buspi`), feeding
**Home Assistant** (MQTT) and **Grafana** (InfluxDB).

## Layout

| Path | What |
|---|---|
| `calictl/` | the runtime package — `protocol` (decode/encode), `semantics` (interpret), `device` (BLE), `serve` (the daemon), `mqtt`/`influx` (sinks), `control`/`overrides` (frames), `cli` |
| `protocol/dictionary.yaml` | extracted field map (13 functions, state+control); source of truth for bit layout |
| `protocol/signals.yaml` | the **signal catalog** — surface/omit decision + provenance per field |
| `tools/` | `extract_protocol` (regenerates the dictionary), `audit_signals` + `app_scales` + `app_setters` + `app_ranges` + `catalog` (the auditor), `triage` (catalog decisions), `hooks/` |
| `tests/` | pytest; **must stay green** |
| `docs/business-logic/` | RE notes (control recipes, feature gating, the write gate, signal catalog + scales) |
| `docs/superpowers/` | specs + plans |
| `ui/` | machine-usable GUI specs (`screens/*.yaml`) + `prototype.html` — **authoritative for app UI semantics**. Icons are VW/partner copyright: **not committed** (gitignored `ui/assets/svg/`); regenerate locally with `ui/assets/vd2svg.py` from the APK. |
| `calictl/deploy/` | systemd unit, Mosquitto + HA compose, Grafana dashboard, `push_dashboard.py` |

## Hard rules (don't break these)

- **Runtime `calictl/*` is stdlib-only at import.** `bleak`, `paho.mqtt`, `influxdb_client`,
  `yaml` are imported **lazily inside functions**, never at module top. Tooling in `tools/`
  may use PyYAML. Tests rely on this (they run without BLE/MQTT installed).
- **`serve` is the single BLE owner.** `hci0` on buspi is shared with other readers
  (Anker/Victron/Govee). Never open a second BLE connection from the daemon path; the
  `asyncio.Lock` in `serve.py` (created **inside** the running loop) serializes all access.
- **BLE codec is MSB-first**; control frames are **full-packet** (resend every field;
  unchanged fields = the leave-unchanged sentinel, usually `3` for 2-bit).
- **Every dictionary field has a catalog decision** (`surface` w/ name, or `omit` w/ reason).
  A dropped or unaccounted field **fails CI** (`tests/test_signal_coverage.py`).
- **Semantics correctness isn't auto-checked.** The guardrail validates *presence + scale*,
  not interpretation logic. When a field's app setter/getter is **inverted** or **combined**
  (`python3 -m tools.audit_signals --report` → `SEMANTIC-REVIEW-NEEDED`), verify the polarity
  against `ui/screens/*.yaml` / the decompiled getter, not a naive `bool(field)`. (This is
  how the camping-lights inversion was missed — see `docs/business-logic/signal-catalog.md`.)
- **Grafana dashboards don't auto-update.** After changing surfaced signals, edit
  `calictl/deploy/camper-dashboard.json` and push to Pi **and** Cloud via `push_dashboard.py`.
  A PostToolUse hook (`tools/hooks/dashboard-sync-reminder.py`) reminds you.
- **Don't label unverified values with a unit.** Several scales are `UNVERIFIED` (SoC is a
  coarse 0–15 level not %, currents/temps are raw). See `docs/business-logic/signal-scales.md`.
- **Never commit** the APK, decompiled sources (`decompile/`), VW manuals (`manuals/`), or
  secrets/tokens (`*.env`) — all gitignored. VW material: citations only.

## Commands

```
python3 -m pytest tests/ -q                          # the suite (keep green)
DECOMPILE_SRC=<sources> python3 -m tools.audit_signals --report   # coverage + semantic-review
python3 -m calictl status                            # live read of all functions (needs BLE + free slot)
python3 -m calictl serve [--dry-run]                 # the unified daemon
```

## Known state

- **Control writes WORK** (issue #2 solved 2026-07-07). The gate was a **liveness heartbeat
  on char `00001003`**: while a +1 4-byte BE counter ticks (~0.6 s), the unit honours
  actuation writes. Actuation is **one-shot arm** — the load latches, so the heartbeat only
  spans the write window (`device.actuate`, held under the `serve` lock). **Verified
  on-device: `cooler` (power/level) and `campingmode` (master/lights/usb) actuate for real.**
  `lighting` is wired (`set lighting power|brightness`) and builds a valid SET_BRIGHTNESS
  frame, but **on-device apply is UNCONFIRMED** — the frame ACKs yet the zones don't change
  (a LightValue/SET_PROFILE semantic gap, not the arm gate — `re-gap-inventory.md` §A1).
  Extend via `control.BUILDERS`. See `docs/business-logic/remote-control-connect-gate.md`.
- **`vehicle` function reads char 1004** (`calictl get vehicle`, live-verified): ignition
  (terminal-15), car variant, unit RTC, 2-axis roll/pitch leveling — hand-added to
  `dictionary.yaml` (NOT emitted by `extract_protocol`; preserve on regen). `general` was
  repointed 1002→1001 (it had been reading the opaque VIN char, not the SW-version char).
  No BLE firmware/OTA path exists (`re-gap-inventory.md` §A6).
- **Not installed on this van:** stairs, living-room heater, roof-A/C, satellite, solar.
  Those functions are `Installed`-gated and only publish on vehicles that have them; their
  polarities can't be live-verified here (`satelliteantenna`, `stairs` are open review items).
- **buspi**: `sudo` needs the Pi password; `serve` runs under `~/solix-env`; secrets in
  `/etc/buspi/*.env` (root, 0600). The van allows buspi + the phone app connected at once.

## Documentation (sphinx + sphinx-needs)

- **Write Sphinx-renderable docstrings** (RST field lists: `:param:`, `:returns:`) on
  new/changed public functions. Keep them next to the code.
- **Author requirements as `sphinx-needs` objects IN the docstrings** — `.. req::` with
  `:id: R_<NAME>` in the implementing code, `.. test::` with `:id: T_<NAME>` +
  `:links: R_<NAME>` in the verifying test's docstring (the link is the trace). Example:
  `calictl.semantics.vehicle` (`R_VEHICLE_1004`) ← `tests/…test_vehicle_decode_char_1004`
  (`T_VEHICLE_DECODE`). Add each autodoc'd target to `docs/sphinx/api.rst`.
- Build/verify the trace: `docs/sphinx/README.md` (`sphinx -b html -W` and `-b needs`; a
  resolved trace shows up as the req's `links_back` in `needs.json`). `needs_id_required=True`.

## Working style

Read `docs/business-logic/` before changing decode/semantics. Follow the existing
dictionary-driven pattern; put manual offsets only in `overrides.py`. When surfacing a new
signal: catalog it (`tools/triage.py`) → emit it in `semantics` → update dashboard + HA →
run the auditor. Prefer small, test-backed changes.
