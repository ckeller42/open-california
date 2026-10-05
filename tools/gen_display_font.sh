#!/usr/bin/env bash
# Regenerates the status display's Latin-1 fonts (ASCII + U+00A0-U+00FF: ä ö ü ß · …) (#154).
# LVGL's built-in Montserrat is ASCII-only; the German screen needs these glyphs.
# Run from the repo root after one IDF build (it fetches LVGL into managed_components/):
#   tools/gen_display_font.sh firmware/managed_components/lvgl__lvgl/scripts/generators/built_in_font/Montserrat-Medium.ttf
# (LVGL 9.6 ships Montserrat-Medium.ttf there, the face its built-in fonts use; there is no -Regular.)
# Montserrat is SIL OFL 1.1: firmware/components/cali_display/FONTS-LICENSE carries the licence.
# tests/test_display_font.py checks the generated glyphs cover every display string.
set -euo pipefail
TTF="${1:?usage: gen_display_font.sh <font.ttf>}"
OUT=firmware/components/cali_display
for size in 16 24; do
  npx --yes lv_font_conv@1.5.3 --font "$TTF" --size $size --bpp 4 --format lvgl \
      --range 0x20-0x7E,0xA0-0xFF --range 0x2026 --no-compress \
      --lv-font-name font_latin1_$size -o "$OUT/font_latin1_$size.c"
  f="$OUT/font_latin1_$size.c"
  # licence notice on top (OFL 1.1 travels with the font) + one final newline (end-of-file-fixer)
  printf '%s\n%s\n' "/* Montserrat, Copyright 2011 The Montserrat Project Authors, SIL Open Font License 1.1: see FONTS-LICENSE. */" \
      "$(cat "$f")" > "$f"
done
