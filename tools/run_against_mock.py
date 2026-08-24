"""Run real ``calictl`` commands against the in-process :mod:`tools.mock_unit`.

No van, no phone, no BLE: this installs a fake ``bleak`` module whose ``BleakClient``
is backed by one shared :class:`~tools.mock_unit.MockCamperUnit`, then dispatches to the
normal ``calictl`` CLI. The full runtime path runs unchanged — ``cli.cmd_set`` →
``device.actuate`` (handshake, 1003 heartbeat, control write, readback) — but every GATT
call lands on the mock.

Examples::

    python -m tools.run_against_mock set cooler power on      # -> cooler.on False->True, OK
    python -m tools.run_against_mock set cooler power off      # -> back to False
    python -m tools.run_against_mock set lighting brightness 8 # -> NOT APPLIED (unsolved gate)
    python -m tools.run_against_mock get cooler                # -> decoded + interpreted

The single ``MockCamperUnit`` persists across calictl's connect-per-operation calls, so a
``set`` followed by a ``get`` reflects the change (within one process invocation).
"""
from __future__ import annotations

import os

# Set a mock BLE address before anything imports calictl.device (which resolves
# DEFAULT_ADDR from the environment at import time). The mock ignores the value.
os.environ.setdefault("CALICTL_ADDR", "MO:CK:CA:MP:ER:00")

import sys
import types

from tools.mock_unit import MockBleakClient, MockCamperUnit


def install_fake_bleak(unit: MockCamperUnit | None = None) -> MockCamperUnit:
    """Register a fake ``bleak`` module in ``sys.modules`` backed by ``unit`` (a fresh
    one if omitted). Returns the unit so callers can inspect/seed it."""
    unit = unit or MockCamperUnit()
    mod = types.ModuleType("bleak")
    mod.BleakClient = MockBleakClient.bind(unit)
    sys.modules["bleak"] = mod
    return unit


def main(argv=None) -> int:
    install_fake_bleak()
    from calictl import cli
    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
