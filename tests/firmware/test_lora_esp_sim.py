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

pytestmark = pytest.mark.linux_only

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
    assert (
        on[0] < cure and cure in on
    )  # 1/1 at the LoRa start (TX and RST driven: no cure), then the cycle's step
    off_since = max([0] + [t for t, bits in b.bus_history if bits == (0, 0) and t < cure])
    assert cure - off_since >= 5000  # BUS_OFF_MS
    assert [p for t, p in sends(b, "OK") if t > cure][:3] == [2, 3, 4]
    assert b.aw.bus == (1, 1)  # and it stays on: the steady state in every supply state


def test_module_silent_mid_run_watchdog_resets_and_cures(sim):
    """Latched while joined: the idle liveness probe (a plain AT every LIVENESS_S, every PROBE_RETRY_S
    while unanswered) counts 3 unanswered ATs within minutes, the watchdog resets, RST does not help, the
    bus power cycle does, the task joins again and sends port 2 soon after. Before the probe the
    timeouts came only with the port-2 rounds (about every 4.8 h at SF12): the reset came about 10 h
    after the latch."""
    b = esp_sim.Board()
    b.at(HOUR, lambda board: setattr(board.rak, "latched", True))
    b = sim(b, 3 * HOUR)
    assert sends(b, "OK")[0][0] < HOUR
    reset = b.logged("lora: module silent: reset")
    assert reset and HOUR < reset[0] <= HOUR + 5 * MIN, reset
    assert b.rak.cured and b.rak.cured[0] > reset[0]
    assert b.logged("lora: module silent after reset: bus power cycle")
    joins = [t for t, c in b.rak.cmds if t > b.rak.cured[0] and c.startswith("AT+JOIN=")]
    assert joins, "no join after the cure"
    after = [(t, p) for t, p in sends(b, "OK") if t > joins[0]]
    assert after and after[0][1] == 2 and after[0][0] - joins[0] <= MIN
    st = state_at(b, 3 * HOUR)
    assert st["joined"] is True and st["at_unanswered"] == 0 and st["module"] == "present"


def test_liveness_probe_is_cheap(sim):
    """The probe is a plain AT (no airtime): one per LIVENESS_S while the module answers."""
    b = sim(esp_sim.Board(), 2 * HOUR)
    first_send = sends(b)[0][0]
    probes = [t for t, c in b.rak.cmds if c == "AT" and t > first_send]
    assert 2 * HOUR / (5 * MIN) - 2 <= len(probes) <= 2 * HOUR / (5 * MIN) + 1, len(probes)
    assert len(sends(b)) == 3  # no extra uplink


def test_aw9523_absent_no_abort_module_on_usb_works(sim):
    """Without the AW9523 the display does not start (its init probes 0x34 and 0x58), so the battery is never
    read; the LoRa start's bus power on fails at the probe (logged, no abort); the module on VBUS works."""
    b = sim(esp_sim.Board(aw9523=False), 3 * MIN)
    assert b.logged("display: unavailable (aw9523)") and b.logged("lora: bus power on failed")
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


def test_bus_power_steady_on_in_every_supply_state(sim):
    """The LoRa start sets both bus bits to 1 and they stay there on USB and on the battery: unplugging USB
    never takes the module's 5 V away (with the bits following USB it lost them until the next battery
    read, up to CALI_BATT_READ_MS)."""
    b = esp_sim.Board(usb=True)
    b.at(3 * MIN, lambda board: board.set_usb(False))
    b.at(6 * MIN, lambda board: board.set_usb(True))
    b = sim(b, 9 * MIN)
    assert [bits for _, bits in b.bus_history] == [(1, 1)]
    assert b.bus_history[0][0] < sends(b)[0][0]  # on before the first uplink
    assert b.rak.power_losses == 0
    assert b.logged("lora: bus power on")


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
    assert b.battery[-1][1]["usb"] is None and b.aw.bus == (1, 1)  # the bus does not need the supply state
    assert [p for _, p in sends(b, "OK")][:3] == [2, 3, 4]


def test_supply_dip_module_keeps_its_5_v(sim):
    """USB gone (the van's 12 V dip) and back: the module keeps its 5 V throughout (1/1 from the boost),
    stays joined and never has to join again."""
    b = esp_sim.Board()
    unplug, plug = 3 * MIN, 6 * MIN
    b.at(unplug, lambda board: board.set_usb(False))
    b.at(plug, lambda board: board.set_usb(True))
    b = sim(b, 6 * HOUR)
    assert next(bat for t, bat in b.battery if unplug + 11_000 < t < plug)["usb"] is False
    assert b.rak.power_losses == 0 and not sends(b, "AT_NO_NETWORK_JOINED")
    assert len([c for _, c in b.rak.cmds if c.startswith("AT+JOIN=")]) == 1
    assert state_at(b, 6 * HOUR)["joined"] is True


