"""The fake unit's own contract — the behaviours the app lab validates and calictl is tested against.

.. test:: Fake unit peripheral contract
   :id: T_FAKE_UNIT_CONTRACT
   :links: R_FAKE_UNIT_FIDELITY
"""
import asyncio

import pytest

pytest.importorskip("bumble")

from _bumble_link import central_device, controller, pair_with, radio, scan_for  # noqa: E402
from bumble.core import ProtocolError  # noqa: E402

from tools.fake_unit_peripheral import IDENTITY, build_unit  # noqa: E402


async def _unit_and_central(**kw):
    link = radio()
    uc = controller(link, "unit")
    unit = build_unit(uc, uc, **kw)
    await unit.start()
    central = central_device(link)
    await central.power_on()
    return link, unit, central


def test_advertises_from_a_rotating_private_address_over_a_fixed_identity():
    async def run():
        _, unit, central = await _unit_and_central()
        first = await scan_for(central)
        await unit.rotate_address()
        second = await scan_for(central)
        return str(first), str(second), unit.advertising_address

    first, second, advertising_address = asyncio.run(run())
    assert first != IDENTITY and second != IDENTITY      # never advertises its identity
    assert first != second                               # rotate_address() moves it
    assert advertising_address == second                 # .advertising_address tracks the CURRENT RPA


def test_pairs_with_a_fresh_passkey_and_reveals_its_identity():
    async def run():
        _, unit, central = await _unit_and_central()
        pair_with(central, unit.next_passkey)
        conn = await central.connect(await scan_for(central))
        await central.pair(conn)
        return str(conn.peer_address), conn.is_encrypted, unit.last_passkey

    peer, encrypted, code = asyncio.run(run())
    assert peer == IDENTITY and encrypted
    assert 0 <= code <= 999_999


def test_pairing_mode_off_refuses_a_new_bond():
    async def run():
        _, unit, central = await _unit_and_central(pairing_mode=False)
        pair_with(central, unit.next_passkey)
        conn = await central.connect(await scan_for(central))
        with pytest.raises(ProtocolError, match="PAIRING_NOT_SUPPORTED"):
            await central.pair(conn)

    asyncio.run(run())


def test_one_connection_slot_hides_the_unit_while_a_central_holds_it():
    async def run():
        link, unit, first = await _unit_and_central()
        await first.connect(await scan_for(first))
        second = central_device(link, name="phone", address="F0:F1:F2:F3:F4:F6")
        await second.power_on()
        with pytest.raises(asyncio.TimeoutError):
            await scan_for(second, timeout=1.0)

    asyncio.run(run())


def test_refuse_connections_drops_the_link():
    async def run():
        _, unit, central = await _unit_and_central()
        unit.refuse_connections = True
        conn = await central.connect(await scan_for(central))
        dropped: asyncio.Future = asyncio.get_running_loop().create_future()
        conn.on("disconnection", lambda reason: not dropped.done() and dropped.set_result(reason))
        await asyncio.wait_for(dropped, 2.0)   # the unit disconnects it, not left to GC/timeout

    asyncio.run(run())


def test_pairing_mode_off_still_allows_a_bonded_central_to_reconnect():
    async def run():
        _, unit, central = await _unit_and_central()
        pair_with(central, unit.next_passkey)
        conn = await central.connect(await scan_for(central))
        await central.pair(conn)
        await conn.disconnect()

        unit.pairing_mode = False   # the pairing screen is closed — NEW bonds are refused, but
        conn2 = await central.connect(await scan_for(central))   # an existing bond still reconnects
        await conn2.encrypt()
        return str(conn2.peer_address), conn2.is_encrypted

    peer, encrypted = asyncio.run(run())
    assert peer == IDENTITY and encrypted


def test_console_pair_command_toggles_pairing_mode():
    async def run():
        _, unit, _ = await _unit_and_central()
        assert unit.pairing_mode is True   # starts open, like the unit's default screen state
        unit._console_line("pair off")
        off = unit.pairing_mode
        unit._console_line("pair on")
        on = unit.pairing_mode
        return off, on

    off, on = asyncio.run(run())
    assert off is False
    assert on is True
