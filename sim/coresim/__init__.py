"""coresim — a simulated M5Stack CoreS3 for host builds of ESP32-S3 firmware (sim/README.md).

The firmware runs on Linux over the C shims in ``sim/shims`` (a FreeRTOS on a virtual clock, UART, GPIO,
I2C, the AW9523 driver, the board package). Each hardware action is one request line to this package,
answered in lockstep while the virtual clock stands still (:mod:`coresim.link`). The fakes in
:mod:`coresim.devices` are generic CoreS3 devices; :class:`coresim.board.CoreS3` wires them.
"""

from .board import CoreS3
from .devices import Aw9523, Axp2101, Rak3172
from .link import run

__all__ = ["Aw9523", "Axp2101", "CoreS3", "Rak3172", "run"]
