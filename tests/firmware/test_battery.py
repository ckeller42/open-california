"""The satellite's battery reading + runtime estimate (``battery.c``, R_FW_BATTERY).

.. test:: Battery: device.battery's shape, the runtime estimate only from a 5-min / 2-point measured drop, reset on USB
   :id: T_FW_BATTERY
   :links: R_FW_BATTERY
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/components/cali_core"
MIN = 60_000


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("batt") / "battery_cli"
    subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include")]
        + [str(CORE / f) for f in ("battery.c", "json.c", "test/battery_cli.c")]
        + ["-o", str(out)],
        check=True,
    )
    return out


def run(cli, *lines):
    out = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return [json.loads(x)["battery"] for x in out.stdout.splitlines()]


def discharge(points):
    """(minute, pct) on battery, 3900 mV."""
    return ["%d %d 3900 0 0" % (m * MIN, p) for m, p in points]


def test_unknown_before_any_reading_and_for_an_absent_chip(cli):
    unknown = dict.fromkeys(("pct", "mv", "usb", "charging", "runtime_min"))
    assert run(cli, "get", "0 -1 -1 -1 -1") == [unknown, unknown]


def test_reading_passes_through(cli):
    (b,) = run(cli, "1000 87 4120 1 1")
    assert b == {"pct": 87, "mv": 4120, "usb": True, "charging": True, "runtime_min": None}


def test_estimate_needs_five_minutes_and_two_points(cli):
    *early, b = run(cli, *discharge([(0, 64), (2, 63), (4, 62), (5, 62)]))
    assert all(x["runtime_min"] is None for x in early)
    assert b["runtime_min"] == 62 * 5 // 2  # 2 points in 5 min -> 155 min
    # 1 point in 9 min: not deep enough, whatever the span
    assert run(cli, "reset", *discharge([(0, 64), (9, 63)]))[-1]["runtime_min"] is None


def test_window_keeps_ten_minutes(cli):
    # 70 -> 60 over the first 10 min, then flat: once the steep start leaves the window, the slope
    # comes from the last 10 min only (no drop -> unknown)
    pts = [(m, max(60, 70 - m)) for m in range(0, 21)]
    out = run(cli, "reset", *discharge(pts))
    assert out[10]["runtime_min"] == 60 * 10 // 10  # 10 points in 10 min, 60 % left -> 60 min
    assert out[-1]["runtime_min"] is None


@pytest.mark.parametrize("line", ["%d 61 4000 1 0", "%d 61 4000 0 1", "%d -1 -1 -1 -1"])
def test_usb_charging_or_unknown_resets_the_window(cli, line):
    lines = discharge([(0, 64), (5, 62)]) + [line % (6 * MIN)] + discharge([(7, 61), (11, 60)])
    out = run(cli, "reset", *lines)
    assert out[1]["runtime_min"] == 155 and out[2]["runtime_min"] is None
    assert out[-1]["runtime_min"] is None  # 4 min since the reset: not wide enough


def test_ten_second_cadence_fits_the_sample_ring(cli):
    # 1 % per 2 min at the real 10 s cadence for 30 min: the ring (64) holds the 10-min window
    lines = ["%d %d 3800 0 0" % (t * 10_000, 80 - t * 10 // 120) for t in range(181)]
    b = run(cli, "reset", *lines)[-1]
    assert b["pct"] == 65 and b["runtime_min"] == 130  # 65 % at 0.5 %/min
