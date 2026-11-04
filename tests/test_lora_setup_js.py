"""calictl/webui/lora_setup.js equals the C ``lora_setup.c`` (R_FW_LORA_SETUP) and seals the key transfer.

``lora_setup.js`` is a classic script like semantics.js; this loads it into a ``node`` vm context and checks
its QR parsers, key packing, plaintext and AES-128-GCM transfer fragments. ``tests/firmware/data/lora_xfer_vector.json``
holds one fixed transfer (written from ``sealTransfer`` and reviewed); the C side decrypts it. The packing is
checked against the C CLI built here with ``cc``. Skipped without node or a C compiler.

.. test:: The JS LoRa setup twin parses the QR texts, packs keys like C and seals the key transfer to the vector
   :id: T_UI_LORA_SETUP_JS
   :links: R_FW_LORA_SETUP
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "calictl" / "webui" / "lora_setup.js"
CORE = ROOT / "firmware/components/cali_core"
VECTOR = ROOT / "tests/firmware/data/lora_xfer_vector.json"
B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
TK = bytes(range(0, 0x100, 0x11)).hex()
DEVEUI = "70B3D57ED0000001"

_HARNESS = (
    'const fs=require("fs"),vm=require("vm");'
    "const ctx=vm.createContext({crypto,Uint8Array});"
    'vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),ctx,{filename:process.argv[1]});'
    'ctx.h=u=>Buffer.from(u).toString("hex");ctx.u8=x=>new Uint8Array(Buffer.from(x,"hex"));'
    "vm.runInContext(`(async()=>(${process.argv[2]}))()`,ctx).then("
    "r=>process.stdout.write(JSON.stringify(r)),e=>{console.error(e);process.exit(1)});"
)


def js(expr):
    """Evaluate ``expr`` (helpers ``h`` = bytes to hex, ``u8`` = hex to bytes) with lora_setup.js loaded."""
    node = shutil.which("node") or pytest.skip("no node")
    out = subprocess.run([node, "-e", _HARNESS, str(JS), expr], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def key(seed):
    """A dummy key from base32 letters (a literal would trip the secret scanner)."""
    a = "".join(B32[(seed + i) % 32] for i in range(39))
    b = "".join(B32[(seed * 3 + i) % 32] for i in range(52))
    return "NNSXS." + a + "." + b


K1, K2 = key(3), key(7)
INPUTS = {"cluster": "eu1.cloud.thethings.network", "appId": "my-van-app", "devId": "van-000001",
          "readKey": K1, "cmdKey": K2}  # fmt: skip
REPAIR = f"OCR1*EU1.CLOUD.THETHINGS.NETWORK*MY-VAN-APP*VAN-000001*{K1}*{K2}"


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("lora_setup_js") / "lora_setup_cli"
    cmd = [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include")]
    cmd += [str(CORE / f) for f in ("lora_setup.c", "test/lora_setup_cli.c")] + ["-o", str(out)]
    subprocess.run(cmd, check=True)
    return out


def run(cli, *lines):
    out = subprocess.run([str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_vector_matches_file():
    v = json.loads(VECTOR.read_text())
    got = js(f'(async()=>(await sealTransfer(u8("{v["plain"]}"),u8("{v["tk"]}"),"{v["deveui"]}",u8("{v["nonce"]}"))).map(h))()')
    assert got == v["fragments"]
    assert all(len(f) // 2 <= 51 for f in got)
    # the vector's plaintext is the Task 1 style plaintext of the dummy keys
    assert js(f"h(packPlain({json.dumps(INPUTS)}))") == v["plain"]


def test_pack_matches_c(cli):
    plain = js(f"h(packPlain({json.dumps(INPUTS)}))")
    out = run(cli, f"repairqr 600 {plain}", f"keypack {K1}", f"keypack {K2}")
    assert out[0] == f"{len(REPAIR)} {REPAIR}"
    assert js(f"parseRepairQr({json.dumps(REPAIR)})") == INPUTS
    assert js(f"parseRepairQr({json.dumps(REPAIR.lower())})") == INPUTS
    assert plain[-232:] == out[1] + out[2]  # read key, command key: the C packing, bit for bit
    assert js("h(await plainHash4(u8('616263')))") == run(cli, "sha4 616263")[0]


def test_setup_qr_matches_c(cli):
    tk = TK
    text = run(cli, f"setupqr {DEVEUI} {tk} {tk}")[0].split(" ", 1)[1]
    got = js(f"(q=>({{d:q.devEui,a:q.appKey,t:h(q.tk)}}))(parseSetupQr({json.dumps(text.lower())}))")
    assert got == {"d": DEVEUI, "a": tk.upper(), "t": tk}


def test_parse_rejects_foreign_and_truncated():
    ok = f"OCS1*{DEVEUI}*{TK.upper()}*{TK.upper()}"
    assert js(f"parseSetupQr({json.dumps(ok)}) !== null")
    for bad in ("WIFI:S:x;;", f"OCS1*{DEVEUI}*{TK.upper()[:-1]}*{TK.upper()}", ok + "*", ok[:-1], ok.replace("OCS1", "OCR1")):
        assert js(f"parseSetupQr({json.dumps(bad)})") is None
    cut = REPAIR.lower().rsplit("*", 1)[0]
    for bad in (cut, REPAIR + "*X", REPAIR.replace("MY-VAN-APP", "MY*VAN"), REPAIR.replace("OCR1", "OCS1"), "OCR1*A*B*C*" + K1 + "*" + K2[:-1], "OCR1*" + "A" * 65 + "*B*C*" + K1 + "*" + K2):
        assert js(f"parseRepairQr({json.dumps(bad)})") is None
    assert js("parseRepairQr('')") is None


def test_confirm_parse():
    assert js("(r=>({s:r.status,h:h(r.hash4)}))(parseConfirm(u8('00aabbccdd')))") == {"s": 0, "h": "aabbccdd"}
    assert js("parseConfirm(u8('020d'), 4).missing") == [1]
    assert js("parseConfirm(u8('01')).status") == 1
    assert js("parseConfirm(u8('03')).status") == 3
    assert js("parseConfirm(u8('00aa'))") is None and js("parseConfirm(u8('07'))") is None
