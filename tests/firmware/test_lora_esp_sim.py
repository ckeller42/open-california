"""The ESP-only LoRa driver on simulated hardware (``cali_lora/lora_esp.c``, the sim tier).

``lora-sim`` builds the real, unchanged ``lora_esp.c`` (the RAK3172 AT driver, the LoRa task, the AW9523 bus
power) and cali_core's ``lora.c`` for the Linux host over a virtual-clock FreeRTOS (``firmware/host/sim``);
the fake RAK3172 and AW9523 of :mod:`tests.firmware.esp_sim` answer every UART line, pin change and I2C
write, and the fake AXP2101 feeds the battery reading through display.c's own read code
(``cali_display/axp2101_read.h``). A virtual hour runs in well under a second, and every run repeats exactly.

.. test:: LoRa driver on simulated hardware: join, uplinks, refusal retry, latched module, watchdog, bus power, supply dip
   :id: T_FW_LORA_ESP_SIM
   :links: R_FW_LORA, R_FW_BATTERY
"""

import pytest

from . import esp_sim
from .conftest import build_host

pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

MIN = 60_000
HOUR = 60 * MIN


@pytest.fixture
def sim(tmp_path):
    """``sim(board, until_ms)`` -> the board after a run that ended cleanly at ``until_ms``."""

    def go(board, until_ms):
        board.every(10_000, until_ms)  # a device.lora reading every 10 s
        esp_sim.run(build_host("lora-sim"), board, until_ms, tmp_path / "store")
        bad = [line for _, line in board.log if line.startswith("SIM ") and not line.startswith("SIM end")]
        assert board.rc == 0 and not bad, (board.rc, bad, board.log[-20:])
        return board

    (tmp_path / "store").mkdir()
    return go


def state_at(board, t_ms):
    """device.lora at the last reading at or before t_ms."""
    return [s for t, s in board.states if t <= t_ms][-1]


def sends(board, answer=None):
    return [(t, port) for t, port, _, a in board.rak.sends if answer is None or a == answer]


def test_switch_off_touches_no_pin(sim):
    b = sim(esp_sim.Board(lora_on=False), 2 * MIN)
    assert not [r for _, r in b.hw if r.split()[0] in ("UART", "GPIO", "PINS")]
    assert not [r for _, r in b.hw if r.startswith("I2C W 58")]  # only the display's probe of the AW9523
    assert b.states and all(lora is None for _, lora in b.states)  # device.lora only while exp_lora is on


def test_join_then_ports_2_3_4(sim):
    b = sim(esp_sim.Board(), 2 * MIN)
    cmds = [c for _, c in b.rak.cmds]
    assert cmds[0] == "AT" and "AT+NWM=?" in cmds and "AT+CLASS=C" in cmds
    assert cmds.index("AT+JOIN=1:0:8:1") < cmds.index(next(c for c in cmds if c.startswith("AT+SEND=")))
    assert [p for _, p in sends(b)] == [2, 3, 4]
    st = state_at(b, 2 * MIN)
    assert st["module"] == "present" and st["joined"] is True and st["uplinks"] == [1, 1, 1]
    assert st["dev_eui"] == "70B3D57ED0000001" and st["note"] is None


def test_refused_send_retries_once_after_120_s(sim):
    b = esp_sim.Board()
    b.rak.refuse = 1
    b = sim(b, 10 * MIN)
    tries = sends(b)
    refused = [(t, p) for t, p in sends(b, "AT_BUSY_ERROR")]
    assert refused and refused[0][1] == 2
    t0 = refused[0][0]
    after = [(t, p) for t, p in tries if t > t0]
    assert [p for _, p in after[:3]] == [2, 3, 4]  # ports 3/4 wait for the retry, then go with it
    assert 119_000 <= after[0][0] - t0 <= 125_000  # next_energy counts whole seconds
    assert not [p for t, p in tries if t < t0 and p in (3, 4)]
    assert "lora: port 2 refused" in "\n".join(line for _, line in b.log)


def test_second_refusal_in_a_row_waits_the_normal_interval(sim):
    b = esp_sim.Board()
    b.rak.refuse = 2
    b = sim(b, 6 * HOUR)
    (t1, _), (t2, _) = sends(b, "AT_BUSY_ERROR")[:2]
    assert 119_000 <= t2 - t1 <= 125_000
    t3 = next(t for t, _ in sends(b) if t > t2)
    assert t3 - t2 > HOUR  # the normal SF12 interval, not a second 120 s retry


