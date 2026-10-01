"""tools/esp_shot.py decodes kws-de's [SHOT ... RLE16] frames (#154 display verification)."""

import base64
import struct

from tools import esp_shot


def shot(w, h, raw565, lines_between=()):
    """A frame as the firmware prints it: header, base64 in lines, trailer."""
    data = esp_shot.rle16_encode(raw565)
    b64 = base64.b64encode(data).decode()
    lines = [b64[i : i + 76] for i in range(0, len(b64), 76)]
    lines[1:1] = list(lines_between)
    return "[SHOT %d %d RLE16 %d]\n%s\n[/SHOT]\n" % (w, h, len(data), "\n".join(lines))


def px(*vals):
    return b"".join(struct.pack("<H", v) for v in vals)


def test_rle16_round_trip():
    w, h = 4, 2
    raw = px(*([0xF800] * 5 + [0x07E0] * 3))  # red x5, green x3
    rgb = esp_shot.rle16_decode(esp_shot.rle16_encode(raw), w, h)
    assert rgb[:3] == bytes((255, 0, 0)) and rgb[-3:] == bytes((0, 255, 0)) and len(rgb) == w * h * 3


def test_c_side_vector_pins_byte_order():
    # exactly what shot.c emits: count LE (03 00), RGB565 LE (0xF800 -> 00 f8), then count 1, 0x001F -> 1f 00
    wire = bytes.fromhex("030000f801001f00")
    assert esp_shot.rle16_decode(wire, 2, 2) == bytes((255, 0, 0)) * 3 + bytes((0, 0, 255))


def test_parse_log_two_frames_interleaved_with_console_lines():
    log = (
        'STATE {"state":"idle"}\nLOG boot ok\n'
        + shot(2, 2, px(0x001F) * 4)
        + 'LOG display: brightness 80\nSTATE {"state":"bonded"}\n'
        + shot(2, 1, px(0xF800, 0x07E0))
        + "LOG tail\n"
    )
    frames = esp_shot.parse_log(log)
    assert [(f.w, f.h) for f in frames] == [(2, 2), (2, 1)]
    assert frames[0].rgb == bytes((0, 0, 255)) * 4
    assert frames[1].rgb == bytes((255, 0, 0, 0, 255, 0))


def test_log_line_inside_a_frame_does_not_corrupt_it():
    raw = px(*(list(range(0x1000, 0x1000 + 200))))  # no runs -> payload spans several base64 lines
    log = shot(20, 10, raw, lines_between=["LOG net: wifi sta got ip", "STATE {}"])
    (f,) = esp_shot.parse_log(log)
    assert f.rgb == esp_shot.rle16_decode(esp_shot.rle16_encode(raw), 20, 10)


def test_truncated_frames_are_skipped(capsys):
    good = shot(2, 1, px(0xF800, 0x07E0))
    no_trailer = "[SHOT 2 2 RLE16 4]\nAAAA\nLOG x\n"  # then a later complete frame
    short = "[SHOT 2 2 RLE16 400]\nAwAA+A==\n[/SHOT]\n"  # payload shorter than the header says
    frames = esp_shot.parse_log(no_trailer + good + short + "[SHOT 2 2 RLE16 4]\nAwA")
    assert [(f.w, f.h) for f in frames] == [(2, 1)]
    assert frames[0].rgb == bytes((255, 0, 0, 0, 255, 0))
    assert "skipping" in capsys.readouterr().err


def test_cli_writes_pngs(tmp_path):
    log = tmp_path / "serial.log"
    log.write_text("LOG a\n" + shot(3, 2, px(0xF800) * 6) + "LOG b\n" + shot(2, 2, px(0x001F) * 4))
    out = tmp_path / "out"
    assert esp_shot.main(["decode", str(log), str(out)]) == 0
    for name, (w, h) in (("shot0.png", (3, 2)), ("shot1.png", (2, 2))):
        data = (out / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", data[16:24]) == (w, h)  # IHDR width, height


def test_cli_no_frame_fails(tmp_path):
    log = tmp_path / "serial.log"
    log.write_text("just noise\n")
    assert esp_shot.main(["decode", str(log), str(tmp_path / "o")]) == 1
