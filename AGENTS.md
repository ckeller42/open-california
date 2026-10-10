# open-california — project guide for agents and contributors

Reverse-engineered control + monitoring for the **VW California T7 Camper Unit** over
BLE (own-vehicle interoperability). Reads all vehicle telemetry; control *writes* work
(armed by the `1003` liveness heartbeat — see Known state). Runs on a Raspberry Pi (`buspi`),
feeding **Home Assistant** (MQTT) and **Grafana** (InfluxDB).

**New here? Read [`ARCHITECTURE.md`](ARCHITECTURE.md)** for the data-flow overview (BLE → decode →
semantics → sinks). This file is the agent-facing rules + operational state; it is not the tutorial.

## Layout

| Path | What |
|---|---|
| `calictl/` | the runtime package — `protocol` (decode/encode), `semantics` (interpret), `device` (BLE), `serve` (the daemon), `web`/`mqtt`/`influx` (sinks), `control`/`overrides` (frames), `session`/`observer`/`automation`/`firmware`/`anchors`, `freshness` (stale-read guards), `history` (battery history for the UI), `postcheck` (post-write applied-check), `pairing`/`pairing_bluez` (guided-pairing SM + BlueZ transport), `log`, `trace` (BLE trace recorder), `cli` |
| `protocol/dictionary.yaml` | extracted field map (14 functions, state+control); source of truth for bit layout |
| `protocol/signals.yaml` | the **signal catalog** — surface/omit decision + provenance per field |
| `tools/` | `ci.sh` (the LOCAL CI gate), `extract_protocol` (regenerates the dictionary), `audit_signals` + `app_scales` + `app_setters` + `app_ranges` + `catalog` (the auditor), `triage` (catalog decisions), `build_web`, `mock_unit` (the e2e fake — seeds every fitted function), `run_against_mock` (real CLI/`serve` over the mock), `trace_compare` (real-unit trace vs the mock), `fake_unit_peripheral` (the mock as a **Bumble BLE peripheral** with real SMP passkey pairing — shared by `applab`, `tests/test_pairing_link.py` and the `tests/realstack/` VM rig), `applab/` (the **real app** in an emulator against that peripheral — screens in any state + app-vs-calictl frame diffs; see its README; `applab/phone/` = HCI-snoop decoders for the owner's REAL phone, skill `phone-app-lab`), `esplab/` (CoreS3 bench helpers: `flash.sh` from `flasher_args.json`, `esp_cmd.py` no-reset console; `thinky-bench` skill), `gen_c_dict` + `gen_codec_vectors` (C codec header + golden vectors, `--check` in CI), `check_vendor_material` + `check_import_clean` (guards shared by the pre-commit hooks + CI), `hooks/` (Claude Code hook scripts) |
| `tests/` | pytest; **must stay green**. `tests/e2e/` = Playwright over the mock daemon; every test fails on an uncaught JS error. `tests/realstack/` = the real-BlueZ pairing rig (CI VM only, not collected by pytest) |
| `docs/business-logic/` | RE notes (control recipes, feature gating, the write gate, signal catalog + scales) — the full provenance behind the terse "Known state" below |
| `docs/superpowers/` | specs + plans — **local-only** (gitignored, not in the repo); docs that cite a spec there point at an untracked file |
| `ui/` | machine-usable GUI specs (`screens/*.yaml`) + `prototype.html` (an **RE spec preview**, not the served UI) — **authoritative for app UI semantics**. Icons are VW/partner copyright: **not committed** (gitignored `ui/assets/svg/`); they are regenerated locally from the APK with a `vd2svg.py` converter that is itself **not in the repo** (`ui/assets/` is untracked); `build_prototype.py` falls back to neutral placeholders without them. |
| `calictl/deploy/` | systemd unit, Mosquitto + HA compose, Grafana dashboard, `push_dashboard.py` |
| `firmware/` | ESP32-S3 satellite (#154, WIP; controls cooler/camping/lighting/air heater/energy — no roof; the wake-up light with the page's clock `local_now` + the unit's latched config (`R_FW_WAKEUP`), writes only in station mode, one write allow-list `R_FW_WRITE_ALLOWLIST`; bench CoreS3 + mock unit (2026-10-07); **bonded to and running against the real unit since 2026-10-08**, holds its link while parked since #279): `cali_core` (pairing SM/runner/session/console + `control` = C twin of `calictl.control` held to `tests/vectors/control.json` + `control_run` sequencer, WiFi SM/runner, HTTP core, captive DNS, web endpoints incl. `POST /api/command` and calictl's pairing wizard `GET/POST /api/pairing` (`R_FW_PAIRING_WIZARD`, also over the setup hotspot via `GET /app`) — platform-free C) + `cali_ble_nimble` (NimBLE transport) + `platform` (NVS/host kv store, `cali_net` = `net_host.c` fake WiFi / `net_esp.c` esp_wifi+lwIP+mdns) + `web/` (status page: edit `index.html`+`page.js`+`strings.json`, `gen_c_dict` renders `index_gen.html`/`strings_gen.h`) + `host`/`qemu`/`main` builds (3 MB app partition); see `docs/firmware.md`, owner how-to `docs/howto-esp-wifi-setup.md` |

## Hard rules (don't break these)

- **Runtime `calictl/*` is stdlib-only at import.** `bleak`, `paho.mqtt`, `influxdb_client`,
  `yaml` are imported **lazily inside functions**, never at module top. Tooling in `tools/`
  may use PyYAML. Tests rely on this (they run without BLE/MQTT installed).
- **`serve` is the single BLE owner.** `hci0` on buspi is shared with other readers
  (Anker/Victron/Govee). Never open a second BLE connection from the daemon path; the
  `asyncio.Lock` in `serve.py` (created **inside** the running loop) serializes all access.
- **BLE codec is MSB-first**; control frames are **full-packet** (resend every field;
  unchanged fields = the leave-unchanged sentinel, usually `3` for 2-bit).
- **The web UI is un-built JS; `tsc --checkJs` is its hard gate** (`calictl/webui/jsconfig.json`
  and the ESP32 page's `firmware/web/jsconfig.json`, baseline **0 errors** — keep it there). `node --check` only parses; an undeclared identifier in a
  renderer once shipped to buspi because no test rendered that screen. Run `tools/ci.sh webcheck`
  (also CI `pre-commit` + the `webcheck` pre-commit hook when webui or `firmware/web` JS is staged). Any label/enum shown to the user must
  use the unit's own vocabulary (Sofortheizen / Dauerbetrieb / Flüstermodus …), EN + DE
  (`strings.de.js`; `tests/test_i18n_de.py` guards literal `t()` keys).
- **Every dictionary field has a catalog decision** (`surface` w/ name, or `omit` w/ reason).
  A dropped or unaccounted field **fails CI** (`tests/test_signal_coverage.py`).
- **Every buildable command is documented with an evidence tier** (`control-and-actuation.md` §5);
  a new `what` in `control.BUILDERS` without a row fails CI (`tests/test_command_coverage.py`).
- **Every app recording is cited in `evidence-ledger.md`** and every cited one exists
  (`tests/test_evidence_recordings.py`).
- **No `async` predicate in `wait_for_function`** (tests/e2e) — it never waits; use a sync predicate
  or poll `/api/state` from Python (`tests/test_e2e_patterns.py`).
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
- **Don't label unverified values with a unit.** SoC is a coarse 0–15 **level**, not % — the
  derived `soc*_pct` (level×10, `None` above 10) only mirrors the app's display math; temps are
  raw levels (`UNVERIFIED`). Currents ARE verified amps since 2026-09-07 (`batt2/shore/solar`
  ×0.1, `dcdc` raw, per the app view-model). See `docs/business-logic/signals.md` §4/§5.
- **Never commit** the APK, decompiled sources (`decompile/`), VW manuals (`manuals/`), or
  secrets/tokens (`*.env`) — all gitignored. VW material: citations only. **App screenshots (real phone or
  emulator) go ONLY to the owner's private repo `ckeller42/californiaontour-re` (`screens/`)** — never here
  (owner 2026-10-10); open-california gets text + links. Never open/capture the app's VIN pages
  (Account → Vehicle, and in app 5.4.0 Vehicle → Help → Vehicle Settings).
- **One web UI for buspi and the ESP.** `calictl/webui/` is served by buspi and bundled into the
  firmware (`firmware/web/app_bundle_gen.h`; the ESP decodes raw frames in the browser with the
  `semantics.js` twin). Any webui/semantics change: `python3 -m tools.gen_c_dict` (+
  `tools.gen_semantics_vectors`) in the same PR, then deploy buspi AND flash the ESP. Show only what the
  van has: no "not installed" rows/tiles (owner 2026-10-10).
- **Mermaid diagrams render in the browser, not at build** — `sphinx -W` won't catch a broken
  one. Keep `;`, `&`, bare `<`/`>`, and label-`:` out of `.. mermaid::` blocks AND ```mermaid
  fences (root `*.md` + `docs/**/*.md`); the guard `tests/test_mermaid_syntax.py` lints both.

## Commands

```
tools/ci.sh dev                                      # once per clone: dev deps + pre-commit/pre-push hooks
python3 -m pytest tests/ -q                          # the suite (keep green)
tools/ci.sh [ci|webcheck|test|lint|audit|…]          # the local CI gate — NOT all of GitHub CI (below)
tools/ci.sh cov                                      # suite under coverage; floor gates calictl/ only (pyproject fail_under, a ratchet — raise it, never lower)
DECOMPILE_SRC=<sources> python3 -m tools.audit_signals --report   # coverage + semantic-review
python3 -m calictl status                            # live read of all functions (needs BLE + free slot)
python3 -m calictl serve [--dry-run]                 # the unified daemon (read-only unless --enable-writes)
curl -s localhost:8088/api/state                     # buspi: live decoded state via the RUNNING daemon
CALICTL_LOG_LEVEL=DEBUG python3 -m calictl serve …         # daemon logs via `logging` (calictl/log.py): level, name, timestamp (dropped under journald)
CALICTL_BLE_TRACE=~/ble.jsonl python3 -m calictl serve …   # record every notify/read/write of the REAL unit (JSONL)
python3 -m tools.trace_compare ~/ble.jsonl           # replay that trace through the mock: round-trip, cadence, dynamics
python -m pytest tests/firmware/test_pairing_sm_parity.py tests/firmware/test_runner_fake.py tests/firmware/test_session_fake.py tests/firmware/test_control_parity.py -v   # firmware pure-C tiers, no BLE, macOS OK
python3 -m tools.gen_control_vectors --check && python3 -m tools.gen_c_dict --check   # ESP control vectors + control_consts.h fresh (regenerate after any calictl.control change)
make -C firmware/host cali-host && python -m pytest tests/firmware -v   # firmware host+NimBLE tier (Linux only; tools/ci.sh firmware)
docker run --rm -v "$PWD":/project -w /project/firmware espressif/idf:v6.1 bash -c '. $IDF_PATH/export.sh >/dev/null && idf.py -B build-qemu -D SDKCONFIG=build-qemu/sdkconfig -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;qemu/sdkconfig.qemu" build'   # firmware QEMU tier build
python -m tools.ux_gallery --esp --out docs/screenshots   # ESP page shots for the docs (stub + fixtures, no firmware)
ssh pi@buspi 'cd ~/open-california && git pull && sudo systemctl restart calictl'   # deploy main to buspi
ssh pi@buspi '~/open-california/tools/esplab/flash_ci.sh <ci-run-id> /dev/ttyACM0'   # flash the ESP from THAT merge's ci.yml run
```

**Flash the ESP by explicit CI run id**, never `flash_ci.sh main`: it picks the latest *successful*
run, and the `[skip ci]` screenshot commit that lands right after a merge cancels the merge commit's
run, so `main` silently flashes the previous build (`gh run rerun <id>` first). Verify with
`curl http://<esp>/api/state` → `device.fw`.

`tools/ci.sh` covers ci.yml's `pre-commit` (via `pre-commit run --all-files`, incl. the whole-tree
vendor/MAC/VIN guard) + `test` (one python, not the 3.11–3.13 matrix); its pytest run also covers
`codec-parity` (C tests only with a C compiler) and `gui-e2e` (only with Playwright + Chromium, else
they skip). **Only GitHub runs:** `install-script` (shellcheck), `docs` (the `sphinx -W` site build on
every PR — locally `sh docs/build_site.sh`), `pairing-real-stack` (calictl's real `BluezTransport` vs
the Bumble fake unit over real BlueZ in a VM, `tests/realstack/vm.sh`; not a required check yet), the
firmware jobs, and — on push to `main` only — `docs.yml` (the same build + Pages deploy) and
`screenshots.yml` (commits `docs/screenshots` to `main` with `[skip ci]`).
Test layers + harnesses: `docs/simulation-and-testing.md`.

**Required checks (keep stable; verify in repo settings):** `test (3.11)`, `test (3.12)`,
`test (3.13)`, `pre-commit`, `no-vendor-material`, `install-script`, `docs`, `codec-parity`,
`gui-e2e`, `firmware-host-e2e`, `firmware-build`, `firmware-qemu`. `pairing-real-stack` runs but is
not required yet. Never rename a job key in `ci.yml` (or give the matrix job a `name:`); branch
protection matches these strings. Workflows: every `uses:` is SHA-pinned with a `# vX.Y.Z` comment,
`persist-credentials: false` on checkout (except `screenshots.yml`, which pushes), a
`timeout-minutes` on every job, and write scopes only on the job that needs them.

**Git hooks = the pre-commit framework** (`.pre-commit-config.yaml`; the old `.githooks/` is retired —
`git config --unset core.hooksPath` on an old clone). On **commit**: ruff + ruff format (Python only),
markdownlint-cli2 (`.markdownlint-cli2.jsonc`), gitleaks, actionlint + zizmor (workflow lint, `--offline`), whitespace/YAML checks and the repo guards
(vendor/MAC/VIN, import-clean, doc-offset, and — when their inputs are staged — web-fresh, codec
vectors + C headers, the webui `tsc` check). On **push**: the full pytest suite + `audit_signals`
(skip once with `SKIP=pytest,audit-signals git push`). Tool versions live only in that config (keep
`ruff==` in `requirements-dev.txt` in step).

When the daemon is up it OWNS buspi's BLE adapter — read live state via its web API `/api/state`
(**buspi runs `--web 8088`** via a systemd drop-in override — the committed unit template has no
`--web`; the CLI default is 8080) or the cache `~/.cache/calictl/last_state.json`;
never open a 2nd BLE connection from buspi. Warm the fast session first with `POST /api/session {"action":"connect"}`
(auto-releases after ~25 s idle).

## Known state (operational takeaways — full provenance in `docs/business-logic/` + `evidence-ledger.md`)

- **Control writes WORK** (issue #2, 2026-07-07): armed by a **+1 4-byte-BE liveness heartbeat on
  char `1003`** (~0.6 s). One-shot arm — the load latches, so the heartbeat only spans the write
  window (`device.actuate`, under the `serve` lock). Live-verified on-device: **cooler**, **campingmode** (master/lights/usb), **lighting** (per-zone
  brightness). **CAPTURE 2026-10-10:** the real app's frames on the real unit are byte-identical to
  `control.build` for every non-motor control exercised (cooler power/level/quiet/timer incl. the
  `State=3` frames, lighting, camping) — the app adds a neutral re-write (`ff771e3e1f1f` / `ff`) 500 ms
  later, which the unit ignores; calictl doesn't send it.
- **Lighting** actuates on an **awake** unit with a bare `SET_BRIGHTNESS` + `0e00…` commit — no
  REQUEST_CONFIG preamble, no 1003 heartbeat, no delay (the wake state is the gate, not any arming
  frame; the app's screen-open REQUEST_CONFIG pull is NOT required for actuation — calictl sends it only to
  read the wake-up/door/favourite config before a wake-up edit whose config is unknown, R5).
  Brightness is the **0-11 enum** (0=OFF, 1-10 = 10–100 %, 11=DEFAULT; 13=NOT_EQUIPPED read-only,
  14=leave-unchanged; `LIGHT_ON_BRIGHTNESS=10`, slider max 10). The `1502` **Mode-4 notification** is a
  decodable state frame carrying the real ramping brightness — the truthful feedback channel; the
  state-char **readback is a write-through echo, never proof of actuation**. `set lighting color` is
  **retired**; the app's lighting commands calictl builds are `power`, zones, `profile`,
  `save_profile N [colour]` (colour = SET_COLOR preface, DECOMPILE-only; the app's colour UI is
  model-gated), `wakeup`, `door_contact`. All of them except the colour preface are
  app-recorded byte-exact (zones, save, `profile` activate, wake-up time + on/off, `door_contact`). Wake-up /
  door / favourite config is latched **only** from the unit's own 1502 Mode-20 / Mode-16-PN-8 / Mode-12
  frames, never from calictl's write. Extend via `control.BUILDERS`. See `control-and-actuation.md`.
- **Roof** (needs ignition ON): press-and-hold — stream move frames while held, STOP/cease on release
  (no confirmation phase). Direction bytes match the app (open `0x01`/stop `0x00`/close `0x04`). The
  **SafetyCounter is app-generated** (monotonic BE-uint32, +1 per 500 ms tick), NOT echoed; the unit
  withholds the motor ~3 s after a FRESH counter validates (`1402` bit 7). **Roof view = the app's roof
  screen** (owner 2026-10-10, CAPTURE 2026-10-10, `R_ROOF_VIEW_STREAM`): while the web UI's roof page is
  open (`POST /api/roof` `view`, refreshed ~5 s, lapses after 15 s, `leave` ends a held move) the
  persistent session streams STOP frames (`device.RoofStream`); a press switches the SAME stream to the
  move byte, repeating the current counter, so no withhold; release → STOP. A press without a view
  (API/CLI/HA) starts its own stream (fresh counter, ~3 s withhold). `actuate_roof` is protocol-correct but
  **has NEVER driven a real motor**. App-faithful arm: the **1003 heartbeat ticks during the move**
  (the app's is session-global, decompile + `roof-hold` recording, #235) and the counter streams
  IMMEDIATELY — NO `ARM_DELAY_S` pre-arm (#150: a gap would make the unit see a fresh counter and
  withhold the motor another ~3 s). The real app's heartbeat-through-the-move is CAPTURE-confirmed on the
  unit (2026-10-10); calictl's own roof path is not yet device-verified (#157/#230).
  GUI is press-and-hold (release → STOP via lock-free `_roof_stop`, a fresh token per press made
  before the `_ble` wait, so an early release cancels a queued press); a re-press within 1000 ms is
  debounced (without a view it would restart the counter → another ~3 s withhold). `actuate_roof` polls
  `1402` ~1 Hz and auto-stops at end of travel (`InfoPopUp` 8, `2308`, as the app) or the limit Position
  (open `1` / closed `0`/`14`; `control.roof_limit_positions`)
  — best-effort over the unit's own limit switches. **A roof move/STOP runs inside a live persistent
  session** (`PersistentSession.actuate_roof`, its heartbeat ticking — no second connection; the unit
  does accept several centrals, 2026-10-10); with none up it opens its own connection, heartbeat on. A roof command never warms
  the session first (no keep-warm nudge, no `CALICTL_SESSION_WAIT_S` wait). See
  `protocol-alignment.md` + `protocol-sequences`.
- **Reads go stale + the unit deep-sleeps.** The 1003 heartbeat runs during reads (`device.read_all`/
  `read` do) to keep the link up (dropped after ~15 s otherwise) and refresh the re-read chars. It does
  NOT refresh water: water is measurement-gated (the unit measures only while its water system is
  powered), so a parked read may return the stale latch **FreshWaterLevel = 1**. `freshness.implausible_water_drop`
  (and its C twins in `csrc/ports.c` + the ESP's `session.c`) holds the last good reading ONLY for a drop to
  ≤ 1 L with grey exactly unchanged (#274; grey reads 0 on every frame on this van, so the old
  "any drop with grey frozen" rule held real readings for weeks). The real app has NO water filter (5.0.8 +
  5.4.0: shows every 1302 frame literally, reads once at connect); our guard is a deliberate deviation.
  Every poll reads 1302 after subscribing and the last frame wins (`R_READ_LAST_FRAME_WINS`). Parked, the
  unit deep-sleeps and stops advertising — buspi can't connect for days until physical use wakes it, so
  access is **inherently intermittent**: `serve` persists last-state + an "as of" timestamp, and the
  web UI shows an offline banner. See `value-freshness.md`.
- **`vehicle` (char 1004):** ignition (terminal-15 is **bit 7**, not 0 — was a decode bug), car
  variant, unit RTC, 2-axis roll/pitch leveling. **Hand-added to `dictionary.yaml` — NOT emitted by
  `extract_protocol`, so preserve it on regen.** Leveling/RTC read only while ignition is on: with ignition off `level_roll`/`level_pitch` are `None`
  (the unit sends 0/0; the app shows "-.-°", #282). (`general`
  is char 1001.)
- **Not installed on this van:** stairs, living-room heater, roof-A/C, satellite, solar (semantics
  static-verified against the app's getters — no live check possible here). The pop-top **roof IS
  installed** (`roof.Installed=1`, #106) but its motor has never been driven by calictl.
- **buspi:** `sudo` needs the Pi password; `serve` runs under `~/solix-env` (the installed unit
  adds `--web 8088` through a `systemctl edit` drop-in, not the committed template); secrets in
  `/etc/buspi/*.env` (root 0600). A parked unit is unreachable (deep-sleep, above). The web UI can
  optionally be fronted by HTTPS on the tailnet via `tailscale serve` (tailnet-only, never
  `funnel`) — see the `buspi-deploy` skill + `docs/raspberry-pi-setup.md` "Remote access over
  Tailscale".
- **The unit accepts several centrals at once** (CAPTURE 2026-10-10: the phone app, buspi and the ESP
  connected simultaneously; the app held an idle heartbeat-only link 46 min while parked). On every
  connect **the unit sends the central an ATT Exchange MTU Request** (Client RX MTU 247) and terminates
  the link (HCI `0x13`) if it is unanswered for 30 s — BlueZ/Android answer it; the ESP needs NimBLE's
  GATT server (`CONFIG_BT_NIMBLE_ROLE_PERIPHERAL=y` + `GATT_SERVER=y`, #279, guarded by
  `test_sdkconfig_gatt_server.py`) or it is "kicked" every ~30 s. "`serve` is the single BLE owner"
  is about buspi's own `hci0`, not a unit slot.
- **App versions:** the owner's phone runs CaliforniaOnTour **5.4.0.3036**; decompile + mapping exist for
  5.4.0 and 5.0.8.3028 (private repo `ckeller42/californiaontour-re`, skill `decompile-app`). For this
  van (CommunicationVersion 2) 5.4.0's protocol is unchanged; 5.4.0 adds a V3 layer (chars `1603`,
  `F002`, V3 1602/F001 layouts) calictl must never write to a V2 unit. Real-phone data collection:
  skill `phone-app-lab` (wireless adb from buspi, HCI snoop, never actuate without the owner's request). Turning a
  recording into a documented fact (tier, scoped claim, docs, mock): skill `real-unit-evidence`.
- **Pairing needs a quiet radio:** any BlueZ client holding discovery (calictl unpaired polls —
  now guarded, readers, HA Bluetooth) kills a new LE link with 0x3e; the unit advertises a
  rotating address, only the bonded identity is stable. See `guided-pairing.md`.
- **Guided pairing (#201):** the wizard (`POST /api/pairing`, owner guide `docs/howto-pair-your-camper.md`)
  probes an existing bond first and **keeps a working one**; it drops a bond only on proof it is stale
  (auth-class failure, or link up but the auth read timed out), then re-discovers and re-pairs
  (`pairing.json` kept). Unreachable/asleep unit = `connect_failed` (bounded retries, 20 s CONNECTING
  budget), never a bond drop; `radio_busy` flags another client holding discovery. CI-verified against
  real BlueZ in a VM (`pairing-real-stack`), **not yet live-verified at the van** (#157).

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
- `tests/test_needs_docstrings.py` fails on a need with no/unprefixed/duplicate `:id:` (e.g. options
  pushed out by a directive on the docstring's first line), a dangling `:links:`, or one `docs/api.rst`
  never autodocs. tests/e2e needs are autodoc'd per function (pytest is mocked, Playwright never imported).
- New jargon → an entry in `docs/glossary.md` (Sphinx `glossary`); link its first use per page with
  `{term}` (MyST) / `:term:` (rst). `tests/test_glossary.py` keeps it the only glossary, terms unique.

## Working style

Read `docs/business-logic/` before changing decode/semantics. Follow the existing
dictionary-driven pattern; put manual offsets only in `overrides.py`. When surfacing a new
signal: catalog it (`tools/triage.py`) → emit it in `semantics` → update dashboard + HA →
run the auditor. Prefer small, test-backed changes.

- **Claude automation is committed as a skill or a `tools/` script, never in `.claude/` config.**
  `.claude/*` is gitignored **except `.claude/skills/`** — `.claude/settings.json` and
  `.claude/agents/` are per-user (untracked). Hook *scripts* live in `tools/hooks/` (shared); their
  wiring lives in the gitignored `settings.local.json` (per-user, like the existing hooks).

## Dependabot

`.github/workflows/dependabot-auto-merge.yml` squash-merges a Dependabot PR once CI has passed
on its exact head commit, but only when no bumped dependency is a semver major (it reads the
`update-type` trailers; a grouped PR waits if any member is major). It does not rely on the
repo's "Allow auto-merge" setting. Major bumps and anything CI rejects stay open for review.
It also skips PRs without an `update-type` trailer, PRs with a commit not authored by Dependabot,
and branches matching `EXCLUDE_REF_PREFIXES` (empty here). A merge made with the workflow's token
does not start `push` workflows, so CI and the docs deploy do not re-run on `main` afterwards;
run them by hand if a bump needs it.
