#!/usr/bin/env bash
# labctl — bring the app lab up / down / report status with ONE command, idempotently.
#
# The lab has several moving parts that drift apart across a restart (emulator dies, the fake's
# radio lingers in netsim, the bond needs re-pairing). This wraps them so "resume the lab" is one
# command that only does what is missing. Run it yourself from a terminal that has Removable-Volumes
# access — an assistant sandbox often cannot read the external volume.
#
#   tools/applab/labctl.sh up       # start emulator (if down) + fake unit (if down), launch the app
#   tools/applab/labctl.sh status   # what is running
#   tools/applab/labctl.sh down     # stop the fake CLEANLY (so netsim drops its radio) then the emulator
#   tools/applab/labctl.sh fake     # (re)start only the fake unit
#
# Config via env (defaults suit the 2026 California lab):
#   LAB_DIR       lab install root (env.sh, sdk/, apks/)   [/Volumes/External/android-lab]
#   BUMBLE_PY     a python with `bumble` installed          [$LAB_DIR/venv-bumble/bin/python]
#   AVD           emulator AVD name                          [cali34]
#   FAKE_UNIT_VIN the VIN typed into the app (REQUIRED for a fresh pair; never committed)
#   APP_ID / APP_ACTIVITY  app package / activity            [de.volkswagen.CaliforniaOnTour/…]
set -euo pipefail

LAB_DIR="${LAB_DIR:-/Volumes/External/android-lab}"
AVD="${AVD:-cali34}"
BUMBLE_PY="${BUMBLE_PY:-$LAB_DIR/venv-bumble/bin/python}"
APP_ID="${APP_ID:-de.volkswagen.CaliforniaOnTour}"
APP_ACTIVITY="${APP_ACTIVITY:-de.volkswagen.caliontour.development.CaliforniaOnTourMainActivity}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
STATE="${TMPDIR:-/tmp}/applab"; mkdir -p "$STATE"
FAKE_LOG="$STATE/fake_unit.log"; FAKE_PID="$STATE/fake_unit.pid"
FAKE_FIFO="$STATE/fake_unit.in"   # scenario console: `echo 'set airheater ErrorCode=2' > "$FAKE_FIFO"`

[ -f "$LAB_DIR/env.sh" ] && source "$LAB_DIR/env.sh"   # ANDROID_SDK_ROOT + PATH (adb, emulator)
command -v adb >/dev/null || { echo "adb not on PATH — is LAB_DIR ($LAB_DIR) correct?" >&2; exit 1; }

emu_up()  { adb devices | grep -q 'emulator-.*device$'; }
fake_up() { pgrep -f 'applab/fake_unit_ble.py' >/dev/null; }

start_emulator() {
  emu_up && { echo "emulator: already up"; return; }
  echo "emulator: starting $AVD (headless, netsim BT)…"
  nohup emulator -avd "$AVD" -no-window -no-audio -no-boot-anim -no-snapshot \
    -gpu swiftshader_indirect -packet-streamer-endpoint default >"$STATE/emulator.log" 2>&1 &
  adb wait-for-device
  for _ in $(seq 1 60); do
    [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break; sleep 3
  done
  echo "emulator: booted"
}

start_fake() {
  if fake_up; then echo "fake: already up (pkill for a restart)"; return; fi
  : "${FAKE_UNIT_VIN:?set FAKE_UNIT_VIN to the VIN typed into the app}"
  [ -x "$BUMBLE_PY" ] || { echo "no bumble python at $BUMBLE_PY (set BUMBLE_PY)" >&2; exit 1; }
  echo "fake: starting (keys persist; stable address = bond survives restarts)…"
  # The fake makes its own scenario FIFO ($FAKE_FIFO) and reads it in a thread, so no stdin wiring.
  ( cd "$REPO" && FAKE_UNIT_VIN="$FAKE_UNIT_VIN" FAKE_UNIT_FIFO="$FAKE_FIFO" \
      nohup "$BUMBLE_PY" tools/applab/fake_unit_ble.py >>"$FAKE_LOG" 2>&1 & echo $! >"$FAKE_PID" )
  sleep 6
  fake_up && tail -1 "$FAKE_LOG" || { echo "fake failed to start — see $FAKE_LOG" >&2; exit 1; }
}

launch_app() { adb shell am start -n "$APP_ID/$APP_ACTIVITY" >/dev/null 2>&1 && echo "app: launched"; }

case "${1:-status}" in
  up)     start_emulator; start_fake; launch_app
          echo "lab up. If the app can't find the vehicle, re-pair: Account > Vehicle > Bluetooth Reset, then the wizard." ;;
  fake)   pkill -TERM -f 'applab/fake_unit_ble.py' 2>/dev/null && sleep 2 || true; start_fake ;;
  down)   echo "stopping fake cleanly (SIGTERM → netsim drops the radio)…"
          pkill -TERM -f 'applab/fake_unit_ble.py' 2>/dev/null && sleep 3 || true
          adb emu kill 2>/dev/null || true
          # netsimd is a SEPARATE long-lived process that outlives the emulator, and it is what
          # holds a hard-killed fake's radio. Killing the emulator alone leaves that ghost, so a
          # full `down` takes netsimd with it — otherwise the next `up` is shadowed by the twin.
          pkill -TERM -f 'emulator/netsimd' 2>/dev/null || true
          echo "lab down" ;;
  status) echo "emulator: $(emu_up && echo up || echo down)"
          echo "fake:     $(fake_up && echo "up ($(tail -1 "$FAKE_LOG" 2>/dev/null))" || echo down)"
          # netsimd up while the fake is down is the ghost-radio smell (see README).
          echo "netsimd:  $(pgrep -f 'emulator/netsimd' >/dev/null && echo up || echo down)"
          echo "scenario: echo 'set airheater ErrorCode=2' > $FAKE_FIFO" ;;
  *) echo "usage: labctl.sh {up|down|status|fake}" >&2; exit 2 ;;
esac
