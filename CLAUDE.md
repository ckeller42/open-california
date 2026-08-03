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
  how the camping-lights inversion was missed — see `docs/business-logic/signals.md`.)
- **Grafana dashboards don't auto-update.** After changing surfaced signals, edit
  `calictl/deploy/camper-dashboard.json` and push to Pi **and** Cloud via `push_dashboard.py`.
  A PostToolUse hook (`tools/hooks/dashboard-sync-reminder.py`) reminds you.
- **Don't label unverified values with a unit.** Several scales are `UNVERIFIED` (SoC is a
  coarse 0–15 level not %, currents/temps are raw). See `docs/business-logic/signals.md`.
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
  on-device: `cooler` (power/level), `campingmode` (master/lights/usb), and `lighting`
  (profile + per-zone brightness) actuate for real.**
  **`lighting` PHYSICAL ACTUATION IS UNVERIFIED — probably does NOT work (owner-confirmed
  2026-07-18).** ⚠️ Every prior "lighting applies / live-verified kitchen 0→8" claim was
  **READBACK-based, and the state char is a write-through ECHO**: a SET_BRIGHTNESS is ACKed and the
  reported value updates to what we sent, so a readback ALWAYS "confirms" — even though the physical
  lamp does not light. The owner physically checked at the van: the lamps **stay dark**. So do not
  trust readback as proof of actuation for lighting; only a human seeing the lamp counts.
  What IS solid (protocol facts, unchanged): our SET_BRIGHTNESS/SET_PROFILE frames are byte-identical
  to the app's; the per-zone unchanged sentinel is `14`; `ProfileNumber` is hardcoded to `9` like the
  app (`control.LIGHT_BRIGHTNESS_PROFILE`, `dg/h.java:170` `w(9)`), so the *frame* is correct with the
  lights off; the `0e00…` follow (`control.LIGHT_COMMIT`, `commit_for(fn)`) is the app's neutral
  default frame (Mode 0 = `NO_MODE`), which flushes a single write since the app self-applies by
  streaming frames continuously. What's NOT solved: replicating that streaming/timing well enough to
  drive the physical load — an OPEN RE gap. To crack it: capture the app physically lighting a lamp
  and diff vs our frames (`tools/capture_diff.py`), with a PHYSICAL success criterion, not a readback.
  (Ruled out 2026-07-18 as the cause: persistent session, camping-mode/ignition/battery gates.)
  The "a profile must be active first" rule was ALSO wrong — it was the echo bug — but that's moot
  until physical actuation works at all. **`set lighting color` was decompile-inferred but the app
  exposes no colour control — treat SET_COLOR as unverified/possibly N/A.** Extend via
  `control.BUILDERS`. See
  `docs/business-logic/control-and-actuation.md` + `protocol-sequences.md`.
- **Roof sequence settled 2026-07-13 from the decompiled roof class** (needs ignition ON).
  `control.roof_frame` direction bytes match the app byte-for-byte (open `0x01`/stop `0x00`/close
  `0x04`). Movement is **press-and-hold**: stream move frames while held, STOP (`0x00`) / cease on
  release — **no confirmation phase, no STOP-hold**. The **SafetyCounter is APP-GENERATED**: a
  monotonic BE-uint32 `seed + elapsed_ms/500` (random seed) ⇒ **~+1 every 500 ms** — **NOT** echoed
  from the unit (an earlier capture read of ~0.8/frame + a "4 s STOP-hold" was wrong: two senders
  overlapping + the user releasing the button while reading the dialog). The unit checks
  liveness/monotonicity and reports `SafetyCounterValid` (`1402` bit 7); it **withholds the motor
  for ~3 s until the counter validates** and the app arms a **3000 ms** dead-man (perceived ~4 s =
  3 s + BLE latency — no 4 s constant). **Open gaps (roof NOT-LIVE-VERIFIED until fixed):**
  `actuate_roof` must (a) generate a live monotonic counter (~+1/500 ms), not echo/fixed-`+1`;
  (b) account for the ~3 s self-gate (optionally honour `SafetyCounterValid` + the 3 s timeout);
  (c) use ~500 ms cadence, not 1 Hz. See `protocol-sequences.md` §3.
- **Reads go STALE, and the unit deep-sleeps** (found 2026-07-{09,12}). A bare read returns a
  latched value that decays (fresh-water read 1 L vs true 11 L); `device.read_all`/`read` now run
  the **1003 heartbeat during reads** to keep values fresh + the link alive. But when the van is
  **parked, the camper unit deep-sleeps and stops advertising entirely** — buspi (in the van, bond
  intact) can't even connect for days; only physical use (door/ignition) wakes it, and the phone
  app can grab the single slot on wake. So buspi access is **inherently intermittent**: `serve`
  persists the last-known state + an "as of" timestamp and the web UI shows an **offline** banner
  when unreachable. See `docs/business-logic/value-freshness.md`.
- **`vehicle` function reads char 1004** (`calictl get vehicle`): ignition (terminal-15), car
  variant, unit RTC, 2-axis roll/pitch leveling — hand-added to `dictionary.yaml` (NOT emitted by
  `extract_protocol`; preserve on regen). **byte-0 was FIXED 2026-07-12** (`TerminalOneFive` is
  bit **7**, not 0 — ignition had always decoded "off"; `CarVariant 0/4`, `CarLevelPopUp 4/2`).
  Dynamic fields (leveling, RTC) only read while ignition is on. `general` repointed 1002→1001.
- **Not installed on this van:** stairs, living-room heater, roof-A/C, satellite, solar. Their
  `Installed`-gated; polarities can't be live-verified here — but the **semantics were verified
  statically against the app's getters 2026-07-12** (no bugs; stairs `extended`/`obstacle_sensor`
  non-inverted, satellite `System` is a 2-bit enum). See `docs/business-logic/protocol-alignment.md`.
- **buspi**: `sudo` needs the Pi password; `serve` runs under `~/solix-env`; secrets in
  `/etc/buspi/*.env` (root, 0600). The van *can* have buspi + the phone connected at once **once
  buspi is in** — but see the deep-sleep note: a parked unit isn't reachable at all.

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
