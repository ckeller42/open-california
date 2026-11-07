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


# The per-unit hotspot (ap_identity.c): calictl-esp-XXXX and a xxxx-xxxx-xxxx password.
AP_SSID = CONSTS["NET_AP_SSID_PREFIX"] + "C0FE"
AP_PSK = "wwww-wwww-wwww"  # the widest letter
AP_SSID_HEX = "0123456789ABCDEF-"
AP_ALPHABET = "23456789abcdefghijkmnpqrstuvwxyz"


def display_texts() -> list[tuple[str, str, str]]:
    out = []
    for key, tr in STRINGS.items():
        if key.startswith("d_") or key == "device":
            for lang in ("en", "de"):
                out.append((key, lang, tr[lang].replace("%s", "")))
    for lang in ("en", "de"):  # the QR screen lines as display.c renders them
        out.append(("qr_wlan", lang, STRINGS["d_qr_wlan"][lang] % AP_SSID))
        out.append(("qr_pass", lang, STRINGS["d_qr_pass"][lang] % AP_PSK))
        out.append(
            ("ap_chars", lang, AP_SSID_HEX + AP_ALPHABET)
        )  # every character the hotspot identity can hold
        out.append(("url", lang, f"http://{CONSTS['NET_HOSTNAME']}.local"))
        out += [("batt_footer", lang, t) for t in battery_footers(lang)]
    out.append(("title", "-", "calictl satellite fw 0123456789abcdef-dirty"))
    return out


def battery_footers(lang: str) -> list[str]:
    """The longest station footers display_model.c builds (R_FW_BATTERY): USB charging, and on the
    battery with the widest estimate (100 % over 2 points in 10 min = 500 min)."""
    url = f"http://{CONSTS['NET_HOSTNAME']}.local"
    return [
        f"{url} · USB 100 % {STRINGS['d_charging'][lang]}",
        f"{url} · {STRINGS['d_batt'][lang]} 100 % ~8 h 20",
    ]


def test_display_key_set_includes_failures():
    keys = {k for k, _, _ in display_texts()}
    assert {"device", "d_fail_not_found", "d_fail_auth", "d_fail_other", "d_qr_join_title"} <= keys


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
        s["d_setup"] % (AP_SSID, CONSTS["NET_AP_ADDR"]),
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


@pytest.mark.parametrize("lang", ["en", "de"])
def test_battery_footer_fits_two_lines(lang):
    """The station footer with the battery wraps to at most two lines (the setup footer's room)."""
    adv = advance(16)
    for text in battery_footers(lang):
        assert lines(text, layout("W") - 2 * layout("PAD"), adv) <= 2, (lang, text)


@pytest.mark.parametrize("lang", ["en", "de"])
def test_qr_screen_values_fit_unbroken_and_the_text_fits_the_plate(lang):
    """The QR screen as display.c lays it out: the title + qr_lines[0] in the QR_TEXT_W box beside the
    plate (QR_BOX high, ellipsis on overflow), qr_lines[1] one full-width line below. Values the model
    puts on their own line (the SSID, the IP) must fit that box without a break; the password and the
    .local address must fit the screen width."""
    adv = advance(16)
    line = int(re.search(r"\.line_height = (\d+)", (FONT_DIR / "font_latin1_16.c").read_text()).group(1))
    qr_box = layout("QR_SIZE") + 2 * layout("QR_PLATE")
    box = layout("W") - (layout("QR_X") + qr_box + 4) - 4  # display.c QR_TEXT_W
    s = {k: v[lang] for k, v in STRINGS.items()}
    widest_hex = max("0123456789ABCDEF", key=lambda c: adv[ord(c)])
    widest_sym = max(AP_ALPHABET, key=lambda c: adv[ord(c)])
    ssid = CONSTS["NET_AP_SSID_PREFIX"] + widest_hex * 4
    psk = "-".join([widest_sym * 4] * 3)
    url = f"http://{CONSTS['NET_HOSTNAME']}.local"
    ssid_label = (s["d_qr_wlan"] % "").strip()
    cases = [  # (title, own-line values, label lines above them, foot)
        (s["d_qr_join_title"], [ssid], [ssid_label], s["d_qr_pass"] % psk),
        (s["d_qr_next_title"] % "Campingplatz", ["255.255.255.255"], ["http://"], url),
        # a 32-char SSID (the model's limit) of ordinary glyphs: one unbreakable word, wrapped by char;
        # the widest glyphs (32 x "m") need one more line and end in "..." (DOTS mode), asserted below
        (s["d_qr_next_title"] % ("e" * 32), ["255.255.255.255"], ["http://"], url),
    ]
    for title, values, labels, foot in cases:
        for v in values:
            assert width(v, adv) <= box, (lang, v, width(v, adv), box)
        assert width(foot, adv) <= layout("W"), (lang, foot, width(foot, adv))
        n = max(lines(title, box, adv), -(-width(title, adv) // box)) + len(labels) + len(values)
        assert n * line <= qr_box, (lang, title, n)
    assert layout("ROW_Y0") + qr_box + 2 + line <= 240  # the foot line is on the screen
    assert (
        "lv_obj_set_size(s_qr_text, QR_TEXT_W, QR_BOX)" in DISPLAY_C
        and "LV_LABEL_LONG_MODE_DOTS" in DISPLAY_C
    )
    assert layout("QR_PLATE") + 9 >= 16  # quiet zone: 4 modules at scale 4 (LVGL's own margin is ~9 px)
