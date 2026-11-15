#!/usr/bin/env bash
# Regenerate the iOS docs screenshot gallery in docs/screenshots/ios/ from the mock fixtures.
#
# It runs the DocsGallery generator (ios/AppTests/DocsGallery.swift) on an iOS Simulator and copies
# the PNGs out of the app's Documents container — a hosted test cannot write to the repo directly.
# Needs macOS with Xcode + a simulator, and xcodegen on PATH. Overridable:
#   IOS_SIM_NAME (default "iPhone 17"), IOS_SIM_OS (default 26.2), IOS_BUNDLE_ID.
set -euo pipefail
cd "$(dirname "$0")/.."

SIM_NAME="${IOS_SIM_NAME:-iPhone 17}"
SIM_OS="${IOS_SIM_OS:-26.2}"
BUNDLE="${IOS_BUNDLE_ID:-org.example.opencalifornia.camper}"
OUT="docs/screenshots/ios"

# The udid of the chosen simulator (by name; narrowed to the OS runtime if that device name repeats).
UDID="$(xcrun simctl list -j devices available | SIM_NAME="$SIM_NAME" SIM_OS="$SIM_OS" python3 -c '
import json, os, sys
data = json.load(sys.stdin)["devices"]
name, os_ = os.environ["SIM_NAME"], os.environ["SIM_OS"].replace(".", "-")
cands = [d["udid"] for rt, ds in data.items() for d in ds if d["name"] == name]
pref = [d["udid"] for rt, ds in data.items() if os_ in rt for d in ds if d["name"] == name]
print((pref or cands)[0] if (pref or cands) else "")
')"
[ -n "$UDID" ] || { echo "no '$SIM_NAME' simulator found" >&2; exit 1; }

( cd ios && xcodegen generate >/dev/null )
xcrun simctl boot "$UDID" 2>/dev/null || true

# TEST_RUNNER_ forwards GALLERY into the hosted test; the generator writes into the app container.
TEST_RUNNER_GALLERY=1 xcodebuild test \
  -project ios/Camper.xcodeproj -scheme Camper \
  -destination "id=$UDID" \
  -only-testing:'AppTests/DocsGallery/generate()' >/dev/null

CONTAINER="$(xcrun simctl get_app_container "$UDID" "$BUNDLE" data)"
mkdir -p "$OUT"
cp "$CONTAINER/Documents/gallery/"*.png "$OUT/"
echo "Wrote $(find "$OUT" -name '*.png' | wc -l | tr -d ' ') screenshots to $OUT/"
