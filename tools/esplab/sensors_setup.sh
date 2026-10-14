#!/usr/bin/env bash
# Configure the van's external BLE sensors on the ESP without a key ever passing through a chat
# or a shell argument (R_FW_SENSORS, design F in #360). Run on buspi as pi (the Victron keys are
# the 0600 files vedirect-influx already uses):
#   tools/esplab/sensors_setup.sh [esp-url]        e.g. sensors_setup.sh http://192.168.8.183
# Switches the experimental sensor track on, waits for the passive scan windows (one a minute),
# then for every wanted type that is not configured yet: Victron rows get their key from the key
# file (sent in the POST body, never argv, never printed); every seen row of the type is tried in
# turn — the advert's key-check byte refuses a neighbour's charger with key_mismatch, the first 200
# wins. The Govee needs no key: the seen row with the strongest signal is yours (it is inside the
# van). Idempotent: a configured type is skipped. Ends with the ext block of /api/state.
#   MPPT_KEY_FILE / BATT_KEY_FILE override the key paths; WAIT_MIN the scan wait (default 10);
#   SETTLE_S the final wait for a first reading (default 65).
set -euo pipefail
ESP="${1:-http://192.168.8.183}"
MPPT_KEY_FILE="${MPPT_KEY_FILE:-/etc/vedirect-influx/ble_key.txt}"
BATT_KEY_FILE="${BATT_KEY_FILE:-/etc/vedirect-influx/batterysense_key.txt}"
WAIT_MIN="${WAIT_MIN:-10}"
SETTLE_S="${SETTLE_S:-65}"
RESP=$(mktemp)
trap 'rm -f "$RESP"' EXIT

api() { curl -sf --max-time 10 "$ESP/api/sensors"; }
configured() {   # the configured types, space-separated
    api | python3 -c 'import json,sys; print(" ".join(c["type"] for c in json.load(sys.stdin)["configured"]))'
}
seen_ids() {   # seen_ids <type>: the seen ids of that type, strongest signal first
    api | python3 -c 'import json,sys
rows = [r for r in json.load(sys.stdin)["seen"] if r["type"] == sys.argv[1]]
print(" ".join(str(r["id"]) for r in sorted(rows, key=lambda r: -r["rssi"])))' "$1"
}

read_key() {   # read_key <file> -> KEY (never echoed)
    [ -r "$1" ] || { echo "cannot read key file $1 (run as pi on buspi)" >&2; return 1; }
    KEY=$(tr -d '[:space:]' < "$1")
    [[ "$KEY" =~ ^[0-9a-fA-F]{32}$ ]] || { echo "$1 does not hold a 32-hex key" >&2; return 1; }
}

add() {   # add <id> <alias> [key] -> the HTTP status on stdout, the body in $RESP; the key travels in the request body only
    local body
    if [ -n "${3:-}" ]; then body=$(printf '{"add":%s,"alias":"%s","key":"%s"}' "$1" "$2" "$3")
    else body=$(printf '{"add":%s,"alias":"%s"}' "$1" "$2"); fi
    curl -s --max-time 10 -o "$RESP" -w '%{http_code}' -X POST "$ESP/api/sensors" -d @- <<< "$body"
}

setup_type() {   # setup_type <type> <alias> [keyfile]: 0 configured, 2 nothing seen yet, 1 error
    local type=$1 alias=$2 keyfile=${3:-} id rc KEY=""
    if [[ " $(configured) " == *" $type "* ]]; then echo "$type: already configured"; return 0; fi
    [ -n "$keyfile" ] && { read_key "$keyfile" || return 1; }
    for id in $(seen_ids "$type"); do
        rc=$(add "$id" "$alias" "$KEY")
        if [ "$rc" = 200 ]; then echo "$type: added as $alias (id $id)"; return 0; fi
        if grep -q key_mismatch "$RESP"; then echo "$type: id $id is not ours (key mismatch), next"; continue; fi
        echo "$type: add id $id failed: HTTP $rc $(cat "$RESP")" >&2; return 1
    done
    echo "$type: nothing seen yet"; return 2
}

echo "sensors setup on $ESP"
curl -sf --max-time 10 -X POST "$ESP/api/experimental" -d '{"sensors":true}' >/dev/null \
    || { echo "cannot switch the sensor track on (old firmware or no WiFi?)" >&2; exit 1; }
# ponytail: a flat retry loop; a window runs every 60 s, so WAIT_MIN tries one minute apart
for ((i = 1; i <= WAIT_MIN; i++)); do
    pending=0
    for spec in "victron_mppt mppt-75-15 $MPPT_KEY_FILE" "victron_battery battery-sense $BATT_KEY_FILE" "govee govee"; do
        # shellcheck disable=SC2086   # the spec is three words by construction
        if setup_type $spec; then rc=0; else rc=$?; fi
        case $rc in 0) ;; 2) pending=$((pending + 1)) ;; *) exit 1 ;; esac
    done
    [ "$pending" = 0 ] && break
    echo "waiting for the next scan window ($i/$WAIT_MIN)"; sleep 60
done
echo "configured: $(configured)"
sleep "$SETTLE_S"   # one more window: the first readings
curl -sf --max-time 10 "$ESP/api/state" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("ext"), indent=1))'
