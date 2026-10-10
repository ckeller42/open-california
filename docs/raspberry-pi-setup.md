# Raspberry Pi setup

Take a fresh Raspberry Pi (or any Debian host with a Bluetooth adapter) from nothing to
`calictl` reading your VW California Camper Unit over BLE and running as a background service.

The guided installer does the whole thing — dependencies, virtualenv, **BLE pairing**, the
config file, and the systemd service. Pairing is interactive by nature (the camper shows a
passkey you have to type), so the installer walks you through it.

## Requirements

- Raspberry Pi OS / Debian with Python ≥ 3.11 (Debian 12+ / Raspberry Pi OS Bookworm or newer; buspi runs Debian 13 / Python 3.13).
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
| `CALICTL_UI_IDLE_S` | `25` | release the persistent session after this long without web-UI activity, so the phone app can use the single slot |
| `CALICTL_SESSION_WAIT_S` | `6` | how long a command waits for the supervisor's session before falling back to a cold connect (a roof move never waits: it takes the slot for its own connection) |
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

The heartbeat / arm / settle / follow / roof timings are the proven on-device values; the mock and
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

To enable it under systemd, append `--web 8080` to the unit's `ExecStart` and restart
(`sudo systemctl daemon-reload && sudo systemctl restart calictl`).

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
working alongside it. Check it with `sudo tailscale serve status`; remove it with
`sudo tailscale serve --https=443 off`.

- **Use `serve`, never `funnel`.** `tailscale funnel` would publish the same unauthenticated,
  write-capable UI to the **entire public internet** — anyone could actuate the heater/roof. Serve
  keeps it tailnet-only.
- **Access is device-based, not a password.** Anyone whose device is on your tailnet can reach it;
  share access by adding their device (or sharing the node) — there's no in-app login.
- `tailscale serve`/`cert` need root; if you don't want `sudo` each time, run
  `sudo tailscale set --operator=$USER` once.

## Troubleshooting

- **`VWCAMPER` not found** — make sure the camper is on its "Connect device" screen (it only
  advertises pairable there), the Pi's adapter is up (`bluetoothctl power on`), and nothing else
  already holds the single BLE slot.
- **`calictl status` fails after pairing** — the bond didn't complete; re-run pairing. Only one
  controller reads reliably at a time; close the phone app while testing.
- **Existing hosts** — the reference host `buspi` predates this installer and keeps its config in
  `/etc/buspi/`; that's a legacy location. New installs use `/etc/opencalifornia/`. The unit file
  is rendered per host, so both work — nothing needs migrating.

(known-issue-kernel-6-18-50-breaks-ble-reconnects)=

## Known issue: kernel 6.18.50 breaks BLE reconnects

Raspberry Pi OS kernel **6.18.50** (rolled out 2026-09) breaks reconnecting to a bonded LE peer
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

Diagnosed on `buspi` 2026-10-08. Until a fixed kernel is verified, pin 6.18.34:

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

Related pitfalls seen in the same debugging session:

- **A removed bond can resurrect**: `bluetoothd` re-persists bonds from memory when it shuts down,
  so a bond deleted shortly before a reboot/restart may be back afterwards (and a stale bond
  blocks re-pairing). After an unpair, verify with `bluetoothctl info <addr>` and remove again if
  needed.
- **Reboots restart BLE scanner services** (Home Assistant integrations, vendor readers). A
  co-resident client holding discovery kills new LE connections (`radio_busy` in the pairing
  wizard, HCI 0x3e) — stop those scanners before pairing.
- **The unit's passkey window is ~30 s** from the moment the wizard shows "waiting for passkey";
  entering the code later fails the attempt (`Authentication Canceled`), so have eyes on the
  camper's screen before you start.
