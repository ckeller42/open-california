#!/usr/bin/env bash
# Android APK decompile pipeline for Volkswagen CaliforniaOnTour app
#
# This script automates the decompilation of the Volkswagen CaliforniaOnTour
# Android app to extract the protocol dictionary for vehicle interoperability.
#
# LEGAL NOTE: The APK is Volkswagen-copyrighted. Do not commit to version
# control. This script is for own-vehicle interoperability research only.
#
# Pipeline:
#   1. Fetch APK using apkeep (EFForg/apkeep)
#   2. Extract DEX files using unzip
#   3. Decompile DEX files using jadx (skylot/jadx, requires Java 17+)
#   4. Output ready for tools/extract_protocol.py
#
# Usage: ./decompile.sh [PACKAGE_ID] [WORK_DIR] [OUTPUT_DIR]
#
# Defaults:
#   PACKAGE_ID    de.volkswagen.CaliforniaOnTour
#   WORK_DIR      temporary directory (mktemp)
#   OUTPUT_DIR    WORK_DIR
#

set -euo pipefail

# --- Configuration ---
PACKAGE_ID="${1:-de.volkswagen.CaliforniaOnTour}"
WORK_DIR="${2:-$(mktemp -d)}"
OUTPUT_DIR="${3:-${WORK_DIR}}"
APK_FILE="${WORK_DIR}/${PACKAGE_ID}.apk"
DEX_DIR="${WORK_DIR}/dex"

die() { echo "ERROR: $*" >&2; exit 1; }
check_tool() { command -v "$1" &>/dev/null; }

# --- Check dependencies ---
echo "Checking dependencies..."
MISSING=()

check_tool "unzip" || MISSING+=("unzip (included in coreutils)")
check_tool "jadx" || MISSING+=("jadx: github.com/skylot/jadx (requires Java 17+)")

if [[ ! -f "$APK_FILE" ]]; then
  check_tool "apkeep" || MISSING+=("apkeep: github.com/EFForg/apkeep")
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "Missing tools:" >&2
  printf '  - %s\n' "${MISSING[@]}" >&2
  die "Install above and retry"
fi

# --- Prepare directories ---
mkdir -p "$WORK_DIR" "$DEX_DIR" "$OUTPUT_DIR"
echo "Work directory: $WORK_DIR"

# --- Step 1: Fetch APK ---
if [[ -f "$APK_FILE" ]]; then
  echo "Step 1: APK exists, skipping fetch ($APK_FILE)"
else
  echo "Step 1: Fetching APK with apkeep..."
  TMP_APK="${WORK_DIR}/tmp-apk"
  mkdir -p "$TMP_APK"
  apkeep -a "$PACKAGE_ID" -d apk-pure "$TMP_APK" || die "apkeep failed"
  [[ -f "${TMP_APK}/${PACKAGE_ID}.apk" ]] || die "APK not found in apkeep output"
  mv "${TMP_APK}/${PACKAGE_ID}.apk" "$APK_FILE"
  rm -rf "$TMP_APK"
fi

# --- Step 2: Extract DEX ---
echo "Step 2: Extracting DEX files..."
unzip -o "$APK_FILE" "classes*.dex" -d "$DEX_DIR" || die "unzip failed"

# --- Step 3: Decompile ---
echo "Step 3: Decompiling with jadx..."
jadx -d "$OUTPUT_DIR" "$DEX_DIR"/classes.dex "$DEX_DIR"/classes2.dex --no-res || die "jadx failed"

# --- Done ---
SOURCES="${OUTPUT_DIR}/sources"
echo ""
echo "Decompilation complete! Sources in: $SOURCES"
echo ""
echo "Next: extract protocol dictionary"
echo "  tools/extract_protocol.py '$SOURCES' protocol/dictionary.yaml"
