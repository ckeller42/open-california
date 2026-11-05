"""The ESP's AES-128-GCM open (``lora_aead.c``, PSA) against the vector the JS sealer made."""

import hashlib
import json
import subprocess

import pytest

from .conftest import HOST_DIR, build_host

pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

VEC = json.loads((HOST_DIR.parents[1] / "tests/firmware/data/lora_xfer_vector.json").read_text())


def _xfer(frags):
    """Feed ``frags`` to lora_aead_cli's ``xfer``; return the (rc, confirm, plain) of the last line."""
    lines = [f"xfer {VEC['tk']} {VEC['deveui']} {f}" for f in frags]
    out = subprocess.run(
        [str(build_host("lora_aead_cli"))], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return out[-1].split()


def test_psa_opens_js_vector():
    """Real PSA GCM opens the WebCrypto-sealed vector; one flipped tag byte is status 1.

    .. test:: AES-GCM on the ESP (PSA) opens the JS-sealed key transfer
       :id: T_FW_LORA_AEAD
       :links: R_FW_LORA_SETUP
    """
    rc, conf, plain = _xfer(VEC["fragments"])
    want = "00" + hashlib.sha256(bytes.fromhex(VEC["plain"])).digest()[:4].hex()
    assert (rc, conf, plain) == ("1", want, VEC["plain"])
    bad = list(VEC["fragments"])
    last = bytearray(bytes.fromhex(bad[-1]))
    last[-1] ^= 1
    bad[-1] = last.hex()
    rc, conf, plain = _xfer(bad)
    assert (rc, conf[:2], plain) == ("1", "01", "-")
