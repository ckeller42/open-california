"""LoRa onboarding formats and the key-transfer reassembly (``lora_setup.c``, R_FW_LORA_SETUP).

.. test:: LoRa setup: key packing, setup and repair QR text, key-transfer fragments (order, gaps, bad tag, duplicates), SHA-256
   :id: T_FW_LORA_SETUP
   :links: R_FW_LORA_SETUP
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/components/cali_core"
B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
TK = bytes(range(0, 0x100, 0x11))
DEVEUI = bytes.fromhex("70B3D57ED0000001")


def key(seed):
    """A dummy key built from base32 letters (a literal would trip the secret scanner)."""
    a = "".join(B32[(seed + i) % 32] for i in range(39))
    b = "".join(B32[(seed * 3 + i) % 32] for i in range(52))
    return "NNSXS." + a + "." + b


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("lora_setup") / "lora_setup_cli"
    cmd = [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include")]
    cmd += [str(CORE / f) for f in ("lora_setup.c", "test/lora_setup_cli.c")] + ["-o", str(out)]
    # AddressSanitizer + UBSan catch an overrun the output would not show; skip them if the toolchain lacks them
    if subprocess.run(cmd + ["-fsanitize=address,undefined"], capture_output=True).returncode:
        subprocess.run(cmd, check=True)
    return out


def run(cli, *lines):
    out = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def plaintext(cluster="eu1.cloud.thethings.network", app="my-van-app", dev="van-000001", ver=1):
    keys = []
    for k in (key(3), key(7)):
        keys.append(pack(k))
    c, a, d = (s.encode() for s in (cluster, app, dev))
    return bytes([ver, len(c)]) + c + bytes([len(a)]) + a + bytes([len(d)]) + d + b"".join(keys)


def pack(k):
    """Reference packing: the bit string in whole bytes, the leftover bits right-aligned in one more byte."""
    out = b""
    for part in (k[6:45], k[46:]):
        bits = "".join(f"{B32.index(c):05b}" for c in part)
        full = len(bits) // 8 * 8
        out += int(bits[:full], 2).to_bytes(full // 8, "big") + bytes([int(bits[full:], 2)])
    return out


def fragments(plain, nonce=bytes(range(12)), tag=bytes(16), size=50):
    ct = bytes(p ^ TK[i % 16] for i, p in enumerate(plain))
    blob = nonce + ct + tag
    parts = [blob[i : i + size] for i in range(0, len(blob), size)]
    return [bytes([i | (0x80 if i == len(parts) - 1 else 0)]) + p for i, p in enumerate(parts)]


def xfer(cli, frags):
    """Feed fragments; return a list of (rc, confirm, plain) per fragment."""
    lines = [f"xfer {TK.hex()} {DEVEUI.hex()} {f.hex()}" for f in frags]
    res = []
    for ln in run(cli, *lines):
        rc, conf, pl = ln.split()
        res.append((int(rc), "" if conf == "-" else conf, "" if pl == "-" else pl))
    return res


def test_key_pack_round_trip(cli):
    k = key(5)
    assert len(k) == 98
    out = run(cli, f"keypack {k}")
    assert len(out[0]) == 116 and bytes.fromhex(out[0]) == pack(k)
    assert run(cli, f"keyunpack {out[0]}") == [k]
    lower = k[:10] + k[10].lower() + k[11:]
    for bad in (lower, k[:6] + k[7:], k[:45] + "X" + k[46:], k.replace("NNSXS.", "NNSXT.")):
        assert run(cli, f"keypack {bad}") == ["-1"]
    # non-zero padding bits do not unpack
    raw = bytearray(bytes.fromhex(out[0]))
    raw[24] |= 0x80
    assert run(cli, f"keyunpack {raw.hex()}") == ["-1"]


def test_setup_qr_text(cli):
    out = run(cli, f"setupqr {DEVEUI.hex()} {TK.hex()} {TK.hex()}")[0]
    n, text = out.split(" ", 1)
    assert text == f"OCS1*70B3D57ED0000001*{TK.hex().upper()}*{TK.hex().upper()}"
    assert n == "87" and len(text) == 87  # the spec says 88; its own format is 87 long
    assert all(c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:" for c in text)


def test_repair_qr_from_plaintext(cli):
    out = run(cli, f"repairqr 600 {plaintext().hex()}", f"repairqr 600 {plaintext(ver=2).hex()}")
    want = f"OCR1*EU1.CLOUD.THETHINGS.NETWORK*MY-VAN-APP*VAN-000001*{key(3)}*{key(7)}"
    assert out[0] == f"{len(want)} {want}"
    assert out[1] == "0 -"
    # too long a cluster, a trailing byte and an invalid character are rejected
    long = plaintext(cluster="a" * 65).hex()
    assert (
        run(
            cli,
            f"repairqr 600 {long}",
            f"repairqr 600 {plaintext().hex()}00",
            f"repairqr 600 {plaintext(app='a*b').hex()}",
        )
        == ["0 -"] * 3
    )


@pytest.mark.parametrize("order", ["in", "reverse"])
def test_xfer_in_order_and_reverse(cli, order):
    plain = plaintext()
    frags = fragments(plain)
    assert len(frags) == 4
    res = xfer(cli, frags if order == "in" else frags[::-1])
    want = "00" + hashlib.sha256(plain).digest()[:4].hex()
    assert res[-1] == (1, want, plain.hex())
    if order == "in":
        assert [r[0] for r in res[:-1]] == [0, 0, 0]


def test_xfer_missing_reports_bitmap(cli):
    plain = plaintext()
    f = fragments(plain)
    res = xfer(cli, [f[0], f[2], f[3], f[1]])
    # the last fragment arrives with index 1 missing: confirmation 02 with bitmap 0b1101
    assert res[2] == (2, "020d", "")
    assert res[3][0] == 1 and res[3][1].startswith("00") and res[3][2] == plain.hex()


def test_xfer_bad_tag(cli):
    plain = plaintext()
    res = xfer(cli, fragments(plain, tag=b"\x01" * 16))
    assert res[-1] == (1, "01", "")


def test_xfer_duplicate_and_stale_fragments(cli):
    f = fragments(plaintext())
    # an identical repeat is harmless; a repeat with other bytes resets the transfer
    other = f[0][:-1] + bytes([f[0][-1] ^ 1])
    res = xfer(cli, [f[0], f[0], other])
    assert [r[0] for r in res] == [0, 0, -1]
    # the state is gone: the transfer starts anew and completes
    assert xfer(cli, f)[-1][0] == 1
    # index > 7 (reserved bits), an empty body, a short middle fragment (a fragment beyond the last follows)
    bad = [bytes([0x08]) + bytes(50), bytes([0x80]), bytes([0]) + bytes(10)]
    for b in bad:
        assert xfer(cli, [b])[0][0] == -1
    assert xfer(cli, [f[3], bytes([4]) + bytes(50)])[-1][0] == -1


def test_sha256_4_vector(cli):
    assert run(cli, "sha4 616263") == ["ba7816bf"]
    data = bytes(range(200))
    assert run(cli, f"sha4 {data.hex()}") == [hashlib.sha256(data).digest()[:4].hex()]


def test_repair_qr_output_bounds(cli):
    """A small ``max`` is refused and never written past (the sanitizer build traps an overrun)."""
    pt = plaintext().hex()
    want = f"OCR1*EU1.CLOUD.THETHINGS.NETWORK*MY-VAN-APP*VAN-000001*{key(3)}*{key(7)}"
    sizes = list(range(0, 8)) + [len(want) - 1, len(want), len(want) + 1, len(want) + 2]
    out = run(cli, *[f"repairqr {m} {pt}" for m in sizes])
    for m, line in zip(sizes, out):
        # max includes the NUL: the text fits from len + 1
        assert line == (f"{len(want)} {want}" if m >= len(want) + 1 else "0 -"), m


def test_key_unpack_rejects_padding_in_last_byte(cli):
    raw = bytearray(bytes.fromhex(run(cli, f"keypack {key(5)}")[0]))
    for bit in (0x10, 0x80):
        bad = bytearray(raw)
        bad[57] |= bit
        assert run(cli, f"keyunpack {bad.hex()}") == ["-1"]
    assert run(cli, f"keyunpack {raw.hex()}") == [key(5)]


def test_xfer_reassembly_bound(cli):
    # 7 * 50 + 50 > 320, 6 * 50 + 21 > 320: rejected; 6 * 50 + 20 ends at 320: accepted (as the last fragment)
    assert xfer(cli, [bytes([0x07]) + bytes(50)])[0][0] == -1
    assert xfer(cli, [bytes([0x86]) + bytes(21)])[0][0] == -1
    assert xfer(cli, [bytes([0x86]) + bytes(20)])[0] == (2, "0240", "")
