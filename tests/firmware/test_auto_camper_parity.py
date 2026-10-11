"""The ESP's auto-camper decision equals calictl's (R_FW_AUTO_CAMPER).

``tools/gen_auto_camper_vectors.py`` asks ``calictl.automation.auto_camper_restore_decide`` over an
edge x debt grid and a seeded random sweep; ``firmware/components/cali_core/auto_camper.c`` (driven by
``test/auto_camper_cli.c``, host ``cc``) must return the same state, action and notice text for every
case.

.. test:: The C auto-camper decision reproduces every Python vector
   :id: T_FW_AUTO_CAMPER_PARITY
   :links: R_FW_AUTO_CAMPER, R_AUTO_CAMPER_RESTORE
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import gen_auto_camper_vectors as gen

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
V = json.loads(gen.OUT.read_text(encoding="utf-8"))


def test_checked_in_vectors_are_fresh():
    assert gen.OUT.read_text(encoding="utf-8") == gen.render()


def test_vectors_reach_every_outcome():
    notices = {(c["out"][2] or "")[:30] for c in V["cases"]}
    assert {
        "",
        "Auto camper: camping restored ",
        "Auto camper gave up: camping d",
        "Auto camper gave up: camping w",
        "Auto camper stood down: batter",
    } <= notices
    stood = {c["out"][2] for c in V["cases"] if (c["out"][2] or "").startswith("Auto camper stood")}
    assert any("SoC" in s for s in stood) and any("SoC" not in s for s in stood)
    assert any(c["out"][0] and c["out"][4] == [True, True] for c in V["cases"])  # usb + lights restored


def _n(v):
    return -1 if v is None else int(v)


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("ac") / "auto_camper_cli"
    subprocess.run(
        [
            cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
            "-I", str(CORE / "include"),
            str(CORE / "auto_camper.c"), str(CORE / "test" / "auto_camper_cli.c"),
            "-o", str(out),
        ],
        check=True,
    )  # fmt: skip
    return out


def test_c_twin_matches_every_vector(cli):
    lines = []
    for c in V["cases"]:
        owe, pre, until, fails, pign, pm, pusb, plights = c["state"]
        pu, pl = pre if pre else (None, None)
        vals = c["in"] + [owe, pu, pl, until, fails, pign, pm, pusb, plights]
        lines.append(" ".join(str(_n(v)) for v in vals))
    cfg = V["cfg"]
    argv = [str(cli), str(cfg["min_soc"]), str(cfg["max_fails"]), str(cfg["window"]), str(V["now"])]
    got = subprocess.run(argv, input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    outs = got.stdout.splitlines()
    assert len(outs) == len(V["cases"])
    for c, line in zip(V["cases"], outs):
        act, restored, notice, owe, pre, until, fails, pign, pm, pusb, plights = c["out"]
        pu, pl = pre if pre else (None, None)
        want = " ".join(
            str(_n(v)) for v in (act, restored, owe, pu, pl, until, fails, pign, pm, pusb, plights)
        )
        assert line == want + "|" + (notice or ""), c["id"]