def test_rejoin_sends_port_2_and_4_soon(sim):
    """The module lost its join (it restarted on its own): the next send gets AT_NO_NETWORK_JOINED, the
    task joins again and sends port 2 (and the daily port 4) right after the join, as after the first
    one, instead of waiting for the old schedule (about 4.8 h at SF12)."""
    b = esp_sim.Board()
    b.at(HOUR, lambda board: board.rak.reboot())
    b = sim(b, 8 * HOUR)
    refused = sends(b, "AT_NO_NETWORK_JOINED")
    assert refused
    joins = [t for t, c in b.rak.cmds if c.startswith("AT+JOIN=") and t > refused[0][0]]
    assert joins
    after = [(t, p) for t, p in sends(b, "OK") if t > joins[0]]
    assert [p for _, p in after[:3]] in ([2, 3, 4], [2, 4]), after[:3]
    assert after[0][0] - joins[0] <= MIN


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


def test_bus_power_on_is_retried_until_it_works(sim):
    """The AW9523 does not answer at the LoRa start (logged, no abort) and answers 2 min later: the bus goes to
    1/1 at the next retry (every 60 s), so the module keeps its 5 V without USB from then on."""
    b = esp_sim.Board(aw9523=False)
    b.at(2 * MIN, lambda board: setattr(board.aw, "present", True))
    b = sim(b, 5 * MIN)
    assert b.logged("lora: bus power on failed")
    on = [t for t, bits in b.bus_history if bits == (1, 1)]
    assert on and 2 * MIN <= on[0] <= 3 * MIN + 2_000, on
    assert b.logged("lora: bus power on")


# ---- the console's read-only register dump (cali_display/i2c_dump.c) ----


def test_i2c_dump_reads_only_the_listed_registers(sim):
    b = esp_sim.Board()
    for i, line in enumerate(["i2c dump 0x58", "i2c dump 0x34", "i2c dump 0x23"]):
        b.at(2 * MIN + i * 1000, lambda board, line=line: board.emit(f"EV 0 CONSOLE {line}"))
    b = sim(b, 3 * MIN)
    lines = [line for _, line in b.log if line.startswith("LOG i2c: ")]
    aw = [x for x in lines if x.startswith("LOG i2c: 0x58 reg")]
    axp = [x for x in lines if x.startswith("LOG i2c: 0x34 reg")]
    assert len(aw) == 12 and "LOG i2c: 0x58 reg 0x10 = 0x23" in aw  # the AW9523B's ID
    assert "LOG i2c: 0x58 reg 0x02 = 0x02" in aw and "LOG i2c: 0x58 reg 0x03 = 0x80" in aw  # the bus bits 1/1
    assert len(axp) == 14 and "LOG i2c: 0x34 reg 0x00 = 0x28" in axp  # VBUS good + battery present
    assert any("usage: i2c dump 0x58|0x34" in x for x in lines)
    dump = [r for t, r in b.hw if t >= 2 * MIN and r.startswith("I2C")]
    assert dump and not [r for r in dump if r.startswith("I2C W")]  # reads only
    assert not [r for r in dump if r.startswith("I2C R 58 7f")]


def test_i2c_dump_of_a_missing_chip(sim):
    b = esp_sim.Board(aw9523=False)
    b.at(MIN, lambda board: board.emit("EV 0 CONSOLE i2c dump 0x58"))
    b = sim(b, 2 * MIN)
    assert b.logged("i2c: 0x58 does not answer")


def test_lora_at_is_answered_during_the_join_backoff(sim):
    """The field case: the join fails (no accept), and the RX settings are needed exactly then. A `lora at`
    list queued during the backoff wait is answered within seconds, between joins (never during an AT)."""
    b = esp_sim.Board()
    b.rak.join = "fail"
    b.rak.join_ms = 6000
    asked = 50_000  # the first join fails at ~8 s, its 60 s backoff runs until ~68 s
    b.at(asked, lambda board: board.emit("EV 0 CONSOLE lora at RX1DL,RX2DL,RX2DR,JN1DL"))
    b = sim(b, 2 * MIN)
    for name in ("RX1DL", "RX2DL", "RX2DR", "JN1DL"):
        t = [t for t, c in b.rak.cmds if c == f"AT+{name}=?"]
        assert t and asked <= t[0] <= asked + 10_000, (name, t)
    joins = [t for t, c in b.rak.cmds if c.startswith("AT+JOIN=")]
    assert joins[0] < asked < joins[1]  # it was the backoff, not a pass of the main loop


def test_lora_at_list_is_checked_whole(sim):
    b = esp_sim.Board()
    b.at(MIN, lambda board: board.emit("EV 0 CONSOLE lora at RX1DL,APPKEY"))
    b = sim(b, 2 * MIN)
    assert b.logged("lora: at: not a readable setting: APPKEY")
    assert "AT+RX1DL=?" not in [
        c for _, c in b.rak.cmds
    ]  # nothing of a list with a bad name (apply_keys reads APPKEY itself)


def test_lora_at_answers_the_hil_diag_list(sim):
    """The whole list tools/esplab/hil.sh diag sends (11 names, longer than the console's 23-character token)."""
    names = "RX1DL,RX2DL,RX2DR,RX2FQ,JN1DL,JN2DL,BAND,CLASS,ADR,DR,NJS"
    b = esp_sim.Board()
    b.at(MIN, lambda board: board.emit(f"EV 0 CONSOLE lora at {names}"))
    b = sim(b, 2 * MIN)
    asked = [c for t, c in b.rak.cmds if t >= MIN and c.endswith("=?")]
    assert [f"AT+{n}=?" for n in names.split(",")] == [c for c in asked if c[3:-2] in names.split(",")][:11]
