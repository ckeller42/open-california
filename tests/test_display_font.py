"""Every status-display string is drawable with the generated Latin-1 fonts (#154).

LVGL's built-in Montserrat is ASCII-only; the German screen needs ä ö ü ß · …. The glyph set is read
from the generated font sources themselves (lv_font_conv writes one ``/* U+00E4 "ä" */`` comment per
glyph bitmap), so a glyph the TTF lacks fails here, not as a blank box on the screen. The glyph
advances (``adv_w``) and ``display.c``'s layout defines also prove every spec row text fits its
two-line value box and every row label fits its column, so a long text can't be ellipsized unseen.

.. test:: Status display fonts cover every display string (EN + DE, setup footer as rendered) and the texts fit the layout
   :id: T_FW_DISPLAY_FONT
   :links: R_FW_STATUS_DISPLAY
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


# --- layout: the spec texts fit the value box (R9) --------------------------------------------------
DISPLAY_C = (FONT_DIR / "display.c").read_text(encoding="utf-8")


def layout(name: str) -> int:
    return int(re.search(rf"^#define {name} (\d+)\b", DISPLAY_C, re.M).group(1))


def advance(size: int) -> dict[int, int]:
    """Codepoint -> advance in px (adv_w is 1/16 px; LVGL rounds per glyph; kerning ignored,
    Montserrat's pairs mostly tighten, so this errs wide)."""
    src = (FONT_DIR / f"font_latin1_{size}.c").read_text(encoding="utf-8")
    adv = [int(a) for a in re.findall(r"\.adv_w = (\d+)", src)]
    out = {}
    for start, length, gid in re.findall(
        r"\.range_start = (\d+), \.range_length = (\d+), \.glyph_id_start = (\d+)", src
    ):
        for i in range(int(length)):
            out[int(start) + i] = (adv[int(gid) + i] + 8) >> 4
    return out


def width(text: str, adv: dict[int, int]) -> int:
    return sum(adv[ord(c)] for c in text)


def lines(text: str, box: int, adv: dict[int, int]) -> int:
    """Greedy word wrap at spaces only (LVGL may also break at , . - so this errs high)."""
    n, cur = 1, ""
    for word in text.split(" "):
        trial = (cur + " " + word) if cur else word
        if cur and width(trial, adv) > box:
            n, cur = n + 1, word
        else:
            cur = trial
    return n


def spec_row_texts(lang: str) -> list[str]:
    """Each spec row state with a representative value (12-char SSID, a long LAN IP, -100 dBm)."""
    s = {k: v[lang] for k, v in STRINGS.items()}
    ssid = "Campingplatz"
    texts = [
        s["d_running"] % "23 h 59 min",
        s["d_setup"] % (CONSTS["NET_AP_SSID"], CONSTS["NET_AP_ADDR"]),
        f"{ssid} · 192.168.178.186 · -100 dBm",
        s["d_joining"] % ssid,
        s["d_retrying"] % ssid,
        s["d_connected"] % "10 s",
        s["d_stale"] % "3600 s",
    ]
    texts += [
        s[k]
        for k in ("d_connecting", "d_pairing", "d_passkey", "d_link_lost", "d_pair_error", "d_not_paired")
    ]
    texts += [s["d_wifi_off"] + " · " + s[k] for k in ("d_fail_not_found", "d_fail_auth", "d_fail_other")]
    return texts


@pytest.mark.parametrize("lang", ["en", "de"])
def test_spec_texts_fit_two_lines_and_labels_fit_their_column(lang):
    adv = advance(16)
    box = layout("W") - layout("PAD") - layout("VALUE_X")
    for text in spec_row_texts(lang):
        assert lines(text, box, adv) <= 2, (lang, text, width(text, adv), box)
    for key in ("device", "d_wifi", "d_camper"):
        assert width(STRINGS[key][lang], adv) <= layout("VALUE_X") - layout("LABEL_X") - 6, (lang, key)


def test_value_box_is_two_lines_and_the_title_clears_the_fw_label():
    line = int(re.search(r"\.line_height = (\d+)", (FONT_DIR / "font_latin1_16.c").read_text()).group(1))
    assert "lv_obj_set_size(r->value, W - PAD - VALUE_X, 2 * line)" in DISPLAY_C
    assert layout("ROW_H") >= 2 * line
    assert layout("PAD") + width("calictl satellite", advance(24)) < layout("TITLE_END_X")
