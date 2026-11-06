#!/usr/bin/env bash
# On the GitHub runner: KVM access, a guest kernel WITH Bluetooth (the runner's own has none), then
# boot it with virtme-ng and run in_vm.sh inside as root. The invocation below was proven on
# ubuntu-latest while building this job (#201); the non-obvious parts:
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

# apt runs in timed steps with a limit each. On some runners (mostly the West US regions) this
# phase took up to 30 min, and under -qq the log stayed empty. Without -qq apt prints one `Get:`
# line per finished file and a closing `Fetched <size> in <time> (<rate>)`: the log shows which
# step is slow and the mirror's throughput, also when a limit stops the step.
apt_step() {   # apt_step <limit seconds> <label> <apt-get args...>
  local limit=$1 label=$2 t0=$SECONDS
  shift 2
  echo "::group::$label"
  if sudo timeout "$limit" apt-get "$@"; then
    echo "::endgroup::"
    echo "$label: $((SECONDS - t0)) s"
  else
    local rc=$?
    echo "::endgroup::"
    if [ "$rc" -eq 124 ]; then echo "::error::$label took longer than $limit s (slow apt mirror on this runner)"; fi
    return "$rc"
  fi
}
apt_step 300 "apt-get update" update
# The guest kernel by its version, not the linux-image-generic metapackage: that one depends on
# linux-firmware (~680 MB of device firmware a QEMU guest never loads), and the recommends add a
# desktop stack. ~1 GB of download becomes ~200 MB (measured on a clean noble).
KVER=$(apt-cache depends linux-image-generic | sed -n 's/.*Depends: linux-image-\([0-9].*-generic\)$/\1/p')
[ -n "$KVER" ] || { echo "::error::no kernel version from linux-image-generic"; exit 1; }
echo "guest kernel: $KVER"
PKGS=(bluez dbus qemu-system-x86 "linux-image-$KVER" "linux-modules-extra-$KVER")
apt_step 1200 "apt-get download" install -y --no-install-recommends --download-only "${PKGS[@]}"
apt_step 600 "apt-get install" install -y -qq --no-install-recommends "${PKGS[@]}"

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
