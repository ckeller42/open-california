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
    assert first != IDENTITY and second != IDENTITY  # never advertises its identity
    assert first != second  # rotate_address() moves it
    assert advertising_address == second  # .advertising_address tracks the CURRENT RPA


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
            sc=True,
            mitm=False,
            bonding=True,
            delegate=PairingDelegate(io_capability=PairingDelegate.IoCapability.NO_OUTPUT_NO_INPUT),
        )
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
        await asyncio.wait_for(dropped, 2.0)  # the unit disconnects it, not left to GC/timeout

    asyncio.run(run())


def test_pairing_mode_off_still_allows_a_bonded_central_to_reconnect():
    async def run():
        _, unit, central = await _unit_and_central()
        pair_with(central, unit.next_passkey)
        conn = await central.connect(await scan_for(central))
        await central.pair(conn)
        await conn.disconnect()

        unit.pairing_mode = False  # the pairing screen is closed — NEW bonds are refused, but
        conn2 = await central.connect(await scan_for(central))  # an existing bond still reconnects
        await conn2.encrypt()
        return str(conn2.peer_address), conn2.is_encrypted

    peer, encrypted = asyncio.run(run())
    assert peer == IDENTITY and encrypted


def test_console_pair_command_toggles_pairing_mode():
    async def run():
        _, unit, _ = await _unit_and_central()
        assert unit.pairing_mode is True  # starts open, like the unit's default screen state
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
        await asyncio.wait_for(unit.tasks[-1], 2.0)  # let the scheduled forget_bonds() run
        after = await unit.device.keystore.get_all()
        return before, after

    before, after = asyncio.run(run())
    assert before  # sanity: pairing actually stored a bond
    assert after == []  # the console command's dispatcher path really clears it


def test_console_rotate_command_changes_advertising_address():
    async def run():
        _, unit, central = await _unit_and_central()
        first = await scan_for(central)
        unit._console_line("rotate")
        await asyncio.wait_for(unit.tasks[-1], 2.0)  # let the scheduled rotate_address() run
        second = await scan_for(central)
        return str(first), str(second), unit.advertising_address

    first, second, advertising_address = asyncio.run(run())
    assert first != second  # the console command's dispatcher path really rotates
    assert advertising_address == second


async def _connected_peer(**kw):
    from bumble.device import Peer

    _, unit, central = await _unit_and_central(**kw)
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
        ch = _char(peer, "1102")  # cooler
        got: asyncio.Queue = asyncio.Queue()
        await ch.subscribe(lambda v: got.put_nowait(bytes(v)))
        await asyncio.wait_for(got.get(), 2.0)  # the on-subscribe push of the current value
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


def test_drop_on_read_hangs_up_on_that_read_only_once():
    """``drop_on_read`` (test knob, default off) ends the link on the next GATT read of that
    function instead of answering it; other reads, and later links, are served as before."""

    async def run():
        unit, peer = await _connected_peer()
        assert unit.drop_on_read is None
        other = bytes(await _char(peer, "1602").read_value())  # energy: served
        dropped: asyncio.Future = asyncio.get_running_loop().create_future()
        peer.connection.on("disconnection", lambda reason: not dropped.done() and dropped.set_result(reason))
        unit.drop_on_read = "cooler"
        with pytest.raises((Exception, asyncio.CancelledError)):  # no response: the link ends
            await asyncio.wait_for(_char(peer, "1102").read_value(), 2.0)
        await asyncio.wait_for(dropped, 2.0)
        return other, unit.drop_on_read

    other, knob = asyncio.run(run())
    assert other and knob is None  # one-shot


# --- FAKE_UNIT_RECORD: the app-recording tap -------------------------------------------------


def _events(path):
    from calictl.trace import read_events

    return list(read_events(path))


def test_recording_is_off_by_default(tmp_path, monkeypatch):
    """build_unit() records nothing unless given a path — the env var is read by the lab CLI only,
    so tests and the realstack rig never pick up a stray FAKE_UNIT_RECORD."""
    monkeypatch.setenv("FAKE_UNIT_RECORD", str(tmp_path / "r.jsonl"))

    async def run():
        unit, peer = await _connected_peer()
        await _char(peer, "1102").read_value()
        return unit

    unit = asyncio.run(run())
    assert not unit.rec.enabled
    assert not (tmp_path / "r.jsonl").exists()


