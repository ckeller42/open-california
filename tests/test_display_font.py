"""Every status-display string is drawable with the generated Latin-1 fonts (#154).

LVGL's built-in Montserrat is ASCII-only; the German screen needs ä ö ü ß · …. The glyph set is read
from the generated font sources themselves (lv_font_conv writes one ``/* U+00E4 "ä" */`` comment per
glyph bitmap), so a glyph the TTF lacks fails here, not as a blank box on the screen.

.. test:: Status display fonts cover every display string (EN + DE, setup footer as rendered)
   :id: T_FW_DISPLAY_FONT
   :links: R_FW_STATUS_DISPLAY_MODEL
"""

import json
import re
from pathlib import Path

import pytest

from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "firmware/components/cali_display"
STRINGS = json.loads((ROOT / "firmware/web/strings.json").read_text(encoding="utf-8"))


def glyphs(size: int) -> set[int]:
    src = (FONT_DIR / f"font_latin1_{size}.c").read_text(encoding="utf-8")
    assert f"font_latin1_{size}" in src
    return {int(h, 16) for h in re.findall(r"/\* U\+([0-9A-F]{4,5}) ", src)}


def display_texts() -> list[tuple[str, str, str]]:
    out = []
    for key, tr in STRINGS.items():
        if key.startswith("d_") or key == "device":
            for lang in ("en", "de"):
                out.append((key, lang, tr[lang].replace("%s", "")))
    for lang in ("en", "de"):  # the setup footer as display.c renders it
        out.append(
            ("footer", lang, STRINGS["d_footer_setup"][lang] % (CONSTS["NET_AP_SSID"], CONSTS["NET_AP_PSK"]))
        )
        out.append(("url", lang, f"http://{CONSTS['NET_HOSTNAME']}.local"))
    out.append(("title", "-", "calictl satellite fw 0123456789abcdef-dirty"))
    return out


def test_display_key_set_includes_failures():
    keys = {k for k, _, _ in display_texts()}
    assert {"device", "d_fail_not_found", "d_fail_auth", "d_fail_other", "d_footer_setup"} <= keys


@pytest.mark.parametrize("size", [16, 24])
def test_every_display_string_is_covered(size):
    have = glyphs(size)
    assert 0xE4 in have and 0x2026 in have  # ä and … actually generated
    for key, lang, text in display_texts():
        bad = [c for c in text if ord(c) not in have]
        assert not bad, (size, key, lang, bad)
