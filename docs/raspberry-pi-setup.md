# Raspberry Pi setup

Take a fresh Raspberry Pi (or any Debian host with a Bluetooth adapter) from nothing to
`calictl` reading your VW California {term}`Camper Unit` over BLE and running as a background service.

The guided installer does the whole thing — dependencies, virtualenv, **BLE pairing**, the
config file, and the systemd service. Pairing is interactive by nature (the camper shows a
passkey you have to type), so the installer walks you through it.

## Requirements

- Raspberry Pi OS / Debian with Python ≥ 3.11 (Debian 12+ / Raspberry Pi OS Bookworm or newer; {term}`buspi` runs Debian 13 / Python 3.13).
- A Bluetooth LE adapter (the Pi's built-in radio is fine).
- Your own VW California with the Camper Unit, within Bluetooth range.
- `sudo` rights. ~5 minutes.

## Install (one line)

```sh
curl -fsSL https://raw.githubusercontent.com/ckeller42/open-california/main/install.sh | sh
```

Prefer to read it first (recommended for any `curl | sh`):

```sh
curl -fsSL https://raw.githubusercontent.com/ckeller42/open-california/main/install.sh -o install.sh
less install.sh          # inspect
sh install.sh
```

Useful flags: `--dry-run` (preview every step, do nothing), `--no-service` (skip the daemon),
`--with-sinks` (also install the MQTT/InfluxDB client libs), `--dir PATH`, `--config-dir DIR`,
`--force`, `--yes`. See `sh install.sh --help`.

## What it does

1. `apt-get install bluez git python3-venv python3-pip`.
2. Clones the repo (default `~/open-california`) and builds a venv with `bleak`.
3. **Guided pairing** (below) — bonds the Pi to the camper and captures its address.
4. Saves the bond to calictl's **pairing cache**, `~/.local/state/calictl/pairing.json`
   (`0600` — it's your vehicle's identity, kept local, never committed). This is the same file
   the web UI's pairing wizard maintains, so **Unpair / re-pair from the browser persist across
   reboots**. Sink credentials (`--with-sinks`) go to `/etc/opencalifornia/calictl.env` (root, `0600`).
5. Runs `calictl status` to confirm reads, then installs + enables the `calictl` systemd
   service (the read/monitor loop).

> **`CALICTL_ADDR` is a manual override, not the normal config.** calictl resolves it *before*
> the pairing cache, so if you set it in `calictl.env` the wizard's Unpair/re-pair will appear to
> work and then silently revert on the next restart. Leave it unset unless you're forcing a target
> on a dev box.

## The pairing step

The Camper Unit uses **LE Passkey Entry**: it *displays* a 6-digit code and the Pi *types* it.
It also advertises a **rotating address**, so it's found by name (`VWCAMPER`), not a fixed MAC —
the stable identity address only exists after bonding.

The installer will:

1. Ask you to open **Bluetooth → "Connect device"** on the camper so it shows a passkey screen.
   Keep it on that screen during pairing.
2. Scan for `VWCAMPER` and find its current address.
3. Open `bluetoothctl` for you. Run:

   ```
   agent KeyboardDisplay
   default-agent
   pair <address>          # the installer fills this in
   # when prompted, type the 6 digits shown on the camper, then Enter
   trust <address>
   quit
   ```

4. Verify the bond (`Paired: yes`) and capture the resolved identity address.

**Gotcha:** the passkey changes on every attempt — always read the number currently on the
camper screen. "Pairing successful" / `Bonded: yes` is the real signal; ignore any leftover
"enter passkey" prompt text.

### Other Bluetooth software on this Pi

Pairing needs the Pi's Bluetooth adapter free to scan and connect. If something else on the Pi
keeps Bluetooth discovery running at full duty — the Home Assistant Bluetooth integration, a BLE
reader (Anker/Victron/Govee), another instance of `calictl` — a new connection attempt is likely
to fail outright while it does. Pause any such software for the few minutes pairing takes, then
resume it once you're bonded; it doesn't interfere with a working bond, only with making a new
connection. Re-pairing later from the web UI hits the same constraint — see
[How to pair your camper](howto-pair-your-camper.md#before-you-start).

## Manual install (if you skip the script)

```sh
sudo apt-get install -y bluez git python3-venv python3-pip
git clone https://github.com/ckeller42/open-california ~/open-california
cd ~/open-california
python3 -m venv .venv && .venv/bin/pip install bleak
# pair (see "The pairing step" above), note the identity MAC, then save the bond to the
# pairing cache (the file the web wizard's pair/unpair maintain — NOT CALICTL_ADDR, see above):
mkdir -p ~/.local/state/calictl
printf '{"address": "%s"}\n' "<your-identity-mac>" > ~/.local/state/calictl/pairing.json
chmod 600 ~/.local/state/calictl/pairing.json
.venv/bin/python -m calictl status                        # verify reads (resolves from the cache)
```

Then adapt `calictl/deploy/calictl.service` (paths, `User`, `EnvironmentFile`) and
`sudo systemctl enable --now calictl`.

## After install

Need to re-pair later — after moving the SD card to a new Pi, a factory reset, or a
"Bluetooth zurücksetzen" on the camper unit itself — or just prefer a guided flow over
`bluetoothctl`? Use the web UI's pairing wizard instead:
[How to pair your camper](howto-pair-your-camper.md).

```sh
~/open-california/.venv/bin/python -m calictl status        # all functions
~/open-california/.venv/bin/python -m calictl set cooler power on
journalctl -u calictl -f                                    # daemon logs
```

Log lines carry a level and the module (`INFO calictl.serve: polled 14 functions`; the journal adds
the timestamp — outside systemd calictl stamps them itself). `CALICTL_LOG_LEVEL=DEBUG` in
`calictl.env` adds the per-poll chatter; `WARNING` keeps only failures and refusals. To record the
unit's raw BLE traffic for offline analysis add `CALICTL_BLE_TRACE=/home/pi/ble.jsonl` (one JSON
line per notification/read/write; `python3 -m tools.trace_compare` replays it against the mock).

If polls keep failing while the unit is **visibly advertising** (the journal shows repeated connect
timeouts), tune `CALICTL_CONNECT_TIMEOUT_S` (per-attempt BLE connect timeout, default `30` s; each
connect makes up to 3 attempts). An awake unit answers in well under a second, but one that
advertises while ignoring connection requests burns the whole timeout on every attempt, so a long
timeout samples the unit rarely and keeps missing its brief connectable windows. **Lower** it (e.g.
`10`) to sample more often on a flaky link; **raise** it only if connects are being cut short while
the unit is genuinely responding.

### Environment variables

All knobs are read from the environment (for the service: `calictl.env`, the unit's
`EnvironmentFile`). Timing values are seconds. Module-level ones are read once at import, so a
change needs a daemon restart.

| Variable | Default | Meaning |
|---|---|---|
| `CALICTL_ADDR` | unset | manual BLE address override (wins over the pairing cache) — see "The pairing step" above |
| `CALICTL_PAIRING_CACHE` | `$XDG_STATE_HOME/calictl/pairing.json` (`~/.local/state/…`) | path of the persisted-bond file the pairing wizard maintains |
| `CALICTL_ENABLE_WRITES` | off | `1`/`true`/`yes` = allow control writes (same as `--enable-writes`) |
| `CALICTL_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `CALICTL_LOG_TIMESTAMP` | on, except under the systemd journal (`JOURNAL_STREAM` set) | `0`/`false`/`no` = no timestamp; any other value forces it on |
| `CALICTL_BLE_TRACE` | unset | path of a JSONL trace of every notify/read/write/link event |
| `CALICTL_BLE_TRACE_HEARTBEAT` | off | non-empty, not `0` = also trace the `1003` heartbeat writes |
| `CALICTL_CONNECT_TIMEOUT_S` | `30` | per-attempt BLE connect timeout (above) |
| `CALICTL_ADAPTER_RESET` | off | `1` = power-cycle the adapter to recover from a failed connect. Off because `hci0` is shared with the other buspi BLE readers |
| `CALICTL_PERSISTENT_SESSION` | `1` | `0` = no persistent armed session; connect per operation |
| `CALICTL_UI_IDLE_S` | `25` | release the persistent session after this long without web-UI activity — nobody is watching, so the held link and its heartbeat stop, sparing the unit and the shared radio |
| `CALICTL_SESSION_WAIT_S` | `6` | how long a command waits for the supervisor's session before falling back to a cold connect (a roof move never waits: it runs inside a live session, or opens its own connection at once) |
| `CALICTL_FAST_CONFIRM_S` | `1.2` | how long a lighting command waits for the `1502` notification before returning an optimistic "sent" |
| `CALICTL_STATE_CACHE` | `~/.cache/calictl/last_state.json` | persisted last-known state (shown while the van is asleep) |
| `CALICTL_OUTCOMES_CACHE` | `~/.cache/calictl/poll_outcomes.jsonl` | per-poll outcome log (classifies telemetry gaps: deep sleep vs BLE error vs daemon down) |
| `CALICTL_FW_SNAPSHOT_DIR` | `~/.cache/calictl/fw-snapshots` | where a firmware change's raw-frame capture is written |
| `CALICTL_STORE_GENERALPURPOSE` | off | `1`/`true`/`yes` = log the raw F000/F001 diagnostic register to InfluxDB (RE probe) |
| `CALICTL_MQTT_EXPIRE_AFTER_S` | `300` | Home Assistant `expire_after`: sensors go `unavailable` after this long without a state message |
| `CALICTL_HEARTBEAT_PERIOD_S` | `0.6` | `1003` liveness-heartbeat period |
| `CALICTL_HEARTBEAT_WARMUP_S` | `2.0` | heartbeat warm-up before a read pass |
| `CALICTL_ARM_DELAY_S` | `3.0` | heartbeat time before a control write (lets the unit register it) |
| `CALICTL_SETTLE_S` | `2.5` | wait after a write before the readback |
| `CALICTL_FOLLOW_DELAY_S` | `0.3` | gap before a commit/follow frame |
| `CALICTL_WATER_PUSH_WAIT_S` | `0` (disabled) | wait for a fresh `1302` water push during a read — an unvalidated hypothesis, see `business-logic/value-freshness.md` |
| `CALICTL_ROOF_PERIOD_S` | `0.5` | roof move-frame (SafetyCounter) cadence |
| `CALICTL_ROOF_MAX_TRAVEL_S` | `30.0` | hard cap on one roof move |
| `CALICTL_ROOF_LIMIT_POLL_S` | `1.0` | how often a roof move polls `Position` to auto-stop at the limit |
| `CALICTL_AUTO_CAMPER_MIN_SOC`, `…_WINDOW_S`, `…_MAX_FAILS` | `20`, `120`, `3` | auto camper mode guards — see `business-logic/auto-camper-mode.md` |
| `CALICTL_OBSERVE_BURST_INTERVAL_S`, `CALICTL_OBSERVE_BURST_S` | `3`, `180` | fast-poll burst after an engine start — see `business-logic/auto-camper-mode.md` |

The {term}`heartbeat` / arm / settle / follow / roof timings are the proven on-device values; the mock and
e2e harness shrink them for speed. **Do not lower them for real hardware.**

Add Home Assistant / MQTT / InfluxDB / Grafana: `calictl/deploy/homeassistant/HOMEASSISTANT.md`
and `calictl/deploy/GRAFANA.md`. `install.sh --with-sinks` installs the Python client libs
**and prompts for the MQTT broker credentials**, writing them to `calictl.env` — so the
calictl→broker side is done; you still stand up the broker + Home Assistant and pair HomeKit
(the UI steps) per HOMEASSISTANT.md.

## Web UI

The daemon can also serve a browser replica of the app's Vehicle-tab controls (dashboard →
per-feature screens) from the same BLE-owning process — no extra connection, no broker:

```sh
# add --web to the service, or run it directly:
~/open-california/.venv/bin/python -m calictl serve --web 8080
#   then open http://<pi-hostname-or-ip>:8080
```

- The daemon is **read-only by default** (controls disabled) — pass `--enable-writes` (or
  `CALICTL_ENABLE_WRITES=1`) to allow control writes.
- The UI is **unauthenticated** — expose it only on a trusted LAN (same posture as the
  Home Assistant / Grafana stack), never on the open internet. To reach it **remotely**, use
  Tailscale (see below) — a private, encrypted overlay — rather than port-forwarding.
- Uninstalled features are hidden. Controls the unit refuses in the current state are **greyed
  out with a reason** (hover/long-press the row): camping mode only when stationary (ignition
  off); camping lights + rear USB only while camping mode is on; roof open/close while a roof
  alert blocks movement (roof moved too often, low battery, fault, roof open while the vehicle
  may move). The roof is **press-and-hold**: it moves while the button is held and stops the
  moment you release (anywhere on the page). Lighting writes DO
  actuate the lamps, but the state-char readback is a write-through echo, so the UI says
  "Sent — check the lamp" rather than confirming from the readback.
- The UI is in **English by default**; the ⋮ menu has a **Deutsch / English** entry that switches
  the language. On first load it follows the browser language (German browsers start in German);
  your choice is remembered per browser (`localStorage`).
- Every screen's ⋮ menu has a **Help** entry: a short sheet (English/German) that explains that
  screen and links to the matching page of these docs — see
  [In-app help](UI-DESIGN-RATIONALE.md#in-app-help-help). The screens themselves only show
  short status hints (e.g. "🕒 measured 3 min ago").

To enable it under systemd, append `--web 8080` to the unit's `ExecStart` and restart
(`sudo systemctl daemon-reload && sudo systemctl restart calictl`).

### Satellite mode (`--no-ble`)

When only the ESP32 satellite talks to the camper unit, run the daemon without BLE. `serve
--no-ble` never opens a BLE connection (no session, no poll, no pairing, no commands). The web UI
serves the cached last state (read-only), MQTT publishes availability only, and InfluxDB is
filled by `calictl-esp-ingest` from the ESP SD log. The web UI shows a "🛰 Satellite mode"
pill ("🛰 Satellit-Modus" in German) instead of an offline banner. Set it with a drop-in
(`sudo systemctl edit calictl`); the empty `ExecStart=` line clears the unit's own command:

```ini
[Service]
ExecStart=
ExecStart=/home/<user>/solix-env/bin/python -m calictl serve --web 8088 --no-ble
```

Use the same interpreter path as the installed unit (`systemctl cat calictl`), then
`sudo systemctl restart calictl`. Remove the drop-in to return to BLE mode.

### Remote access over Tailscale (HTTPS)

The web UI is plain HTTP and unauthenticated, so it must stay on a trusted network. To reach it
from **outside that network** — your phone on cellular, say — put it on a
[Tailscale](https://tailscale.com) tailnet instead of forwarding a port. Tailscale is a private,
encrypted (WireGuard) overlay network between your own devices; `tailscale serve` then fronts the
daemon with a **valid HTTPS certificate** and exposes it **only to devices on your tailnet**.

This is optional and changes no code — it's Pi-side configuration.

**One-time setup:**

1. **Install Tailscale on the Pi and log in** — `curl -fsSL https://tailscale.com/install.sh | sh`
   then `sudo tailscale up`. Install it on the phone/laptop you'll browse from too, on the same
   tailnet. (In the admin console, keep **MagicDNS** enabled — on by default.)
2. **Enable Serve + HTTPS for the tailnet** (once per tailnet, as an admin): in the
   [admin console](https://login.tailscale.com/admin) enable **HTTPS Certificates** (DNS page) and
   **Serve** (the first `tailscale serve` prints a one-click enable link if it isn't on yet).
3. **Front the daemon** — with the port you gave `--web` (e.g. `8080`):

   ```sh
   sudo tailscale serve --bg 8080
   ```

   First run provisions a Let's Encrypt cert (~30–60 s). It prints your URL:

   ```
   https://<pi-name>.<your-tailnet>.ts.net/   →  proxy http://127.0.0.1:8080
   ```

Open that `https://…` URL from any device on your tailnet — clean padlock, encrypted, no browser
warning. The config **persists across reboots**; the plain `http://<pi>:<port>` LAN path keeps
working alongside it. Over this HTTPS URL the UI is also an offline-capable app: *Add to Home
screen* / *Install app*, it opens instantly and shows the last values while the Pi is unreachable.
Check it with `sudo tailscale serve status`; remove it with
`sudo tailscale serve --https=443 off`.

- **Use `serve`, never `funnel`.** `tailscale funnel` would publish the same unauthenticated,
  write-capable UI to the **entire public internet** — anyone could actuate the heater/roof. Serve
  keeps it tailnet-only.
- **Access is device-based, not a password.** Anyone whose device is on your tailnet can reach it;
  share access by adding their device (or sharing the node) — there's no in-app login.
- `tailscale serve`/`cert` need root; if you don't want `sudo` each time, run
  `sudo tailscale set --operator=$USER` once.

## ESP ingest (gap fill)

The [ESP32 satellite](firmware.md) logs every unit frame to its microSD card, stamped with its own
time. `calictl esp-ingest` pulls that log over WiFi (`GET /api/log`) and writes the readings into
InfluxDB **only where buspi has none of its own** — no point of that function within ±90 s — so
buspi's live data always wins and the ESP fills the hours buspi was off or out of range
(`R_ESP_INGEST_GAPFILL`). It is not BLE: `calictl serve` stays the single BLE owner.

- **Points** — measurement `camper`, the same `function` + fields as serve, plus the tag
  `source=esp` (a series of its own: dashboards filtering by function + field show both; the ESP
  data can be audited or deleted by that tag), at the record's own time from the card.
- **Thinning** — the ESP sees every notify (several a second while something changes); of the
  gap records, only the **last per function and 30 s slot** (`floor(ts / 30 s)`, buspi's poll
  cadence) is written, once a later slot's record arrives — the newest waits in the cursor. A
  record whose alert/state codes (`*_code`: `fault_code`, `alert_code`, `fresh_alert_code`,
  `waste_alert_code`, `error_code`; `ignition_on`) differ from the function's previous point is
  always written, at its own time. Counted as `thinned`.
- **Time** — a record with `ts` keeps it; one without (between boot/link-up and the first clock) is
  back-stamped from the next stamped record of the same ESP boot by uptime; the running boot's
  unstamped records wait for their stamp; an older boot that never got one is skipped and counted.
- **Replay** — the frames go through serve's own decode, interpretation and water guard (the 1 L
  hold and the ramp debounce), in seq order.
- **Cursor** — `~/.cache/calictl/esp_cursor.json` (`--cursor`): the last ingested seq plus the
  replay state (incl. each slot's pending point and the last written codes). It moves only after a successful write; a failed run (ESP or InfluxDB unreachable)
  retries from the same place, and a re-run writes identical points. A single dropped page (a
  reset connection or a timeout on the satellite's WiFi) is retried up to 3 times, 2 s then 4 s
  apart, before the run gives up.
- **Diagnostics** — the satellite's own `diag`/`perf`/`evt`/`log` records ([firmware](firmware.md),
  `R_FW_DIAG_LOG`) exist only on the ESP, so they skip replay and gap fill and are written as they
  are, at their time (same back-stamping and cursor), one measurement each (`R_ESP_INGEST_DIAG`):
  `esp_diag` (heap, PSRAM, CPU, RSSI, `link_up`, `uptime_s`, `stack_min_<task>`), `esp_perf` (tag
  `route`, or `char` + `op`; fields `ms`, `bytes`, `status`), `esp_evt` (tag `kind`; its numbers and
  strings such as `reason`, `reset_reason`, `result`, `state`, `error`, `decision`), `esp_log` (tags
  `lvl`, `tag`; `msg`, or `dropped`). The summary counts them per measurement (`diag_points`);
  `--verify` lists replayed vs stored per measurement. They feed the dashboard's **📟 ESP
  satellite** row (heap, CPU, RSSI, link timeline + reconnect time, reboots, HTTP p50/p95 per
  route, BLE op latency, warnings).
- **UX records** — the satellite's `ux` records (one per web-UI beacon event, `R_UX_TELEMETRY`)
  become the same `ui_ux` points calictl writes for its own `POST /api/ux` (same tags, same field
  types), tagged `source=esp` — calictl's own carry no `source` — so both land in one series and
  stay apart. They feed the **🧭 UI usage** row (views per screen, controls used, page-load
  p50/p95, script errors), each split buspi / esp.
- **Sensors** — the satellite's own BLE sensor readings (`ext` records, at most one a minute per
  sensor: Govee, Victron MPPT, Victron battery sense) become the rows buspi's own readers write —
  measurement `govee` (`temp_c`, `humidity`, `battery`, plus `online` = 1), `victron_mppt`
  (`battery_voltage`, `battery_current`, `charge_state`, `error_code`, `load_current`, `pv_power`,
  `yield_today_kwh`), `victron_battery` (`battery_voltage`, `temperature_c`) — floats only, a field
  the sensor marks n/a left out, tags `device=<the ESP's alias>` + `source=esp`. **Gap-filled like
  the camper data:** written only when buspi's own series of that measurement (no `source` tag, any
  `device`) has no point within ±90 s; an unknown type is dropped. Counted in `diag_points`
  (`R_ESP_INGEST_EXT`, verified by `T_ESP_INGEST_EXT`).
- **Config** — serve's env (`INFLUXDB_TOKEN`, `INFLUX_URL`/`INFLUX_ORG`/`INFLUX_BUCKET`, from
  `/etc/buspi/secrets.env`). Each run logs `{pulled, written, skipped_covered, thinned,
  skipped_no_time, held, lost, diag_points}` (`lost` = records the card deleted at 90 % full before they were ingested).

