#!/bin/sh
# Runs the Swift package tests under AddressSanitizer, then UndefinedBehaviorSanitizer (macOS).
# The UBSan leg links the runtime by hand: SwiftPM compiles the C targets with
# -fsanitize=undefined but does not link libclang_rt.ubsan_osx_dynamic for the test bundle.
# Each leg builds first, then runs the tests verbosely (one line per test start) under a watchdog:
# after SANITIZE_LIMIT seconds (default 600) it writes a stack sample of every xctest process to
# SANITIZE_SAMPLES (default the working directory) and stops the leg, so a hang shows where it is.
set -eu
cd "$(dirname "$0")/.."
limit="${SANITIZE_LIMIT:-600}"
samples="${SANITIZE_SAMPLES:-.}"

leg() {
    swift build --build-tests "$@"
    swift test --skip-build --verbose "$@" &
    pid=$!
    (
        sleep "$limit"
        echo "ios_sanitize: no result after ${limit}s; sampling xctest" >&2
        for p in $(pgrep xctest); do sample "$p" 5 -file "$samples/sanitize-sample-$p.txt" >/dev/null 2>&1; done
        kill "$pid"
    ) &
    dog=$!
    rc=0
    wait "$pid" || rc=$?
    kill "$dog" 2>/dev/null || true
    wait "$dog" 2>/dev/null || true
    return "$rc"
}

leg --sanitize=address
lib="$(cd "$(dirname "$(xcrun --find clang)")/../lib/clang"/*/lib/darwin && pwd)"
leg --sanitize=undefined -Xlinker "$lib/libclang_rt.ubsan_osx_dynamic.dylib" -Xlinker -rpath -Xlinker "$lib"
