# open-california — project guide for Claude

Reverse-engineered control + monitoring for the **VW California T7 Camper Unit** over
BLE (own-vehicle interoperability). Reads all vehicle telemetry; control *writes* work
(armed by the `1003` liveness heartbeat — see Known state). Runs on a Raspberry Pi (`buspi`),
feeding **Home Assistant** (MQTT) and **Grafana** (InfluxDB).

**New here? Read [`ARCHITECTURE.md`](ARCHITECTURE.md)** for the data-flow overview (BLE → decode →
semantics → sinks). This file is the agent-facing rules + operational state; it is not the tutorial.

## Layout

| Path | What |
|---|---|
| `calictl/` | the runtime package — `protocol` (decode/encode), `semantics` (interpret), `device` (BLE), `serve` (the daemon), `web`/`mqtt`/`influx` (sinks), `control`/`overrides` (frames), `session`/`observer`/`automation`/`firmware`/`anchors`, `cli` |
| `protocol/dictionary.yaml` | extracted field map (14 functions, state+control); source of truth for bit layout |
| `protocol/signals.yaml` | the **signal catalog** — surface/omit decision + provenance per field |
| `tools/` | `extract_protocol` (regenerates the dictionary), `audit_signals` + `app_scales` + `app_setters` + `app_ranges` + `catalog` (the auditor), `triage` (catalog decisions), `build_web`, `hooks/` |
| `tests/` | pytest; **must stay green** |
| `docs/business-logic/` | RE notes (control recipes, feature gating, the write gate, signal catalog + scales) — the full provenance behind the terse "Known state" below |
| `docs/superpowers/` | specs + plans |
| `ui/` | machine-usable GUI specs (`screens/*.yaml`) + `prototype.html` (an **RE spec preview**, not the served UI) — **authoritative for app UI semantics**. Icons are VW/partner copyright: **not committed** (gitignored `ui/assets/svg/`); regenerate locally with `ui/assets/vd2svg.py` from the APK. |
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
  how the camping-lights inversion was missed — see `docs/business-logic/signals.md`.)
  **The unit's own on-screen display is the ground truth** — an owner photo of the camper screen
  catches mislabels that a readback echo hides (`usb_powered`, `quiet_scheduled`=Mode 4-not-NightTimerSet,
  the lamp-map — all caught by the screen, cross-checked against a decompile call-stack).
- **Grafana dashboards don't auto-update.** After changing surfaced signals, edit
  `calictl/deploy/camper-dashboard.json` and push to Pi **and** Cloud via `push_dashboard.py`.
  A PostToolUse hook (`tools/hooks/dashboard-sync-reminder.py`) reminds you.
- **Don't label unverified values with a unit.** Several scales are `UNVERIFIED` (SoC is a
  coarse 0–15 level not %, currents/temps are raw). See `docs/business-logic/signals.md`.
- **Never commit** the APK, decompiled sources (`decompile/`), VW manuals (`manuals/`), or
  secrets/tokens (`*.env`) — all gitignored. VW material: citations only.
- **Mermaid diagrams render in the browser, not at build** — `sphinx -W` won't catch a broken
  one. Keep `;`, `&`, bare `<`/`>`, and label-`:` out of `.. mermaid::` text; the guard
  `tests/test_mermaid_syntax.py` enforces it.

## Commands

```
python3 -m pytest tests/ -q                          # the suite (keep green)
DECOMPILE_SRC=<sources> python3 -m tools.audit_signals --report   # coverage + semantic-review
python3 -m calictl status                            # live read of all functions (needs BLE + free slot)
python3 -m calictl serve [--dry-run]                 # the unified daemon (read-only unless --enable-writes)
curl -s localhost:8088/api/state                     # buspi: live decoded state via the RUNNING daemon
```
When the daemon is up it OWNS the single BLE slot — read live state via its web API `/api/state`
(**buspi runs `--web 8088`**; the CLI default is 8080) or the cache `~/.cache/calictl/last_state.json`;
never open a 2nd BLE connection. Warm the fast session first with `POST /api/session {"action":"connect"}`
(auto-releases after ~25 s idle).

## Known state (operational takeaways — full provenance in `docs/business-logic/` + `evidence-ledger.md`)

