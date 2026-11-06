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
# The limits (120 + 900 + 300 s = 22 min) stay inside the job's 30 min with room for pip and the rig
# (~3 min), so a slow step always reports its own error before the job limit cancels it.
# The runner's apt reads its hosts from apt-mirrors.txt, azure.archive.ubuntu.com first. On westus2
# runners that endpoint gave 7–36 kB/s while archive.ubuntu.com gave 38–45 MB/s on the same runners
# (2026-11-06), so drop it: a cache miss (first run, a kernel bump, an evicted entry) then still
# downloads at a normal rate.
if [ -f /etc/apt/apt-mirrors.txt ]; then
  sudo sed -i '/azure\.archive\.ubuntu\.com/d' /etc/apt/apt-mirrors.txt
  cat /etc/apt/apt-mirrors.txt
fi
apt_step 120 "apt-get update" update
# The guest kernel by its version, not the linux-image-generic metapackage: that one depends on
# linux-firmware (~680 MB of device firmware a QEMU guest never loads), and the recommends add a
# desktop stack. ~1 GB of download becomes ~200 MB (measured on a clean noble).
KVER=$(apt-cache depends linux-image-generic | sed -n 's/.*Depends: linux-image-\([0-9].*-generic\)$/\1/p')
[ -n "$KVER" ] || { echo "::error::no kernel version from linux-image-generic"; exit 1; }
echo "guest kernel: $KVER"
PKGS=(bluez dbus qemu-system-x86 "linux-image-$KVER" "linux-modules-extra-$KVER")
# ~/debs: the .deb files the workflow restored from its cache. apt reuses each one whose version
# still matches and downloads only the rest; autoclean drops versions the index no longer has, so
# the saved set does not grow. The workflow saves it under a key that names the set.
DEBS=~/debs
if compgen -G "$DEBS/*.deb" >/dev/null; then
  echo "cached .deb files: $(find "$DEBS" -name '*.deb' | wc -l)"
  sudo cp "$DEBS"/*.deb /var/cache/apt/archives/
fi
apt_step 900 "apt-get download" install -y --no-install-recommends --download-only "${PKGS[@]}"
sudo apt-get autoclean -qq
rm -rf "$DEBS" && mkdir -p "$DEBS" && cp /var/cache/apt/archives/*.deb "$DEBS/"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "debs_key=$(find "$DEBS" -name '*.deb' -printf '%f\n' | sort | sha256sum | cut -c1-16)" >> "$GITHUB_OUTPUT"
fi
apt_step 300 "apt-get install" install -y -qq --no-install-recommends "${PKGS[@]}"

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
