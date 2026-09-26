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

# bumble.host.Host.send_command_sync is decorated @utils.deprecated(...) and fires this
# DeprecationWarning from INSIDE bumble/smp.py's start_encryption() every time SMP pairing
# reaches the encrypt step -- library-internal tech debt in bumble 0.0.235, not our code. Narrow
# ignore (exact message + category), not a blanket filter.
pytestmark = pytest.mark.filterwarnings(
    r"ignore:Use utils\.AsyncRunner\.spawn\(\) instead\.:DeprecationWarning"
)


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


def test_just_works_pairing_is_refused():
    """A central that pairs as NoInputNoOutput (no passkey, no MITM) is refused: the real unit hung
    up on exactly that Pairing Request (btmon on buspi, 2026-09-26) — so must the fake, or a
    calictl that forgets its KeyboardOnly agent passes CI."""
    from bumble.pairing import PairingConfig, PairingDelegate

    async def run():
        _, unit, central = await _unit_and_central()
        central.pairing_config_factory = lambda conn: PairingConfig(
            sc=True, mitm=False, bonding=True,
            delegate=PairingDelegate(io_capability=PairingDelegate.IoCapability.NO_OUTPUT_NO_INPUT))
        conn = await central.connect(await scan_for(central))
        with pytest.raises(ProtocolError):
            await central.pair(conn)
        return conn.is_encrypted

    assert asyncio.run(run()) is False


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


def test_console_forget_command_clears_bonds():
    async def run():
        _, unit, central = await _unit_and_central()
        pair_with(central, unit.next_passkey)
        conn = await central.connect(await scan_for(central))
        await central.pair(conn)
        await conn.disconnect()

        before = await unit.device.keystore.get_all()
        unit._console_line("forget")
        await asyncio.wait_for(unit.tasks[-1], 2.0)   # let the scheduled forget_bonds() run
        after = await unit.device.keystore.get_all()
        return before, after

    before, after = asyncio.run(run())
    assert before                 # sanity: pairing actually stored a bond
    assert after == []            # the console command's dispatcher path really clears it


def test_console_rotate_command_changes_advertising_address():
    async def run():
        _, unit, central = await _unit_and_central()
        first = await scan_for(central)
        unit._console_line("rotate")
        await asyncio.wait_for(unit.tasks[-1], 2.0)   # let the scheduled rotate_address() run
        second = await scan_for(central)
        return str(first), str(second), unit.advertising_address

    first, second, advertising_address = asyncio.run(run())
    assert first != second                # the console command's dispatcher path really rotates
    assert advertising_address == second


async def _connected_peer():
    from bumble.device import Peer

    _, unit, central = await _unit_and_central()
    conn = await central.connect(await scan_for(central))
    peer = Peer(conn)
    await peer.discover_services()
    await peer.discover_characteristics()
    return unit, peer


def _char(peer, slot):
    from bumble.core import UUID

    from tools.fake_unit_peripheral import cu
    return peer.get_characteristics_by_uuid(UUID(cu(slot)))[0]


def test_heartbeat_writes_are_counted():
    """``unit.beats`` counts 1003 writes — the firmware e2e checks its heartbeat with it."""
    async def run():
        unit, peer = await _connected_peer()
        before = unit.beats
        for n in (0x100000, 0x100001, 0x100002):
            await _char(peer, "1003").write_value(n.to_bytes(4, "big"), with_response=True)
        return before, unit.beats

    assert asyncio.run(run()) == (0, 3)


def test_set_raw_serves_the_frame_and_notifies_a_subscriber():
    """``set_raw`` serves a frame verbatim (a truncated one too) and pushes it to a subscriber."""
    async def run():
        unit, peer = await _connected_peer()
        ch = _char(peer, "1102")                     # cooler
        got: asyncio.Queue = asyncio.Queue()
        await ch.subscribe(lambda v: got.put_nowait(bytes(v)))
        await asyncio.wait_for(got.get(), 2.0)      # the on-subscribe push of the current value
        short = unit.raw["cooler"][:1]
        unit.set_raw("cooler", short, notify=False)
        read_back = bytes(await ch.read_value())
        changed = bytes([unit.raw["cooler"][0] ^ 0xFF]) + b"\x00" * 3
        unit.set_raw("cooler", changed)
        pushed = await asyncio.wait_for(got.get(), 2.0)
        return short, read_back, changed, pushed

    short, read_back, changed, pushed = asyncio.run(run())
    assert read_back == short and len(short) == 1
    assert pushed == changed
