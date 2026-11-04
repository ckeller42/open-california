"""The per-unit setup hotspot: MAC-suffixed name, random stored password (``cali_ap_identity``).

Driven through ``firmware/components/cali_core/test/ap_identity_cli.c`` on the host platform
(``CALI_HOST_MAC`` = the fake MAC, ``/dev/urandom``, a file-backed kv store).

.. test:: The setup hotspot name carries the MAC suffix and its password is random, valid and kept
   :id: T_FW_AP_IDENTITY
   :links: R_FW_WIFI_PROVISION
"""

import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/components/cali_core"
PLATFORM = ROOT / "firmware/components/platform"

PSK = re.compile(r"^[2-9a-km-np-z]{4}-[2-9a-km-np-z]{4}-[2-9a-km-np-z]{4}$")


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("apid") / "ap_identity_cli"
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            "-I",
            str(PLATFORM / "include"),
            "-I",
            str(ROOT / "csrc"),
            str(CORE / "ap_identity.c"),
            str(CORE / "test/ap_identity_cli.c"),
            str(PLATFORM / "host/platform_host.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def run(cli, kv_dir, env=None):
    e = {k: v for k, v in os.environ.items() if k != "CALI_HOST_MAC"}
    e.update(env or {})
    r = subprocess.run([str(cli), str(kv_dir)], capture_output=True, text=True, env=e, check=True)
    return dict(f.split("=", 1) for f in r.stdout.split())


def write_kv(kv_dir, key, value):
    """A host kv record by hand: LE32 length, bytes, LE32 CRC32 over both (test_platform_kv.py)."""
    kv_dir.mkdir(parents=True, exist_ok=True)
    body = struct.pack("<I", len(value)) + value
    (kv_dir / (key + ".kv")).write_bytes(body + struct.pack("<I", zlib.crc32(body)))


def test_ssid_carries_the_mac_suffix(cli, tmp_path):
    assert run(cli, tmp_path, env={"CALI_HOST_MAC": "020000001A2B"})["ssid"] == "calictl-esp-1A2B"
    assert run(cli, tmp_path)["ssid"] == "calictl-esp-C0FE"  # the host fake MAC 02:00:00:00:C0:FE


def test_password_is_random_valid_and_stable(cli, tmp_path):
    a = run(cli, tmp_path)
    b = run(cli, tmp_path)
    assert PSK.match(a["psk"]) and a["psk"] == b["psk"]  # survives a restart
    assert run(cli, tmp_path / "other")["psk"] != a["psk"]  # another unit, another password


def test_the_password_does_not_depend_on_the_mac(cli, tmp_path):
    a = run(cli, tmp_path, env={"CALI_HOST_MAC": "020000001A2B"})
    assert run(cli, tmp_path, env={"CALI_HOST_MAC": "020000001A2C"})["psk"] == a["psk"]


@pytest.mark.parametrize(
    "bad", [b"abc", b"2222-2222-2222-", b"2222-2222-222l", b"2222_2222_2222", b"2222-2222-2222x", b""]
)
def test_a_corrupt_password_is_replaced(cli, tmp_path, bad):
    write_kv(tmp_path, "ap_psk", bad)  # short (power cut mid-write), or not of the shape
    p = run(cli, tmp_path)["psk"]
    assert PSK.match(p)
    assert run(cli, tmp_path)["psk"] == p  # and the new one is kept


def test_a_valid_stored_password_is_kept(cli, tmp_path):
    write_kv(tmp_path, "ap_psk", b"2345-6789-abcd")
    assert run(cli, tmp_path)["psk"] == "2345-6789-abcd"


def test_wifi_qr_text(cli, tmp_path):
    r = run(cli, tmp_path, env={"CALI_HOST_MAC": "020000001A2B"})
    assert r["qr"] == "WIFI:T:WPA;S:calictl-esp-1A2B;P:%s;;" % r["psk"]
