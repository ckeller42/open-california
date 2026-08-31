"""Async transport runner drives the pure pairing SM against a fake transport.

.. test:: Transport runner drives the pairing SM
   :id: T_PAIRING_RUNNER
   :links: R_PAIRING_RUNNER
"""
import asyncio

from calictl import pairing
from calictl.pairing_bluez import PairingRunner


class FakeTransport:
    """Records every dispatched call; lets a test script verify()/persist_bond() results."""

    def __init__(self, verify_result=5, bond_address="AA:BB:CC:DD:EE:FF"):
        self.calls = []
        self.verify_result = verify_result
        self.bond_address = bond_address
        self.raise_on = set()

    def _maybe_raise(self, name):
        if name in self.raise_on:
            raise RuntimeError("boom:%s" % name)

    async def start_scan(self):
        self.calls.append("start_scan")
        self._maybe_raise("start_scan")

    async def stop_scan(self):
        self.calls.append("stop_scan")
        self._maybe_raise("stop_scan")

    async def connect(self):
        self.calls.append("connect")
        self._maybe_raise("connect")

    async def pair(self):
        self.calls.append("pair")
        self._maybe_raise("pair")

    async def send_passkey(self, pk):
        self.calls.append("send_passkey:%s" % pk)
        self._maybe_raise("send_passkey")

    async def verify(self):
        self.calls.append("verify")
        self._maybe_raise("verify")
        return self.verify_result

    async def persist_bond(self):
        self.calls.append("persist_bond")
        self._maybe_raise("persist_bond")
        return self.bond_address

    async def disconnect(self):
        self.calls.append("disconnect")
        self._maybe_raise("disconnect")

    async def remove_bond(self):
        self.calls.append("remove_bond")
        self._maybe_raise("remove_bond")

    async def aclose(self):
        self.calls.append("aclose")
        self._maybe_raise("aclose")


async def _drive_happy_path(runner, pk=123456):
    await runner.start()
    await runner.handle(pairing.EV_DEVICE_FOUND)
    await runner.handle(pairing.EV_CONNECTED)
    await runner.handle(pairing.EV_PASSKEY_REQUESTED)
    await runner.enter_passkey(pk)
    await runner.handle(pairing.EV_PAIR_OK)


def test_happy_path_call_sequence():
    async def _run():
        t = FakeTransport()
        r = PairingRunner(t)
        await _drive_happy_path(r)
        return t.calls, r.state, r.snapshot()

    calls, state, snap = asyncio.run(_run())
    # Reaching BONDED also closes the transport (agent lifecycle: the D-Bus KeyboardOnly agent
    # must not squat as the system default past this flow's end).
    assert calls == ["start_scan", "stop_scan", "connect", "pair",
                      "send_passkey:123456", "verify", "persist_bond", "aclose"]
    assert state == pairing.PairingState(pairing.BONDED, 0, pairing.ERR_NONE)


def test_snapshot_names():
    async def _run():
        t = FakeTransport(bond_address="11:22:33:44:55:66")
        r = PairingRunner(t)
        await _drive_happy_path(r)
        return r.snapshot()

    snap = asyncio.run(_run())
    assert snap == {"state": "bonded", "attempts": 0, "error": None,
                     "address": "11:22:33:44:55:66"}


def test_stale_event_makes_no_transport_calls():
    async def _run():
        t = FakeTransport()
        r = PairingRunner(t)
        await r.handle(pairing.EV_PAIR_OK)  # stale: IDLE ignores it
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    assert calls == []
    assert state == pairing.PairingState(pairing.IDLE, 0, pairing.ERR_NONE)


def test_timeout_timer_fires_and_cleans_up(monkeypatch):
    monkeypatch.setitem(pairing.TIMEOUT_S, pairing.SCANNING, 0.01)

    async def _run():
        t = FakeTransport()
        r = PairingRunner(t)
        await r.start()
        await asyncio.sleep(0.05)
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    # cleanup(SCANNING) = [ACT_STOP_SCAN]; landing in ERROR also closes the transport.
    assert calls == ["start_scan", "stop_scan", "aclose"]
    assert state == pairing.PairingState(pairing.ERROR, 0, pairing.ERR_TIMEOUT)