def test_join_accept_never_received_backs_off(sim):
    b = esp_sim.Board()
    b.rak.join = "silent"
    b = sim(b, 20 * MIN)
    joins = [t for t, c in b.rak.cmds if c.startswith("AT+JOIN=")]
    gaps = [b2 - a for a, b2 in zip(joins, joins[1:])]
    # 30 s join wait + the backoff (60, 120, 240 s ...) + a few AT round trips
    assert len(gaps) >= 3
    for gap, backoff in zip(gaps, (60, 120, 240)):
        assert (30 + backoff) * 1000 <= gap <= (30 + backoff) * 1000 + 2000, gaps
    st = state_at(b, joins[1] - 1)
    assert st["joined"] is False and st["note"] == "join failed"
    assert not b.rak.sends


def test_latched_module_rst_fails_bus_power_cycle_cures(sim):
    b = esp_sim.Board()
    b.rak.latched = True
    b = sim(b, 3 * MIN)
    assert b.rak.cured, "the bus step 0/0 -> 1/1 with TX and RST released never came"
    cure = b.rak.cured[0]
    assert b.rak.resets >= 1  # the RST pulse came first and did not help
    assert b.logged("lora: module silent after reset: bus power cycle")
    on = [t for t, bits in b.bus_history if bits == (1, 1)]
    assert on[0] == cure
    off_since = max([0] + [t for t, bits in b.bus_history if bits == (0, 0) and t < cure])
    assert cure - off_since >= 5000  # BUS_OFF_MS
    assert [p for t, p in sends(b, "OK") if t > cure][:3] == [2, 3, 4]
    # on USB the steady state is 0/0 again afterwards (bus_follow_usb)
    assert b.aw.bus == (0, 0) and b.bus_history[-1][1] == (0, 0)


def test_module_silent_mid_run_watchdog_resets_and_cures(sim):
    """Latched after a dip while joined: the AT timeouts of the next port-2 rounds count up to
    CALI_LORA_SILENT_MAX (3), the watchdog resets, RST does not help, the bus power cycle does. At SF12 a
    round is about every 4.8 h and one round adds 2 (AT+DR=? and AT+SEND), so the reset comes in the
    second silent round: about 10 h after the dip."""
    b = esp_sim.Board()
    b.at(HOUR, lambda board: setattr(board.rak, "latched", True))
    b = sim(b, 12 * HOUR)
    assert sends(b, "OK")[0][0] < HOUR
    reset = b.logged("lora: module silent: reset")
    assert reset and reset[0] > HOUR
    assert b.rak.cured and b.rak.cured[0] > reset[0]
    assert b.logged("lora: module silent after reset: bus power cycle")
    assert [t for t, c in b.rak.cmds if t > b.rak.cured[0] and c.startswith("AT+JOIN=")], (
        "no join after the cure"
    )
    silent = [s["at_unanswered"] for t, s in b.states if HOUR < t < reset[0]]
    assert max(silent) >= 2
    st = state_at(b, 12 * HOUR)
    assert st["joined"] is True and st["at_unanswered"] == 0 and st["module"] == "present"


def test_aw9523_absent_no_abort_module_on_usb_works(sim):
    """Without the AW9523 the display does not start (its init probes 0x34 and 0x58), so the battery is never
    read, the supply stays unknown and bus_follow_usb() leaves the bus alone; the module on VBUS works."""
    b = sim(esp_sim.Board(aw9523=False), 3 * MIN)
    assert b.logged("display: unavailable (aw9523)")
    assert not [
        r for _, r in b.hw if r.startswith("I2C W 58")
    ]  # no board-package init: it would abort on the NACK
    assert b.battery[-1][1]["usb"] is None
    assert [p for _, p in sends(b, "OK")][:3] == [2, 3, 4]
    assert b.states[-1][0] >= 3 * MIN - 10_000  # still running at the end


def test_aw9523_absent_latched_module_stays_silent(sim):
    b = esp_sim.Board(aw9523=False)
    b.rak.latched = True
    b = sim(b, 25 * MIN)
    assert b.logged("lora: bus power off failed") and b.logged("lora: bus power on failed")
    assert not b.rak.cured and not b.rak.sends
    assert state_at(b, 25 * MIN)["module"] == "absent" and state_at(b, 25 * MIN)["note"] == "module silent"
    # the next check comes after SILENT_RETRY_S (600 s): an "AT" again after the wait
    ats = [t for t, c in b.rak.cmds if c == "AT"]
    assert any(later - first >= 600_000 for first, later in zip(ats, ats[1:]))


