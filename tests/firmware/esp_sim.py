"""Hardware fakes for the simulated-hardware tier (``firmware/host/sim``, docs/firmware.md "Sim").

The firmware binary (``lora-sim``) runs the real ESP-only driver on a virtual clock and asks these fakes,
in lockstep, for every pin, UART and I2C action (protocol: ``firmware/host/sim/sim_hw.c``). The virtual
clock stands still while a fake answers, so a run is deterministic and a 600 s wait costs no wall time.

* :class:`Aw9523` — the IO expander at 0x58, a register file (output 0x02/0x03, direction 0x04/0x05,
  0 = output; 0x7F = soft reset). Its P0_1 (BUS_OUT_EN) and P1_7 (BOOST_EN) feed the M-Bus 5 V.
* :class:`Rak3172` — the LoRaWAN module on UART1: the AT commands ``lora_esp.c`` uses, ``+EVT`` events with
  a delay, refusals (``AT_BUSY_ERROR``), a join accept that never comes, and the "latched silent" state of
  the 2026-11-22 bench: after a supply dip it answers nothing, RST does not help, only the bus step from
  0/0 to 1/1 while its TX and RST pins are released (inputs) clears it.
* :class:`Board` — the wiring: the module has power on USB (VBUS) or with both bus bits 1 (the boost).
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field

AW9523 = 0x58
TX, RX, RST = 43, 18, 5
REPLY_MS = 5  # the module's answer to a command
TX_DONE_MS = 2000  # AT+SEND -> +EVT:TX_DONE
KEYS = "lora keys 70B3D57ED0000001 0000000000000001 000102030405060708090A0B0C0D0E0F"


class Aw9523:
    def __init__(self, present=True):
        self.present = present
        self.regs = [0] * 256
        self.history: list[tuple[int, tuple[int, int]]] = []  # (t_ms, bus bits) after every change

    def write(self, data: bytes):
        reg = data[0]
        if reg == 0x7F:  # soft reset: outputs low, every pin an output (this model's defaults)
            self.regs = [0] * 256
            return
        for i, b in enumerate(data[1:]):
            self.regs[(reg + i) & 0xFF] = b

    def _out(self, port, bit):
        driven = not (self.regs[0x04 + port] >> bit & 1)  # direction bit 0 = output
        return int(driven and self.regs[0x02 + port] >> bit & 1)

    @property
    def bus(self) -> tuple[int, int]:
        """(BUS_OUT_EN P0_1, BOOST_EN P1_7)."""
        return self._out(0, 1), self._out(1, 7)


@dataclass
class Rak3172:
    board: Board
    latched: bool = False
    refuse: int = 0  # the next N AT+SEND answer AT_BUSY_ERROR
    join: str = "ok"  # ok | fail (JOIN_FAILED_RX_TIMEOUT) | silent (no accept ever)
    join_ms: int = 6000
    joined: bool = False
    in_reset: bool = False
    tx_released: bool = False
    rst_released: bool = False
    resets: int = 0
    cured: list = field(default_factory=list)  # t_ms of each latch cure
    cmds: list = field(default_factory=list)  # (t_ms, line) every line the firmware wrote, answered or not
    sends: list = field(default_factory=list)  # (t_ms, port, hex, answer)
    values: dict = field(
        default_factory=lambda: {
            "NWM": "1",
            "DR": "0",
            "DEVEUI": "AC1F09FFFE000001",
            "APPEUI": "0000000000000000",
            "APPKEY": "00000000000000000000000000000000",
        }
    )

    def alive(self):
        return self.board.powered() and not self.latched and not self.in_reset

    def reboot(self):
        self.joined = False

    def uart(self, t, line):
        self.cmds.append((t, line))
        if not self.alive():
            return []
        rx = lambda text, d=REPLY_MS: f"EV {d} RX {text}"  # noqa: E731
        if line == "AT":
            return [rx("OK")]
        if line.startswith("AT+") and line.endswith("=?"):
            name = line[3:-2]
            return [rx(f"AT+{name}={self.values.get(name, '0')}"), rx("OK")]
        if line.startswith("AT+JOIN="):
            out = [rx("OK")]
            if self.join == "ok":
                self.joined = True
                out.append(rx("+EVT:JOINED", self.join_ms))
            elif self.join == "fail":
                out.append(rx("+EVT:JOIN_FAILED_RX_TIMEOUT", self.join_ms))
            return out
        if line.startswith("AT+SEND="):
            port, _, payload = line[8:].partition(":")
            if not self.joined:
                answer = "AT_NO_NETWORK_JOINED"
            elif self.refuse:
                self.refuse -= 1
                answer = "AT_BUSY_ERROR"
            else:
                answer = "OK"
            self.sends.append((t, int(port), payload, answer))
            return [rx(answer)] + ([rx("+EVT:TX_DONE", TX_DONE_MS)] if answer == "OK" else [])
        if line.startswith("AT+") and "=" in line:
            name, _, value = line[3:].partition("=")
            self.values[name] = value
            return [rx("OK")]
        return [rx("AT_ERROR")]

    def gpio(self, t, pin, what, value):
        if what == "DIR" and pin == TX:
            self.tx_released = value == "in"
        elif what == "DIR" and pin == RST:
            self.rst_released = value == "in"
        elif what == "LVL" and pin == RST and not self.rst_released:
            if value == "0":
                self.in_reset = True
            elif self.in_reset:
                self.in_reset = False
                self.resets += 1
                if not self.latched:
                    self.reboot()

    def pins(self, tx):
        if tx == TX:
            self.tx_released = False  # the UART drives TX again

    def bus_changed(self, t, before, after, powered_before):
        if before != (1, 1) and after == (1, 1) and self.tx_released and self.rst_released and self.latched:
            self.latched = False
            self.cured.append(t)
            self.reboot()
        if powered_before and not self.board.powered():
            self.reboot()  # 5 V gone: the module starts over when it comes back


class Board:
    """The CoreS3 + the RAK3172 on the M-Bus. ``at(t_ms, fn)`` runs ``fn(board)`` at a virtual time."""

    def __init__(self, usb=True, aw9523=True, lora_on=True, keys=True):
        self.aw = Aw9523(aw9523)
        self.rak = Rak3172(self)
        self.usb = usb
        self.lora_on = lora_on
        self.keys = keys
        self.t = 0
        self.log: list[tuple[int, str]] = []  # (virtual t_ms, line)
        self.states: list[tuple[int, dict]] = []  # (t_ms, device.lora) at every scheduled tick
        self.hw: list[tuple[int, str]] = []  # every request
        self._at: dict[int, list] = {}
        self._out: list[str] = []
        self.rc = None

    def powered(self):
        return bool(self.usb) or self.aw.bus == (1, 1)

    def at(self, t_ms, fn):
        self._at.setdefault(t_ms, []).append(fn)
        self._out.append(f"EV {max(0, t_ms - self.t)} TICK")

    def every(self, period_ms, until_ms):
        for t in range(period_ms, until_ms, period_ms):
            self.at(t, lambda b: None)

    def set_usb(self, on):
        powered = self.powered()
        self.usb = on
        self._out.append(f"EV 0 USB {int(on)}")
        if powered and not self.powered():
            self.rak.reboot()

    def lines(self, prefix):
        return [(t, line) for t, line in self.log if line.startswith(prefix)]

    def logged(self, text):
        return [t for t, line in self.log if text in line]

    def handle(self, t, req):
        self.t = t
        self.hw.append((t, req))
        kind, _, rest = req.partition(" ")
        out = []
        if kind == "BOOT":
            if self.usb is not None:
                out.append(f"EV 0 USB {int(self.usb)}")
            if self.keys:
                out.append(f"EV 0 CONSOLE {KEYS}")
            if self.lora_on:
                out.append("EV 0 CONSOLE lora on")
        elif kind == "UART":
            out += self.rak.uart(t, rest)
        elif kind == "GPIO":
            pin, what, value = rest.split()
            self.rak.gpio(t, int(pin), what, value)
        elif kind == "PINS":
            self.rak.pins(int(rest.split()[0]))
        elif kind == "I2C":
            op, addr, *data = rest.split()
            here = self.aw.present and int(addr, 16) == AW9523
            if here and op == "W":
                before, powered = self.aw.bus, self.powered()
                self.aw.write(bytes.fromhex(data[0]))
                if self.aw.bus != before:
                    self.aw.history.append((t, self.aw.bus))
                    self.rak.bus_changed(t, before, self.aw.bus, powered)
            out.append("ACK" if here else "NACK")
        elif kind == "TICK":
            self.states.append((t, json.loads(rest)["lora"]))
            for fn in self._at.pop(t, []):
                fn(self)
        out += self._out
        self._out = []
        return out


def run(binary, board: Board, until_ms: int, store, wall_s=60) -> Board:
    """Run ``lora-sim`` against ``board`` up to the virtual ``until_ms``; returns the board (logs, fakes)."""
    p = subprocess.Popen(
        [str(binary), "--store", str(store), "--until", str(until_ms)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    timer = threading.Timer(wall_s, p.kill)
    timer.start()
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            if line.startswith("HW "):
                _, t, req = line.split(" ", 2)
                reply = board.handle(int(t), req) + ["END"]
                p.stdin.write("\n".join(reply) + "\n")
                p.stdin.flush()
            elif line.startswith("T "):
                board.t = int(line[2:])
            else:
                board.log.append((board.t, line))
        board.rc = p.wait()
    finally:
        timer.cancel()
        if p.poll() is None:
            p.kill()
    return board
