#!/usr/bin/env bash
# Regenerate the iOS docs screenshot gallery in docs/screenshots/ios/ from the mock fixtures.
#
# Run this whenever the app's screens change (a new version, new wording, a new control) to refresh
# the shots in docs/ios-app.md. It renders the screens listed in ios/AppTests/DocsGallery.swift on an
# iOS Simulator and copies the PNGs out of the app's Documents container — a hosted test cannot write
# to the repo directly. Then review the diff and commit docs/screenshots/ios/.
#
# Needs macOS with Xcode + a simulator, and xcodegen on PATH. It resolves the simulator and the app's
# bundle id itself, so it keeps working if either changes. Overridable:
#   IOS_SIM_NAME (default "iPhone 17"), IOS_SIM_OS (default 26.2), IOS_BUNDLE_ID.
set -euo pipefail
cd "$(dirname "$0")/.."

SIM_NAME="${IOS_SIM_NAME:-iPhone 17}"
SIM_OS="${IOS_SIM_OS:-26.2}"
OUT="docs/screenshots/ios"

# The udid of a simulator: the named one (pinned to the OS if that device repeats), else the named
# one on any OS, else any available iPhone — so this runs on a fresh machine or a CI runner too.
UDID="$(xcrun simctl list -j devices available | SIM_NAME="$SIM_NAME" SIM_OS="$SIM_OS" python3 -c '
import json, os, sys
devs = json.load(sys.stdin)["devices"]
name, os_ = os.environ["SIM_NAME"], os.environ["SIM_OS"].replace(".", "-")
def pick(pred):
    return next((d["udid"] for rt, ds in devs.items() for d in ds if pred(rt, d)), "")
print(pick(lambda rt, d: d["name"] == name and os_ in rt)
      or pick(lambda rt, d: d["name"] == name)
      or pick(lambda rt, d: d["name"].startswith("iPhone")))
')"
[ -n "$UDID" ] || { echo "no iPhone simulator available" >&2; exit 1; }

( cd ios && xcodegen generate >/dev/null )
xcrun simctl boot "$UDID" 2>/dev/null || true

# TEST_RUNNER_ forwards GALLERY into the hosted test (prefix stripped); the generator writes the PNGs
# into the app container. The Swift Testing id needs the trailing () or zero tests run.
TEST_RUNNER_GALLERY=1 xcodebuild test \
  -project ios/Camper.xcodeproj -scheme Camper \
  -destination "id=$UDID" \
  -only-testing:'AppTests/DocsGallery/generate()' >/dev/null

# The app's bundle id, read from what the test run just installed (robust to a bundle-id change).
BUNDLE="${IOS_BUNDLE_ID:-}"
if [ -z "$BUNDLE" ]; then
  BUNDLE="$(xcrun simctl listapps "$UDID" | python3 -c '
import re, sys
ids = re.findall(r"CFBundleIdentifier\s*=\s*\"([^\"]+)\"", sys.stdin.read())
print(next((i for i in ids if i.endswith(".camper")), ""))
')"
fi
[ -n "$BUNDLE" ] || { echo "could not find the camper app on the simulator" >&2; exit 1; }

CONTAINER="$(xcrun simctl get_app_container "$UDID" "$BUNDLE" data)"
mkdir -p "$OUT"
cp "$CONTAINER/Documents/gallery/"*.png "$OUT/"
echo "Wrote $(find "$OUT" -name '*.png' | wc -l | tr -d ' ') screenshots to $OUT/ (from $BUNDLE on $UDID)"
