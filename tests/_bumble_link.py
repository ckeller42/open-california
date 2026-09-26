"""Test-only Bumble plumbing: a virtual radio (``LocalLink``) and a scanning/pairing central.

Imported by the Bumble-based pairing tests only; ``calictl/`` never imports Bumble. The leading
underscore keeps pytest from collecting it.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bumble.controller import Controller
from bumble.core import UUID, AdvertisingData
from bumble.device import Device, DeviceConfiguration, Peer
from bumble.gatt import Characteristic
from bumble.hci import Address
from bumble.link import LocalLink
from bumble.pairing import PairingConfig, PairingDelegate

from calictl import device as device_mod
from calictl.pairing import EV_CONNECTED, EV_DEVICE_FOUND, EV_PAIR_OK, EV_PASSKEY_REQUESTED

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


async def _or_link_drop(conn, coro):
    """Await ``coro`` unless the link drops first; a drop becomes ``ConnectionError``. Bumble
    cancels the pending GATT/SMP request with CancelledError, which the runner's ``except
    Exception`` would not catch (real bleak surfaces the same drop as an ordinary exception)."""
    dropped = asyncio.get_running_loop().create_future()

    def on_drop(reason):
        if not dropped.done():
            dropped.set_result(reason)

    conn.on("disconnection", on_drop)
    if conn.device.connections.get(conn.handle) is not conn:
        # The link dropped before we listened (the fake unit refuses links as soon as they
        # complete — on Python 3.11 that disconnect lands before this call); the event is gone,
        # and a GATT/SMP request on the dead link would wait forever.
        coro.close()
        conn.remove_listener("disconnection", on_drop)
        raise ConnectionError("link dropped")
    work = asyncio.ensure_future(coro)
    try:
        done, _ = await asyncio.wait({work, dropped}, return_when=asyncio.FIRST_COMPLETED)
        if work in done and not work.cancelled():
            return work.result()
        work.cancel()
        raise ConnectionError("link dropped")
    finally:
        conn.remove_listener("disconnection", on_drop)


class BumbleTransport:
    """The pairing runner's transport interface on a Bumble central — the CI stand-in for
    ``calictl.pairing_bluez.BluezTransport`` (BlueZ cannot run on GitHub's runners)."""

    def __init__(self, central, *, device_name: str = NAME, cache_path: Path | None = None,
                 connect_timeout: float = 5.0, on_event=None):
        self.on_event = on_event
        self.central = central
        self._name = device_name
        self._cache_path = cache_path
        self._connect_timeout = connect_timeout
        self._found = None
        self._conn = None
        self._passkey = None
        self.radio_busy = False
        self.before_connect = None
        pair_with(central, self._ask_passkey)

    async def _emit(self, ev, arg=0):
        if self.on_event is not None:
            await self.on_event(ev, arg)

    async def _ask_passkey(self) -> int:
        self._passkey = asyncio.get_running_loop().create_future()
        await self._emit(EV_PASSKEY_REQUESTED)
        return await self._passkey

    def _on_adv(self, adv):
        if self._found is None and adv.data.get(AdvertisingData.COMPLETE_LOCAL_NAME) == self._name:
            self._found = adv.address
            asyncio.get_running_loop().create_task(self._emit(EV_DEVICE_FOUND))

    async def start_scan(self):
        self._found = None
        self.central.on("advertisement", self._on_adv)
        await self.central.start_scanning()

    async def stop_scan(self):
        self.central.remove_listener("advertisement", self._on_adv)
        await self.central.stop_scanning()

    async def connect(self):
        if self.before_connect is not None:
            hook, self.before_connect = self.before_connect, None
            await hook()
        try:
            # Bug found via this harness: Bumble 0.0.235's HCI_LE_Create_Connection_Cancel_Command
            # handler (controller.py) acks SUCCESS but never clears Controller.pending_le_connection
            # nor emits a completion event, so central.connect(timeout=...) to an address that has
            # gone stale (e.g. the unit rotated its RPA between scan and connect) hangs well past its
            # own `timeout=` argument instead of raising -- real bleak/BlueZ reliably fails a connect
            # to an address nothing answers on within its timeout. An outer wait_for bounds it; on
            # timeout we also clear the wedged local flag ourselves, else it would block every later
            # connect attempt (this central's own controller) with COMMAND_DISALLOWED_ERROR.
            self._conn = await asyncio.wait_for(
                self.central.connect(self._found, timeout=self._connect_timeout),
                timeout=self._connect_timeout + 2.0)
        except TimeoutError as e:
            self.central.host.controller.pending_le_connection = None
            raise ConnectionError("connect timed out (stale address)") from e
        await _or_link_drop(self._conn, Peer(self._conn).discover_services())  # bleak connects AND discovers
        await self._emit(EV_CONNECTED)

    async def pair(self):
        await _or_link_drop(self._conn, self.central.pair(self._conn))
        await self._emit(EV_PAIR_OK)

    async def send_passkey(self, pk):
        if self._passkey is not None and not self._passkey.done():
            self._passkey.set_result(pk)

    async def verify(self):
        peer = Peer(self._conn)
        try:
            await peer.discover_services()
            await peer.discover_characteristics()
            for uuid in (device_mod.VERSION_CHAR, device_mod.AUTH_CHAR):   # 1004 is auth-gated
                await peer.get_characteristics_by_uuid(UUID(uuid))[0].read_value()
            return sum(1 for s in peer.services for c in s.characteristics
                       if c.properties & Characteristic.Properties.READ) or None
        except Exception:
            return None

    async def persist_bond(self):
        identity = str(self._conn.peer_address)   # resolved from the RPA once the IRK arrived
        if self._cache_path is not None:
            self._cache_path.write_text(json.dumps({"address": identity}))
        return identity

    async def disconnect(self):
        if self._conn is not None:
            # Bug found via this harness: Connection.disconnect() on an ALREADY-dropped link
            # (e.g. the unit refused/disconnected first) sends HCI_Disconnect_Command for a
            # connection handle the controller no longer knows, then awaits an EVENT_DISCONNECTION
            # that will never fire again for that (already-popped) connection object -- it hangs
            # forever, wedging the retry loop's ACT_DISCONNECT before ACT_START_SCAN ever runs.
            # Real bleak's disconnect() is a no-op/quick-return on an already-dropped client, so
            # only call Bumble's disconnect while the device still tracks this connection.
            if self.central.connections.get(self._conn.handle) is self._conn:
                try:
                    await self._conn.disconnect()
                except Exception:
                    pass
            self._conn = None

    async def remove_bond(self):
        await self.central.keystore.delete_all()
        if self._cache_path is not None:
            self._cache_path.unlink(missing_ok=True)
