"""The ESP's C control twin equals calictl.control on the generated vectors (#154 B).

``firmware/components/cali_core/control.c`` is compiled with ``csrc/codec.c`` and the line driver
``test/control_cli.c`` by the host ``cc`` (macOS too, no NimBLE) and, in the Linux host tier, as a 32-bit (i686) binary —
the ESP32-S3's ``long`` width. Every vector of
``tests/vectors/control.json`` (``tools/gen_control_vectors.py``: calictl's own answers over a value
grid and every app-recorded action) must come out identical: the refusal text, or every planned
write (char, delay, bytes), or bad/none.

.. test:: The C builders and gates reproduce every control vector
   :id: T_FW_CONTROL_PARITY
   :links: R_FW_CONTROL_TWIN, R_FW_WAKEUP

.. test:: The write allow-list is exactly the five control chars at their frame length
   :id: T_FW_WRITE_ALLOWLIST_PURE
   :links: R_FW_WRITE_ALLOWLIST

.. test:: The C lighting-config latch equals semantics.lighting_config
   :id: T_FW_LIGHT_CFG_PARITY
   :links: R_FW_CONTROL_TWIN
"""

import json
import os
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from calictl import overrides, protocol, semantics
from tools import gen_control_vectors

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
V = json.loads(gen_control_vectors.OUT.read_text(encoding="utf-8"))


# The ESP32-S3 is 32-bit (``long`` = 4 bytes): the C twin's ``py_int`` saturates at LONG_MAX there.
# The "m32" leg builds the same driver as an i686 binary (the host tier's toolchain: gcc-multilib or
# CROSS_COMPILE=i686-linux-gnu-, run through qemu-i386 on arm64) and replays every vector against it.
LONG_IS_4 = "typedef char long_is_4_bytes[sizeof(long) == 4 ? 1 : -1];\n"


