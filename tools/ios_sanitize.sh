#!/bin/sh
# Runs the Swift package tests under AddressSanitizer, then UndefinedBehaviorSanitizer (macOS).
# The UBSan leg links the runtime by hand: SwiftPM compiles the C targets with
# -fsanitize=undefined but does not link libclang_rt.ubsan_osx_dynamic for the test bundle.
set -eu
cd "$(dirname "$0")/.."
swift test --sanitize=address
lib="$(cd "$(dirname "$(xcrun --find clang)")/../lib/clang"/*/lib/darwin && pwd)"
swift test --sanitize=undefined -Xlinker "$lib/libclang_rt.ubsan_osx_dynamic.dylib" -Xlinker -rpath -Xlinker "$lib"
