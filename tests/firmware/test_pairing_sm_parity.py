"""The C pairing SM replays tests/vectors/pairing.json exactly (same pinned enums as calictl.pairing).

.. req:: C twin of the pairing SM
   :id: R_FW_PAIRING_SM

   The C port of ``calictl.pairing.step`` for the upcoming ESP32 firmware (#154):
   ``firmware/components/cali_core/pairing_sm.c`` uses the same pinned state/event/action enums
   as the Python original (``csrc/pairing_consts.h``, generated from ``calictl/pairing.py`` by
   ``tools/gen_c_dict.py``), the same transition table, and no strings/clock/BLE calls in the SM
   itself. Proven equal to the Python reference by replaying ``tests/vectors/pairing.json``
   through both. See ``firmware/README.md``'s traceability section (this Python module is the
   "shim" sphinx-needs collects it through, since C comments are not autodoc'd; rendered on its
   own docs page by ``docs/firmware.md``, added in Task 10).

.. test:: C pairing SM matches the Python golden vectors
   :id: T_FW_PAIRING_SM_PARITY
   :links: R_PAIRING_SM, R_FW_PAIRING_SM
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
    out = tmp_path_factory.mktemp("sm") / "pairing_sm_cli"
    subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include"),
                    "-I", str(ROOT / "csrc"), str(CORE / "pairing_sm.c"),
                    str(CORE / "test" / "pairing_sm_cli.c"), "-o", str(out)], check=True)
    return out


def test_c_pairing_sm_replays_the_golden_vectors(sm_cli):
    cases = json.loads((ROOT / "tests" / "vectors" / "pairing.json").read_text())["cases"]
    lines = []
    for c in cases:
        lines.append("S %d %d %d" % tuple(c["start"]))
        for s in c["steps"]:
            lines.append("E %d %d" % (s["ev"], s.get("arg", 0)))
    out = subprocess.run([str(sm_cli)], input="\n".join(lines) + "\n", capture_output=True,
                         text=True, check=True).stdout.splitlines()
    i = 0
    for c in cases:
        for s in c["steps"]:
            want = "%d %d %d |%s" % (*s["state"], "".join(" %d:%d" % tuple(a) for a in s["actions"]))
            assert out[i] == want, "%s step %d: %s != %s" % (c["id"], i, out[i], want)
            i += 1
    assert i == len(out)


def test_a_passkey_outside_waiting_passkey_is_ignored(sm_cli):
    # PAIRING (4) + EV_PASSKEY_ENTERED (4) -> unchanged, no action (same as Python's fall-through)
    out = subprocess.run([str(sm_cli)], input="S 4 0 0\nE 4 123456\n", capture_output=True,
                         text=True, check=True).stdout.splitlines()
    assert out == ["4 0 0 |"]
