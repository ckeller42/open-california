#!/bin/sh
# open-california — guided Raspberry Pi installer.
#
# Takes a fresh Debian / Raspberry Pi OS host from nothing to `calictl` reading the VW
# California Camper Unit over BLE and running as a systemd service — including the
# interactive BLE pairing. Scope: dependencies + venv + guided pairing + env + read-loop
# service. MQTT/InfluxDB/Home-Assistant/Grafana sinks stay optional (see calictl/deploy/).
#
#   curl -fsSL https://raw.githubusercontent.com/ckeller42/open-california/main/install.sh | sh
#
# Prefer inspect-first:
#   curl -fsSL .../install.sh -o install.sh && less install.sh && sh install.sh
#
# POSIX sh (runs under dash when piped to `sh`). Prompts read from /dev/tty so the guided
# flow works even when the script itself arrives on stdin. Idempotent and re-runnable.
# Every privileged action is printed; nothing outside the chosen dirs is touched.
set -eu

REPO_URL="https://github.com/ckeller42/open-california"
DEVICE_NAME="VWCAMPER"                 # the unit advertises this name (rotating address)
DEFAULT_DIR="${HOME:-/root}/open-california"
CONFIG_DIR="/etc/opencalifornia"       # hostname-neutral; buspi's legacy /etc/buspi is separate
SERVICE_NAME="calictl"

DIR="$DEFAULT_DIR"
WITH_SINKS=0; NO_SERVICE=0; DRY_RUN=0; ASSUME_YES=0; FORCE=0
IDENTITY=""

# --- tiny output helpers ----------------------------------------------------
info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
open-california installer
  --with-sinks     also pip-install MQTT/InfluxDB client deps (paho-mqtt, influxdb_client)
  --no-service     do not install/enable the systemd service (just print the command)
  --dir PATH       install location for the repo + venv (default: $DEFAULT_DIR)
  --config-dir DIR env-file directory (default: $CONFIG_DIR)
  --force          overwrite an existing env file
  --dry-run        print every action, execute nothing
  --yes, -y        skip the plan confirmation (pairing still prompts on the terminal)
  -h, --help       this help
EOF
}

# run a side-effecting command, or just print it under --dry-run.
run() {
  if [ "$DRY_RUN" = 1 ]; then printf '  + %s\n' "$*"; return 0; fi
  "$@"
}

# read one line from the controlling terminal (works even when piped to `sh`).
ask() {  # ask "prompt" -> echoes the reply
  _prompt="$1"
  if [ "$DRY_RUN" = 1 ]; then printf '%s [dry-run: auto]\n' "$_prompt" >&2; echo ""; return 0; fi
  if [ ! -r /dev/tty ]; then die "no terminal for prompts (need an interactive shell for pairing)"; fi
  printf '%s ' "$_prompt" > /dev/tty
  IFS= read -r _reply < /dev/tty || _reply=""
  echo "$_reply"
}

