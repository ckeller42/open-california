"""BluezTransport: only the stdlib-only plumbing is testable without a dbus/BLE stack.

.. test:: BlueZ transport construction + passkey-future plumbing
   :id: T_PAIRING_BLUEZ_TRANSPORT
   :links: R_PAIRING_BLUEZ_TRANSPORT

NOT-LIVE-VERIFIED: the dbus_fast agent registration, bleak scan/GATT, and
Device1/Adapter1 calls are exercised against real hardware in the #157 van
session, not here (no dbus/BLE stack in CI) -- see `pairing_bluez.py`'s
``BluezTransport`` docstring.
"""
import asyncio

from calictl.pairing_bluez import BluezTransport


def test_construction_defaults():
    t = BluezTransport()
    assert t.on_event is None
    assert t._device_name == "VWCAMPER"
    assert t._adapter_path == "/org/bluez/hci0"
    assert t._address is None
    assert t._passkey_future is None


def test_construction_with_overrides():
    async def _cb(ev, arg=0):
        pass

    t = BluezTransport(on_event=_cb, device_name="OTHER", adapter_path="/org/bluez/hci1")
    assert t.on_event is _cb
    assert t._device_name == "OTHER"
    assert t._adapter_path == "/org/bluez/hci1"


def test_send_passkey_resolves_the_pending_future():
    async def _run():
        t = BluezTransport()
        t._passkey_future = asyncio.get_running_loop().create_future()
        await t.send_passkey(123456)
        return t._passkey_future.result()

    assert asyncio.run(_run()) == 123456


def test_send_passkey_without_a_pending_future_is_a_noop():
    async def _run():
        t = BluezTransport()
        await t.send_passkey(123456)  # must not raise
        return t._passkey_future

    assert asyncio.run(_run()) is None


def test_send_passkey_ignores_an_already_resolved_future():
    async def _run():
        t = BluezTransport()
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(1)
        t._passkey_future = fut
        await t.send_passkey(2)  # must not raise InvalidStateError
        return t._passkey_future.result()

    assert asyncio.run(_run()) == 1
