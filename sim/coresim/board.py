"""The CoreS3 wiring: the I2C devices on the internal bus, a module on UART1 + its GPIOs, the M-Bus 5 V.

The M-Bus module has power while VBUS (USB) is there or both AW9523 bus bits are 1 (the boost).
Firmware-specific behaviour (what to answer at ``BOOT``, what a scheduled ``TICK`` carries) goes into a
subclass: :meth:`CoreS3.boot` and :meth:`CoreS3.tick`.
"""

from __future__ import annotations

from .devices import Aw9523, Rak3172


class CoreS3:
    def __init__(self, vbus=True, aw9523=True):
        self.vbus = vbus
        self.aw = Aw9523(aw9523)
        self.rak = Rak3172(powered=self.powered)
        self.i2c = {self.aw.addr: self.aw}
        self.t = 0
        self.log: list[tuple[int, str]] = []  # (virtual t_ms, line)
        self.hw: list[tuple[int, str]] = []  # every request
        self.bus_history: list[tuple[int, tuple[int, int]]] = []  # (t_ms, AW9523 bus bits) at every change
        self.rc = None
        self._at: dict[int, list] = {}
        self._out: list[str] = []

    # ---- for the firmware-specific subclass ----

    def boot(self) -> list[str]:
        return []

    def tick(self, t, payload) -> None:
        pass

    # ---- the scenario side ----

    def powered(self):
        return bool(self.vbus) or self.aw.bus == (1, 1)

    def emit(self, line):
        """An answer line for the firmware with the next reply (e.g. ``EV 0 <event>``)."""
        self._out.append(line)

    def at(self, t_ms, fn):
        """Run ``fn(board)`` at the virtual ``t_ms`` (the firmware asks back with a TICK then)."""
        self._at.setdefault(t_ms, []).append(fn)
        self.emit(f"EV {max(0, t_ms - self.t)} TICK")

    def every(self, period_ms, until_ms):
        for t in range(period_ms, until_ms, period_ms):
            self.at(t, lambda b: None)

    def set_vbus(self, on):
        powered = self.powered()
        self.vbus = on
        if powered and not self.powered():
            self.rak.lost_power()

    def logged(self, text):
        return [t for t, line in self.log if text in line]

    # ---- the link ----

    def handle(self, t, req):
        self.t = t
        self.hw.append((t, req))
        kind, _, rest = req.partition(" ")
        out = []
        if kind == "BOOT":
            out += self.boot()
        elif kind == "UART":
            out += self.rak.uart(t, rest)
        elif kind == "GPIO":
            pin, what, value = rest.split()
            self.rak.gpio(t, int(pin), what, value)
        elif kind == "PINS":
            self.rak.pins(int(rest.split()[0]))
        elif kind == "I2C":
            out += self._i2c(t, rest.split())
        elif kind == "TICK":
            self.tick(t, rest)
            for fn in self._at.pop(t, []):
                fn(self)
        out += self._out
        self._out = []
        return out

    def _i2c(self, t, args):
        op, addr, *data = args
        dev = self.i2c.get(int(addr, 16))
        if dev is None or not dev.present:
            return ["NACK"]
        if op == "W":
            before, powered = self.aw.bus, self.powered()
            dev.write(bytes.fromhex(data[0]))
            if self.aw.bus != before:
                self.bus_history.append((t, self.aw.bus))
                self.rak.bus_changed(t, before, self.aw.bus, powered)
        elif op == "R":
            return ["DATA " + dev.read(bytes.fromhex(data[0])[0], int(data[1])).hex(), "ACK"]
        return ["ACK"]
