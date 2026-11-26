#!/usr/bin/env bash
# The satellite bench on one command: the fake camper unit, the fake BLE sensors and a check of the
# test board; `release` makes the board clean before it leaves the bench (for the van).
#   tools/esplab/bench.sh up         restart the fake unit (fresh state), start the fake sensors
#   tools/esplab/bench.sh down       stop the fake sensors and the fake unit
#   tools/esplab/bench.sh status     each part, and the board's bond and WiFi from its console
#   tools/esplab/bench.sh release BUILD_DIR
#                                    erase the board's whole flash (bond, WiFi, hub, LoRa keys) and
#                                    flash BUILD_DIR (flasher_args.json: a CI artifact, flash_ci.sh)
# Runs on the bench laptop (odelly); the fake unit runs on BENCH_UNIT_HOST. The board is the only
# Espressif USB-JTAG port, or ESP_PORT. `release` asks for the board's MAC to be typed back, so a
# board is never erased by a guess (BENCH_YES=<mac> answers it, for scripts).
# Not covered: the camera feed and the Android emulator (docs/simulation-and-testing.md).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
UNIT_HOST="${BENCH_UNIT_HOST:-pi3usv}"
UNIT_SVC="${BENCH_UNIT_SVC:-oc-fakeunit}"
# start|stop script on the unit host: a transient unit (systemd-run --collect), so a plain
# `systemctl stop` would delete it; the script also hands hci0 between bluetoothd and the fake.
UNIT_CTL="${BENCH_UNIT_CTL:-~/fakeunit.sh}"
SENSORS_ARGS="${BENCH_SENSORS_ARGS:---govee 3 --mppt --battery-monitor}"
STATE="${BENCH_STATE:-$HOME/.cache/oc-bench}"
SSH="${BENCH_SSH:-ssh -o ConnectTimeout=10 -o BatchMode=yes}"
ESPTOOL="${ESPTOOL:-$HOME/esp-venv/bin/esptool}"
ESP_CMD="${ESP_CMD:-python3 $here/esp_cmd.py}"
mkdir -p "$STATE"

die() { echo "bench: $*" >&2; exit 1; }

port() {   # the board: ESP_PORT, else exactly one Espressif USB-JTAG port
    if [ -n "${ESP_PORT:-}" ]; then echo "$ESP_PORT"; return; fi
    local p=(/dev/serial/by-id/usb-Espressif_USB_JTAG*)
    [ -e "${p[0]}" ] || die "no Espressif board on USB (or set ESP_PORT)"
    [ "${#p[@]}" -eq 1 ] || die "${#p[@]} Espressif boards on USB: set ESP_PORT"
    echo "${p[0]}"
}

mac_of() {   # the MAC in a by-id name (..._AA:BB:CC:DD:EE:FF-if00), else empty
    basename "$1" | grep -oE '([0-9A-F]{2}:){5}[0-9A-F]{2}' || true
}

board_status() {   # the console STATE line: {"address": bond or null, "wifi": {...}, ...}
    # shellcheck disable=SC2086  # ESP_CMD and SSH are command lines on purpose
    $ESP_CMD "$1" 4 "" status 2>/dev/null | grep -m1 '^STATE ' | cut -c7-
}

unit_addrs() {   # the fake unit's addresses, live (no MAC is kept in this repo): its identity and the controller's
    $SSH "$UNIT_HOST" "journalctl -u $UNIT_SVC --no-pager -o cat | grep -oE 'identity [0-9A-F:]+' | tail -1; hciconfig hci0" 2>/dev/null |
        grep -oE '(identity|BD Address:) [0-9A-F:]{17}' | grep -oE '[0-9A-F:]{17}' || true
}

sensors_pid() {
    local pid
    pid=$(cat "$STATE/sensors.pid" 2>/dev/null) || return 1
    ps -p "$pid" >/dev/null && echo "$pid"   # root-owned (sudo): kill -0 would say EPERM
}

