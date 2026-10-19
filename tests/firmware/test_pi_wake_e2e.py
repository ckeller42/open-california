"""Host tier: the Pi wake end to end — real NimBLE host, the Bumble fake unit (R_FW_PI_WAKE).

``cali-host --http`` pairs, reads and goes online; the host's GPIO line only logs (``gpio9: low`` /
``gpio9: released``). The fake unit's ignition (terminal 15, ``campingmode.Enable`` following it) rising
pulls the line low for 200 ms once; ignition off and on again inside a minute does not; the console's
``pi hold <ms>`` (the wiring test) holds it for that long. ``pi halt <ms>`` holds GPIO8 (the halt line,
R_FW_PI_HALT) the same way, and a wake waits for it.

.. test:: Pi wake on the host tier: the unit's ignition edge pulses GPIO9 once, the console holds it
   :id: T_FW_PI_WAKE_E2E
   :links: R_FW_PI_WAKE
"""

import time

import pytest

from .test_auto_camper_e2e import _tick
from .test_control_e2e import _online
from .test_web_e2e import get_json

pytestmark = [pytest.mark.xdist_group("firmware-host-build"), pytest.mark.linux_only]


@pytest.fixture(autouse=True)
def _no_crank_drop(monkeypatch):
    from tools import mock_unit

    monkeypatch.setattr(mock_unit, "CRANK_DROP_S", 0.0)  # the peripheral never models the drop's link loss


def _pulses(fw):
    return [s for s in fw.log if s.startswith("LOG pi-wake[")]


def test_ignition_edge_pulses_once_and_the_console_holds(host_fw, rec_unit, tmp_path):
    hu, _ = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    pw = get_json(fw, "/api/state")["device"]["pi_wake"]
    assert (pw["enabled"], pw["last_s"], pw["reason"], pw["halted"], pw["halt_reason"]) == (
        True,
        None,
        None,
        False,
        None,
    )
    hu.call(_tick, hu.unit)  # the mock's first tick only remembers terminal 15
    hu.call(_tick, hu.unit, {"TerminalOneFive": 1})
    fw.expect("LOG", lambda s: s == "gpio9: low", timeout=10)
    fw.expect("LOG", lambda s: s == "pi-wake[ignition]: GPIO9 low 200 ms", timeout=5)
    fw.expect("LOG", lambda s: s == "gpio9: released", timeout=5)
    assert get_json(fw, "/api/state")["device"]["pi_wake"]["reason"] == "ignition"
    hu.call(_tick, hu.unit, {"TerminalOneFive": 0})
    time.sleep(1)
    hu.call(_tick, hu.unit, {"TerminalOneFive": 1})
    fw.expect("LOG", lambda s: s.startswith("pi-wake: ignition edge within 60 s"), timeout=10)
    assert len(_pulses(fw)) == 1
    fw.send("pi hold 1500")
    fw.expect("LOG", lambda s: s == "pi-wake[manual]: GPIO9 low 1500 ms", timeout=5)
    t0 = time.monotonic()
    fw.expect("LOG", lambda s: s == "gpio9: released", timeout=5)
    assert time.monotonic() - t0 >= 1.2


def test_console_halt_pulses_gpio8_and_refuses_a_wake_while_low(host_fw, rec_unit, tmp_path):
    """``pi halt 1500`` (the halt wiring test) holds GPIO8 low; a wake meanwhile is busy (R_FW_PI_HALT).

    .. test:: Pi halt on the host tier: the console holds GPIO8, a wake waits for its release
       :id: T_FW_PI_HALT_E2E
       :links: R_FW_PI_HALT
    """
    hu, _ = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    fw.send("pi halt 1500")
    fw.expect("LOG", lambda s: s == "gpio8: low", timeout=5)
    fw.expect("LOG", lambda s: s == "pi-halt[manual]: GPIO8 low 1500 ms", timeout=5)
    fw.send("pi wake")
    fw.expect("LOG", lambda s: s == "pi-wake: busy", timeout=5)
    fw.expect("LOG", lambda s: s == "gpio8: released", timeout=5)
    pw = get_json(fw, "/api/state")["device"]["pi_wake"]
    assert (pw["halted"], pw["halt_reason"], pw["halt_in_s"]) == (True, "manual", None)
