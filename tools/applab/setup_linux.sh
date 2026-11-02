#!/usr/bin/env bash
# setup_linux.sh — one-time, idempotent install of the app lab on a Linux x86_64 host with KVM.
# Every step checks before it acts, so re-running is safe. Nothing it fetches is committed. It never
# touches the network configuration, NetworkManager or any interface.
#
#   tools/applab/setup_linux.sh                     # fresh lab (onboard + pair the app once, README)
#   LAB_FROM=thinky tools/applab/setup_linux.sh     # move a lab: copy the onboarded AVD, VIN, APKs
#
# Env:
#   LAB_DIR            lab root (sdk/, avd/, apks/, venv-bumble/, env.sh, vin)  [$HOME/android-lab]
#   AVD                AVD name                                                   [lab34]
#   BUMBLE_PY          a python with Bumble; unset = $LAB_DIR/venv-bumble (created)
#   BUMBLE_VERSION     the Bumble pin for that venv                               [0.0.235]
#   APK_SRC            scp source of the per-version APK dirs                     [pi@buspi:~/apks/]
#   APK_VERSION        the app version dir to copy (the phone's version)          [5.4.0.3036]
#   LAB_FROM           ssh host of an existing lab: its AVD, vin and APKs are copied (rsync)
#   CMDLINE_TOOLS_ZIP  Google's cmdline-tools archive        [commandlinetools-linux-13114758_latest.zip]
#
# Missing packages (Java 17, unzip, Xvfb, rsync) are installed with `sudo -n apt-get` when sudo needs
# no password; otherwise the script stops with the command to run.
set -euo pipefail

LAB_DIR="${LAB_DIR:-$HOME/android-lab}"
AVD="${AVD:-lab34}"
BUMBLE_VERSION="${BUMBLE_VERSION:-0.0.235}"
BUMBLE_PY="${BUMBLE_PY:-$LAB_DIR/venv-bumble/bin/python}"
APK_SRC="${APK_SRC:-pi@buspi:~/apks/}"
APK_VERSION="${APK_VERSION:-5.4.0.3036}"
LAB_FROM="${LAB_FROM:-}"
CMDLINE_TOOLS_ZIP="${CMDLINE_TOOLS_ZIP:-commandlinetools-linux-13114758_latest.zip}"
IMAGE="system-images;android-34;google_apis;x86_64"
die() { echo "setup_linux: $*" >&2; exit 1; }

# need CMD PKG: install PKG when CMD is missing (sudo -n), else stop with the command.
need() {
  command -v "$1" >/dev/null && return
  if sudo -n true 2>/dev/null; then
    echo "setup_linux: installing $2"
    sudo -n apt-get install -y -q "$2" >/dev/null
  else
    die "no $1: sudo apt-get install -y $2"
  fi
}

[ "$(uname -s)-$(uname -m)" = Linux-x86_64 ] || die "needs Linux x86_64 (on an arm64 Mac follow the README's macOS recipe)"
[ -e /dev/kvm ] || die "no /dev/kvm: enable virtualisation in the firmware setup"
id -nG | tr ' ' '\n' | grep -qx kvm || die "$USER is not in group kvm: sudo usermod -aG kvm $USER, then log in again"
{ [ -r /dev/kvm ] && [ -w /dev/kvm ]; } || die "/dev/kvm is not read/writable for $USER (log in again after usermod)"
[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = yes ] || \
  die "linger is off: netsim.ini lives in \$XDG_RUNTIME_DIR, which systemd removes when the last ssh session ends — sudo loginctl enable-linger $USER"
need java openjdk-17-jre-headless
need unzip unzip
need Xvfb xvfb       # labctl's default GPU mode renders on a private Xvfb display
need rsync rsync

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
export LAB_DIR=$LAB_DIR AVD=$AVD BUMBLE_PY=$BUMBLE_PY
export ANDROID_SDK_ROOT=$SDK ANDROID_HOME=$SDK
export ANDROID_USER_HOME=$LAB_DIR/.android ANDROID_AVD_HOME=$LAB_DIR/avd ANDROID_EMULATOR_HOME=$LAB_DIR/.android
export PATH=\$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:\$ANDROID_SDK_ROOT/platform-tools:\$ANDROID_SDK_ROOT/emulator:\$PATH
EOF
# shellcheck source=/dev/null
. "$LAB_DIR/env.sh"
yes | sdkmanager --licenses >/dev/null || true
sdkmanager --install platform-tools emulator "build-tools;35.0.0" "platforms;android-34" "$IMAGE" >/dev/null
emulator -accel-check 2>&1 | grep -q 'is installed and usable' || die "KVM not usable by the emulator: $(emulator -accel-check 2>&1 | tail -1)"

# Bumble: its own venv unless BUMBLE_PY points at one that works.
if ! { [ -x "$BUMBLE_PY" ] && "$BUMBLE_PY" -c 'import bumble, grpc, google.protobuf' 2>/dev/null; }; then
  [ "$BUMBLE_PY" = "$LAB_DIR/venv-bumble/bin/python" ] || die "BUMBLE_PY=$BUMBLE_PY has no bumble/grpcio/protobuf"
  python3 -m venv "$LAB_DIR/venv-bumble"
  "$BUMBLE_PY" -m pip install -q "bumble==$BUMBLE_VERSION" grpcio protobuf pyyaml
fi

# AVD: copy an onboarded one from LAB_FROM (keeps the app's setup + VIN), else create a fresh one.
if [ -n "$LAB_FROM" ] && [ ! -d "$LAB_DIR/avd/$AVD.avd" ]; then
  echo "setup_linux: copying the AVD, vin and APKs from $LAB_FROM (stop its emulator first)"
  rsync -a "$LAB_FROM:android-lab/avd/$AVD.avd" "$LAB_DIR/avd/"
  rsync -a "$LAB_FROM:android-lab/avd/$AVD.ini" "$LAB_DIR/avd/"
  sed -i "s|^path=.*|path=$LAB_DIR/avd/$AVD.avd|" "$LAB_DIR/avd/$AVD.ini"
  rsync -a "$LAB_FROM:android-lab/vin" "$LAB_DIR/" && chmod 600 "$LAB_DIR/vin"
  rsync -a "$LAB_FROM:android-lab/apks/" "$LAB_DIR/apks/"
fi
avdmanager list avd -c | grep -qx "$AVD" || echo no | avdmanager create avd -n "$AVD" -k "$IMAGE" -d pixel_6 >/dev/null

# APKs: the phone's split APKs per version (buspi:~/apks/<version>/); labctl.sh install installs them.
if [ ! -f "$LAB_DIR/apks/$APK_VERSION/base.apk" ]; then
  mkdir -p "$LAB_DIR/apks/$APK_VERSION"
  scp -q -o BatchMode=yes -o ConnectTimeout=10 "${APK_SRC}${APK_VERSION}/*.apk" "$LAB_DIR/apks/$APK_VERSION/" || \
    echo "setup_linux: no APKs for $APK_VERSION (scp from $APK_SRC failed — buspi offline?). Later: scp '${APK_SRC}${APK_VERSION}/*.apk' $LAB_DIR/apks/$APK_VERSION/" >&2
fi
echo "app lab ready: LAB_DIR=$LAB_DIR AVD=$AVD BUMBLE_PY=$BUMBLE_PY"
echo "next: . $LAB_DIR/env.sh && FAKE_UNIT_VIN=\$(cat $LAB_DIR/vin) tools/applab/labctl.sh up"