up() {
    $SSH "$UNIT_HOST" "$UNIT_CTL stop >/dev/null; $UNIT_CTL start >/dev/null; systemctl is-active $UNIT_SVC" >/dev/null ||
        die "fake unit: start failed on $UNIT_HOST ($UNIT_CTL)"
    echo "fake unit: started fresh on $UNIT_HOST"
    if pid=$(sensors_pid); then
        echo "fake sensors: already running (pid $pid)"
    else
        # shellcheck disable=SC2086,SC2024  # a word list on purpose; the log stays the user's file
        sudo -n nohup python3 "$repo/tools/fake_sensors_ble.py" $SENSORS_ARGS >"$STATE/sensors.log" 2>&1 &
        echo $! >"$STATE/sensors.pid"
        echo "fake sensors: started (log $STATE/sensors.log)"
    fi
    status
}

down() {
    if pid=$(sensors_pid); then
        sudo -n kill "$pid" 2>/dev/null || true
        sleep 2
    fi
    sudo -n python3 "$repo/tools/fake_sensors_ble.py" --stop >/dev/null 2>&1 || true
    rm -f "$STATE/sensors.pid"
    echo "fake sensors: stopped"
    $SSH "$UNIT_HOST" "$UNIT_CTL stop >/dev/null" && echo "fake unit: stopped on $UNIT_HOST" ||
        echo "fake unit: could not stop on $UNIT_HOST"
}

status() {
    local s p st bond fake
    s=$($SSH "$UNIT_HOST" "systemctl is-active $UNIT_SVC" 2>/dev/null || true)
    echo "fake unit:    ${s:-unreachable} ($UNIT_HOST $UNIT_SVC)"
    if pid=$(sensors_pid); then echo "fake sensors: running (pid $pid)"; else echo "fake sensors: stopped"; fi
    if ! p=$(port 2>&1); then echo "board:        $p"; return 0; fi
    st=$(board_status "$p")
    [ -n "$st" ] || { echo "board:        no console answer on $p"; return 0; }
    bond=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("address") or "")' "$st")
    echo "board:        $(mac_of "$p") wifi $(python3 -c 'import json,sys; w=json.loads(sys.argv[1]).get("wifi") or {}; print(w.get("mode"), w.get("ssid") or "-")' "$st")"
    fake=$(unit_addrs)
    if [ -z "$bond" ]; then
        echo "bond:         none (clean)"
    elif grep -qxF "$bond" <<<"$fake"; then
        echo "bond:         the bench's fake unit: run 'bench.sh release' before the board goes to the van"
    else
        echo "bond:         another unit (not this bench's fake unit)"
    fi
}

release() {
    local dir="${1:-}" p mac answer
    if [ -z "$dir" ] || [ ! -f "$dir/flasher_args.json" ]; then
        die "release BUILD_DIR: no flasher_args.json in '${dir}'"
    fi
    p=$(port)
    mac=$(mac_of "$p")
    [ -n "$mac" ] || die "release: $p does not name a board MAC; use its /dev/serial/by-id/ path"
    echo "release: erase the WHOLE flash of $mac (bond, WiFi, hub, LoRa keys), then flash $dir"
    if [ -n "${BENCH_YES:-}" ]; then answer="$BENCH_YES"; else read -r -p "type the MAC to confirm: " answer; fi
    [ "$answer" = "$mac" ] || die "release: not confirmed, nothing changed"
    "$ESPTOOL" --chip esp32s3 -p "$p" erase-flash
    [ -f "$dir/flash.sh" ] || cp "$here/flash.sh" "$dir/flash.sh"
    bash "$dir/flash.sh" "$p"
    sleep 8
    st=$(board_status "$p")
    if [ -z "$st" ] || ! python3 -c 'import json,sys; sys.exit(json.loads(sys.argv[1]).get("address") is not None)' "$st"; then
        die "release: the board did not report a clean state: ${st:-no console answer}"
    fi
    echo "release: done, $mac is clean (no bond, WiFi setup)"
}

case "${1:-}" in
    up) up;;
    down) down;;
    status) status;;
    release) shift; release "$@";;
    *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2;;
esac