```sh
python3 -m calictl esp-ingest --url http://<esp> --dry-run   # pull + time + gap check, write nothing:
                                                             # per function count, time range, tsrc mix,
                                                             # covered vs gap, sample points
python3 -m calictl esp-ingest --url http://<esp>             # one real run
python3 -m calictl esp-ingest --url http://<esp> --verify    # check InfluxDB: buspi vs source=esp points per
                                                             # function, ESP points within ±90 s of a buspi
                                                             # point (must be 0; exit 1 otherwise), and the
                                                             # ESP replay vs buspi's points side by side
                                                             # (water liters, SoC levels, currents)
```

**The timer (not installed by `install.sh`).** `calictl/deploy/calictl-esp-ingest.service` (oneshot)
and `calictl-esp-ingest.timer` (every 5 min). After a first `--dry-run` and a `--verify` look:

```sh
sudo cp calictl/deploy/calictl-esp-ingest.{service,timer} /etc/systemd/system/
sudo systemctl edit calictl-esp-ingest.service   # [Service] Environment=CALICTL_ESP_URL=http://<esp>
sudo systemctl daemon-reload && sudo systemctl enable --now calictl-esp-ingest.timer
journalctl -u calictl-esp-ingest -n 20            # the per-run summary
```

## ESP soak report