def test_timer_does_not_fire_after_state_moves_on(monkeypatch):
    monkeypatch.setitem(pairing.TIMEOUT_S, pairing.SCANNING, 0.02)

    async def _run():
        t = FakeTransport()
        r = PairingRunner(t)
        await r.start()
        await r.handle(pairing.EV_DEVICE_FOUND)  # leaves SCANNING before the timer fires
        await asyncio.sleep(0.05)
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    assert "remove_bond" not in calls
    assert state.st == pairing.CONNECTING
    assert state.error == pairing.ERR_NONE


def test_transport_exception_on_pair_injects_pair_fail_and_retries():
    # pair() runs from PAIRING, the one state the SM's EV_PAIR_FAIL rule reacts to.
    async def _run():
        t = FakeTransport()
        t.raise_on.add("pair")
        r = PairingRunner(t)
        await r.start()
        await r.handle(pairing.EV_DEVICE_FOUND)
        await r.handle(pairing.EV_CONNECTED)
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    # attempts 0 -> 1 < MAX_ATTEMPTS(3): SM re-arms scanning after the failed pair
    assert calls == ["start_scan", "stop_scan", "connect", "pair", "disconnect", "start_scan"]
    assert state == pairing.PairingState(pairing.SCANNING, 1, pairing.ERR_NONE)


def test_transport_exception_on_connect_is_logged_and_left_to_the_timeout():
    # connect() runs from CONNECTING, a state the SM's EV_PAIR_FAIL rule does NOT cover
    # (only PAIRING is) -- the injected event is a harmless no-op; CONNECTING's own
    # TIMEOUT_S entry is the real recovery path for a connect() that never succeeds.
    async def _run():
        t = FakeTransport()
        t.raise_on.add("connect")
        r = PairingRunner(t)
        await r.start()
        await r.handle(pairing.EV_DEVICE_FOUND)
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    assert calls == ["start_scan", "stop_scan", "connect"]
    assert state == pairing.PairingState(pairing.CONNECTING, 0, pairing.ERR_NONE)


def test_transport_exception_on_verify_injects_verify_fail():
    async def _run():
        t = FakeTransport()
        t.raise_on.add("verify")
        r = PairingRunner(t)
        await _drive_happy_path(r)
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    assert calls[-2:] == ["disconnect", "aclose"]   # ERROR also closes the transport
    assert state == pairing.PairingState(pairing.ERROR, 0, pairing.ERR_VERIFY)


def test_verify_returns_none_injects_verify_fail():
    async def _run():
        t = FakeTransport(verify_result=None)
        r = PairingRunner(t)
        await _drive_happy_path(r)
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    assert "persist_bond" not in calls
    assert state == pairing.PairingState(pairing.ERROR, 0, pairing.ERR_VERIFY)


def test_scan_error_is_logged_not_raised(capsys):
    async def _run():
        t = FakeTransport()
        t.raise_on.add("start_scan")
        r = PairingRunner(t)
        await r.start()  # must not raise
        return r.state

    state = asyncio.run(_run())
    assert state == pairing.PairingState(pairing.SCANNING, 0, pairing.ERR_NONE)
    assert "start_scan" in capsys.readouterr().out


def test_reset_injects_reset_done_after_remove_bond():
    async def _run():
        t = FakeTransport()
        r = PairingRunner(t)
        await _drive_happy_path(r)  # -> BONDED
        await r.reset()
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    # remove_bond (RESETTING's entry action), then IDLE (RESET_DONE) closes the transport again.
    assert calls[-2:] == ["remove_bond", "aclose"]
    assert state == pairing.PairingState(pairing.IDLE, 0, pairing.ERR_NONE)


