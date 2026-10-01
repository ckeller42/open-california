#!/usr/bin/env bash
# shellcheck disable=SC2024  # the logs are the bench user's files on purpose, not root's
# CoreS3 status-display board walk against the mock camper unit (#154, R_FW_STATUS_DISPLAY).
#
# Runs ON the Linux bench ("thinky"): CoreS3 on USB, the mock unit (tools/applab/fake_unit_ble.py)
# on a dedicated BLE dongle, a second WiFi stick with two NetworkManager profiles (one joins the
# ESP's setup hotspot, the other the target network). Walks every screen state, takes a `screenshot`
# after each step over the no-reset console path and decodes it to <OUT>/<NN-state>.png.
#
#   1 setup        wifi forget + forget       -> amber WiFi (hotspot), grey camper, setup footer
#   2 joining      POST /api/wifi on hotspot  -> amber WiFi "joining" (shot right after the POST)
#   3 online       (wait)                     -> green WiFi: SSID, IP, RSSI
#   4 pairing      fresh mock unit, pair      -> amber camper (waiting for passkey)
#   5 connected    passkey                    -> green camper, data age
#   6 stale        SIGSTOP the mock 13 s      -> red camper "no data" (link up, data > 10 s old)
#   7 link lost    SIGCONT, then stop mock    -> red camper "link lost, reconnecting"
#   8 dimmed       75 s without a change      -> `LOG display: brightness <pct>` (timestamped log)
#   9 reconnected  start the mock again       -> 09a right after the start (reconnect in progress),
#                                                then green camper, full brightness again
#
# The ESP is left on the target network and bonded to the mock unit (which keeps running).
# Secrets: the target network's passphrase is read from $PSK_DIR/$PSK_FILE inside a python process
# and piped to curl; it never appears on a command line or in the output.
#
# Env (defaults = the thinky bench): PORT ESP_DIR REPO OUT PSK_DIR PSK_FILE SSID HCI PASSKEY
#   SETUP_CON TARGET_CON ESP_SHOT PY AP_ADDR
set -euo pipefail

PORT=${PORT:-/dev/ttyACM0}
ESP_DIR=${ESP_DIR:-$HOME/calictl-esp}             # esp_cmd.py + the mock unit's fifo/keystore
REPO=${REPO:-$HOME/open-california}               # checkout holding tools/applab/fake_unit_ble.py
OUT=${OUT:-$HOME/calictl-esp/walk-$(date +%Y%m%d-%H%M%S)}
PSK_DIR=${PSK_DIR:-$HOME/.calictl-esplab}
PSK_FILE=${PSK_FILE:-minsel.psk}
SSID=${SSID:-minsel}
HCI=${HCI:-2}                                     # mock unit's controller (hci-socket:N)
PASSKEY=${PASSKEY:-123456}
SETUP_CON=${SETUP_CON:-esp-setup}                 # NM profile: the ESP's setup hotspot
TARGET_CON=${TARGET_CON:-esp-minsel}              # NM profile: back to the target network
ESP_SHOT=${ESP_SHOT:-$REPO/tools/esp_shot.py}
PY=${PY:-$HOME/esp-venv/bin/python}
AP_ADDR=${AP_ADDR:-192.168.4.1}                   # = NET_AP_ADDR (tools/wifi_consts.py), the hotspot's own address