`tools/esplab/soak_report.py` reads the last 72 h of the {term}`satellite`'s diagnostics (`esp_evt`,
`esp_diag`, `esp_log` from [ESP ingest](#esp-ingest-gap-fill)), the `esp-ingest` journal summaries
the ESP's `/api/state` and buspi's own `govee`/`victron_*` points, prints one line per criterion (10) plus `VERDICT PASS|FAIL`, writes
`~/esp-soak/<date>.txt` and one `esp_soak` point (exit 0 PASS, 1 FAIL, 2 cannot run). A `SKIP`
never fails the verdict.

| # | Criterion | PASS when |
|---|---|---|
| 1 | `telemetry` | the window holds `esp_diag` samples (an empty window is a FAIL, not a quiet pass) |
| 2 | `reboots` | no boot with `reset_reason` panic, int_wdt, task_wdt, wdt, brownout or cpu_lockup (sw, poweron, usb are listed only) |
| 3 | `fw` | every `boot` point in the window carries the ESP's current `fw` (sha prefix compare; SKIP when the ESP is unreachable or there is no boot point), so the 72 h ran on one build |
| 4 | `link` | at most 10 `link_down` events per boot interval (the `link_up` fraction is printed as information: with camping mode off and no shore power the {term}`camper unit` ends every link after about 30 s) |
| 5 | `touch_nacks` | per boot, max cumulative touch `errors` / hours of that boot is at most 5 |
| 6 | `ingest` | every `esp-ingest` summary has `lost == 0` and the last has `held == 0` (SKIP without a journal summary) |
| 7 | `heap_min` | `heap_min` is at least 32768 B in every `esp_diag` sample |
| 8 | `screen` | always SKIP until an `evt display` record exists (reserved, see [firmware](firmware.md)) |
| 9 | `i2c_timeouts` | `esp_log` lines with `i2c.master` and `timeout`: at most 2 per 24 h |
| 10 | `sensors` | for each ESP sensor alias (`ext.<alias>` paired by its `type`) whose `/api/state` `ext` reading and buspi's own last point (no `source` tag) are both at most 5 min old: `battery_voltage` within 0.05 V, `temp_c`/`temperature_c` within 0.5 °C, `humidity` within 2 %, `battery` within 5; a FAIL names `alias.field esp=… buspi=…` (SKIP when nothing is comparable) |

Requirement `R_SOAK_REPORT` (in the script's docstring, `docs/api.md`).

**Gate rule.** The stable firmware must be green for 72 h; then switch the sensors on
and run another 72 h; then 72 h with it off again, then 72 h on. Only a green window counts.

**The timer (not installed by `install.sh`).** Same shape as the ingest timer, daily at 08:00:

```sh
sudo cp calictl/deploy/calictl-esp-soak.{service,timer} /etc/systemd/system/
sudo systemctl edit calictl-esp-soak.service   # [Service] Environment=CALICTL_ESP_URL=http://<esp>
sudo systemctl daemon-reload && sudo systemctl enable --now calictl-esp-soak.timer
sudo systemctl start calictl-esp-soak.service && cat ~/esp-soak/$(date -I).txt
```

## Wake and shutdown wires from the ESP satellite

The {term}`satellite` can wake a halted Pi 4 and shut it down cleanly (firmware: [Pi wake + shutdown
wiring](firmware.md#pi-wake--shutdown-wiring), `R_FW_PI_WAKE` / `R_FW_PI_HALT`). Three wires from the
CoreS3's Port B (black Grove socket) to the Pi's 40-pin header:

![Wiring from the CoreS3 Port B to the Pi 4 header: yellow G9 to pin 5, white G8 to pin 11, black GND to pin 6](assets/esp-pi-wiring.svg)

| Grove wire (Port B) | CoreS3 pin | Pi header | Does |
|---|---|---|---|
| yellow | G9 | pin 5 ({term}`GPIO`3) | wake: 200 ms low boots a halted Pi |
| white | G8 | pin 11 (GPIO17) | shutdown: 500 ms low = the power key |
| black | GND | pin 6 (GND) | common ground |
| red | 5V | not connected | tape it off |

On the Pi:

1. `/boot/firmware/config.txt`: add `dtoverlay=gpio-shutdown,gpio_pin=17` (the {term}`gpio-shutdown` overlay). **Never put the overlay
   on GPIO3:** the ignition wake pulse would then shut a running Pi down.
2. {term}`EEPROM` (`sudo rpi-eeprom-config --edit`): `WAKE_ON_GPIO=1` and `POWER_OFF_ON_HALT=0`, so GPIO3
   still wakes the halted Pi.
3. {term}`logind` must act on the power key: `grep -r HandlePowerKey /etc/systemd/logind.conf*` must not
   show `ignore` (the default, `poweroff`, is right).
4. Reboot once so the overlay loads.

**Wiring test** (before the overlay is active, or it shuts the Pi down): on the ESP console
`pi halt 5000` holds the shutdown line low for 5 s; meanwhile {term}`pinctrl` (`pinctrl get 17`) on the Pi reads `lo`.
`pi hold 3000` does the same for the wake line (pin 5). The satellite's "Shut down after parking"
and battery-guard settings are in its Satellite settings ([firmware](firmware.md#pi-halt-r_fw_pi_halt)).

## Troubleshooting

- **`VWCAMPER` not found** — make sure the camper is on its "Connect device" screen (it only
  advertises pairable there), the Pi's adapter is up (`bluetoothctl power on`), and no other
  scanner on the Pi keeps discovery running.
- **`calictl status` fails after pairing** — the bond didn't complete; re-run pairing. With the
  daemon running, read its `/api/state` instead — the CLI would open a second link from the Pi.
- **Existing hosts** — the reference host `buspi` predates this installer and keeps its config in
  `/etc/buspi/`; that's a legacy location. New installs use `/etc/opencalifornia/`. The unit file
  is rendered per host, so both work — nothing needs migrating.

(known-issue-kernel-6-18-50-breaks-ble-reconnects)=

## Known issue: kernel 6.18.50 breaks BLE reconnects

Raspberry Pi OS kernel **6.18.50** breaks reconnecting to a bonded LE peer
that rotates its address (an RPA — the camper unit does) on controllers without LL privacy, such
as the Pi 4's CYW43455. The failure is silent and total:

- Pairing **succeeds** (an active scan finds the unit and the first connect goes straight to the
  discovered address), but **every reconnect fails**: the daemon logs
  `poll skipped: no BLE session to <addr> after retries (TimeoutError)` on every poll, forever.
- In `btmon`, each attempt is a passive scan with `Filter policy: Ignore not in accept list`
  followed ~8 s later by `MGMT Connect Failed` — and **no `LE Create Connection` is ever issued**.
  The kernel waits for controller-side RPA resolution that this chip cannot do, instead of
  resolving in the host as older kernels did. Kernel **6.18.34 works** (it connects directly to
  the resolved RPA).

Until a fixed kernel is verified, pin 6.18.34:

```sh
# && throughout: a failed copy must not leave config.txt pointing at a missing kernel image
sudo cp /boot/vmlinuz-6.18.34+rpt-rpi-v8 /boot/firmware/kernel8-634.img \
  && sudo cp /boot/initrd.img-6.18.34+rpt-rpi-v8 /boot/firmware/initrd8-634.img \
  && printf 'kernel=kernel8-634.img\ninitramfs initrd8-634.img followkernel\n' | sudo tee -a /boot/firmware/config.txt \
  && sudo apt-mark hold linux-image-6.18.34+rpt-rpi-v8 linux-image-6.18.34+rpt-rpi-2712 \
  && sudo reboot
```

The `kernel=` line survives OS updates (newer kernels install but are not booted); remove the two
lines from `config.txt` to test a new kernel, and re-add them if reconnects fail again. To qualify
a kernel, run `sudo tools/kernel_ble_check.sh` with the unit awake: it watches one poll window in
`btmon` and tells OK, the regression signature (adverts seen, zero `LE Create Connection`), or
"unit asleep, inconclusive" apart.

Related pitfalls:

- **A removed bond can resurrect**: `bluetoothd` re-persists bonds from memory when it shuts down,
  so a bond deleted shortly before a reboot/restart may be back afterwards (and a stale bond
  blocks re-pairing). After an unpair, verify with `bluetoothctl info <addr>` and remove again if
  needed.
- **Reboots restart BLE scanner services** (Home Assistant integrations, vendor readers). A
  co-resident client holding discovery kills new LE connections (`radio_busy` in the pairing
  wizard, HCI {term}`0x3e <HCI reason code>`) — stop those scanners before pairing.
- **The unit's passkey window is ~30 s** from the moment the wizard shows "waiting for passkey";
  entering the code later fails the attempt (`Authentication Canceled`), so have eyes on the
  camper's screen before you start.
