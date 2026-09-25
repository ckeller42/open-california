"""Harness A: calictl's real PairingRunner + state machine pair with the fake unit over a Bumble
LocalLink — every PR, no BlueZ, no radio.

.. test:: Pairing runner against the fake unit
   :id: T_PAIRING_LINK
   :links: R_PAIRING_SM, R_FAKE_UNIT_FIDELITY
"""
import asyncio
import json

import pytest

pytest.importorskip("bumble")

from _bumble_link import BumbleTransport, central_device, controller, radio, scan_for  # noqa: E402

from calictl import pairing  # noqa: E402
from calictl.pairing_bluez import PairingRunner  # noqa: E402
from tools.fake_unit_peripheral import IDENTITY, build_unit  # noqa: E402


async def _setup(tmp_path=None, **unit_kw):
    link = radio()
    uc = controller(link, "unit")
    unit = build_unit(uc, uc, **unit_kw)
    await unit.start()
    central = central_device(link)
    await central.power_on()
    t = BumbleTransport(central, cache_path=(tmp_path / "pairing.json") if tmp_path else None,
                        connect_timeout=1.0)
    runner = PairingRunner(t)
    t.on_event = runner.handle
    return link, unit, central, t, runner


async def _until(runner, names, timeout=15.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while runner.snapshot()["state"] not in names:
        assert loop.time() < end, "stuck: %s" % runner.snapshot()
        await asyncio.sleep(0.02)
    return runner.snapshot()


async def _pair(runner, unit, typed=None):
    await runner.start()
    snap = await _until(runner, {"waiting_passkey", "error"})
    if snap["state"] == "error":
        return snap
    code = await unit.next_passkey()
    await runner.enter_passkey(code if typed is None else typed(code))
    return await _until(runner, {"bonded", "error", "waiting_passkey"})


def test_pairs_and_persists_the_identity_not_the_rotating_address(tmp_path):
    async def run():
        _, unit, _, _, runner = await _setup(tmp_path)
        advertised = unit.advertising_address
        return await _pair(runner, unit), advertised

    snap, advertised = asyncio.run(run())
    assert snap["state"] == "bonded"
    assert snap["address"] == IDENTITY != advertised
    assert json.loads((tmp_path / "pairing.json").read_text()) == {"address": IDENTITY}


def test_passkey_with_a_leading_zero_pairs():
    async def run():
        _, unit, _, _, runner = await _setup(fixed_passkey=12345)
        return await _pair(runner, unit, typed=lambda code: int("012345"))

    assert asyncio.run(run())["state"] == "bonded"


def test_wrong_passkey_three_times_ends_pairing_failed():
    async def run():
        _, unit, _, _, runner = await _setup()
        await runner.start()
        for _ in range(pairing.MAX_ATTEMPTS):
            await _until(runner, {"waiting_passkey"})
            code = await unit.next_passkey()
            await runner.enter_passkey((code + 1) % 1_000_000)
            await _until(runner, {"scanning", "connecting", "pairing", "error"})
        return await _until(runner, {"error"})

    snap = asyncio.run(run())
    assert snap["error"] == "pairing_failed" and snap["attempts"] == pairing.MAX_ATTEMPTS


def test_pairing_mode_off_ends_pairing_failed():
    # Fake default = the unit refuses at SMP (Task 3 records the real behaviour; if the unit
    # refuses at connect instead, this expectation becomes "connect_failed").
    async def run():
        _, unit, _, _, runner = await _setup(pairing_mode=False)
        await runner.start()
        return await _until(runner, {"error"})

    assert asyncio.run(run())["error"] == "pairing_failed"


def test_unit_held_by_another_central_is_not_found(monkeypatch):
    monkeypatch.setitem(pairing.TIMEOUT_S, pairing.SCANNING, 1.5)

    async def run():
        link, unit, _, _, runner = await _setup()
        phone = central_device(link, name="phone", address="F0:F1:F2:F3:F4:F6")
        await phone.power_on()
        await phone.connect(await scan_for(phone))          # the phone app holds the only slot
        await runner.start()
        return await _until(runner, {"error"})

    assert asyncio.run(run())["error"] == "timeout"


def test_refused_links_retry_then_end_connect_failed():
    async def run():
        _, unit, _, _, runner = await _setup()
        unit.refuse_connections = True
        await runner.start()
        return await _until(runner, {"error"})

    snap = asyncio.run(run())
    assert snap["error"] == "connect_failed" and snap["attempts"] == pairing.MAX_ATTEMPTS


def test_rotation_between_scan_and_connect_recovers():
    async def run():
        _, unit, _, t, runner = await _setup()
        t.before_connect = unit.rotate_address               # the found address goes stale
        return await _pair(runner, unit)

    snap = asyncio.run(run())
    assert snap["state"] == "bonded" and snap["attempts"] == 1


def test_reconnect_by_identity_after_the_address_rotates():
    async def run():
        _, unit, central, t, runner = await _setup()
        await _pair(runner, unit)
        await t.disconnect()
        await asyncio.sleep(0.2)
        await unit.rotate_address()
        rpa = await scan_for(central)
        resolved = central.address_resolver.resolve(rpa)
        conn = await central.connect(rpa)
        await conn.encrypt()
        return str(resolved), str(conn.peer_address), conn.is_encrypted

    resolved, peer, encrypted = asyncio.run(run())
    assert resolved == peer == IDENTITY and encrypted


def test_repair_after_unit_forgets_bonds():
    async def run():
        _, unit, _, t, runner = await _setup()
        await _pair(runner, unit)
        await t.disconnect()
        await unit.forget_bonds()                            # the unit's "Bluetooth zurücksetzen"
        await runner.reset()                                 # the wizard's re-pair path
        return await _pair(runner, unit)

    snap = asyncio.run(run())
    assert snap["state"] == "bonded" and snap["address"] == IDENTITY
