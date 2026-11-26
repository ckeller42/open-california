"""LoRa scenarios that run on both targets: the simulated CoreS3 (CI) and the HIL bench board
(``tools/esplab/hil.sh scenarios``). See :mod:`tests.firmware.hil` for the targets; sim-only scenarios (a
forced latch, a missing AW9523, refusals) are in ``test_lora_esp_sim.py``, because a board cannot be put
into those states on purpose.

On the board, every run is also run on the sim; the differences go to ``$HIL_OUT/drift-<scenario>.txt``
("fake drift") and fail nothing by themselves.

.. test:: LoRa scenarios on sim and board: RX settings readable, liveness probe, rejoin with uplinks
   :id: T_FW_LORA_SCENARIOS
   :links: R_FW_LORA
"""

import os
from pathlib import Path

import pytest

from . import hil
from .conftest import build_host

RX_SETTINGS = ["RX1DL", "RX2DL", "RX2DR", "RX2FQ", "JN1DL", "JN2DL", "BAND", "CLASS", "ADR", "NJS"]
BOARD = os.environ.get("HIL_TARGET", "sim") == "board"
# the sim target needs the 32-bit host build; the board target does not (its drift run uses HIL_SIM_BIN)
pytestmark = [] if BOARD else [pytest.mark.linux_only]


@pytest.fixture
def target(tmp_path):
    if BOARD:
        return hil.BoardTarget(os.environ.get("HIL_PORT", ""))
    return hil.SimTarget(build_host("lora-sim"), tmp_path / "store")


@pytest.fixture
def run(target, tmp_path, request):
    """``run(steps, duration_s)`` -> Result; on the board also the sim run and its drift report."""

    def go(steps, duration_s):
        res = target.run(steps, duration_s)
        if BOARD:
            out = Path(os.environ.get("HIL_OUT", tmp_path))
            out.mkdir(parents=True, exist_ok=True)
            name = request.node.name
            (out / f"{name}.board.log").write_text("\n".join(f"{t} {line}" for t, line in res.log) + "\n")
            sim_bin = os.environ.get("HIL_SIM_BIN")  # hil.sh builds lora-sim when it can
            if not sim_bin or not Path(sim_bin).exists():
                (out / f"drift-{name}.txt").write_text("no sim binary (HIL_SIM_BIN): no drift report\n")
                return res
            sim = hil.SimTarget(sim_bin, tmp_path / "sim-store").run(steps, duration_s)
            (out / f"{name}.sim.log").write_text("\n".join(f"{t} {line}" for t, line in sim.log) + "\n")
            diffs = hil.drift(sim, res)
            (out / f"drift-{name}.txt").write_text("\n".join(diffs) + "\n" if diffs else "no drift\n")
        return res

    return go


def actions():
    """A scenario that changes the board's state (a real join, uplinks): on the board only with HIL_ACTIONS=1."""
    return pytest.mark.skipif(
        BOARD and os.environ.get("HIL_ACTIONS") != "1", reason="changes the board: HIL_ACTIONS=1"
    )


def test_rx_settings_are_readable(run):
    """Read-only. `lora at <setting>` for the join-accept diagnosis: every RX window and join delay answers."""
    res = run(
        [(2 + 3 * i, f"lora at {name}") for i, name in enumerate(RX_SETTINGS)], 3 * len(RX_SETTINGS) + 5
    )
    for name in RX_SETTINGS:
        got = res.answers(f"AT+{name}=?")
        assert got and got[0][-1] == "OK" and got[0][0].startswith(f"AT+{name}="), (name, got)


def test_not_a_readable_setting_is_refused(run):
    """Read-only: a key is never read through the console."""
    res = run([(1, "lora at APPKEY")], 4)
    assert not res.answers("AT+APPKEY=?")
    assert [line for _, line in res.log if "lora: at: not a readable setting: APPKEY" in line]


def test_liveness_probe_while_idle(run):
    """Read-only. Within 6 min of idle a plain AT goes to the module, and the module answers it."""
    res = run([], 6 * 60 + 30)
    assert ["OK"] in res.answers("AT"), res.transcript[-10:]


@actions()
def test_rejoin_then_uplinks(run):
    """Changes the board (a real join and up to three uplinks). LoRa off and on again: the module is set up,
    joins, and the energy round goes out right after the join."""
    res = run([(1, "lora off"), (6, "lora on")], 150)
    cmds = [text for _, d, text in res.transcript if d == ">>"]
    joins = [i for i, c in enumerate(cmds) if c.startswith("AT+JOIN=")]
    assert joins, cmds
    events = [text for _, d, text in res.transcript if d == "<<"]
    assert "+EVT:JOINED" in events, events[-10:]
    assert any(c.startswith("AT+SEND=2:") for c in cmds[joins[0] :]), cmds
