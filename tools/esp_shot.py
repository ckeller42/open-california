"""Decode CoreS3 screen dumps from the serial console into PNGs (#154).

The firmware `screenshot` command (and kws-de's KWS_UI_SCREENSHOT build) prints:

    [SHOT <w> <h> RLE16 <nbytes>]
    <base64 of (uint16 count, uint16 RGB565 pixel) little-endian pairs>
    [/SHOT]

Usage: python -m tools.esp_shot decode <captured-serial.txt> <out_dir>
Writes out_dir/shot0.png, shot1.png, ... one per [SHOT] block. Stdlib only.
Ported from the flashing-cores3-on-bar skill's decode-shots.py.
"""

from __future__ import annotations

import base64
import pathlib
import re
import struct
import sys
import zlib
from typing import NamedTuple

BLOCK = re.compile(r"\[SHOT (\d+) (\d+) RLE16 (\d+)\](.*?)\[/SHOT\]", re.S)


class Frame(NamedTuple):
    w: int
    h: int
    rgb: bytes  # RGB888, w*h*3


def rle16_encode(rgb565: bytes) -> bytes:
    """Little-endian RGB565 pixels -> (u16 count, u16 pixel) LE pairs (runs capped at 65535)."""
    px = struct.unpack("<%dH" % (len(rgb565) // 2), rgb565)
    out = bytearray()
    i = 0
    while i < len(px):
        run = 1
        while i + run < len(px) and px[i + run] == px[i] and run < 65535:
            run += 1
        out += struct.pack("<HH", run, px[i])
        i += run
    return bytes(out)


def rle16_decode(data: bytes, w: int, h: int) -> bytes:
    """RLE16 pairs -> RGB888, padded/truncated to w*h pixels."""
    out = bytearray()
    for count, px in struct.iter_unpack("<HH", data[: len(data) // 4 * 4]):
        r, g, b = px >> 11, (px >> 5) & 0x3F, px & 0x1F
        # bit replication so full-scale 5/6-bit values reach 255 (the skill decoder's <<3 stops at 248)
        out += bytes((r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)) * count
    return bytes(out[: w * h * 3]).ljust(w * h * 3, b"\x00")


def parse_log(text: str) -> list[Frame]:
    """All complete frames in a console log. A truncated frame (no [/SHOT], or fewer payload bytes
    than the header's n) is skipped with a warning on stderr, never decoded into garbage."""
    frames = []
    pos = 0
    while m := BLOCK.search(text, pos):
        inner = m[4].rfind("[SHOT ")
        if inner >= 0:  # header without trailer, then a later frame: resume at the later header
            print("esp_shot: skipping truncated frame (no [/SHOT])", file=sys.stderr)
            pos = m.start(4) + inner
            continue
        pos = m.end()
        w, h, n = int(m[1]), int(m[2]), int(m[3])
        # per LINE: drop whole non-base64 lines (another task's LOG mid-frame), never single characters
        b64 = "".join(ln for ln in map(str.strip, m[4].splitlines()) if re.fullmatch(r"[A-Za-z0-9+/=]+", ln))
        try:
            data = base64.b64decode(b64, validate=True)
        except ValueError:
            data = b""
        if len(data) != n or n % 4:
            print(
                "esp_shot: skipping frame with bad payload (%d bytes, header says %d)" % (len(data), n),
                file=sys.stderr,
            )
            continue
        frames.append(Frame(w, h, rle16_decode(data, w, h)))
    return frames


def write_png(path: pathlib.Path, w: int, h: int, rgb: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + rgb[y * w * 3 : (y + 1) * w * 3] for y in range(h))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] != "decode":
        print(__doc__, file=sys.stderr)
        return 2
    text = pathlib.Path(argv[1]).read_bytes().decode("latin1")
    out = pathlib.Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    frames = parse_log(text)
    for i, f in enumerate(frames):
        p = out / f"shot{i}.png"
        write_png(p, f.w, f.h, f.rgb)
        print(p)
    if not frames:
        print("no complete [SHOT]...[/SHOT] frame found in the log", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
