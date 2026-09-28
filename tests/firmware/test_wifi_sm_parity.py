"""The C WiFi SM replays tests/vectors/wifi_sm.json exactly (same pinned enums as tools.wifi_sm_ref).

``firmware/components/cali_core/wifi_sm.c`` is a transcription of ``tools/wifi_sm_ref.py``'s
``step()``: the same pinned state/event/action enums (``csrc/net_consts.h``, generated from the
Python twin by ``tools/gen_c_dict.py``), the same transition table, no malloc, no clock (time only
as ``WEV_TICK``'s ``now_ms``) and no platform calls. Proven equal to the Python twin by replaying
the generated golden vectors through both (``tests/test_wifi_sm_ref.py`` replays the Python side).

.. test:: C WiFi SM matches the Python golden vectors
   :id: T_FW_WIFI_SM_PARITY
   :links: R_FW_WIFI_PROVISION
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"


@pytest.fixture(scope="module")
def sm_cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("wsm") / "wifi_sm_cli"
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
            str(CORE / "wifi_sm.c"),
            str(CORE / "test" / "wifi_sm_cli.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def _fmt(state, actions):
    return "%d %d %d %d %d %d |%s" % (*state, "".join(" %d:%d" % tuple(a) for a in actions))


def test_c_wifi_sm_replays_the_golden_vectors(sm_cli):
    cases = json.loads((ROOT / "tests" / "vectors" / "wifi_sm.json").read_text())["cases"]
    lines = []
    for c in cases:
        lines.append("S %d %d %d %d %d %d" % tuple(c["start"]))
        for s in c["steps"]:
            lines.append("E %d %d" % (s["ev"], s["arg"]))
    out = subprocess.run(
        [str(sm_cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    ).stdout.splitlines()
    i = 0
    for c in cases:
        for s in c["steps"]:
            want = _fmt(s["state"], s["actions"])
            assert out[i] == want, "%s step %d: %s != %s" % (c["id"], i, out[i], want)
            i += 1
    assert i == len(out)


def test_first_join_failure_clears_credentials(sm_cli):
    # CONNECTING (2), AP up, never joined + FAILED (6) reason 15 (auth) -> SETUP_AP, LOG + CLEAR
    out = subprocess.run(
        [str(sm_cli)], input="S 2 1 0 0 0 0\nE 6 15\n", capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert out == ["1 1 0 0 0 0 | 6:15 5:0"]


def test_64bit_now_survives_the_c_twin(sm_cli):
    # ONLINE, AP up, stamped just below 2^32 ms: the close deadline crosses 32 bits
    big = (1 << 32) - 10
    out = subprocess.run(
        [str(sm_cli)],
        input="S 3 1 1 0 %d 0\nE 8 %d\n" % (big, big + 30000),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert out == ["3 0 1 0 %d 0 | 1:0" % big]
