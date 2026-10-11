#!/usr/bin/env bash
# Flash the ESP32-S3 satellite with the firmware CI built for a branch (default main) or a run id.
#   tools/esplab/flash_ci.sh [branch|run-id] [port]      e.g. flash_ci.sh main /dev/ttyACM0
# Needs: gh logged in, ~/esp-venv/bin/esptool (flash.sh), the user in `dialout`. Works on buspi and thinky.
# Writes every image flasher_args.json lists — bootloader, partition table, ota_data_initial.bin
# (otadata: boot ota_0) and the app at ota_0 — never NVS, so bonds and WiFi survive. Since R_FW_OTA
# the normal update is OTA (POST /api/ota); this is the USB path for the first OTA layout and recovery.
set -euo pipefail
REF="${1:-main}"; PORT="${2:-/dev/ttyACM0}"; REPO=ckeller42/open-california
here="$(cd "$(dirname "$0")" && pwd)"
if [[ "$REF" =~ ^[0-9]+$ ]]; then RUN="$REF"; else
  RUN=$(gh run list -R "$REPO" --workflow ci.yml --branch "$REF" --status success --limit 1 --json databaseId --jq '.[0].databaseId')
fi
[ -n "$RUN" ] && [ "$RUN" != null ] || { echo "no successful CI run for '$REF'" >&2; exit 1; }
sha=$(gh run view "$RUN" -R "$REPO" --json headSha --jq '.headSha[0:7]')
dir="${ESP_FW_DIR:-$HOME/calictl-esp}/ci-$sha-$RUN"; mkdir -p "$dir"   # per run: a re-run of the same commit gets its own artifact
[ -f "$dir/flasher_args.json" ] || gh run download "$RUN" -R "$REPO" -n firmware-esp32s3 -D "$dir"
python3 - "$dir" <<'PY'   # every listed image (ota_data_initial.bin too) or nothing: no half-flashed layout
import json, os, sys
d = sys.argv[1]
missing = [f for f in json.load(open(os.path.join(d, "flasher_args.json")))["flash_files"].values()
           if not os.path.exists(os.path.join(d, f))]
sys.exit("artifact lacks " + " ".join(missing) if missing else 0)
PY
cp "$here/flash.sh" "$dir/flash.sh"
echo "flashing CI run $RUN ($REF @ $sha) from $dir to $PORT"
[ -e "$PORT" ] || { echo "no ESP on $PORT (plug it in, or pass the port)" >&2; exit 2; }
exec bash "$dir/flash.sh" "$PORT"
