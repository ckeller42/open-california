"""The firmware's host platform layer: a file-backed, CRC-checked key-value store (#154).

``firmware/components/platform/host/platform_host.c`` stores one file per key under the store
directory — a 4-byte little-endian length, the value bytes, then a 4-byte little-endian CRC32
(reflected polynomial 0xEDB88320) over length + bytes. A short or CRC-mismatched file reads back as
corrupt (-2), which the NimBLE bond store (``ble_store_kv.c``) treats as "no record", so a damaged
store boots unpaired instead of crashing or using half a key. Driven through the line-protocol CLI
``firmware/components/platform/test/kv_cli.c``, compiled with the host ``cc`` (runs on macOS too).
"""
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / "firmware" / "components" / "platform"


@pytest.fixture(scope="module")
def kv_cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("kv") / "kv_cli"
    subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(PLATFORM / "include"),
                    str(PLATFORM / "host" / "platform_host.c"), str(PLATFORM / "test" / "kv_cli.c"),
                    "-o", str(out)], check=True)
    return out


def run(kv_cli, store, script):
    """Run one kv_cli process on ``store`` with ``script`` on stdin; return its stdout lines."""
    r = subprocess.run([str(kv_cli), str(store)], input=script, capture_output=True, text=True,
                       timeout=30, check=True)
    return r.stdout.splitlines()


def _file(store, key):
    return next(store.glob(key + "*"))


def test_kv_roundtrip_and_persistence(kv_cli, tmp_path):
    assert run(kv_cli, tmp_path, "set a 0102ff\nget a\n") == ["OK", "0102ff"]
    assert run(kv_cli, tmp_path, "get a\n") == ["0102ff"]           # survives a new process


def test_corrupt_store_boots_unpaired(kv_cli, tmp_path):
    run(kv_cli, tmp_path, "set sec_peer_0 00112233\n")
    f = _file(tmp_path, "sec_peer_0")
    f.write_bytes(f.read_bytes()[:-3])   # truncate
    assert run(kv_cli, tmp_path, "get sec_peer_0\n") == ["CORRUPT"]


def test_file_format_is_le_length_bytes_crc32(kv_cli, tmp_path):
    run(kv_cli, tmp_path, "set k deadbeef01\n")
    raw = _file(tmp_path, "k").read_bytes()
    body = struct.pack("<I", 5) + bytes.fromhex("deadbeef01")
    assert raw == body + struct.pack("<I", zlib.crc32(body))   # zlib.crc32 = reflected 0xEDB88320


def test_a_flipped_bit_is_corrupt(kv_cli, tmp_path):
    run(kv_cli, tmp_path, "set k 00112233\n")
    f = _file(tmp_path, "k")
    raw = bytearray(f.read_bytes())
    raw[5] ^= 0x01
    f.write_bytes(bytes(raw))
    assert run(kv_cli, tmp_path, "get k\n") == ["CORRUPT"]


def test_a_length_prefix_lying_about_the_size_is_corrupt(kv_cli, tmp_path):
    run(kv_cli, tmp_path, "set k 00112233\n")
    f = _file(tmp_path, "k")
    raw = f.read_bytes()
    f.write_bytes(struct.pack("<I", 0xFFFFFFF0) + raw[4:])
    assert run(kv_cli, tmp_path, "get k\n") == ["CORRUPT"]


def test_missing_key_overwrite_empty_value_and_erase(kv_cli, tmp_path):
    assert run(kv_cli, tmp_path, "get nope\n") == ["MISSING"]
    assert run(kv_cli, tmp_path, "set a 01\nset a 0203\nget a\n") == ["OK", "OK", "0203"]
    assert run(kv_cli, tmp_path, "set e -\nget e\n") == ["OK", "-"]   # zero-length value
    assert run(kv_cli, tmp_path, "erase\nget a\nget e\n") == ["OK", "MISSING", "MISSING"]
    assert run(kv_cli, tmp_path, "set b 99\nget b\n") == ["OK", "99"]  # usable after an erase


def test_a_value_larger_than_the_buffer_is_too_big(kv_cli, tmp_path):
    # kv_cli reads into a 256-byte buffer; a 300-byte record returns -2 (too big), not a truncation.
    assert run(kv_cli, tmp_path, "set big %s\nget big\n" % ("ab" * 300)) == ["OK", "CORRUPT"]
