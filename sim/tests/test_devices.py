"""Unit tests of the device fakes: register maps, the AT command set, the fault modes, strictness."""

import pytest
from coresim.devices import JOIN_ACCEPT_MS, SEND_REPLY_MS, TX_DONE_MS, Aw9523, Rak3172, Unmodelled


def test_aw9523_power_on_state_is_the_probed_board():
    aw = Aw9523()
    assert aw.read(0x02, 2) == bytes([0x11, 0x03]) and aw.bus == (0, 0)


def test_aw9523_bus_bits_need_output_direction():
    aw = Aw9523()
    aw.write(bytes([0x02, 0x02, 0x80]))
    assert aw.bus == (1, 1)
    aw.write(bytes([0x04, 0x02, 0x00]))  # P0_1 an input: it drives nothing
    assert aw.bus == (0, 1)
    aw.write(bytes([0x7F, 0x00]))
    assert aw.bus == (0, 0) and aw.read(0x04, 2) == b"\0\0"


@pytest.mark.parametrize("data", [bytes([0x20, 0x00]), bytes([0x05, 0x00, 0x00])])
def test_aw9523_unmodelled_write_fails_loudly(data):
    with pytest.raises(Unmodelled):
        Aw9523().write(data)


def test_aw9523_unmodelled_read_fails_loudly():
    with pytest.raises(Unmodelled):
        Aw9523().read(0x10, 1)


def ev(lines):
    """EV answer lines -> [(delay_ms, text)]."""
    return [(int(x.split()[1]), x.split(" ", 3)[3]) for x in lines]


def test_rak3172_queries_settings_join_send():
    r = Rak3172()
    assert ev(r.uart(0, "AT")) == [(1, "OK")]
    assert ev(r.uart(0, "AT+DR=?")) == [(1, "AT+DR=0"), (1, "OK")]
    assert ev(r.uart(0, "AT+CLASS=C")) == [(1, "OK")] and r.values["CLASS"] == "C"
    assert ev(r.uart(0, "AT+SEND=2:00")) == [(SEND_REPLY_MS, "AT_NO_NETWORK_JOINED")]
    assert ev(r.uart(0, "AT+JOIN=1:0:8:1")) == [(1, "OK"), (1 + JOIN_ACCEPT_MS, "+EVT:JOINED")]
    assert ev(r.uart(2, "AT+SEND=2:00"))[0][1] == "AT_NO_NETWORK_JOINED"  # the accept is not there yet
    t = 1 + JOIN_ACCEPT_MS
    assert ev(r.uart(t, "AT+SEND=2:00")) == [
        (SEND_REPLY_MS, "OK"),
        (SEND_REPLY_MS + TX_DONE_MS, "+EVT:TX_DONE"),
    ]
    assert [a for *_, a in r.sends] == ["AT_NO_NETWORK_JOINED", "AT_NO_NETWORK_JOINED", "OK"]


@pytest.mark.parametrize("line", ["AT+FOO=1", "AT+FOO=?", "ATZ", "AT+RX1DL=?"])
def test_rak3172_unmodelled_command_fails_loudly(line):
    with pytest.raises(Unmodelled):
        Rak3172().uart(0, line)


def test_rak3172_refuse_and_join_modes():
    r = Rak3172(refuse=1, joined=True)
    assert ev(r.uart(0, "AT+SEND=2:00"))[0][1] == "AT_BUSY_ERROR"
    assert ev(r.uart(0, "AT+SEND=2:00"))[0][1] == "OK"
    assert [t for _, t in ev(Rak3172(join="fail").uart(0, "AT+JOIN=1:0:8:1"))] == [
        "OK",
        "+EVT:JOIN_FAILED_RX_TIMEOUT",
    ]
    assert [t for _, t in ev(Rak3172(join="silent").uart(0, "AT+JOIN=1:0:8:1"))] == ["OK"]


def test_rak3172_latch_rst_does_not_help_bus_step_with_released_pins_does():
    r = Rak3172(latched=True)
    r.gpio(0, 5, "LVL", "0")
    r.gpio(0, 5, "LVL", "1")
    assert r.resets == 1 and r.uart(0, "AT") == []
    r.bus_changed(1, (0, 0), (1, 1), True)  # pins still driven: no cure
    assert r.latched
    r.gpio(2, 43, "DIR", "in")
    r.gpio(2, 5, "DIR", "in")
    r.bus_changed(3, (1, 1), (1, 1), True)  # no 0/0 -> 1/1 step: no cure
    r.bus_changed(3, (0, 1), (1, 1), True)  # one bit stayed on: no cure either
    r.bus_changed(3, (1, 0), (1, 1), True)
    assert r.latched
    r.bus_changed(4, (0, 0), (1, 1), True)
    assert not r.latched and r.cured == [4] and ev(r.uart(5, "AT")) == [(1, "OK")]


def test_rak3172_latch_on_dip():
    up = [True]
    r = Rak3172(powered=lambda: up[0], latch_on_dip=True, joined=True)
    up[0] = False
    r.bus_changed(0, (0, 0), (0, 0), True)
    assert r.latched and not r.joined


def test_axp2101_status_registers():
    from coresim.devices import Axp2101

    a = Axp2101(vbus=True, battery=True, charging=True, mv=3876, pct=64)
    assert (
        a.read(0x00, 2) == bytes([0x28, 0x20])
        and a.read(0x34, 2) == bytes([0x0F, 0x24])
        and a.read(0xA4, 1) == b"\x40"
    )
    a.vbus, a.battery, a.charging = False, False, False
    assert a.read(0x00, 2) == bytes([0x00, 0x40])
    a.write(bytes([0x30, 0x01]))
    assert a.read(0x30, 1) == b"\x01"


@pytest.mark.parametrize("op", ["read", "write"])
def test_axp2101_unmodelled_register_fails_loudly(op):
    from coresim.devices import Axp2101

    with pytest.raises(Unmodelled):
        Axp2101().read(0x90, 1) if op == "read" else Axp2101().write(bytes([0x90, 0xBF]))


def test_rak3172_restart_drops_its_pending_output():
    restarts = []
    r = Rak3172(on_restart=lambda: restarts.append(1))
    r.uart(0, "AT+JOIN=1:0:8:1")
    r.gpio(1, 5, "LVL", "0")
    r.gpio(2, 5, "LVL", "1")  # RST: the board sends RXCLEAR, the JOINED never comes
    assert restarts == [1] and not r.is_joined(10_000)
