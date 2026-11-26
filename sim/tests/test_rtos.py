"""The virtual-clock FreeRTOS (sim/shims/sim_rtos.c) on its own: an hour of delays and queue waits runs at
once, in order, at exact virtual times."""

import shutil
import subprocess
from pathlib import Path

import pytest

SHIMS = Path(__file__).resolve().parents[1] / "shims"


def test_virtual_clock_delays_queues_events(tmp_path):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    exe = tmp_path / "rtos_check"
    subprocess.run(
        [cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-I", str(SHIMS / "include"), "-o", str(exe),
         str(Path(__file__).parent / "c/rtos_check.c"), str(SHIMS / "sim_rtos.c"), "-lpthread"],
        check=True,
    )  # fmt: skip
    out = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=20, check=True
    ).stdout.splitlines()
    lines = [x for x in out if not x.startswith("T ")]
    assert lines == [
        "event x at 1234",
        "got 1 at 600000",
        "got 2 at 1200000",
        "got 3 at 1800000",
        "timeout at 2800000",
        "timeout at 3800000",
        "timeout at 4800000",
        "SIM end 5000000",
    ]
