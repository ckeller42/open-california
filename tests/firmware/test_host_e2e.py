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
