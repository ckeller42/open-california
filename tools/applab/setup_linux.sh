#!/usr/bin/env bash
# setup_linux.sh — one-time, idempotent install of the app lab on a Linux x86_64 host with KVM
# (thinky). Every step checks before it acts, so re-running is safe. Nothing it fetches is
# committed. It never touches the network configuration, NetworkManager or any interface.
#
#   tools/applab/setup_linux.sh
#
# Env (defaults suit thinky):
#   LAB_DIR            lab root (sdk/, avd/, apks/, env.sh)          [$HOME/android-lab]
#   AVD                AVD name (the runbook + XY points use it)     [lab34]
#   BUMBLE_PY          the python that has Bumble                    [$HOME/esp-venv/bin/python]
#   APK_SRC            scp source of the CaliforniaOnTour APK        [pi@buspi:~/apks/]
#   CMDLINE_TOOLS_ZIP  Google's cmdline-tools archive                [commandlinetools-linux-13114758_latest.zip]
set -euo pipefail

LAB_DIR="${LAB_DIR:-$HOME/android-lab}"
AVD="${AVD:-lab34}"
BUMBLE_PY="${BUMBLE_PY:-$HOME/esp-venv/bin/python}"
APK_SRC="${APK_SRC:-pi@buspi:~/apks/}"
CMDLINE_TOOLS_ZIP="${CMDLINE_TOOLS_ZIP:-commandlinetools-linux-13114758_latest.zip}"
IMAGE="system-images;android-34;google_apis;x86_64"
die() { echo "setup_linux: $*" >&2; exit 1; }

[ "$(uname -s)-$(uname -m)" = Linux-x86_64 ] || die "needs Linux x86_64 (the APK ships x86_64 libs; on an arm64 Mac follow the README's macOS recipe)"
[ -e /dev/kvm ] || die "no /dev/kvm: enable virtualisation in the firmware setup"
id -nG | tr ' ' '\n' | grep -qx kvm || die "$USER is not in group kvm: sudo usermod -aG kvm $USER, then log in again"
{ [ -r /dev/kvm ] && [ -w /dev/kvm ]; } || die "/dev/kvm is not read/writable for $USER (log in again after usermod)"
command -v java >/dev/null || die "no java (sdkmanager needs 17+): sudo apt-get install -y openjdk-17-jre-headless"
command -v unzip >/dev/null || die "no unzip: sudo apt-get install -y unzip"
{ [ -x "$BUMBLE_PY" ] && "$BUMBLE_PY" -c 'import bumble' 2>/dev/null; } || die "no Bumble at $BUMBLE_PY (set BUMBLE_PY)"
[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = yes ] || \
  die "linger is off: netsim.ini lives in \$XDG_RUNTIME_DIR, which systemd removes when the last ssh session ends — sudo loginctl enable-linger $USER"

mkdir -p "$LAB_DIR"/sdk "$LAB_DIR"/avd "$LAB_DIR"/apks "$LAB_DIR"/.android
SDK="$LAB_DIR/sdk"
if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/ct.zip" "https://dl.google.com/android/repository/$CMDLINE_TOOLS_ZIP"
  unzip -q "$tmp/ct.zip" -d "$tmp"
  mkdir -p "$SDK/cmdline-tools"
  mv "$tmp/cmdline-tools" "$SDK/cmdline-tools/latest"
  rm -rf "$tmp"
fi
cat >"$LAB_DIR/env.sh" <<EOF
export ANDROID_SDK_ROOT=$SDK ANDROID_HOME=$SDK
export ANDROID_USER_HOME=$LAB_DIR/.android ANDROID_AVD_HOME=$LAB_DIR/avd ANDROID_EMULATOR_HOME=$LAB_DIR/.android
export PATH=\$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:\$ANDROID_SDK_ROOT/platform-tools:\$ANDROID_SDK_ROOT/emulator:\$PATH
EOF
# shellcheck source=/dev/null
. "$LAB_DIR/env.sh"
yes | sdkmanager --licenses >/dev/null || true
sdkmanager --install platform-tools emulator "build-tools;35.0.0" "platforms;android-34" "$IMAGE" >/dev/null
emulator -accel-check 2>&1 | grep -q 'is installed and usable' || die "KVM not usable by the emulator: $(emulator -accel-check 2>&1 | tail -1)"
avdmanager list avd -c | grep -qx "$AVD" || echo no | avdmanager create avd -n "$AVD" -k "$IMAGE" -d pixel_6 >/dev/null
"$BUMBLE_PY" -c 'import grpc, google.protobuf' 2>/dev/null || "$BUMBLE_PY" -m pip install -q grpcio protobuf
ls "$LAB_DIR"/apks/*.apk >/dev/null 2>&1 || scp -q -o BatchMode=yes -o ConnectTimeout=10 "${APK_SRC}*.apk" "$LAB_DIR/apks/" || true
ls "$LAB_DIR"/apks/*.apk >/dev/null 2>&1 || echo "setup_linux: no APK in $LAB_DIR/apks yet (scp from $APK_SRC failed — buspi offline?). Copy it by hand later: scp ${APK_SRC}*.apk $LAB_DIR/apks/" >&2
echo "app lab ready: LAB_DIR=$LAB_DIR AVD=$AVD BUMBLE_PY=$BUMBLE_PY"
echo "next: LAB_DIR=$LAB_DIR AVD=$AVD BUMBLE_PY=$BUMBLE_PY FAKE_UNIT_VIN=\$(cat $LAB_DIR/vin) tools/applab/labctl.sh up"
