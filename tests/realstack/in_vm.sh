#!/usr/bin/env bash
# Inside the CI VM (as root): kernel Bluetooth + dbus + bluetoothd, then the rig.
# vm.sh passes PYTHONPATH (the runner user's site-packages: bumble/bleak/dbus_fast) on the command
# line — the guest shell does not inherit the host's environment.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOGS=/tmp/realstack
mkdir -p "$LOGS"

modprobe hci_vhci
ls -l /dev/vhci
mkdir -p /run/dbus /var/lib/bluetooth
mount -t tmpfs tmpfs /var/lib/bluetooth   # fresh, writable bond store (the host root may be read-only)
dbus-daemon --system --fork
BTD=/usr/libexec/bluetooth/bluetoothd
[ -x "$BTD" ] || BTD=/usr/lib/bluetooth/bluetoothd
"$BTD" -n -d >"$LOGS/bluetoothd.log" 2>&1 &
btmon >"$LOGS/btmon.log" 2>&1 &
sleep 2

dump() {
  echo "----- bluetoothd (tail) -----"; grep -v gatt-client "$LOGS/bluetoothd.log" | tail -n 200 || true
  echo "----- btmon (tail) -----"; tail -n 300 "$LOGS/btmon.log" || true
}

rc=0
timeout 600 python3 "$HERE/rig.py" || rc=$?
if [ "$rc" -ne 0 ]; then
  echo "rig exited $rc"
  dump
fi
exit "$rc"
