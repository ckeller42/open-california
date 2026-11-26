#!/usr/bin/env bash
# The HIL bench (sim/README.md "HIL session"): a spare CoreS3 + RAK3172 on odelly's USB, driven through its
# console (no reset) with the same scenarios as the sim tier.
#
#   HIL_PORT=/dev/serial/by-id/usb-Espressif_..._<MAC>-if00 tools/esplab/hil.sh <step> [args]
#
# Read-only steps (they never write to the board's flash or NVS):
#   identify     status, lora status, wifi status (chip and MAC come from the by-id name: no esptool, no reset)
#   tail S       the console for S seconds, timestamped, into a capture file
#   diag         the RX settings for the join-accept diagnosis (lora at RX1DL ... NJS) into a capture file
#   scenarios    the dual-target scenarios on the board (and the sim), JUnit report + drift reports
# Steps that change the board, each only with the owner present:
#   scenarios --actions   also the scenarios that join and send uplinks (HIL_ACTIONS=1)
#   capture-join          lora off, lora on, then 150 s of console: a raw AT transcript of setup, join, uplinks
#   flash-esp-latest      flash the latest main CI build to HIL_PORT (tools/esplab/flash_ci.sh); needs
#                         HIL_FLASH=owner-present as well
#
# Output: $HIL_OUT (default ~/hil/<YYYYmmdd-HHMMSS>). Only the board named by HIL_PORT is ever opened:
# a /dev/serial/by-id/usb-Espressif_... path that exists and is not /dev/ttyUSB0 (odelly's node32).
# Run by hand, or by a systemd user timer (sim/README.md shows one; not installed by anything here).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; root="$(cd "$here/../.." && pwd)"
die() { echo "hil.sh: $*" >&2; exit 2; }

port="${HIL_PORT:-}"
[ -n "$port" ] || die "HIL_PORT is not set: name the HIL board by its /dev/serial/by-id/usb-Espressif_... path"
case "$port" in /dev/serial/by-id/usb-Espressif*) ;; *) die "HIL_PORT $port is not a /dev/serial/by-id/usb-Espressif_... path" ;; esac
[ -e "$port" ] || die "HIL_PORT $port does not exist (is the board plugged in?)"
real="$(readlink -f "$port")"
[ "$real" != /dev/ttyUSB0 ] || die "HIL_PORT $port resolves to /dev/ttyUSB0: never this board"

out="${HIL_OUT:-$HOME/hil/$(date +%Y%m%d-%H%M%S)}"; mkdir -p "$out"
cmd() { python3 "$here/esp_cmd.py" "$port" "$@"; }
# console steps with the scenario layer's timestamps (ms from the start): run_steps DURATION_S "line" ...
run_steps() {
  (cd "$root" && "${PY:-python3}" - "$port" "$@" <<'PY'
import sys
from tests.firmware import hil
port, dur, lines = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
res = hil.BoardTarget(port).run([(1 + 3 * i, l) for i, l in enumerate(lines)], dur)
for t, line in res.log:
    print(f"{t:>8} {line}")
PY
  )
}
step="${1:-}"; shift || true
case "$step" in
  identify)
    { echo "port $port -> $real"; cmd 2 "status" "lora status" "wifi status"; } | tee "$out/identify.txt" ;;
  tail)
    cmd --tail "${1:-60}" | tee "$out/console-tail.log" ;;
  diag)
    run_steps 45 "lora status" "lora at RX1DL" "lora at RX2DL" "lora at RX2DR" "lora at RX2FQ" "lora at JN1DL" \
      "lora at JN2DL" "lora at BAND" "lora at CLASS" "lora at ADR" "lora at DR" "lora at NJS" | tee "$out/rx-diag.log" ;;
  scenarios)
    actions=0; [ "${1:-}" = --actions ] && actions=1
    cd "$root"
    # the sim side of the drift report needs the 32-bit host build (gcc-multilib): without it, no drift report
    sim_bin="$root/firmware/host/lora-sim"
    if ! { bash firmware/host/fetch_nimble.sh && make -s -C firmware/host lora-sim; } >"$out/sim-build.log" 2>&1; then
      echo "no lora-sim build (see $out/sim-build.log): board runs only, no drift report" >&2; sim_bin=""
    fi
    HIL_TARGET=board HIL_PORT="$port" HIL_ACTIONS="$actions" HIL_OUT="$out" HIL_SIM_BIN="$sim_bin" \
      "${PY:-python3}" -m pytest tests/firmware/test_lora_scenarios.py -v -rs -p no:cacheprovider --junitxml "$out/junit.xml" ;;
  capture-join)
    echo "changes the board: LoRa off and on, a real join and uplinks"
    run_steps 160 "lora off" "lora status" "lora on" | tee "$out/capture-join.log" ;;
  flash-esp-latest)
    [ "${HIL_FLASH:-}" = owner-present ] || die "flash-esp-latest writes the board's flash: set HIL_FLASH=owner-present"
    exec bash "$here/flash_ci.sh" main "$port" ;;
  *) die "usage: HIL_PORT=... $0 identify|tail S|diag|scenarios [--actions]|capture-join|flash-esp-latest" ;;
esac
echo "output: $out"
