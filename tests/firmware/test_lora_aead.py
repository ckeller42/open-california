"""The ESP's AES-128-GCM open (``lora_aead.c``, PSA) against the vector the JS sealer made."""

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from .conftest import HOST_DIR, build_host

pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

VEC = json.loads((HOST_DIR.parents[1] / "tests/firmware/data/lora_xfer_vector.json").read_text())


def _xfer(frags):
    """Feed ``frags`` to lora_aead_cli's ``xfer``; return the (rc, confirm, plain) of the last line."""
    lines = [f"xfer {VEC['tk']} {VEC['deveui']} {f}" for f in frags]
    out = subprocess.run(
        [str(build_host("lora_aead_cli"))],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=True,
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


KEY, NONCE, AD, PT = "000102030405060708090a0b0c0d0e0f", "a0a1a2a3a4a5a6a7a8a9aaab", "0011223344556677", "6b657920706c61696e74657874"


def _cli(*cmd):
    out = subprocess.run(
        [str(build_host("lora_aead_cli"))], input=" ".join(map(str, cmd)) + "\n", capture_output=True, text=True, check=True
    ).stdout.split()
    return out


def _webcrypto_seal(bits):
    """key/nonce/AD/plaintext sealed by WebCrypto (node); returns (ct hex, tag hex)."""
    node = shutil.which("node")
    if not node:
        (pytest.fail if os.environ.get("CI") else pytest.skip)("node not available")
    code = (
        'const vm=require("vm"),c=vm.createContext({crypto:globalThis.crypto,Uint8Array,Buffer,console});'
        "vm.runInContext(`(async()=>{const h=s=>Uint8Array.from(Buffer.from(s,'hex'));"
        f"const k=await crypto.subtle.importKey('raw',h('{KEY}'),'AES-GCM',false,['encrypt']);"
        f"const r=Buffer.from(await crypto.subtle.encrypt({{name:'AES-GCM',iv:h('{NONCE}'),additionalData:h('{AD}'),tagLength:{bits}}},k,h('{PT}'))).toString('hex');"
        f"console.log(r.slice(0,{len(PT)}),r.slice({len(PT)}))}})()`,c)"
    )
    return subprocess.run([node, "-e", code], capture_output=True, text=True, check=True).stdout.split()


@pytest.mark.parametrize("bits", [128, 64])
def test_seal_matches_webcrypto(bits):
    """``cali_aead_seal_psa`` equals WebCrypto for tag 16 and 8; ``open`` accepts it, one flipped tag bit is -1.

    .. test:: AES-GCM seal on the ESP (PSA) matches WebCrypto, full and 8-byte tag
       :id: T_FW_LORA_AEAD_SEAL
       :links: R_FW_LORA_SETUP
    """
    ct, tag = _webcrypto_seal(bits)
    tl = bits // 8
    assert _cli("seal", tl, KEY, NONCE, AD, PT) == ["0", ct, tag]
    assert _cli("open", tl, KEY, NONCE, AD, ct, tag) == ["0", PT]
    bad = f"{int(tag[:2], 16) ^ 1:02x}" + tag[2:]
    assert _cli("open", tl, KEY, NONCE, AD, ct, bad)[0] == "-1"
