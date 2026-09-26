#!/usr/bin/env bash
# Fetch upstream apache/mynewt-nimble at the pinned tag into firmware/host/_deps/nimble, plus the
# Mbed TLS it links against (built 32-bit by the Makefile) into firmware/host/_deps/mbedtls.
# Pin rationale (ESP-IDF v6.1 vendors esp-nimble based on NimBLE 1.6.0, but upstream's TCP socket
# transport is only complete from 1.10.0): see firmware/README.md "Host build notes".
set -euo pipefail
PIN="${NIMBLE_PIN:-nimble_1_10_0_tag}"
MBEDTLS_PIN="${MBEDTLS_PIN:-mbedtls-3.6.5}"
DEPS="$(cd "$(dirname "$0")" && pwd)/_deps"

fetch() {  # fetch <dest> <tag> <url> [extra clone args...]
    local dest="$1" tag="$2" url="$3"; shift 3
    if [ -d "$dest/.git" ] && [ "$(git -C "$dest" describe --tags --exact-match --match "$tag" 2>/dev/null)" = "$tag" ]; then
        return 0
    fi
    rm -rf "$dest"; mkdir -p "$(dirname "$dest")"
    git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" "$@" "$url" "$dest"
}

fetch "$DEPS/nimble" "$PIN" https://github.com/apache/mynewt-nimble.git
fetch "$DEPS/mbedtls" "$MBEDTLS_PIN" https://github.com/Mbed-TLS/mbedtls.git --recurse-submodules --shallow-submodules