confirm() {  # confirm "question" -> 0 if yes
  [ "$ASSUME_YES" = 1 ] && return 0
  _a=$(ask "$1 [y/N]")
  case "$_a" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# --- bluetoothctl output parsing (pure; unit-tested via OC_INSTALL_LIB) ------
# Extract the first MAC on a `bluetoothctl devices` line naming DEVICE_NAME.
# Input: bluetoothctl output on stdin. Output: the MAC, or empty.
parse_device_mac() {
  # lines look like: "Device AA:BB:CC:DD:EE:FF VWCAMPER"
  awk -v name="$DEVICE_NAME" \
    '$1=="Device" && $0 ~ name { print $2; exit }
     /^\[NEW\] Device/ && $0 ~ name { print $3; exit }'
}

# --- steps ------------------------------------------------------------------
# fail a precondition — but only warn under --dry-run, so the walkthrough previews anywhere.
_require() { if [ "$DRY_RUN" = 1 ]; then warn "$*"; else die "$*"; fi; }

preflight() {
  info "Preflight checks"
  [ "$(uname -s)" = "Linux" ] || _require "this installer targets Linux (Raspberry Pi OS / Debian)"
  [ "$(id -u)" != "0" ] || _require "run as your normal user, not root (sudo is used per-step)"
  command -v apt-get >/dev/null 2>&1 || _require "no apt-get — this installer supports Debian-family only"
  command -v sudo >/dev/null 2>&1 || _require "sudo is required"
  if [ "$DRY_RUN" != 1 ] && ! command -v bluetoothctl >/dev/null 2>&1; then
    warn "bluetoothctl not found yet — the bluez step installs it"
  fi
}

install_deps() {
  info "Installing system dependencies (bluez, git, python venv)"
  pkgs="bluez git python3-venv python3-pip"
  run sudo apt-get update
  # shellcheck disable=SC2086
  run sudo apt-get install -y $pkgs
}

acquire_repo() {
  if [ -f "./calictl/cli.py" ] && [ -d "./calictl" ]; then
    DIR="$(pwd)"
    info "Using the repo checkout at $DIR"
    return 0
  fi
  if [ -d "$DIR/.git" ]; then
    info "Repo already present at $DIR — updating"
    run git -C "$DIR" pull --ff-only
  else
    info "Cloning $REPO_URL -> $DIR"
    run git clone --depth 1 "$REPO_URL" "$DIR"
  fi
}

setup_venv() {
  info "Creating the virtualenv and installing bleak"
  run python3 -m venv "$DIR/.venv"
  run "$DIR/.venv/bin/pip" install --upgrade pip
  run "$DIR/.venv/bin/pip" install bleak
  if [ "$WITH_SINKS" = 1 ]; then
    info "Installing sink deps (paho-mqtt, influxdb_client)"
    run "$DIR/.venv/bin/pip" install paho-mqtt influxdb_client
  fi
}

# Scan for the vehicle by NAME (its BLE address rotates until bonded). Echoes the MAC.
scan_for_vehicle() {
  if [ "$DRY_RUN" = 1 ]; then printf '  + bluetoothctl scan for %s\n' "$DEVICE_NAME" >&2; echo "AA:BB:CC:DD:EE:FF"; return 0; fi
  run sudo systemctl start bluetooth || true
  bluetoothctl power on >/dev/null 2>&1 || true
  # a bounded scan populates the device list; then match by name.
  bluetoothctl --timeout 20 scan on >/dev/null 2>&1 || true
  bluetoothctl devices 2>/dev/null | parse_device_mac
}

pair_vehicle() {
  info "Pairing with the camper (LE Passkey Entry)"
  cat <<EOF

  On the CAMPER: open Bluetooth settings and choose "Connect device" so a 6-digit
  passkey is shown on its screen. The unit must stay on that screen during pairing.

EOF
  _ignore=$(ask "Press Enter when the camper is showing its pairing screen...")

  addr=""
  _try=1
  while [ "$_try" -le 3 ]; do
    info "Scanning for $DEVICE_NAME (attempt $_try/3)..."
    addr=$(scan_for_vehicle)
    [ -n "$addr" ] && break
    warn "Did not find $DEVICE_NAME advertising."
    confirm "Retry the scan?" || break
    _try=$((_try + 1))
  done
  [ -n "$addr" ] || die "Could not find $DEVICE_NAME. Ensure it's on the connect-device screen, then re-run."
  info "Found $DEVICE_NAME at $addr"

  if [ "$DRY_RUN" = 1 ]; then
    printf '  + interactive bluetoothctl pair %s (you type the passkey)\n' "$addr"
    IDENTITY="$addr"; return 0
  fi

  # Guided interactive pairing: hand the terminal to bluetoothctl. The user runs the
  # printed commands and types the passkey shown on the camper. This is more robust than
  # feeding the passkey through a pipe (which races the agent prompt).
  cat <<EOF

  A bluetoothctl prompt will open. Run these lines (address is filled in for you):

      agent KeyboardDisplay
      default-agent
      pair $addr
      # when it asks, type the 6 digits shown on the camper, then Enter
      trust $addr
      quit

EOF
  _ignore=$(ask "Press Enter to open bluetoothctl...")
  bluetoothctl < /dev/tty > /dev/tty 2>&1 || true

  # Verify + capture the resolved identity address.
  if bluetoothctl devices Paired 2>/dev/null | parse_device_mac | grep -q .; then
    IDENTITY=$(bluetoothctl devices Paired 2>/dev/null | parse_device_mac)
  elif bluetoothctl paired-devices 2>/dev/null | parse_device_mac | grep -q .; then
    IDENTITY=$(bluetoothctl paired-devices 2>/dev/null | parse_device_mac)
  else
    die "Pairing not confirmed ($DEVICE_NAME is not in the paired list). See docs/raspberry-pi-setup.md for manual pairing."
  fi
  info "Bonded. Identity address captured (kept local, never committed)."
}

write_env() {
  envfile="$CONFIG_DIR/calictl.env"
  info "Writing $envfile (root:0600)"
  if [ -f "$envfile" ] && [ "$FORCE" != 1 ] && [ "$DRY_RUN" != 1 ]; then
    warn "$envfile already exists — leaving it (use --force to overwrite)"
    return 0
  fi
  run sudo install -d -m 755 "$CONFIG_DIR"
  if [ "$DRY_RUN" = 1 ]; then
    printf '  + write CALICTL_ADDR=%s -> %s (0600 root)\n' "${IDENTITY:-<identity>}" "$envfile"
    return 0
  fi
  printf 'CALICTL_ADDR=%s\n' "$IDENTITY" | sudo tee "$envfile" >/dev/null
  sudo chmod 600 "$envfile"
}

verify_reads() {
  info "Verifying BLE reads (calictl status) before starting the daemon"
  if [ "$DRY_RUN" = 1 ]; then printf '  + %s -m calictl status\n' "$DIR/.venv/bin/python"; return 0; fi
  # shellcheck disable=SC1090
  CALICTL_ADDR="$IDENTITY" "$DIR/.venv/bin/python" -m calictl status \
    || die "calictl status failed — the bond may not be complete. See docs/raspberry-pi-setup.md."
}

install_service() {
  if [ "$NO_SERVICE" = 1 ]; then
    info "Skipping the service (per --no-service). Start the daemon with:"
    printf '    CALICTL_ADDR=%s %s -m calictl serve\n' "${IDENTITY:-<addr>}" "$DIR/.venv/bin/python"
    return 0
  fi
  info "Installing the systemd service ($SERVICE_NAME)"
  _user=$(id -un)
  unit="/etc/systemd/system/$SERVICE_NAME.service"
  _body="[Unit]
Description=VW California Camper Unit (BLE) monitor -> calictl
After=bluetooth.target network-online.target
Wants=bluetooth.target network-online.target

[Service]
ExecStart=$DIR/.venv/bin/python -m calictl serve
WorkingDirectory=$DIR
EnvironmentFile=$CONFIG_DIR/calictl.env
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=30
User=$_user

[Install]
WantedBy=multi-user.target"
  if [ "$DRY_RUN" = 1 ]; then
    printf '  + write systemd unit -> %s (User=%s, EnvironmentFile=%s)\n' "$unit" "$_user" "$CONFIG_DIR/calictl.env"
  else
    printf '%s\n' "$_body" | sudo tee "$unit" >/dev/null
    run sudo systemctl daemon-reload
    run sudo systemctl enable --now "$SERVICE_NAME"
  fi
}

summary() {
  cat <<EOF

$(info "Done.")
  Repo:     $DIR
  Env:      $CONFIG_DIR/calictl.env   (CALICTL_ADDR — local, 0600 root)
  Service:  $( [ "$NO_SERVICE" = 1 ] && echo "not installed (--no-service)" || echo "$SERVICE_NAME (enabled)" )

  Try it:
    $DIR/.venv/bin/python -m calictl status
    $DIR/.venv/bin/python -m calictl set cooler power on
    journalctl -u $SERVICE_NAME -f          # daemon logs

  Add MQTT / InfluxDB / Home Assistant / Grafana: see calictl/deploy/ (HOMEASSISTANT.md, GRAFANA.md).
EOF
}

plan() {
  cat <<EOF
open-california installer — planned actions${DRY_RUN:+ (DRY RUN)}:
  1. apt-get install bluez git python3-venv python3-pip   (sudo)
  2. clone/update the repo at            $DIR
  3. create venv + pip install bleak$( [ "$WITH_SINKS" = 1 ] && echo " + sinks" )
  4. guided BLE pairing with $DEVICE_NAME (you type the passkey shown on the camper)
  5. write CALICTL_ADDR ->               $CONFIG_DIR/calictl.env   (sudo, 0600)
  6. verify reads (calictl status), then $( [ "$NO_SERVICE" = 1 ] && echo "skip the service" || echo "enable the $SERVICE_NAME service" )
EOF
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --with-sinks) WITH_SINKS=1 ;;
      --no-service) NO_SERVICE=1 ;;
      --dry-run)    DRY_RUN=1 ;;
      --yes|-y)     ASSUME_YES=1 ;;
      --force)      FORCE=1 ;;
      --dir)        DIR="${2:?--dir needs a path}"; shift ;;
      --config-dir) CONFIG_DIR="${2:?--config-dir needs a path}"; shift ;;
      -h|--help)    usage; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
  done

  plan
  confirm "Proceed?" || die "aborted."

  preflight
  install_deps
  acquire_repo
  setup_venv
  pair_vehicle
  write_env
  verify_reads
  install_service
  summary
}

# Library guard: `OC_INSTALL_LIB=1 . ./install.sh` sources the functions without running
# main (used by tests to exercise the pure helpers like parse_device_mac).
[ "${OC_INSTALL_LIB:-0}" = 1 ] || main "$@"