def test_bus_power_follows_usb(sim):
    b = esp_sim.Board(usb=True)
    b.at(3 * MIN, lambda board: board.set_usb(False))
    b.at(6 * MIN, lambda board: board.set_usb(True))
    b = sim(b, 9 * MIN)
    on_usb = [bits for t, bits in b.bus_history if t < 3 * MIN]
    assert not on_usb or on_usb[-1] == (0, 0)
    assert [bits for t, bits in b.bus_history if 3 * MIN <= t < 6 * MIN] == [(1, 1)]
    assert [bits for t, bits in b.bus_history if t >= 6 * MIN] == [(0, 0)]
    battery = next(t for t, bits in b.bus_history if bits == (1, 1))
    assert battery - 3 * MIN <= 1000  # the next pass of the LoRa task
    assert b.logged("lora: bus power on (battery)") and b.logged("lora: bus power off (usb)")


# ---- the AXP2101 (display.c's battery read, axp2101_read.h) ----


def test_battery_from_axp2101_registers(sim):
    b = esp_sim.Board()
    b.axp.mv, b.axp.pct, b.axp.charging = 3876, 64, True
    b = sim(b, 1 * MIN)
    assert b.battery[-1][1] == {"pct": 64, "mv": 3876, "usb": True, "charging": True, "runtime_min": None}
    assert b.axp.regs[0x30] & 0x01 and b.axp.regs[0x18] & 0x08  # the ADC and fuel-gauge enable bits


def test_axp2101_absent_battery_unknown_bus_untouched(sim):
    b = sim(esp_sim.Board(axp2101=False), 3 * MIN)
    assert b.logged("display: unavailable (axp2101)")
    assert b.battery[-1][1]["usb"] is None and not b.logged("lora: bus power")
    assert [p for _, p in sends(b, "OK")][:3] == [2, 3, 4]


def test_supply_dip_battery_keeps_the_module_powered(sim):
    """USB gone (the van's 12 V dip): on 0/0 the module loses its 5 V until the next battery read (every
    CALI_BATT_READ_MS) shows no VBUS and the bus goes to 1/1; from then on it keeps power, also when USB
    comes back (0/0 again, fed by VBUS). The restarted module answers the next send with
    AT_NO_NETWORK_JOINED, and the task joins again."""
    b = esp_sim.Board()
    unplug, plug = 3 * MIN, 6 * MIN
    b.at(unplug, lambda board: board.set_usb(False))
    b.at(plug, lambda board: board.set_usb(True))
    b = sim(b, 6 * HOUR)
    on = next(t for t, bits in b.bus_history if bits == (1, 1))
    assert unplug < on <= unplug + 10_000 + 1_000
    assert [bits for t, bits in b.bus_history if t > plug] == [(0, 0)]
    off = next(t for t, bits in b.bus_history if t > plug)
    assert plug < off <= plug + 10_000 + 1_000
    assert next(bat for t, bat in b.battery if unplug + 11_000 < t < plug)["usb"] is False
    assert sends(b, "AT_NO_NETWORK_JOINED"), "the restarted module never refused a send"
    joins = [t for t, c in b.rak.cmds if c.startswith("AT+JOIN=")]
    assert len(joins) == 2 and joins[1] > plug
    assert state_at(b, 6 * HOUR)["joined"] is True


def test_supply_dip_latches_the_module_power_cycle_cures(sim):
    """The 2026-11-22 incident: the dip leaves the module latched silent; back on USB the watchdog's reset
    does not help, the bus power cycle does, and the module joins again."""
    b = esp_sim.Board()
    b.rak.latch_on_dip = True
    b.at(HOUR, lambda board: board.set_usb(False))
    b.at(2 * HOUR, lambda board: board.set_usb(True))
    b = sim(b, 12 * HOUR)
    assert b.rak.latched is False and b.rak.cured and b.rak.cured[0] > 2 * HOUR
    assert b.logged("lora: module silent: reset") and b.logged(
        "lora: module silent after reset: bus power cycle"
    )
    joins = [t for t, c in b.rak.cmds if c.startswith("AT+JOIN=")]
    assert joins[-1] > b.rak.cured[0]
    assert state_at(b, 12 * HOUR)["joined"] is True


def test_power_loss_drops_the_modules_pending_events(sim):
    """USB gone with the bus at 0/0 while the join accept is still due (the boot's 0/0, LoRa not started yet
    on the bus): the module restarts and its scheduled +EVT:JOINED never reaches the firmware."""
    b = esp_sim.Board()
    b.rak.join_ms = 20_000

    def cut(board):
        board.aw.regs[0x02] &= ~0x02  # force 0/0 behind the firmware's back: no 5 V without VBUS
        board.aw.regs[0x03] &= ~0x80
        board.set_usb(False)

    joins = []
    b.at(5_000, lambda board: joins.append([t for t, c in board.rak.cmds if c.startswith("AT+JOIN=")]))
    b.at(6_000, cut)
    b = sim(b, 40_000)
    assert joins[0], "no join before the cut"
    assert not [line for t, line in b.log if "<< +EVT:JOINED" in line and t < 26_000]
