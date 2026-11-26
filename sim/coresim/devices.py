"""Generic device fakes of the M5Stack CoreS3 and its modules.

* :class:`Aw9523` — the IO expander (I2C 0x58): a register file, output 0x02/0x03, direction 0x04/0x05
  (0 = output), 0x7F soft reset. :attr:`Aw9523.bus` = (P0_1 BUS_OUT_EN, P1_7 BOOST_EN), the M-Bus 5 V.
* :class:`Axp2101` — the power chip (I2C 0x34): 0x00 (bit 5 VBUS good, bit 3 battery present), 0x01 (bits
  6:5 charge state), 0x34/0x35 battery mV (14 bit), 0xA4 fuel-gauge percent, and the enable registers 0x18
  (bit 3 fuel gauge) and 0x30 (bit 0 battery voltage ADC).
* :class:`Rak3172` — the RAK3172 LoRaWAN module (RUI3 AT firmware) on a UART: ``AT``, ``AT+<X>=?``,
  ``AT+<X>=<v>``, ``AT+JOIN``, ``AT+SEND``, ``+EVT`` events with a delay, and these fault modes: refused
  sends (``AT_BUSY_ERROR``), a join accept that fails or never comes, and *latched silent* (no answer at
  all; RST does not help; only a power-up of the bus from 0/0 to 1/1 while its TX and RST pins are
  released clears it).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Timings, from the van capture sim/tests/data/rak3172-van-sf12-joined.log (SF12, EU868):
REPLY_MS = 1  # a query or a setting: the answer within the console's 1 ms resolution
SEND_REPLY_MS = 20  # AT+SEND -> OK: 20-21 ms in all 5 sends
TX_DONE_MS = 8800  # OK -> +EVT:TX_DONE: 8.19-8.86 s (RX1 + RX2 windows after the uplink at SF12)
JOIN_ACCEPT_MS = (
    5000  # AT+JOIN OK -> +EVT:JOINED: the join accept's RX1 delay (5 s, EU868 JOIN_ACCEPT_DELAY1)
)


class Unmodelled(AssertionError):
    """The firmware used something this fake does not model: extend the fake (with its evidence), never
    let it answer with a made-up default."""


class Aw9523:
    addr = 0x58
    # Registers this model has (AW9523B datasheet): input 0x00/0x01, output 0x02/0x03, direction 0x04/0x05,
    # global control 0x11 (P0 push-pull bit 4), soft reset 0x7F.
    READ = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x11}
    WRITE = {0x02, 0x03, 0x04, 0x05, 0x11, 0x7F}
    # The real board's output registers before any driver write: out0 0x11, out1 0x03, so both bus bits
    # 0 (the owner's register probe on the bench CoreS3, 2026-11-22). Direction 0x00 is the datasheet's
    # reset value (verified only through the bus bits the probe read).
    POWER_ON = {0x02: 0x11, 0x03: 0x03}

    def __init__(self, present=True):
        self.present = present
        self.regs = [0] * 256
        for r, v in self.POWER_ON.items():
            self.regs[r] = v

    def write(self, data: bytes):
        reg = data[0]
        regs = range(reg, reg + max(1, len(data) - 1))
        if reg not in self.WRITE or (reg != 0x7F and not set(regs) <= self.WRITE):
            raise Unmodelled(f"AW9523 write {data.hex()}")
        if reg == 0x7F:
            # soft reset: every pin an output driving 0 (assumed: the datasheet's output reset value depends
            # on the AD pins; the board package's init writes output and direction right after it anyway)
            self.regs = [0] * 256
            return
        for i, b in enumerate(data[1:]):
            self.regs[reg + i] = b

    def read(self, reg: int, n: int) -> bytes:
        if not set(range(reg, reg + n)) <= self.READ:
            raise Unmodelled(f"AW9523 read {reg:#04x}+{n}")
        return bytes(self.regs[reg + i] for i in range(n))

    def out(self, port, bit):
        """The level a pin drives: its output bit, 0 while it is an input (direction bit 1)."""
        driven = not (self.regs[0x04 + port] >> bit & 1)
        return int(driven and self.regs[0x02 + port] >> bit & 1)

    @property
    def bus(self) -> tuple[int, int]:
        """(BUS_OUT_EN P0_1, BOOST_EN P1_7)."""
        return self.out(0, 1), self.out(1, 7)


@dataclass
class Axp2101:
    """Register meanings: M5Unified ``AXP2101_Class.inl`` and XPowersLib (the AXP2101 datasheet's map).
    The power-on values of 0x18 and 0x30 are assumed 0 (unverified: no saved register dump of the board)."""

    addr = 0x34
    READ = {0x00, 0x01, 0x18, 0x30, 0x34, 0x35, 0xA4}
    WRITE = {0x18, 0x30}
    present: bool = True
    vbus: bool = True
    battery: bool = True
    mv: int = 4012
    pct: int = 87
    charging: bool = False
    regs: dict = field(default_factory=lambda: {0x18: 0x00, 0x30: 0x00})

    def read(self, reg, n):
        if not set(range(reg, reg + n)) <= self.READ:
            raise Unmodelled(f"AXP2101 read {reg:#04x}+{n}")
        live = {
            0x00: (0x20 if self.vbus else 0) | (0x08 if self.battery else 0),
            0x01: (0b01 if self.charging else 0b10) << 5,
            0x34: self.mv >> 8 & 0x3F,
            0x35: self.mv & 0xFF,
            0xA4: self.pct,
        }
        return bytes(live.get(r, self.regs.get(r, 0)) for r in range(reg, reg + n))

    def write(self, data: bytes):
        if not set(range(data[0], data[0] + len(data) - 1)) <= self.WRITE:
            raise Unmodelled(f"AXP2101 write {data.hex()}")
        for i, b in enumerate(data[1:]):
            self.regs[data[0] + i] = b


@dataclass
class Rak3172:
    """``powered()`` says whether the module has its 5 V now (the board decides)."""

    powered: object = lambda: True
    tx_pin: int = 43  # the CoreS3's M-Bus wiring of the M5 LoRaWAN module: TX G43, RX G18, RST G5
    rst_pin: int = 5
    # Fault modes, each from a real incident:
    latched: bool = False  # silent after a supply dip; RST and USB cuts did not help, the bus 0/0 -> 1/1 did (bench 2026-11-22)
    latch_on_dip: bool = (
        False  # a power loss leaves it latched silent (the same incident; van 12 V dip 2026-11-24)
    )
    refuse: int = 0  # the next N AT+SEND answer AT_BUSY_ERROR (van log, the refused port-2 sends behind #566)
    join: str = "ok"  # ok | fail (JOIN_FAILED_RX_TIMEOUT) | silent: no accept ever (six lost join accepts, 2026-11-26)
    join_ms: int = JOIN_ACCEPT_MS
    joined: bool = (
        False  # joined from the start (a test's shortcut); a JOIN joins only once its accept is due
    )
    join_at: int | None = None  # t_ms when the pending join's accept arrives
    on_restart: object = lambda: None  # the board drops the module's pending output (sim_hw.c RXCLEAR)
    in_reset: bool = False
    tx_released: bool = False
    rst_released: bool = False
    resets: int = 0
    power_losses: int = 0  # times its 5 V went away
    cured: list = field(default_factory=list)  # t_ms of each latch cure
    cmds: list = field(default_factory=list)  # (t_ms, line) every line written to it, answered or not
    sends: list = field(default_factory=list)  # (t_ms, port, hex, answer)
    values: dict = field(
        default_factory=lambda: {
            "NWM": "1",
            "DR": "0",
            "DEVEUI": "AC1F09FFFE000001",
            "APPEUI": "0000000000000000",
            "APPKEY": "00000000000000000000000000000000",
            "BAND": "4",
            "NJM": "1",
            "CFM": "0",
            "CLASS": "A",
            "ADR": "1",
        }
    )

    def alive(self):
        return self.powered() and not self.latched and not self.in_reset

    def is_joined(self, t):
        return self.joined or (self.join_at is not None and t >= self.join_at)

    def reboot(self):
        """Power-up or reset: no join, and nothing it had scheduled to send comes any more."""
        self.joined = False
        self.join_at = None
        self.on_restart()

    def lost_power(self):
        self.power_losses += 1
        self.reboot()  # the module starts over when its 5 V comes back
        if self.latch_on_dip:
            self.latched = True

    def uart(self, t, line):
        """One line from the host; returns ``EV`` answer lines."""
        self.cmds.append((t, line))
        if not self.alive():
            return []

        def rx(text, d=REPLY_MS):
            return f"EV {d} RX {text}"

        if line == "AT":
            return [rx("OK")]
        if line.startswith("AT+") and line.endswith("=?"):
            name = line[3:-2]
            if name not in self.values:
                raise Unmodelled(f"RAK3172 query {line}")
            return [rx(f"AT+{name}={self.values[name]}"), rx("OK")]
        if line.startswith("AT+JOIN="):
            out = [rx("OK")]
            if self.join == "ok":
                self.join_at = t + REPLY_MS + self.join_ms
                out.append(rx("+EVT:JOINED", REPLY_MS + self.join_ms))
            elif self.join == "fail":
                out.append(rx("+EVT:JOIN_FAILED_RX_TIMEOUT", self.join_ms))
            return out
        if line.startswith("AT+SEND="):
            port, _, payload = line[8:].partition(":")
            if not self.is_joined(t):
                answer = "AT_NO_NETWORK_JOINED"
            elif self.refuse:
                self.refuse -= 1
                answer = "AT_BUSY_ERROR"
            else:
                answer = "OK"
            self.sends.append((t, int(port), payload, answer))
            done = [rx("+EVT:TX_DONE", SEND_REPLY_MS + TX_DONE_MS)] if answer == "OK" else []
            return [rx(answer, SEND_REPLY_MS)] + done
        name, eq, value = line[3:].partition("=")
        if line.startswith("AT+") and eq and name in self.SETTINGS:
            self.values[name] = value
            return [rx("OK")]
        raise Unmodelled(f"RAK3172 command {line}")

    # The settings this model takes (RUI3 AT command manual): AT+<name>=<value> -> OK, AT+<name>=? -> value.
    SETTINGS = ("NWM", "BAND", "NJM", "CFM", "CLASS", "ADR", "DEVEUI", "APPEUI", "APPKEY", "DR")

    def gpio(self, t, pin, what, value):
        if what == "DIR" and pin == self.tx_pin:
            self.tx_released = value == "in"
        elif what == "DIR" and pin == self.rst_pin:
            self.rst_released = value == "in"
        elif what == "LVL" and pin == self.rst_pin and not self.rst_released:
            if value == "0":
                self.in_reset = True
            elif self.in_reset:
                self.in_reset = False
                self.resets += 1
                if not self.latched:
                    self.reboot()

    def pins(self, tx):
        if tx == self.tx_pin:
            self.tx_released = False  # the UART drives TX again

    def bus_changed(self, t, before, after, powered_before):
        if before == (0, 0) and after == (1, 1) and self.tx_released and self.rst_released and self.latched:
            self.latched = False
            self.cured.append(t)
            self.reboot()
        if powered_before and not self.powered():
            self.lost_power()
