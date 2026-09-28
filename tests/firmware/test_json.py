"""cali_json: bounded, never torn.

.. test:: JSON writer escapes and refuses to overflow
   :id: T_FW_JSON_WRITER
   :links: R_FW_HTTP_STATUS
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"


@pytest.fixture(scope="module")
def json_cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("j") / "json_cli"
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            str(CORE / "json.c"),
            str(CORE / "test" / "json_cli.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def run(cli, cap, script):
    return subprocess.run([str(cli), str(cap)], input=script, capture_output=True, text=True).stdout.strip()


def test_escapes_and_nesting(json_cli):
    out = run(
        json_cli,
        256,
        'K name S he said "hi"\\n K n I 42 K ok B 1 K none N K arr A I 1 I 2 ] K o { K x I 3 } END',
    )
    assert json.loads(out) == {
        "name": 'he said "hi"\n',
        "n": 42,
        "ok": True,
        "none": None,
        "arr": [1, 2],
        "o": {"x": 3},
    }


def test_overflow_reports_and_emits_nothing(json_cli):
    out = run(json_cli, 16, "K name S 0123456789012345678901234567890123456789 END")
    assert out == "OVERFLOW"
