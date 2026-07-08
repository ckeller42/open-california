# Raspberry Pi guided installer (`install.sh`) — design

## Context

The repo has no Raspberry Pi setup path. The README Quickstart lists `calictl` commands but
assumes the package is already installed, the vehicle is already BLE-bonded, and `CALICTL_ADDR`
is set. The systemd unit (`calictl/deploy/calictl.service`) hardcodes buspi-specific paths
(`/home/pi`, `/etc/buspi`). The vehicle-bonding procedure that actually worked exists only in
session memory, not the repo. So a new owner cannot go from a blank Pi to a running monitor.

This adds a single **guided installer** that takes a fresh Debian/Raspberry Pi OS host from
nothing to `calictl` reading the vehicle over BLE and running as a systemd service — including
the interactive BLE pairing. Delivered as `install.sh` runnable via `curl … | sh`.

Scope decided with the owner: **Core + read service** — deps, venv, pairing, env, and the
read-loop systemd service. The MQTT/InfluxDB/Home-Assistant/Grafana *sinks* stay optional and
are pointed at the existing `calictl/deploy/` docs (a `--with-sinks` flag installs the Python
sink deps, but does not stand up brokers/servers).

## Constraints that shape the design

- **`curl … | sh` consumes stdin with the script.** Ordinary `read` prompts break. Every
  interactive prompt MUST read from `/dev/tty` (the rustup/nvm trick), or the guided flow hangs.
- **Pairing is LE Passkey Entry and cannot be zero-touch.** The camper *displays* a rotating
  6-digit passkey on its "Gerät verbind." (connect-device) screen; the Pi must *type* it, and a
  human must navigate the camper to that screen. The installer *guides* this; it can't automate
  it away. Success is verified by `Bonded: yes` (not by matching the leftover "enter passkey"
  prompt text — that stale-text match caused false negatives during the buspi bring-up).
- **The vehicle advertises a rotating private address (RPA).** Discover it **by device name
  `VWCAMPER`**, never a fixed MAC; the stable **identity** MAC only exists after bonding and is
  **owner PII** — captured per-run, written to a local `0600 root` env file, never committed.
- **`serve` is the single BLE owner** (`hci0`). Verify reads with `calictl status` *before*
  enabling the service, or the running daemon holds the only connection slot.
- **Runtime is stdlib-only + `bleak`.** Core install needs only `bleak`; sinks are separate.

## Deliverables

1. `install.sh` (repo root) — the guided installer.
2. `docs/raspberry-pi-setup.md` — the longform manual the script automates, and the fallback
   when the scripted pairing needs to be done by hand. Includes the pairing walkthrough.
3. README "Raspberry Pi setup" section — the one-liner + an inspect-first variant.
4. A `shellcheck` + `sh -n` CI job for `install.sh`.

## `install.sh` behaviour

Runs as the normal user; uses `sudo` only for apt / the env file / the service, each printed and
confirmed. Idempotent and re-runnable. Prompts read from `/dev/tty`.

1. **Preflight** — require Linux, an `apt` front-end, and a BLE adapter (`bluetoothctl list`).
   Refuse to run as root. Print the plan; on non-Debian or missing adapter, explain and stop.
2. **Dependencies** — `sudo apt-get install -y bluez git python3-venv python3-pip`.
3. **Acquire the repo** — if invoked from inside a clone, use it; otherwise
   `git clone https://github.com/ckeller42/open-california "$DIR"` (default `~/open-california`,
   override `--dir`). `cd` there.
4. **Virtualenv** — `python3 -m venv .venv`; `.venv/bin/pip install --upgrade pip bleak`.
   `--with-sinks` also installs `paho-mqtt influxdb_client`.
5. **Guided pairing** — `sudo systemctl start bluetooth`, power the adapter on. Prompt the user
   to open Bluetooth → *Connect device* on the camper. Scan (bounded, with retries) for name
   `VWCAMPER`, resolve its current address. Register a `KeyboardDisplay` agent, initiate
   `pair`, and when the passkey is requested, prompt the user to type the 6 digits shown on the
   camper (from `/dev/tty`). Verify via `Bonded: yes`/`Paired: yes`. On repeated failure, hand
   off to an interactive `bluetoothctl` session with printed instructions, then re-check the
   bond. Extract the resolved **identity** MAC from `bluetoothctl devices Paired`/`info`.
6. **Env file** — `sudo install -d -m755 "$CONFIG_DIR"`; write `"$CONFIG_DIR/calictl.env"`
   (`CALICTL_ADDR=<identity>`) as `root:0600`. Refuse to overwrite an existing file without
   `--force`. `CONFIG_DIR` defaults to the hostname-neutral **`/etc/opencalifornia`**
   (override `--config-dir`). **buspi keeps its existing `/etc/buspi/` by design** — it was a
   hand-install, its committed unit points there, and the installer renders the unit per host,
   so nothing needs migrating; `/etc/buspi` is treated as a legacy location, not the default.
7. **Verify, then enable** — run `.venv/bin/python -m calictl status` (BLE slot free) to prove
   reads. Then render `calictl.service` from `calictl/deploy/calictl.service` with the real
   `--dir`, venv path, invoking user, and `EnvironmentFile=/etc/opencalifornia/calictl.env`;
   `sudo systemctl enable --now calictl`. `--no-service` skips this and just prints the command.
8. **Summary** — how to actuate (`calictl set cooler power on`), read logs
   (`journalctl -u calictl -f`), and add MQTT/Influx/HA (link `calictl/deploy/…`).

`--dry-run` prints every action (apt, clone, pairing steps, env, service) and executes nothing.
`--yes` skips the plan-confirmation prompt (pairing still needs the tty). Flags: `--with-sinks`,
`--no-service`, `--dir <path>`, `--force`, `--dry-run`, `--yes`.

## Delivery

`install.sh` lands on `main` (via PR #4 or a follow-up), after which:

```sh
# one-liner
curl -fsSL https://raw.githubusercontent.com/ckeller42/open-california/main/install.sh | sh

# inspect-first (recommended)
curl -fsSL https://raw.githubusercontent.com/ckeller42/open-california/main/install.sh -o install.sh
less install.sh && sh install.sh
```

## Security posture

The script is in-repo and inspectable; every privileged action is printed and confirmed (unless
`--yes`); no secrets are piped; the env file is `0600 root`; the vehicle identity MAC never
enters the repo (the CI/pre-commit MAC + VIN guards still hold). The one-liner is documented
alongside the inspect-first variant.

## Testing

- **CI**: `shellcheck install.sh` + `sh -n install.sh` as a new job; keep it green.
- **`--dry-run`**: a full no-op walkthrough asserting the step sequence prints without executing.
- **Testable helper**: the bluetoothctl-output parsing (scan → RPA, and paired-devices →
  identity MAC) is factored into a small pure function with fixture strings, so address
  extraction is unit-tested without a radio.
- **Not automatable here**: the live pairing needs the vehicle (documented). buspi is already
  bonded, so re-pairing is unnecessary there; the doc covers manual pairing for a new host.

## Out of scope

Standing up Mosquitto / InfluxDB / Home Assistant / Grafana (existing `calictl/deploy/` docs);
non-Debian distros; non-systemd init; unattended pairing (impossible — see constraints).
