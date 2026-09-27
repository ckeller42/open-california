"""Host firmware e2e against the Bumble fake unit.

``cali-host`` (``firmware/host/host_main.c``) is the firmware's session, console and pairing runner
on the NimBLE Linux port, talking HCI over TCP to a Bumble controller linked to the fake unit
(``tools/fake_unit_peripheral.py``). Driven through its console line protocol: ``pair`` /
``passkey N`` / ``forget`` / ``status`` / ``quit`` in, ``STATE {json}`` / ``SNAP {json}`` /
``LOG text`` out.

.. test:: Host firmware pairs and reads the fake unit end to end
   :id: T_FW_HOST_E2E
   :links: R_FW_PAIRING_SM, R_FAKE_UNIT_FIDELITY, R_FW_SESSION
"""
import time

import pytest

from calictl import overrides, protocol

# One xdist worker: the host_fw fixture runs `make` in firmware/host (like test_ble_store_kv.py).
pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]


async def _served_frames(unit):
    """{function: bytes} the fake serves for every function with a state char."""
    return {fn: unit.read_state(fn) for fn in unit.funcs if unit.funcs[fn].state_char}


async def _beats_seen(unit):
    return unit.beats


async def _serve_raw(unit, fn, frame, notify):
    unit.set_raw(fn, frame, notify=notify)


def _funcs():
    funcs = protocol.load()
    overrides.apply(funcs)
    return funcs


def _pair(fw, hu):
    fw.send("pair")
    fw.expect("STATE", lambda s: s["state"] == "waiting_passkey")
    fw.send("passkey %06d" % hu.call(hu.unit.next_passkey))
    return fw.expect("STATE", lambda s: s["state"] in ("bonded", "error"), timeout=40)


def test_fresh_pair_persists_the_identity(host_fw, hci_unit):
    fw = host_fw(hci_unit)
    fw.expect("STATE", lambda s: s["state"] == "idle")          # no bond -> idle, no scan
    s = _pair(fw, hci_unit)
    assert s["state"] == "bonded" and s["address"] == "C0:FF:EE:CA:11:F0"


def test_read_all_matches_python_decode(host_fw, hci_unit):
    fw = host_fw(hci_unit)
    _pair(fw, hci_unit)
    snap = fw.expect("SNAP", timeout=40)
    funcs = _funcs()
    served = hci_unit.call(_served_frames, hci_unit.unit)          # {function: bytes} the fake served
    assert set(snap["fn"]) == set(served)
    for name, frame in served.items():
        assert snap["fn"][name] == protocol.decode(funcs[name], frame), name


def test_heartbeat_keeps_the_link(host_fw, hci_unit):
    """20 s on one link: >= 20 new beats in the window, no session drop/reconnect logged in it,
    and the same link still delivers (a pushed change still produces a SNAP)."""
    fn = "cooler"
    fw = host_fw(hci_unit)
    _pair(fw, hci_unit)
    fw.expect("SNAP", timeout=40)
    mark = len(fw.log)
    before = hci_unit.call(_beats_seen, hci_unit.unit)
    time.sleep(20)
    beats = hci_unit.call(_beats_seen, hci_unit.unit) - before
    window = fw.log[mark:]
    assert not [line for line in window if line.startswith("LOG session:")], window
    assert beats >= 20, beats
    old = hci_unit.call(_served_frames, hci_unit.unit)[fn]
    new = bytes([old[0] ^ 0xFF]) + old[1:]
    want = protocol.decode(_funcs()[fn], new)
    hci_unit.call(_serve_raw, hci_unit.unit, fn, new, True)
    fw.expect("SNAP", lambda s: s["fn"].get(fn) == want, timeout=15)
    assert not [line for line in fw.log[mark:] if line.startswith("LOG session:")]


def test_passkey_outside_waiting_is_ignored(host_fw, hci_unit):
    fw = host_fw(hci_unit)
    fw.send("passkey 000000")                                     # idle: must be ignored
    fw.send("status")
    assert fw.expect("STATE")["state"] == "idle"
    fw.send("status")
    assert fw.expect("STATE")["state"] == "idle"
    assert not any("waiting_passkey" in line or "pairing" in line for line in fw.log)


def test_short_frame_yields_present_fields_only(host_fw, hci_unit):
    fn = "cooler"
    funcs = _funcs()
    full = hci_unit.call(_served_frames, hci_unit.unit)[fn]
    short = full[:1]
    hci_unit.call(_serve_raw, hci_unit.unit, fn, short, False)
    fw = host_fw(hci_unit)
    _pair(fw, hci_unit)
    snap = fw.expect("SNAP", timeout=40)
    want = protocol.decode(funcs[fn], short)
    assert snap["fn"][fn] == want
    assert len(want) < len(protocol.decode(funcs[fn], full))      # fields past the end are absent


