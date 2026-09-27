#!/usr/bin/env bash
# On the GitHub runner: KVM access, a guest kernel WITH Bluetooth (the runner's own has none), then
# boot it with virtme-ng and run in_vm.sh inside as root. The invocation is the one the Task 9 spike
# proved on ubuntu-latest (docs: .superpowers/sdd/2026-09-25-pairing-verification/task-9-report.md):
#   - pip-install as the runner user (a `sudo pip` collides with Debian's cryptography);
#   - call vng by ABSOLUTE path under `sudo env PATH=… PYTHONPATH=…` (sudo's secure_path/site
#     don't see ~/.local);
#   - pass PYTHONPATH again on the guest command line (the guest shell doesn't inherit it).
# Pass/fail is decided by the rig's own last line, not vng's exit status.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO="$PWD"

echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' | sudo tee /etc/udev/rules.d/99-kvm4all.rules
sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=kvm
ls -l /dev/kvm

sudo apt-get update -qq
sudo apt-get install -y -qq bluez dbus qemu-system-x86 linux-image-generic
KVER=$(ls /boot/vmlinuz-*-generic | sort -V | tail -1 | sed 's|/boot/vmlinuz-||')
sudo apt-get install -y -qq "linux-modules-extra-$KVER" || true
echo "guest kernel: $KVER"

# The system python3 (the guest runs the same interpreter off the shared root filesystem).
/usr/bin/python3 -m pip install -q virtme-ng "bumble==0.0.235" bleak dbus-fast pyyaml
USERSITE=$(/usr/bin/python3 -m site --user-site)
VNG=$(command -v vng || true)
[ -n "$VNG" ] || VNG="$(/usr/bin/python3 -m site --user-base)/bin/vng"
"$VNG" --version

LOG=$(mktemp)
sudo env "PATH=$PATH" "PYTHONPATH=$USERSITE" "$VNG" --run "/boot/vmlinuz-$KVER" --user root -- \
  env "PYTHONPATH=$USERSITE" bash "$REPO/tests/realstack/in_vm.sh" 2>&1 | tee "$LOG" || true
grep -q '^ALL PASS' "$LOG"