- **Control writes WORK** (issue #2, 2026-07-07): armed by a **+1 4-byte-BE liveness heartbeat on
  char `1003`** (~0.6 s). One-shot arm — the load latches, so the heartbeat only spans the write
  window (`device.actuate`, under the `serve` lock). Live-verified on-device: **cooler** (power/level),
  **campingmode** (master/lights/usb), **lighting** (per-zone brightness).
- **Lighting** actuates on an **awake** unit with a bare `SET_BRIGHTNESS` + `0e00…` commit — no
  REQUEST_CONFIG preamble, no 1003 heartbeat, no delay (the wake state is the gate, not any arming
  frame; the app's screen-open REQUEST_CONFIG pull is NOT required and calictl no longer sends it).
  Brightness is the **0-11 enum** (0=OFF, 1-10 = 10–100 %, 11=DEFAULT; 13=NOT_EQUIPPED read-only,
  14=leave-unchanged; `LIGHT_ON_BRIGHTNESS=10`, slider max 10). The `1502` **Mode-4 notification** is a
  decodable state frame carrying the real ramping brightness — the truthful feedback channel; the
  state-char **readback is a write-through echo, never proof of actuation**. `set lighting color`
  (SET_COLOR) is unverified (app exposes no colour control). Extend via `control.BUILDERS`. See
  `control-and-actuation.md`.
- **Roof** (needs ignition ON): press-and-hold — stream move frames while held, STOP/cease on release
  (no confirmation phase). Direction bytes match the app (open `0x01`/stop `0x00`/close `0x04`). The
  **SafetyCounter is app-generated** (monotonic BE-uint32, ~+1 per 500 ms), NOT echoed; the unit
  withholds the motor ~3 s until it validates (`1402` bit 7). `actuate_roof` is protocol-correct but
  **has NEVER driven a real motor**. App-faithful arm (fixed #150): it handshakes (reads+subscribe)
  then streams the counter IMMEDIATELY — NO 1003 heartbeat, NO `ARM_DELAY_S` pre-arm (a gap would make
  the unit see a fresh counter and withhold the motor another ~3 s); the counter IS the liveness proof.
  `_handshake` = no-heartbeat/no-delay arm; old `_arm` (heartbeat+delay) still serves cooler/camping.
  GUI is press-and-hold (release → STOP via lock-free `_roof_stop`); a re-press within 1000 ms is
  debounced (would restart the counter → another ~3 s withhold). `actuate_roof` polls `Position`
  (`1402`) ~1 Hz and auto-stops at the limit (open `1` / closed `0`/`14`; `control.roof_limit_positions`)
  — best-effort over the unit's own limit switches. See `protocol-alignment.md` + `protocol-sequences`.
- **Reads go stale + the unit deep-sleeps.** A bare read returns a decaying latch (fresh-water 1 L vs
  true 11 L) unless the 1003 heartbeat runs during reads (`device.read_all`/`read` do). Parked, the
  unit deep-sleeps and stops advertising — buspi can't connect for days until physical use wakes it, so
  access is **inherently intermittent**: `serve` persists last-state + an "as of" timestamp, and the
  web UI shows an offline banner. See `value-freshness.md`.
- **`vehicle` (char 1004):** ignition (terminal-15 is **bit 7**, not 0 — was a decode bug), car
  variant, unit RTC, 2-axis roll/pitch leveling. **Hand-added to `dictionary.yaml` — NOT emitted by
  `extract_protocol`, so preserve it on regen.** Leveling/RTC read only while ignition is on. (`general`
  is char 1001.)
- **Not installed on this van:** stairs, living-room heater, roof-A/C, satellite, solar (semantics
  static-verified against the app's getters — no live check possible here). The pop-top **roof IS
  installed** (`roof.Installed=1`, #106) but its motor has never been driven by calictl.
- **buspi:** `sudo` needs the Pi password; `serve` runs under `~/solix-env`; secrets in
  `/etc/buspi/*.env` (root 0600). A parked unit is unreachable (deep-sleep, above). The web UI is
  also fronted by HTTPS on the tailnet via `tailscale serve` (tailnet-only, never `funnel`) — see
  the `buspi-deploy` skill + `docs/raspberry-pi-setup.md` "Remote access over Tailscale".

## Documentation (sphinx + sphinx-needs)

- **Write Sphinx-renderable docstrings** (RST field lists: `:param:`, `:returns:`) on
  new/changed public functions. Keep them next to the code.
- **Author requirements as `sphinx-needs` objects IN the docstrings** — `.. req::` with
  `:id: R_<NAME>` in the implementing code, `.. test::` with `:id: T_<NAME>` +
  `:links: R_<NAME>` in the verifying test's docstring (the link is the trace). Example:
  `calictl.semantics.vehicle` (`R_VEHICLE_1004`) ← `tests/…test_vehicle_decode_char_1004`
  (`T_VEHICLE_DECODE`). Add each autodoc'd target to `docs/api.rst`.
- Build/verify the trace: `docs/building-the-docs.md` (`sphinx -b html -W` and `-b needs`; a
  resolved trace shows up as the req's `links_back` in `needs.json`). `needs_id_required=True`.

## Working style

Read `docs/business-logic/` before changing decode/semantics. Follow the existing
dictionary-driven pattern; put manual offsets only in `overrides.py`. When surfacing a new
signal: catalog it (`tools/triage.py`) → emit it in `semantics` → update dashboard + HA →
run the auditor. Prefer small, test-backed changes.
