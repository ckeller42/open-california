#!/usr/bin/env bash
# Flash the calictl ESP32-S3 image in this directory, layout from flasher_args.json (never hand-typed).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-/dev/ttyACM0}"
ARGS=$(python3 - <<PY
import json; a=json.load(open("flasher_args.json")); x=a["extra_esptool_args"]
out=["--chip",x["chip"],"-p","$PORT","-b","460800","--before",x["before"].replace("_","-"),"--after",x["after"].replace("_","-"),"write-flash"]
out+=[s.replace("_","-") if s.startswith("--") else s for s in a["write_flash_args"]]
for off,f in sorted(a["flash_files"].items(), key=lambda kv:int(kv[0],16)): out+=[off,f]
print(" ".join(out))
PY
)
echo "esptool $ARGS"
exec ~/esp-venv/bin/esptool $ARGS
