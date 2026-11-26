#!/usr/bin/env bash
# The HIL bench (sim/README.md "HIL session"): a spare CoreS3 + RAK3172 on odelly's USB, driven through its
# console (no reset) with the same scenarios as the sim tier.
#
#   HIL_PORT=/dev/serial/by-id/usb-Espressif_..._<MAC>-if00 tools/esplab/hil.sh <step> [args]
#
# Read-only steps (they never write to the board's flash or NVS):
#   identify     status, lora status, wifi status, the AW9523 + AXP2101 register dumps (i2c dump: reads only)
#                (chip and MAC come from the by-id name: no esptool, no reset)
#   tail S       the console for S seconds, timestamped, into a capture file
#   diag         the RX settings for the join-accept diagnosis (lora at RX1DL ... NJS) and the register dumps
#                into capture files
#   scenarios    the dual-target scenarios on the board, then on the sim in the oc-linux container (podman):
#                JUnit reports + drift reports
# Steps that change the board, each only with the owner present:
#   scenarios --actions   also the scenarios that join and send uplinks (HIL_ACTIONS=1)
#   capture-join          lora off, lora on, then 150 s of console: a raw AT transcript of setup, join, uplinks
#   flash RUN_ID          flash the firmware of that CI run to HIL_PORT (tools/esplab/flash_ci.sh, an explicit
#                         run id as AGENTS.md requires, never "main"); needs HIL_FLASH=owner-present as well
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
# the sim half of `scenarios` in a container (odelly: podman, no gcc-multilib): the GHCR oc-linux image when
# it pulls, else a local build of tools/docker/linux.Dockerfile (as tools/ci.sh linux does)
sim_half() {
  local eng img=ghcr.io/ckeller42/open-california/oc-linux:latest
  eng=$(command -v podman || command -v docker) || { echo "no podman or docker: no sim half" >&2; return 1; }
  if ! "$eng" pull -q "$img" >/dev/null 2>&1; then
    img=oc-linux-local; "$eng" build -q -t "$img" -f "$root/tools/docker/linux.Dockerfile" "$root" >/dev/null || return 1
  fi
  local user=(--userns=keep-id)   # podman: the files it writes belong to the caller
  [ "$(basename "$eng")" = docker ] && user=(--user "$(id -u):$(id -g)")
  "$eng" run --rm "${user[@]}" -v "$root":/w:Z -v "$out":/out:Z -w /w -e HIL_OUT=/out -e HIL_TARGET=sim \
    -e HIL_ACTIONS="$1" "$img" sh -c 'bash firmware/host/fetch_nimble.sh && python3 -m pytest \
    tests/firmware/test_lora_scenarios.py -v -rs -p no:cacheprovider --junitxml /out/junit-sim.xml'
}
step="${1:-}"; shift || true
case "$step" in
  identify)
    { echo "port $port -> $real"; cmd 2 "status" "lora status" "wifi status" "i2c dump 0x58" "i2c dump 0x34"; } \
      | tee "$out/identify.txt" ;;
  tail)
    cmd --tail "${1:-60}" | tee "$out/console-tail.log" ;;
  diag)
    run_steps 30 "lora status" "lora at RX1DL,RX2DL,RX2DR,RX2FQ,JN1DL,JN2DL,BAND,CLASS,ADR,DR,NJS" | tee "$out/rx-diag.log"
    run_steps 12 "i2c dump 0x58" "i2c dump 0x34" | tee "$out/i2c-dump.log" ;;
  scenarios)
    actions=0; [ "${1:-}" = --actions ] && actions=1
    cd "$root"
    # the board half on the host (it needs the serial port) ...
    HIL_TARGET=board HIL_PORT="$port" HIL_ACTIONS="$actions" HIL_OUT="$out" \
      "${PY:-python3}" -m pytest tests/firmware/test_lora_scenarios.py -v -rs -p no:cacheprovider \
      --junitxml "$out/junit-board.xml" || status=$?
    # ... then the sim half in the oc-linux container (the 32-bit host build), which writes the drift reports
    sim_half "$actions" || { sim_status=$?; echo "the sim half failed (see $out/junit-sim.xml): drift reports may be missing" >&2; }
    exit "${status:-${sim_status:-0}}" ;;   # a board failure first, else the sim half's
  capture-join)
    echo "changes the board: LoRa off and on, a real join and uplinks"
    run_steps 160 "lora off" "lora status" "lora on" | tee "$out/capture-join.log" ;;
  flash)
    [ "${HIL_FLASH:-}" = owner-present ] || die "flash writes the board's flash: set HIL_FLASH=owner-present"
    [[ "${1:-}" =~ ^[0-9]+$ ]] || die "flash needs an explicit CI run id (never main: AGENTS.md)"
    exec bash "$here/flash_ci.sh" "$1" "$port" ;;
  *) die "usage: HIL_PORT=... $0 identify|tail S|diag|scenarios [--actions]|capture-join|flash RUN_ID" ;;
esac
echo "output: $out"
