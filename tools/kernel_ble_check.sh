#!/bin/bash
# Qualify a Raspberry Pi kernel for calictl's BLE reconnects (the 6.18.50 regression,
# docs/raspberry-pi-setup.md "Known issue"). Run on the Pi, with the camper unit awake:
#
#   sudo tools/kernel_ble_check.sh [--port 8088] [--window 100]
#
# Watches one poll window in btmon and decides by the discriminating signature, not by
# luck: a broken kernel sees the unit's adverts but never puts an LE Create Connection
# on the air. Exit 0 = kernel OK, 1 = regression signature, 2 = inconclusive (unit not
# seen — asleep/out of range), 3 = undetermined (counts printed; judge by hand).
set -u

PORT=8088 WINDOW=100
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT=$2; shift 2 ;;
    --window) WINDOW=$2; shift 2 ;;
    --selftest) SELFTEST=1; shift ;;
    *) echo "usage: $0 [--port N] [--window SECONDS]" >&2; exit 64 ;;
  esac
done

# verdict <advert_rows> <create_conns> <connect_fails> <online> -> prints verdict, returns code
verdict() {
  local adverts=$1 creates=$2 fails=$3 online=$4
  if [ "$online" = "True" ] && [ "$creates" -ge 1 ]; then
    echo "OK: kernel $(uname -r) connects (adverts=$adverts create_conn=$creates online=$online)"; return 0
  elif [ "$adverts" -ge 1 ] && [ "$creates" -eq 0 ] && [ "$fails" -ge 1 ]; then
    echo "REGRESSION: unit advertising (rows=$adverts) but the kernel never issued an" \
         "LE Create Connection ($fails Connect Failed) — the 6.18.50 signature. Pin a good kernel."
    return 1
  elif [ "$adverts" -eq 0 ]; then
    echo "INCONCLUSIVE: unit not seen on air (asleep/out of range) — wake it and rerun."; return 2
  fi
  echo "UNDETERMINED: adverts=$adverts create_conn=$creates connect_failed=$fails online=$online"; return 3
}

if [ "${SELFTEST:-0}" = 1 ]; then
  # the parse/verdict logic against both signatures, no radio needed
  verdict 6 0 2 False >/dev/null; rc=$?; [ $rc -eq 1 ] || { echo "selftest FAIL: regression case"; exit 70; }
  verdict 6 2 0 True  >/dev/null; rc=$?; [ $rc -eq 0 ] || { echo "selftest FAIL: ok case"; exit 70; }
  verdict 0 0 0 False >/dev/null; rc=$?; [ $rc -eq 2 ] || { echo "selftest FAIL: asleep case"; exit 70; }
  echo "selftest OK"; exit 0
fi

command -v btmon >/dev/null || { echo "btmon not found (apt install bluez)" >&2; exit 64; }
ADDR=$(curl -sf "http://localhost:$PORT/api/pairing" | python3 -c 'import json,sys; print(json.load(sys.stdin)["address"] or "")' 2>/dev/null)
[ -n "$ADDR" ] || { echo "no bonded address from the daemon on :$PORT — pair first" >&2; exit 64; }

LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT
echo "watching $ADDR for ${WINDOW}s on kernel $(uname -r) ..."
timeout "$WINDOW" btmon >"$LOG" 2>/dev/null

ADVERTS=$(grep -c "LE Address: $ADDR" "$LOG")                 # MGMT Device Found, host-resolved
CREATES=$(grep -cE "LE (Extended )?Create Connection" "$LOG")
FAILS=$(grep -c "MGMT Event: Connect Failed" "$LOG")
ONLINE=$(curl -sf "http://localhost:$PORT/api/state" | python3 -c 'import json,sys; m=json.load(sys.stdin)["_meta"]; print(m["online"] and m["age_s"] < '"$WINDOW"' + 120)' 2>/dev/null)

verdict "$ADVERTS" "$CREATES" "$FAILS" "${ONLINE:-False}"
