"""LoRa commands on FPort 20 (``cali_lora_cmd``): sealed by WebCrypto (AES-GCM, 8-byte tag), opened by PSA."""

import os
import shutil
import subprocess

import pytest

from .conftest import build_host

pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

KEY = "000102030405060708090a0b0c0d0e0f"
EUI = "70b3d57ed0000001"
MAP = {
    1: "airheater power on",
    2: "airheater power off",
    3: "campingmode master on",
    4: "campingmode master off",
}


def _seal(ctr, code):
    """The 11-byte frame ctr(2 BE) | cmd | tag(8), sealed by WebCrypto (node vm)."""
    node = shutil.which("node")
    if not node:
        (pytest.fail if os.environ.get("CI") else pytest.skip)("node not available")
    c = f"{ctr:04x}"
    js = (
        'const vm=require("vm"),c=vm.createContext({crypto:globalThis.crypto,Uint8Array,Buffer,console});'
        "vm.runInContext(`(async()=>{const h=s=>Uint8Array.from(Buffer.from(s,'hex'));"
        f"const k=await crypto.subtle.importKey('raw',h('{KEY}'),'AES-GCM',false,['encrypt']);"
        f"const r=Buffer.from(await crypto.subtle.encrypt({{name:'AES-GCM',iv:h('04{EUI}00{c}'),additionalData:h('{c}'),tagLength:64}},k,h('{code:02x}'))).toString('hex');"
        f"console.log('{c}'+r)}})()`,c)"
    )
    return subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout.strip()


def _run(*lines):
    out = subprocess.run(
        [str(build_host("lora_cmd_cli"))],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [o for o in out if not o.startswith("LOG ")]


CTR0 = "kvset lora_cmd_ctr 0000"  # what hub.c writes with a new data key


def _cmd(frame, key=KEY):
    return f"cmd {key} {EUI} {frame}"


@pytest.mark.parametrize("code", [1, 2, 3, 4])
def test_cmd_codes(code):
    """Each command code opens and maps to its function, name and value.

    .. test:: sealed LoRa command (FPort 20) opens and maps codes 1..4
       :id: T_FW_LORA_CMD
    """
    assert _run(CTR0, _cmd(_seal(1, code))) == [f"0 {MAP[code]}"]


def test_cmd_replay():
    """The same counter twice is rejected the second time.

    .. test:: a replayed LoRa command is rejected
       :id: T_FW_LORA_CMD_REPLAY
    """
    f = _seal(7, 1)
    assert _run(CTR0, _cmd(f), _cmd(f)) == ["0 airheater power on", "-1"]


def test_cmd_old_ctr_after_restart():
    """The last counter lives in KV: an older or equal one is rejected, a newer one accepted.

    .. test:: the LoRa command counter survives a restart
       :id: T_FW_LORA_CMD_CTR
    """
    assert _run(
        "kvset lora_cmd_ctr 0009", _cmd(_seal(9, 1)), _cmd(_seal(8, 1)), _cmd(_seal(10, 2)), "kv lora_cmd_ctr"
    ) == ["-1", "-1", "0 airheater power off", "lora_cmd_ctr 000a"]


def test_cmd_bad_tag():
    """A flipped tag bit, a wrong key or a wrong DevEUI is -1 and does not move the counter.

    .. test:: a LoRa command with a bad tag is rejected
       :id: T_FW_LORA_CMD_TAG
    """
    f = _seal(3, 1)
    bad = f[:-1] + f"{int(f[-1], 16) ^ 1:x}"
    assert _run(
        CTR0, _cmd(bad), _cmd(f, "ff" * 16), f"cmd {KEY} 70b3d57ed0000002 {f}", "kv lora_cmd_ctr", _cmd(f)
    ) == ["-1", "-1", "-1", "lora_cmd_ctr 0000", "0 airheater power on"]


def test_cmd_wrong_len_and_code():
    """Any length but 11 and an unknown code are -1.

    .. test:: a LoRa command of the wrong length or code is rejected
       :id: T_FW_LORA_CMD_LEN
    """
    f = _seal(4, 1)
    assert _run(CTR0, _cmd(f[:-2]), _cmd(f + "00"), _cmd(_seal(4, 5)), _cmd(_seal(4, 0))) == ["-1"] * 4


def test_cmd_guard_never_fails_open():
    """A corrupt, odd-sized or missing counter record rejects every frame; ctr 0 is rejected after a new key.

    .. test:: the LoRa command replay guard never fails open
       :id: T_FW_LORA_CMD_FAILCLOSED
    """
    assert _run("kvcorrupt lora_cmd_ctr", _cmd(_seal(5, 1))) == ["-1"]
    assert _run("kvset lora_cmd_ctr 05", _cmd(_seal(5, 1))) == ["-1"]
    assert _run(_cmd(_seal(1, 1))) == ["-1"]  # key present, counter erased/never written
    assert _run(CTR0, _cmd(_seal(0, 1)), _cmd(_seal(1, 1))) == ["-1", "0 airheater power on"]


def test_cmd_counter_restarts_with_a_new_key():
    """After a rekey the counter is 0 again: ctr 1 opens with the new key, the old key's frame is -1.

    .. test:: the LoRa command counter restarts with a new data key
       :id: T_FW_LORA_CMD_REKEY
    """
    new = "ff" * 16
    assert _run("kvset lora_cmd_ctr 0009", CTR0, _cmd(_seal(1, 1), new), _cmd(_seal(1, 1))) == [
        "-1",
        "0 airheater power on",
    ]
