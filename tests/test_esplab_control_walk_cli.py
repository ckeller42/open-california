"""``tools/esplab_control_walk.py`` runs as a plain script from any directory, as documented.

The bench run (2026-10-07) hit ``ModuleNotFoundError: No module named 'calictl'`` in
``gate_state`` when started as ``python tools/esplab_control_walk.py`` (sys.path[0] = tools/).
"""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "esplab_control_walk.py"


def test_cli_imports_calictl_from_any_cwd(tmp_path):
    fifo = tmp_path / "unit.in"  # a plain file stands in for the FIFO
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--url",
            "http://127.0.0.1:9",
            "--fifo",
            str(fifo),
            "--record",
            str(tmp_path / "unit.jsonl"),
            "--timeout",
            "0.1",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # it gets past gate_state's calictl import and fails on the unreachable firmware instead
    assert "ModuleNotFoundError" not in r.stderr, r.stderr
    assert "URLError" in r.stderr or "Connection refused" in r.stderr, r.stderr


def test_held_state_ignores_fields_the_units_clock_moves():
    """The mock (like the unit) counts the cooler start timer, the heater run time and the battery
    age down/up from its own RTC every second, so a pushed TimerCounterMin=52 is rewritten at once
    and the walk's "held" check never matched (bench 2026-10-07, cooler.jsonl:259 on a second run).
    No builder or gate reads those fields; the walk does not wait for them."""
    from tools import esplab_control_walk as w

    case = next(c for c in w.app_cases() if c["id"] == "cooler.jsonl:259")
    want = w.gate_state(case)
    assert "State" in want["cooler"]
    assert not {k for fields in want.values() for k in fields} & w.CLOCK_FIELDS
    # the firmware's state carries every field (the counter already moved); the gate fields hold
    got = {fn: dict(fields, TimerCounterMin=7) for fn, fields in want.items()}
    assert w.held_state(lambda: got, want, timeout=1, every=0) is True