def test_persist_bond_exception_stays_bonded_with_no_address():
    async def _run():
        t = FakeTransport()
        t.raise_on.add("persist_bond")
        r = PairingRunner(t)
        await _drive_happy_path(r)  # must not raise
        return t.calls, r.state, r.snapshot()

    calls, state, snap = asyncio.run(_run())
    # persist_bond's own failure is swallowed (bond is intact); BONDED still closes the transport.
    assert calls[-2:] == ["persist_bond", "aclose"]
    assert state == pairing.PairingState(pairing.BONDED, 0, pairing.ERR_NONE)
    assert snap["address"] is None


def test_reset_clears_stale_address():
    async def _run():
        t = FakeTransport(bond_address="AA:BB:CC:DD:EE:FF")
        r = PairingRunner(t)
        await _drive_happy_path(r)  # -> BONDED, address set
        await r.reset()
        return r.snapshot()

    snap = asyncio.run(_run())
    assert snap["address"] is None


def test_cancel_clears_stale_address():
    async def _run():
        t = FakeTransport(bond_address="AA:BB:CC:DD:EE:FF")
        r = PairingRunner(t)
        await _drive_happy_path(r)  # -> BONDED, address set
        await r.cancel()
        return r.snapshot()

    snap = asyncio.run(_run())
    assert snap["address"] is None


def test_on_bonded_callback_fires_with_the_persisted_address():
    """`runner.on_bonded` (wired by serve.py to adopt the address into the running daemon) must
    fire once persist_bond lands a real address -- not on the BONDED state transition itself
    (which happens one step earlier, before persist_bond has run)."""
    async def _run():
        seen = []
        t = FakeTransport(bond_address="AA:BB:CC:DD:EE:FF")
        r = PairingRunner(t)
        r.on_bonded = lambda addr: seen.append(addr)
        await _drive_happy_path(r)
        return seen

    seen = asyncio.run(_run())
    assert seen == ["AA:BB:CC:DD:EE:FF"]


def test_on_bonded_callback_does_not_fire_when_persist_bond_yields_no_address():
    async def _run():
        seen = []
        t = FakeTransport()
        t.raise_on.add("persist_bond")
        r = PairingRunner(t)
        r.on_bonded = lambda addr: seen.append(addr)
        await _drive_happy_path(r)
        return seen

    assert asyncio.run(_run()) == []


def test_aclose_is_skipped_when_the_transport_has_none():
    """Guard: a transport without `aclose` (only the dbus_fast-backed BluezTransport has one)
    must not crash the flow-end cleanup."""
    class BareTransport:
        def __init__(self):
            self.calls = []

        async def stop_scan(self):
            self.calls.append("stop_scan")

        async def start_scan(self):
            self.calls.append("start_scan")

    async def _run():
        t = BareTransport()
        r = PairingRunner(t)
        await r.start()
        await r.cancel()  # SCANNING -> IDLE: must not raise despite no aclose() on the transport
        return r.state

    state = asyncio.run(_run())
    assert state == pairing.PairingState(pairing.IDLE, 0, pairing.ERR_NONE)


def test_aclose_exception_is_logged_not_raised(capsys):
    async def _run():
        t = FakeTransport()
        t.raise_on.add("aclose")
        r = PairingRunner(t)
        await _drive_happy_path(r)  # must not raise
        return r.state

    state = asyncio.run(_run())
    assert state == pairing.PairingState(pairing.BONDED, 0, pairing.ERR_NONE)
    assert "aclose" in capsys.readouterr().out


def test_cancel_stops_and_returns_idle():
    async def _run():
        t = FakeTransport()
        r = PairingRunner(t)
        await r.start()
        await r.cancel()
        return t.calls, r.state

    calls, state = asyncio.run(_run())
    # idle-via-cancel also closes the transport (agent lifecycle).
    assert calls == ["start_scan", "stop_scan", "aclose"]
    assert state == pairing.PairingState(pairing.IDLE, 0, pairing.ERR_NONE)
