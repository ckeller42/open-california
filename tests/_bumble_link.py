"""Test-only Bumble plumbing: a virtual radio (``LocalLink``) and a scanning/pairing central.

Imported by the Bumble-based pairing tests only; ``calictl/`` never imports Bumble. The leading
underscore keeps pytest from collecting it.
"""
from __future__ import annotations

import asyncio

from bumble.controller import Controller
from bumble.core import AdvertisingData
from bumble.device import Device, DeviceConfiguration
from bumble.hci import Address
from bumble.link import LocalLink
from bumble.pairing import PairingConfig, PairingDelegate

NAME = "VWCAMPER"


def radio() -> LocalLink:
    """A fresh virtual radio shared by every controller created on it."""
    return LocalLink()


def controller(link: LocalLink, name: str) -> Controller:
    return Controller(name, link=link)


def central_device(link: LocalLink, name: str = "calictl", address: str = "F0:F1:F2:F3:F4:F5") -> Device:
    """A Bumble central on ``link`` with an in-memory keystore (no files written)."""
    ctrl = controller(link, name)
    return Device.from_config_with_hci(DeviceConfiguration(name=name, address=Address(address)), ctrl, ctrl)


async def scan_for(central: Device, name: str = NAME, timeout: float = 5.0) -> Address:
    """Scan until an advertisement carries ``name``; return its (possibly resolvable) address."""
    found: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_adv(adv):
        if adv.data.get(AdvertisingData.COMPLETE_LOCAL_NAME) == name and not found.done():
            found.set_result(adv.address)

    central.on("advertisement", on_adv)
    await central.start_scanning()
    try:
        return await asyncio.wait_for(found, timeout)
    finally:
        central.remove_listener("advertisement", on_adv)
        await central.stop_scanning()


class KeyboardCentral(PairingDelegate):
    """A central that types a passkey: ``passkey_source`` is an async callable returning it."""

    def __init__(self, passkey_source):
        super().__init__(io_capability=PairingDelegate.IoCapability.KEYBOARD_INPUT_ONLY)
        self._source = passkey_source

    async def get_number(self) -> int:
        return await self._source()


def pair_with(central: Device, passkey_source) -> None:
    """Make ``central`` pair (LE SC, MITM, bonding) typing codes from ``passkey_source``."""
    central.pairing_config_factory = lambda conn: PairingConfig(
        sc=True, mitm=True, bonding=True, delegate=KeyboardCentral(passkey_source))
