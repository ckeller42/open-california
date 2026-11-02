#!/usr/bin/env bash
# The iPhone bench lab: the iOS app on a real iPhone, driven by its lab UI tests, against the fake
# camper unit on a Linux host's BLE adapter (docs/ios-development-setup.md, "The bench lab").
#
# Run it on the Mac the iPhone is connected to, in that Mac's desktop session (Terminal): code
# signing needs the login keychain, which an SSH session cannot open. From another machine:
#   ssh <mac> 'open -a Terminal <a .command file that calls this script>'
#
# Environment:
#   IOSLAB_UDID      the iPhone's device id (xcrun devicectl list devices) — required, never committed
#   IOSLAB_XCCONFIG  your signing config to copy to ios/Config/Local.xcconfig (default: keep the one there)
#   IOSLAB_DIR       results, step marks, logs (default ~/ios-lab)
#   IOSLAB_DERIVED   DerivedData (default ~/ios-lab-dd)
#
# Commands:
#   install                  build the app and install it on the iPhone
#   build                    build the app and its UI tests once (for run / tour)
#   run <action>…            one LabControlUITests action per test run (see that file for the list;
#                            plus wait.<seconds>). Each control action first gets a "step" event in
#                            $IOSLAB_DIR/steps.jsonl for tools/capture_diff.py. The iPhone's Auto-Lock
#                            is read first, set to Never and restored at the end (also after a failure
#                            or STOP); IOSLAB_KEEP_AUTOLOCK=1 skips that. A file $IOSLAB_DIR/STOP ends the
#                            run before the next action.
#   tour <variant>…          LabTourUITests screenshots per variant (light, dark, de, ax5), exported to
#                            $IOSLAB_DIR/tour/<variant>/
#   pair <passkey>           LabPairingUITests: pair with the fake unit through the guide
set -euo pipefail
: "${IOSLAB_UDID:?set IOSLAB_UDID to the iPhone device id}"
DIR="${IOSLAB_DIR:-$HOME/ios-lab}"
DD="${IOSLAB_DERIVED:-$HOME/ios-lab-dd}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="/opt/local/bin:$PATH"
cd "$REPO"
mkdir -p "$DIR/res" "$DIR/tour"
DEST=(-destination "id=$IOSLAB_UDID")
PROJ=(-project ios/Camper.xcodeproj -scheme Camper)

prepare() {
    [[ -n "${IOSLAB_XCCONFIG:-}" ]] && cp "$IOSLAB_XCCONFIG" ios/Config/Local.xcconfig
    xcodegen generate --spec ios/project.yml >/dev/null
}

# act <action>: one LabControlUITests run; prints its result on one line.
act() {
    rm -rf "$DIR/res/$1.xcresult"
    TEST_RUNNER_LAB_ACTION="$1" xcodebuild test-without-building "${PROJ[@]}" "${DEST[@]}" -derivedDataPath "$DD" \
        -only-testing:AppUITests/LabControlUITests/testLabAction -resultBundlePath "$DIR/res/$1.xcresult" 2>&1 \
        | grep -E "LAB_AUTOLOCK=|error:|skipped -|Test Case .*(passed|failed|skipped)" \
        | sed -E "s/^.*(error|testLabAction\]) ?:? ?//" | head -4 | tr "\n" ";" || true
}

run() {
    touch "$DIR/steps.jsonl"
    local n saved="" r
    n=$(($(wc -l <"$DIR/steps.jsonl") + 1))
    if [[ -z "${IOSLAB_KEEP_AUTOLOCK:-}" ]]; then
        r=$(act device.autolock.read)
        saved=$(sed -nE "s/.*LAB_AUTOLOCK=([^;]*).*/\1/p" <<<"$r" | tr -d "\r" | sed -E "s/[[:space:]]+$//")
        echo "autolock was: ${saved:-unknown}"
        [[ -n "$saved" ]] && printf '%s\n' "$saved" >"$DIR/autolock.saved"
        if [[ -n "$saved" && "$saved" != Never && "$saved" != Nie ]]; then
            echo "device.autolock.Never | $(act device.autolock.Never)"
            # shellcheck disable=SC2064  # the value is fixed now, on purpose
            trap "echo \"device.autolock.$saved | \$(act 'device.autolock.$saved')\"" EXIT
        fi
    fi
    local a fn what val v
    for a in "$@"; do
        [[ -e "$DIR/STOP" ]] && { echo "stopped before $a"; exit 1; }
        IFS=. read -r fn what val <<<"$a"
        if [[ "$fn" == wait ]]; then sleep "$what"; echo "$a | waited"; continue; fi
        if [[ "$fn" != link && "$fn" != device && "$fn" != setup ]]; then
            if [[ "$val" =~ ^[0-9]+$ ]]; then v=$val; else v="\"$val\""; fi
            python3 -c "import json,time;print(json.dumps({'t':time.time(),'ev':'step','n':$n,'verb':'ios','arg':['$a'],'expect':['$fn','$what',$v]}))" >>"$DIR/steps.jsonl"
            n=$((n + 1))
        fi
        echo "$a | $(act "$a")"
    done
    echo RUN_FINISHED
}

case "${1:-}" in
    install)
        prepare
        xcodebuild build "${PROJ[@]}" "${DEST[@]}" -derivedDataPath "$DD" -allowProvisioningUpdates -quiet
        xcrun devicectl device install app --device "$IOSLAB_UDID" "$DD/Build/Products/Debug-iphoneos/Camper.app"
        echo "installed $(git log --oneline -1)"
        ;;
    build)
        prepare
        xcodebuild build-for-testing "${PROJ[@]}" "${DEST[@]}" -derivedDataPath "$DD" -allowProvisioningUpdates -quiet
        echo built
        ;;
    run) shift; run "$@" ;;
    tour)
        shift
        for t in "$@"; do
            rm -rf "$DIR/tour/$t.xcresult" "$DIR/tour/$t"
            r=$(TEST_RUNNER_LAB_TOUR="$t" xcodebuild test-without-building "${PROJ[@]}" "${DEST[@]}" -derivedDataPath "$DD" \
                -only-testing:AppUITests/LabTourUITests/testTour -resultBundlePath "$DIR/tour/$t.xcresult" 2>&1 \
                | grep -E "error:|Test Case .*(passed|failed)" | head -3 | tr "\n" ";" || true)
            xcrun xcresulttool export attachments --path "$DIR/tour/$t.xcresult" --output-path "$DIR/tour/$t" >/dev/null 2>&1 || true
            echo "$t | $r"
        done
        ;;
    pair)
        prepare
        TEST_RUNNER_LAB_PASSKEY="${2:?passkey}" xcodebuild test "${PROJ[@]}" "${DEST[@]}" -derivedDataPath "$DD" \
            -allowProvisioningUpdates -only-testing:AppUITests/LabPairingUITests -resultBundlePath "$DIR/pair.xcresult"
        ;;
    *) sed -n '2,27p' "$0"; exit 2 ;;
esac