def test_notify_during_read_all(host_fw, hci_unit):
    fn = "cooler"
    funcs = _funcs()
    fw = host_fw(hci_unit)
    _pair(fw, hci_unit)
    first = fw.expect("SNAP", timeout=40)
    old = hci_unit.call(_served_frames, hci_unit.unit)[fn]
    new = bytes([old[0] ^ 0xFF]) + old[1:]
    want = protocol.decode(funcs[fn], new)
    assert want != first["fn"][fn]
    hci_unit.call(_serve_raw, hci_unit.unit, fn, new, True)       # the unit pushes the change
    nxt = fw.expect("SNAP", lambda s: s["fn"].get(fn) == want, timeout=15)
    assert {k: v for k, v in nxt["fn"].items() if k != fn} == \
        {k: v for k, v in first["fn"].items() if k != fn}


async def _rotate_when_free(unit, timeout=10.0):
    """Rotate the unit's RPA once the old link is gone (``rotate_address`` is a no-op while
    connected — the harness ends the link when the firmware process exits, a moment later).
    Returns (old, new) advertising address."""
    import asyncio
    end = asyncio.get_running_loop().time() + timeout
    while unit.conn is not None:
        assert asyncio.get_running_loop().time() < end, "unit still connected"
        await asyncio.sleep(0.05)
    old = unit.advertising_address
    await unit.rotate_address()
    return old, unit.advertising_address


async def _drop_link(unit):
    """The unit hangs up on the central (a link lost mid-session)."""
    if unit.conn:
        await unit.conn.disconnect()


def test_restart_reconnects_with_the_bond_after_rotation(host_fw, hci_unit, tmp_path):
    """Reboot with the stored bond after the unit moved to a fresh resolvable private address:
    the firmware finds it again through the bond's IRK and re-encrypts, no passkey."""
    store = tmp_path / "store"
    fw = host_fw(hci_unit, store_dir=store); _pair(fw, hci_unit); fw.stop()
    old, new = hci_unit.call(_rotate_when_free, hci_unit.unit, timeout=15)
    assert old != new
    fw2 = host_fw(hci_unit, store_dir=store)
    fw2.expect("SNAP", timeout=40)                         # reconnected, no passkey asked
    assert not any("waiting_passkey" in l for l in fw2.log)


def test_unit_forgot_us_repairs(host_fw, hci_unit, tmp_path):
    """The unit dropped our bond ("Bluetooth zurücksetzen"): ``forget`` clears ours, a fresh pair
    bonds again."""
    store = tmp_path / "store"
    fw = host_fw(hci_unit, store_dir=store); _pair(fw, hci_unit); fw.stop()
    hci_unit.call(hci_unit.unit.forget_bonds)
    fw2 = host_fw(hci_unit, store_dir=store)
    fw2.expect("LOG session: reconnect in", timeout=40)     # the stale LTK is refused: no session
    assert not any(line.startswith("SNAP") for line in fw2.log)
    fw2.send("forget"); fw2.expect("STATE", lambda s: s["state"] == "idle" and not s["address"])
    assert _pair(fw2, hci_unit)["state"] == "bonded"


def test_pairing_mode_off_is_pairing_failed(host_fw):
    """The unit's pairing screen is closed: every attempt is refused -> pairing_failed after 3."""
    from .conftest import HciUnit
    hu = HciUnit(pairing_mode=False)
    try:
        fw = host_fw(hu)
        fw.send("pair")
        s = fw.expect("STATE", lambda s: s["state"] == "error", timeout=90)
        assert s["error"] == "pairing_failed" and s["attempts"] == 3
    finally:
        hu.close()


def test_just_works_build_is_refused(host_fw, hci_unit):
    """A build that sets the IO capability only AFTER connecting (the 2026-09-26 calictl bug) must
    fail against the fake unit — proof the fake would catch that regression."""
    fw = host_fw(hci_unit, binary="cali-host-jw")
    fw.send("pair")
    s = fw.expect("STATE", lambda s: s["state"] in ("error", "bonded", "waiting_passkey"), timeout=90)
    assert s["state"] == "error"


def test_link_drop_mid_read_reconnects(host_fw, hci_unit):
    """The unit hangs up on an established session: backoff reconnect by bond + a fresh read-all."""
    fw = host_fw(hci_unit); _pair(fw, hci_unit); fw.expect("SNAP", timeout=40)
    hci_unit.call(_drop_link, hci_unit.unit)               # fake disconnects the central
    fw.expect("LOG session: reconnect in", timeout=15)      # the session saw the link go
    fw.expect("SNAP", timeout=60)                           # backoff reconnect + fresh read_all