mkdir -p "$OUT"
LOG="$OUT/walk.log"
stamp() { "$PY" -u -c 'import sys,time
for l in sys.stdin: print(time.strftime("%H:%M:%S"), l, end="")'; }
say() { echo "== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }
# esp cmd <wait_s> <cmd>...: console without resetting the chip, output timestamped into the log
# (SNAP lines, ~1/s of decoded unit data, are dropped from the log)
esp() {
    sg dialout -c "$(printf '%q ' "$PY" "$ESP_DIR/esp_cmd.py" "$PORT" "$@")" |
        grep --line-buffered -v '^SNAP' | stamp >>"$LOG"
}
shot() {  # shot <name>: screenshot first (state as of now), then status -> $OUT/<name>.png
    local raw="$OUT/$1.txt" tmp="$OUT/.dec"
    sg dialout -c "$(printf '%q ' "$PY" "$ESP_DIR/esp_cmd.py" "$PORT" 4 screenshot status)" >"$raw"
    grep -E '^(STATE|LOG)' "$raw" | stamp >>"$LOG" || true
    rm -rf "$tmp"
    if "$PY" "$ESP_SHOT" decode "$raw" "$tmp" >/dev/null && [ -f "$tmp/shot0.png" ]; then
        mv "$tmp/shot0.png" "$OUT/$1.png"; say "shot $1.png"
    else
        say "shot $1 FAILED"
    fi
}
MOCK="fake_unit_ble[.]py"   # pkill/pgrep pattern; the bracket keeps it from matching its own shell
mock_stop() { sudo -n pkill -f "$MOCK" || true; sleep 2; }
mock_signal() { sudo -n pkill "-$1" -f "$MOCK"; }
mock_start() {
    sudo -n hciconfig "hci$HCI" down || true
    # the whole subshell is redirected: nothing keeps this script's stdout (an ssh channel) open
    (cd "$REPO" && exec sudo -n env FAKE_UNIT_PASSKEY="$PASSKEY" FAKE_UNIT_FIFO="$ESP_DIR/fake_unit.in" \
        FAKE_UNIT_KEYSTORE="$ESP_DIR/fake_unit_keys.json" HOME="$HOME" \
        setsid nohup "$PY" tools/applab/fake_unit_ble.py "hci-socket:$HCI") >>"$OUT/fake_unit.log" 2>&1 </dev/null &
    sleep 8
}
post_wifi() {  # JSON {"ssid","psk"} built from the psk file, piped to curl (never on a command line)
    SSID="$SSID" PSKF="$PSK_DIR/$PSK_FILE" "$PY" -c 'import json,os,pathlib
print(json.dumps({"ssid": os.environ["SSID"], "psk": pathlib.Path(os.environ["PSKF"]).read_text().strip()}))' |
        curl -s -m 10 -H 'Content-Type: application/json' --data-binary @- "http://$AP_ADDR/api/wifi"
    echo
}

say "1 setup: wifi forget + forget"
# forget twice: on a live link the first one ends in error "timeout" (remove_bond fails while the
# link is being torn down, bench 2026-10-01); the second one, with no link, reaches idle.
esp 3 "wifi forget" forget
esp 3 forget
sleep 5
shot 01-setup

say "2 joining: POST /api/wifi over the setup hotspot ($SETUP_CON)"
for _ in 1 2 3 4 5; do sudo -n nmcli con up "$SETUP_CON" >>"$LOG" 2>&1 && break; sleep 5; done
post_wifi | tee -a "$LOG"
shot 02-joining

say "3 online"
sleep 15
shot 03-online
sudo -n nmcli con up "$TARGET_CON" >>"$LOG" 2>&1 || true

say "4 pairing: fresh mock unit (keystore cleared), pair"
mock_stop
rm -f "$ESP_DIR/fake_unit_keys.json"
mock_start
esp 10 pair
shot 04-pairing

say "5 connected: passkey"
esp 15 "passkey $PASSKEY"
shot 05-connected

say "6 stale: mock unit frozen (SIGSTOP) - the link stays up, no data arrives"
mock_signal STOP
esp 13 "wifi status"
shot 06-stale
mock_signal CONT
esp 15 status

say "7 link lost: stop the mock unit"
mock_stop
esp 3 status
shot 07-link-lost

say "8 dimming: 75 s without a status-class change (console open, brightness lines timestamped)"
esp 75 "wifi status"
shot 08-dimmed

say "9 reconnected: mock unit back (same keystore -> bond kept)"
mock_start
shot 09a-reconnecting   # as early as possible: a red "no data" flash here = the kept pre-drop age (M11)
esp 20 status
shot 09-reconnected
grep 'display: brightness' "$LOG" | tee "$OUT/brightness.txt" || true
say "done: $OUT"
