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
#   tools/applab/labctl.sh install  # install/update the app from $LAB_DIR/apks/<version>/ (split APKs)
#
# Config via env (defaults per OS; setup_linux.sh writes them into $LAB_DIR/env.sh):
#   LAB_DIR       lab install root (env.sh, sdk/, apks/)   [Linux ~/android-lab, macOS /Volumes/External/android-lab]
#   BUMBLE_PY     a python with `bumble` installed          [$LAB_DIR/venv-bumble/bin/python]
#   AVD           emulator AVD name                          [Linux lab34, macOS cali34]
#   LAB_GPU       emulator -gpu mode                         [Linux swangle_indirect, macOS swiftshader_indirect]
#   LAB_DISPLAY   X display for LAB_GPU on Linux; started as a private Xvfb when it is not up [:120]
#   APK_VERSION   app version dir under $LAB_DIR/apks for `install` [the newest]
#   FAKE_UNIT_VIN the VIN typed into the app (REQUIRED for a fresh pair; never committed;
#                 default: $LAB_DIR/vin when it exists)
#   APP_ID / APP_ACTIVITY  app package / activity            [de.volkswagen.CaliforniaOnTour/…]
set -euo pipefail

if [ "$(uname -s)" = Linux ]; then
  LAB_DIR="${LAB_DIR:-$HOME/android-lab}"; AVD="${AVD:-lab34}"
  # Host SwiftShader/llvmpipe (swiftshader_indirect) segfaults qemu as soon as the app renders;
  # ANGLE over SwiftShader Vulkan on a private Xvfb survives repeated cold app starts (README).
  LAB_GPU="${LAB_GPU:-swangle_indirect}"; LAB_DISPLAY="${LAB_DISPLAY:-:120}"
else
  LAB_DIR="${LAB_DIR:-/Volumes/External/android-lab}"; AVD="${AVD:-cali34}"
  LAB_GPU="${LAB_GPU:-swiftshader_indirect}"; LAB_DISPLAY=""
fi
BUMBLE_PY="${BUMBLE_PY:-$LAB_DIR/venv-bumble/bin/python}"
APP_ID="${APP_ID:-de.volkswagen.CaliforniaOnTour}"
APP_ACTIVITY="${APP_ACTIVITY:-de.volkswagen.caliontour.development.CaliforniaOnTourMainActivity}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
STATE="${TMPDIR:-/tmp}/applab"; mkdir -p "$STATE"
FAKE_LOG="$STATE/fake_unit.log"; FAKE_PID="$STATE/fake_unit.pid"
FAKE_FIFO="$STATE/fake_unit.in"   # scenario console: `echo 'set airheater ErrorCode=2' > "$FAKE_FIFO"`

[ -f "$LAB_DIR/env.sh" ] && source "$LAB_DIR/env.sh"   # ANDROID_SDK_ROOT + PATH (adb, emulator)
[ -z "${FAKE_UNIT_VIN:-}" ] && [ -r "$LAB_DIR/vin" ] && FAKE_UNIT_VIN="$(cat "$LAB_DIR/vin")"
command -v adb >/dev/null || { echo "adb not on PATH — is LAB_DIR ($LAB_DIR) correct?" >&2; exit 1; }

emu_up()  { adb devices | grep -q 'emulator-.*device$'; }
# By pid file, never by pattern: on thinky the ESP's fake (UB500) runs the same script.
fake_up() { [ -f "$FAKE_PID" ] && kill -0 "$(cat "$FAKE_PID")" 2>/dev/null; }
stop_fake() {   # SIGTERM only: a hard kill leaves a ghost radio in netsim
  fake_up || { rm -f "$FAKE_PID"; return 0; }
  kill -TERM "$(cat "$FAKE_PID")"
  for _ in $(seq 1 20); do fake_up || break; sleep 0.5; done
  rm -f "$FAKE_PID"
}

