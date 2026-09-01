# Raspberry Pi setup

Take a fresh Raspberry Pi (or any Debian host with a Bluetooth adapter) from nothing to
`calictl` reading your VW California Camper Unit over BLE and running as a background service.

The guided installer does the whole thing — dependencies, virtualenv, **BLE pairing**, the
config file, and the systemd service. Pairing is interactive by nature (the camper shows a
passkey you have to type), so the installer walks you through it.

## Requirements

- Raspberry Pi OS / Debian (any recent version; buspi runs Debian 13 / Python 3.13).
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
4. Writes `CALICTL_ADDR` to `/etc/opencalifornia/calictl.env` (root, `0600` — it's your
   vehicle's identity, kept local, never committed).
5. Runs `calictl status` to confirm reads, then installs + enables the `calictl` systemd
   service (the read/monitor loop).

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

## Manual install (if you skip the script)

```sh
sudo apt-get install -y bluez git python3-venv python3-pip
git clone https://github.com/ckeller42/open-california ~/open-california
cd ~/open-california
python3 -m venv .venv && .venv/bin/pip install bleak
# pair (see "The pairing step" above), note the identity MAC, then:
sudo install -d -m755 /etc/opencalifornia
echo "CALICTL_ADDR=<your-identity-mac>" | sudo tee /etc/opencalifornia/calictl.env
sudo chmod 600 /etc/opencalifornia/calictl.env
CALICTL_ADDR=<mac> .venv/bin/python -m calictl status     # verify reads
```
Then adapt `calictl/deploy/calictl.service` (paths, `User`, `EnvironmentFile`) and
`sudo systemctl enable --now calictl`.

## After install

```sh
~/open-california/.venv/bin/python -m calictl status        # all functions
~/open-california/.venv/bin/python -m calictl set cooler power on
journalctl -u calictl -f                                    # daemon logs
```

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
- Uninstalled features are hidden; roof control asks for confirmation. Lighting writes DO
  actuate the lamps, but the state-char readback is a write-through echo, so the UI says
  "Sent — check the lamp" rather than confirming from the readback.
- Labels are neutral by default. To render your app's exact text, rebuild the screen data
  against your own APK strings — `python3 -m tools.build_web --strings <apk.cvr.json>`
  (local only; never committed, like the icons).

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
working alongside it. Check it with `tailscale serve status`; remove it with
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
