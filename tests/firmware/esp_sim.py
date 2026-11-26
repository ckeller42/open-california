"""The satellite on the simulated CoreS3 (``coresim``, sim/README.md): what ``lora-sim`` needs besides the
generic board — the console lines at boot (bench keys, the LoRa switch) and ``device.lora`` +
``device.battery`` in each scheduled tick (``lora_sim_main.c``). The supply state reaches the firmware only
through the fake AXP2101."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import coresim
except ImportError:  # not installed (pip install -e sim): this checkout's copy
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sim"))
    import coresim

KEYS = "lora keys 70B3D57ED0000001 0000000000000001 000102030405060708090A0B0C0D0E0F"


class Board(coresim.CoreS3):
    def __init__(self, usb=True, aw9523=True, axp2101=True, lora_on=True, keys=True):
        super().__init__(vbus=usb, aw9523=aw9523, axp2101=axp2101)
        self.lora_on = lora_on
        self.keys = keys
        self.states: list[tuple[int, dict]] = []  # (t_ms, device.lora) at every scheduled tick
        self.battery: list[tuple[int, dict]] = []  # (t_ms, device.battery) at every scheduled tick

    @property
    def usb(self):
        return self.vbus

    def boot(self):
        out = []
        if self.keys:
            out.append(f"EV 0 CONSOLE {KEYS}")
        if self.lora_on:
            out.append("EV 0 CONSOLE lora on")
        return out

    def tick(self, t, payload):
        doc = json.loads(payload)
        self.states.append((t, doc.get("lora")))  # None while exp_lora is off
        self.battery.append((t, doc["battery"]))

    def set_usb(self, on):
        self.set_vbus(on)


def run(binary, board: Board, until_ms: int, store, wall_s=60) -> Board:
    """Run ``lora-sim`` against ``board`` up to the virtual ``until_ms``."""
    return coresim.run([binary, "--store", store, "--until", until_ms], board, wall_s)
