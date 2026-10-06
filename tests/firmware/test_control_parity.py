"""The ESP's C control twin equals calictl.control on the generated vectors (#154 B).

``firmware/components/cali_core/control.c`` is compiled with ``csrc/codec.c`` and the line driver
``test/control_cli.c`` by the host ``cc`` (macOS too, no NimBLE). Every vector of
``tests/vectors/control.json`` (``tools/gen_control_vectors.py``: calictl's own answers over a value
grid and every app-recorded action) must come out identical: the refusal text, or every planned
write (char, delay, bytes), or bad/none.

.. test:: The C builders and gates reproduce every control vector
   :id: T_FW_CONTROL_PARITY
   :links: R_FW_CONTROL_TWIN

.. test:: The write allow-list is exactly the five control chars at their frame length
   :id: T_FW_WRITE_ALLOWLIST_PURE
   :links: R_FW_WRITE_ALLOWLIST
"""

import json
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from calictl import overrides, protocol
from tools import gen_control_vectors

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
V = json.loads(gen_control_vectors.OUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("control") / "control_cli"
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


def _check(cli, cases, states_of):
    lines = []
    for c in cases:
        lines += state_lines(states_of(c))
        lines.append("P %s %s %s" % (c["function"], c["what"], tok(c["value"])))
    out = drive(cli, lines)
    assert len(out) == len(cases)
    bad = [(c["id"], got, want(c["expect"])) for c, got in zip(cases, out) if got != want(c["expect"])]
    assert not bad, bad[:10]


def test_grid_vectors(cli):
    _check(cli, V["cases"], lambda c: V["states"][c["state"]])


def test_app_recorded_vectors(cli):
    funcs = protocol.load()
    overrides.apply(funcs)
    _check(
        cli,
        V["app"],
        lambda c: {f: protocol.decode(funcs[f], bytes.fromhex(h)) for f, h in c["frames_hex"].items()},
    )


def test_vectors_cover_every_outcome():
    """The parity run is only as strong as the vectors: every outcome kind, every REASON text the C
    header carries (but the wake-up one, which is ELSEWHERE on the ESP), and multi-frame plans."""
    kinds = {c["expect"]["kind"] for c in V["cases"]}
    assert kinds == {"frames", "refused", "bad", "none", "elsewhere"}
    reasons = {c["expect"]["reason"] for c in V["cases"] if c["expect"]["kind"] == "refused"}
    assert len(reasons) == 8, sorted(reasons)
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