def test_recording_taps_every_event_kind(tmp_path):
    from bumble.device import Peer

    rec = tmp_path / "r.jsonl"

    async def run():
        _, unit, central = await _unit_and_central(record=str(rec), vin="TESTVIN")
        pair_with(central, unit.next_passkey)
        conn = await central.connect(await scan_for(central))
        await central.pair(conn)
        peer = Peer(conn)
        await peer.request_mtu(247)
        await peer.discover_services()
        await peer.discover_characteristics()
        got: asyncio.Queue = asyncio.Queue()
        await _char(peer, "1102").subscribe(lambda v: got.put_nowait(bytes(v)))
        await asyncio.wait_for(got.get(), 2.0)  # the on-subscribe push
        await _char(peer, "1602").read_value()
        await _char(peer, "1002").read_value()
        await _char(peer, "1003").write_value((0x100000).to_bytes(4, "big"), with_response=True)
        await _char(peer, "f000").write_value(b"\x01", with_response=True)
        # cooler ON with every other field at the app's leave-unchanged default
        await _char(peer, "1101").write_value(bytes.fromhex("fd771e3e1f1f"), with_response=True)
        await asyncio.sleep(0.1)  # (the on-subscribe push above is already a recorded notify)
        await conn.disconnect()
        await asyncio.sleep(0.2)
        return unit

    unit = asyncio.run(run())
    evs = _events(rec)
    kinds = [e["ev"] for e in evs]
    assert kinds[0] == "connect"
    for k in ("pair", "mtu", "subscribe", "read", "write", "notify", "disconnect"):
        assert k in kinds, k
    assert [e["state"] for e in evs if e["ev"] == "pair"][:2] == ["passkey_shown", "bonded"]
    assert all(set(e) <= {"t", "t_ms", "conn", "ev", "state", "reason"} for e in evs if e["ev"] == "pair")
    assert next(e for e in evs if e["ev"] == "mtu")["mtu"] >= 23
    sub = next(e for e in evs if e["ev"] == "subscribe")
    assert (sub["char"], sub["fn"], sub["hex"]) == ("1102", "cooler", "0100")
    assert kinds.index("subscribe") < kinds.index("notify")  # no notify recorded before a subscribe
    reads = {e["char"]: e for e in evs if e["ev"] == "read"}
    assert reads["1602"]["fn"] == "energy"
    assert reads["1002"]["hex"] == "<vin-hash>"
    assert unit.vin_fingerprint.hex() not in rec.read_text()
    writes = {e["char"]: e for e in evs if e["ev"] == "write"}
    assert (writes["1003"]["fn"], writes["1003"]["hex"]) == ("heartbeat", "00100000")
    assert writes["f000"]["fn"] is None
    assert (writes["1101"]["fn"], writes["1101"]["hex"]) == ("cooler", "fd771e3e1f1f")
    assert isinstance(next(e for e in evs if e["ev"] == "disconnect")["reason"], int)
    assert all(e["conn"] == 1 for e in evs)
    t_ms = [e["t_ms"] for e in evs]
    assert t_ms[0] <= 1 and t_ms == sorted(t_ms)  # connect emits ~0 ms after _t0; <=1 absorbs rounding


def test_a_second_connection_is_numbered_and_restarts_t_ms(tmp_path):
    rec = tmp_path / "r.jsonl"

    async def run():
        _, unit, central = await _unit_and_central(record=str(rec))
        conn = await central.connect(await scan_for(central))
        await asyncio.sleep(0.05)
        await conn.disconnect()
        await asyncio.sleep(0.2)
        conn2 = await central.connect(await scan_for(central))
        await conn2.disconnect()
        await asyncio.sleep(0.2)

    asyncio.run(run())
    evs = _events(rec)
    assert [e["conn"] for e in evs if e["ev"] == "connect"] == [1, 2]
    second = [e for e in evs if e["conn"] == 2]
    assert second[0]["ev"] == "connect" and second[0]["t_ms"] <= 1
    assert [e["ev"] for e in evs].count("disconnect") == 2


def test_a_write_error_disables_recording_once(tmp_path, caplog):
    bad = tmp_path / "missing-dir" / "r.jsonl"

    async def run():
        unit, peer = await _connected_peer(record=str(bad))
        v1 = bytes(await _char(peer, "1602").read_value())
        v2 = bytes(await _char(peer, "1602").read_value())
        return unit, v1, v2

    unit, v1, v2 = asyncio.run(run())
    assert v1 and v1 == v2  # the lab keeps serving
    assert unit.rec.path is None and not unit.rec.enabled
    assert sum("recording disabled" in r.getMessage() for r in caplog.records) == 1


def test_lighting_config_frames_are_notified_to_a_subscribed_central():
    """Wake-up (Mode 20) and REQUEST_CONFIG (Mode 12, favourite bits) replies reach a 1502 subscriber
    (dg/h m0/d0; lighting-wakeup.jsonl + vineflower dg/a.java:286-290).

    .. test:: Fake peripheral notifies the lighting config echoes
       :id: T_FAKE_LIGHT_CONFIG_NOTIFY
       :links: R_LIGHT_WAKEUP
    """
    from calictl import overrides, protocol

    f = protocol.load()
    overrides.apply(f)
    light = f["lighting"]

    async def run():
        unit, peer = await _connected_peer()
        got: asyncio.Queue = asyncio.Queue()
        await _char(peer, "1502").subscribe(lambda v: got.put_nowait(bytes(v)))
        await asyncio.wait_for(got.get(), 2.0)  # on-subscribe push
        ctl = _char(peer, "1501")

        async def drain():  # every notify of one write; the ack is the first, the stored state last
            await asyncio.sleep(0.3)
            frames = []
            while not got.empty():
                frames.append(protocol.decode(light, got.get_nowait()))
            return frames

        async def w(hx):
            await ctl.write_value(bytes.fromhex(hx), with_response=True)
            return await drain()

        wake = (await w("0e146ac49c701100eeeeeeeeeeeeeeee"))[0]  # wake-up 07:00
        await w("010400000000000000000005e00eeeee")  # save favourite 1 (staged, no notify of note)
        save = (await w("0e00000000000000eeeeeeeeeeeeeeee"))[0]  # commit -> save ack
        cfg = (await w("0d0c000000000000eeeeeeeeeeeeeeee"))[0]  # REQUEST_CONFIG
        real = protocol.decode(light, bytes(await _char(peer, "1502").read_value()))
        return wake, save, cfg, real

    wake, save, cfg, real = asyncio.run(run())
    assert (wake["Mode"], wake["Timestamp"]) == (20, 0x6AC49C70)
    assert (save["Mode"], save["ProfileNumber"]) == (4, 1)  # the save ack names favourite 1
    assert cfg["Mode"] == 12 and cfg["LightValue"] & 1 == 1
    assert real["Mode"] not in (4, 12, 20)  # a read returns the real state, no sticky ack