start_emulator() {
  emu_up && { echo "emulator: already up"; return; }
  echo "emulator: starting $AVD (headless, netsim BT, -gpu $LAB_GPU)…"
  if [ -n "$LAB_DISPLAY" ]; then   # Linux: the GPU mode needs an X display; a private Xvfb is enough
    [ -e "/tmp/.X11-unix/X${LAB_DISPLAY#:}" ] || { nohup Xvfb "$LAB_DISPLAY" -screen 0 1280x1024x24 >"$STATE/xvfb.log" 2>&1 & sleep 2; }
    export DISPLAY="$LAB_DISPLAY"
  fi
  nohup emulator -avd "$AVD" -no-window -no-audio -no-boot-anim -no-snapshot \
    -gpu "$LAB_GPU" -packet-streamer-endpoint default >"$STATE/emulator.log" 2>&1 &
  adb wait-for-device
  for _ in $(seq 1 60); do
    [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break; sleep 3
  done
  echo "emulator: booted"
}

start_fake() {
  if fake_up; then echo "fake: already up (labctl.sh fake for a restart)"; return; fi
  : "${FAKE_UNIT_VIN:?set FAKE_UNIT_VIN to the VIN typed into the app}"
  [ -x "$BUMBLE_PY" ] || { echo "no bumble python at $BUMBLE_PY (set BUMBLE_PY)" >&2; exit 1; }
  echo "fake: starting (keys persist; stable address = bond survives restarts)…"
  # The fake makes its own scenario FIFO ($FAKE_FIFO) and reads it in a thread, so no stdin wiring.
  # `cd ... ;` not `cd ... &&`: with `&&` the `&` backgrounds the whole list as a subshell, $! is that
  # subshell, and stop_fake/walk.py SIGTERM it while the python fake lives on as a netsim twin.
  ( cd "$REPO" || exit 1; FAKE_UNIT_VIN="$FAKE_UNIT_VIN" FAKE_UNIT_FIFO="$FAKE_FIFO" \
      nohup "$BUMBLE_PY" tools/applab/fake_unit_ble.py android-netsim >>"$FAKE_LOG" 2>&1 & echo $! >"$FAKE_PID" )
  sleep 6
  fake_up && tail -1 "$FAKE_LOG" || { echo "fake failed to start — see $FAKE_LOG" >&2; exit 1; }
}

install_app() {   # the phone's split APKs, as an update (keeps the app's data: onboarding, VIN)
  local d
  if [ -n "${APK_VERSION:-}" ]; then d="$LAB_DIR/apks/$APK_VERSION"
  else   # the newest version directory that holds a complete copy (base.apk)
    d="$(printf '%s\n' "$LAB_DIR"/apks/[0-9]*/base.apk | sort -V | tail -1)"; d="${d%/base.apk}"
  fi
  [ -f "$d/base.apk" ] || { echo "no $d/base.apk (setup_linux.sh copies it from buspi)" >&2; exit 1; }
  adb install-multiple -r "$d"/*.apk
  adb shell pm grant "$APP_ID" android.permission.ACCESS_FINE_LOCATION 2>/dev/null || true
  adb shell pm grant "$APP_ID" android.permission.ACCESS_COARSE_LOCATION 2>/dev/null || true
  adb shell dumpsys package "$APP_ID" | grep -E 'versionName|primaryCpuAbi'
}

launch_app() { adb shell am start -n "$APP_ID/$APP_ACTIVITY" >/dev/null 2>&1 && echo "app: launched"; }

case "${1:-status}" in
  up)     start_emulator; start_fake; launch_app
          echo "lab up. If the app can't find the vehicle, re-pair: Account > Vehicle > Bluetooth Reset, then the wizard." ;;
  fake)   stop_fake; start_fake ;;
  install) start_emulator; install_app ;;
  down)   echo "stopping fake cleanly (SIGTERM → netsim drops the radio)…"
          stop_fake
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
  *) echo "usage: labctl.sh {up|down|status|fake|install}" >&2; exit 2 ;;
esac
