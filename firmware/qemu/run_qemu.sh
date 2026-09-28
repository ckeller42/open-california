#!/usr/bin/env bash
# run_qemu.sh — boot the esp32s3 QEMU-probe image in Espressif's QEMU (#154, Task 9).
#
#   firmware/qemu/run_qemu.sh [BUILD_DIR [FLASH_FILE]]
#
# BUILD_DIR   an `idf.py build` of the QEMU variant (default: firmware/build-qemu; see README.md)
# FLASH_FILE  the emulated 16 MB SPI flash (default: BUILD_DIR/qemu_flash.bin). Merged from the build
#             (bootloader + partition table + app, esptool merge-bin) only when it is missing or
#             older than the app image, so NVS written by one boot is there on the next boot.
#             Delete it for a factory-fresh flash.
#
# Needs ESP-IDF's environment (`. $IDF_PATH/export.sh`): qemu-system-xtensa is the build pinned by
# the IDF's tools/tools.json (IDF v6.1: espressif/qemu esp-develop-9.2.2-20260417, installed in the
# espressif/idf:v6.1 image). The QEMU arguments are `idf.py qemu`'s for esp32s3
# (tools/idf_py_actions/qemu_ext.py), with two differences: the flash file is not regenerated on
# every run (idf.py qemu re-merges and so wipes NVS unless given --flash-file), and UART0 goes to
# stdio WITHOUT the QEMU monitor multiplexed onto it (`-serial stdio -monitor none` instead of
# `-nographic -serial mon:stdio`): stdin/stdout are the firmware console's line protocol, nothing
# else. Stop with SIGTERM (or SIGINT).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
build="$(cd "${1:-$here/../build-qemu}" && pwd)"
flash="${2:-$build/qemu_flash.bin}"
efuse="$(dirname "$flash")/qemu_efuse.bin"

: "${IDF_PATH:?source \$IDF_PATH/export.sh first}"
command -v qemu-system-xtensa >/dev/null || { echo "run_qemu.sh: qemu-system-xtensa not on PATH" >&2; exit 2; }
app="$build/$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["app"]["file"])' "$build/flasher_args.json")"

if [ ! -f "$flash" ] || [ "$app" -nt "$flash" ]; then
    size="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["flash_settings"]["flash_size"])' "$build/flasher_args.json")"
    (cd "$build" && python -m esptool --chip esp32s3 merge-bin --pad-to-size "$size" \
        --output "$flash" @flash_args) >&2
fi
if [ ! -f "$efuse" ]; then
    # idf.py qemu's default esp32s3 eFuse block, taken from the same IDF (not copied here)
    python - "$efuse" <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ["IDF_PATH"], "tools"))
from idf_py_actions.qemu_ext import QEMU_TARGETS
open(sys.argv[1], "wb").write(QEMU_TARGETS["esp32s3"].default_efuse)
PY
fi

exec qemu-system-xtensa -M esp32s3 -m 32M \
    -drive "file=$flash,if=mtd,format=raw" \
    -drive "file=$efuse,if=none,format=raw,id=efuse" \
    -global driver=nvram.esp32s3.efuse,property=drive,value=efuse \
    -global driver=timer.esp32s3.timg,property=wdt_disable,value=true \
    -display none -monitor none -serial stdio
