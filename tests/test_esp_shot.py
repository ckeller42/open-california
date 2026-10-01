"""tools/esp_shot.py round-trips kws-de's [SHOT … RLE16] frames (#154 display verification)."""

import base64
import struct

from tools import esp_shot


def test_rle16_round_trip():
    w, h = 4, 2
    px = [0xF800] * 5 + [0x07E0] * 3  # red x5, green x3
    raw = b"".join(struct.pack("<H", p) for p in px)
    rgb = esp_shot.rle16_decode(esp_shot.rle16_encode(raw), w, h)
    assert rgb[:3] == bytes((255, 0, 0)) and rgb[-3:] == bytes((0, 255, 0)) and len(rgb) == w * h * 3


def test_parse_log_finds_frames(tmp_path):
    payload = base64.b64encode(esp_shot.rle16_encode(struct.pack("<H", 0x001F) * 4)).decode()
    log = "noise\n[SHOT 2 2 RLE16 %d]\n%s\n[/SHOT]\n" % (len(payload), payload)
    frames = esp_shot.parse_log(log)
    assert [(f.w, f.h) for f in frames] == [(2, 2)]