@pytest.fixture(scope="module", params=["native", pytest.param("m32", marks=pytest.mark.linux_only)])
def cli(request, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("control")
    if request.param == "native":
        cmd = [shutil.which("cc") or pytest.skip("no C compiler")]
    else:
        cmd = [os.environ.get("CROSS_COMPILE", "") + "gcc", "-m32"]
        probe = tmp / "long_is_4.c"
        probe.write_text(LONG_IS_4)
        subprocess.run(cmd + ["-c", str(probe), "-o", str(tmp / "probe.o")], check=True)  # really 32-bit
    out = tmp / "control_cli"
    subprocess.run(
        cmd
        + [
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            "-I",
            str(ROOT / "csrc"),
            str(CORE / "control.c"),
            str(ROOT / "csrc" / "codec.c"),
            str(CORE / "test" / "control_cli.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def drive(cli, lines):
    r = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True, timeout=120
    )
    return r.stdout.splitlines()


def tok(v):
    """One vector value as the driver's token: ``n`` (null -> ""), ``i:<decimal>``, ``s:<percent-encoded>``."""
    if v is None:
        return "n"
    if isinstance(v, int):
        return "i:%d" % v
    return "s:" + urllib.parse.quote(v, safe="")


def state_lines(states):
    return ["X"] + ["S %s %s" % (fn, " ".join("%s=%d" % kv for kv in f.items())) for fn, f in states.items()]


def want(e):
    if e["kind"] == "frames":
        return "OK " + " ".join("%s/%d/%s" % (f["char"], f["delay_ms"], f["hex"]) for f in e["frames"])
    if e["kind"] in ("refused", "elsewhere"):
        return "%s %s" % (e["kind"].upper(), e["reason"])
    return e["kind"].upper()


def p_line(c):
    ln = c.get("local_now")
    return "P %s %s %s%s" % (c["function"], c["what"], tok(c["value"]), "" if ln is None else " t:%d" % ln)


def _check(cli, cases, states_of):
    lines = []
    for c in cases:
        lines += state_lines(states_of(c))
        lines.append(p_line(c))
    out = drive(cli, lines)
    assert len(out) == len(cases)
    bad = [(c["id"], got, want(c["expect"])) for c, got in zip(cases, out) if got != want(c["expect"])]
    assert not bad, bad[:10]


def test_grid_vectors(cli):
    _check(cli, V["cases"], lambda c: V["states"][c["state"]])


def _app_states(c, funcs):
    st = {f: protocol.decode(funcs[f], bytes.fromhex(h)) for f, h in c["frames_hex"].items()}
    if c.get("latch"):
        st["lighting"] = {**st.get("lighting", {}), **c["latch"]}
    return st


def test_app_recorded_vectors(cli):
    funcs = protocol.load()
    overrides.apply(funcs)
    _check(cli, V["app"], lambda c: _app_states(c, funcs))


def test_config_pull_plan_is_request_config_then_commit(cli):
    assert drive(cli, ["Q"]) == [want({"kind": "frames", "frames": V["config_pull"]["frames"]})]


def test_vectors_cover_every_outcome():
    """The parity run is only as strong as the vectors: every outcome kind, every REASON text the C
    header carries and the wake-up's WAKEUP_UNKNOWN, and multi-frame plans."""
    kinds = {c["expect"]["kind"] for c in V["cases"]}
    assert kinds == {"frames", "refused", "bad", "none", "elsewhere"}
    reasons = {c["expect"]["reason"] for c in V["cases"] if c["expect"]["kind"] == "refused"}
    assert len(reasons) == 10, sorted(reasons)
    assert {len(c["expect"].get("frames", [])) for c in V["cases"]} >= {1, 2, 4}
    assert V["app"], "no app-recorded action in the vectors"


def test_write_allow_list_is_exactly_the_control_chars(cli):
    """Every (char, length) pair 0x0000-0xffff x 0-33 bytes: only the five control chars at their
    exact frame length pass — never the roof's 1401, never 1003 (the heartbeat has its own path)."""
    assert drive(cli, ["A"]) == ["OK 1101/6 1201/1 1501/16 1601/1 1701/6"]


@pytest.mark.parametrize(
    "line,out",
    [
        ("W 1501 16", "OK 1"),
        ("W 1501 17", "OK 0"),
        ("W 1501 0", "OK 0"),
        ("W 1401 5", "OK 0"),
        ("W 1003 4", "OK 0"),
    ],
)
def test_write_ok_spot_checks(cli, line, out):
    assert drive(cli, [line]) == [out]


def _latch_cases():
    frames = []
    for mode in (0, 4, 12, 16, 20):
        for pn in (None, 0, 1, 7, 8, 13, 14):
            for lv in (None, 0, 1, 0x7F, 0xFF, 0x1101):
                for ts in (None, 25200):
                    f = {"Mode": mode}
                    for k, v in (("ProfileNumber", pn), ("LightValue", lv), ("Timestamp", ts)):
                        if v is not None:
                            f[k] = v
                    frames.append(f)
    prevs = [
        {},
        {"FavouritesStored": 0b101},
        {"WakeupTimestamp": 23400, "WakeupLightValue": 0x3575, "DoorContact": 1, "FavouritesStored": 0},
    ]
    return [(p, f) for p in prevs for f in frames]


def test_light_config_latch_equals_semantics(cli):
    """``cali_light_cfg`` over (previous latch keys + a frame) = ``semantics.lighting_config(prev, frame)``
    for every Mode x ProfileNumber x LightValue x Timestamp combination, absent fields included."""
    cases = _latch_cases()
    lines = []
    for prev, frame in cases:
        lines += state_lines({"lighting": {**prev, **frame}})
        lines.append("C")
    out = drive(cli, lines)
    want = []
    for prev, frame in cases:
        cfg = semantics.lighting_config(prev, frame)
        want.append("CFG" + "".join(" %s=%d" % (k, cfg[k]) for k in semantics.LIGHT_CONFIG_KEYS if k in cfg))
    bad = [(c, g, w) for c, g, w in zip(cases, out, want) if g != w]
    assert len(out) == len(cases) and not bad, bad[:5]
