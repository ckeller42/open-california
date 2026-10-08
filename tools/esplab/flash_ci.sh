#!/usr/bin/env bash
# Flash the ESP32-S3 satellite with the firmware CI built for a branch (default main) or a run id.
#   tools/esplab/flash_ci.sh [branch|run-id] [port]      e.g. flash_ci.sh main /dev/ttyACM0
# Needs: gh logged in, ~/esp-venv/bin/esptool (flash.sh), the user in `dialout`. Works on buspi and thinky.
set -euo pipefail
REF="${1:-main}"; PORT="${2:-/dev/ttyACM0}"; REPO=ckeller42/open-california
here="$(cd "$(dirname "$0")" && pwd)"
if [[ "$REF" =~ ^[0-9]+$ ]]; then RUN="$REF"; else
  RUN=$(gh run list -R "$REPO" --workflow ci.yml --branch "$REF" --status success --limit 1 --json databaseId --jq '.[0].databaseId')
fi
[ -n "$RUN" ] && [ "$RUN" != null ] || { echo "no successful CI run for '$REF'" >&2; exit 1; }
sha=$(gh run view "$RUN" -R "$REPO" --json headSha --jq '.headSha[0:7]')
dir="${ESP_FW_DIR:-$HOME/calictl-esp}/ci-$sha"; mkdir -p "$dir"
[ -f "$dir/flasher_args.json" ] || gh run download "$RUN" -R "$REPO" -n firmware-esp32s3 -D "$dir"
cp "$here/flash.sh" "$dir/flash.sh"
echo "flashing CI run $RUN ($REF @ $sha) from $dir to $PORT"
[ -e "$PORT" ] || { echo "no ESP on $PORT (plug it in, or pass the port)" >&2; exit 2; }
exec bash "$dir/flash.sh" "$PORT"
