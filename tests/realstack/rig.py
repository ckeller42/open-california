"""Real-BlueZ pairing rig — run ONLY by the `pairing-real-stack` CI job inside a VM whose kernel has
Bluetooth. A Bumble virtual controller appears to BlueZ as a new adapter (vhci), linked to a second
Bumble controller hosting the fake unit; calictl's real PairingRunner + BluezTransport pair with it.
Prints one line per check and exits non-zero on the first failure. Needs root + a running bluetoothd.
The wizard must release its own link at flow end (no manual disconnect here: the daemon reads right
after it), and a wizard run against an ASLEEP unit (no adverts, links refused, while another client
holds discovery) must end connect_failed/timeout with BlueZ's bond and pairing.json kept.

Not collected by pytest (not named ``test_*.py``): it needs root, /dev/vhci, dbus, bluetoothd, bleak
and dbus_fast — see ``tests/realstack/vm.sh`` (runner side) and ``in_vm.sh`` (guest side).

.. test:: Real BlueZ pairs with the fake unit (CI VM)
   :id: T_PAIRING_REALSTACK
   :links: R_PAIRING_SM, R_FAKE_UNIT_FIDELITY, R_PAIRING_BLUEZ_TRANSPORT, R_PAIRING_STALE_BOND_RECOVERY
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
CACHE = Path(tempfile.mkdtemp()) / "pairing.json"
os.environ["CALICTL_PAIRING_CACHE"] = str(CACHE)

from bumble.controller import Controller  # noqa: E402
from bumble.link import LocalLink  # noqa: E402
from bumble.transport import open_transport  # noqa: E402

from calictl import device, overrides, protocol  # noqa: E402
from calictl.pairing_bluez import BluezTransport, PairingRunner  # noqa: E402
from tools.fake_unit_peripheral import IDENTITY, build_unit  # noqa: E402


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        sys.exit(1)


async def until(pred, timeout=40.0):
    """Poll ``pred()`` every 100 ms until it is truthy or ``timeout`` elapses; return its last value."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not (v := pred()) and loop.time() < end:
        await asyncio.sleep(0.1)
    return v


async def until_state(runner, names, timeout=40.0):
    await until(lambda: runner.snapshot()["state"] in names, timeout)
    return runner.snapshot()


async def pair_once(adapter, unit):
    t = BluezTransport(adapter_path="/org/bluez/" + adapter)
    runner = PairingRunner(t)
    t.on_event = runner.handle
    await runner.start()
    snap = await until_state(runner, {"waiting_passkey", "error"})
    print("  .. %s" % snap, flush=True)
    if snap["state"] == "waiting_passkey":
        await runner.enter_passkey(await unit.next_passkey())
        snap = await until_state(runner, {"bonded", "error"})
    return t, runner, snap


async def rerun_over_valid_bond(adapter, unit):
    """Run the wizard while both sides still hold a working bond; it must not reach a passkey."""
    unit.passkey_shown.clear()
    t = BluezTransport(adapter_path="/org/bluez/" + adapter)
    runner = PairingRunner(t)
    t.on_event = runner.handle
    await runner.start()
    snap = await until_state(runner, {"bonded", "error", "waiting_passkey"})
    print("  .. %s" % snap, flush=True)
    return t, runner, snap


async def bond_keys(unit):
    """The fake unit's stored bonds as comparable dicts."""
    return [(name, keys.to_dict()) for name, keys in await unit.device.keystore.get_all()]


async def main():
    before = set(os.listdir("/sys/class/bluetooth"))
    link = LocalLink()
    async with await open_transport("vhci") as hci:
        Controller("bluez-side", host_source=hci.source, host_sink=hci.sink, link=link)
        uc = Controller("unit", link=link)
        unit = build_unit(uc, uc)
        await unit.start()
        await asyncio.sleep(3)
        new = sorted(set(os.listdir("/sys/class/bluetooth")) - before)
        check(bool(new), "BlueZ sees the virtual adapter %s" % new)
        adapter = new[0]
        subprocess.run(["btmgmt", "--index", adapter[3:], "power", "on"], check=False)
        await asyncio.sleep(2)

        funcs = protocol.load()
        overrides.apply(funcs)
        rpa1 = unit.advertising_address
        t, runner, snap = await pair_once(adapter, unit)
        check(snap["state"] == "bonded", "wizard bonds over real BlueZ: %s" % snap)
        check((snap["address"] or "").upper() == IDENTITY,
              "identity cached, not the RPA %s: %s" % (rpa1, snap["address"]))
        check(json.loads(CACHE.read_text())["address"].upper() == IDENTITY, "pairing cache holds the identity")
        # no manual disconnect: the wizard itself must release its link at flow end (aclose)
        check(await until(lambda: unit.conn is None, 15), "the unit sees the wizard's link drop")

        # R17: re-running the wizard over a WORKING bond keeps it — no new passkey, same keys.
        keys = await bond_keys(unit)
        tk, _, snapk = await rerun_over_valid_bond(adapter, unit)
        check(snapk["state"] == "bonded", "wizard over a working bond ends bonded: %s" % snapk)
        check(not unit.passkey_shown.is_set(), "no new passkey was requested (bond kept)")
        check(keys and await bond_keys(unit) == keys, "the unit's bond is unchanged")
        check((snapk["address"] or "").upper() == IDENTITY, "identity still cached: %s" % snapk["address"])
        check(await until(lambda: unit.conn is None, 15), "the unit sees the kept-bond link drop")

        await unit.rotate_address()
        check(unit.advertising_address != rpa1,
              "unit rotated its RPA %s -> %s" % (rpa1, unit.advertising_address))
        raw = await device.CamperDevice(IDENTITY, adapter=adapter).read_all(funcs)
        check(len(raw) >= 5, "daemon reads %d functions over the bond after rotation" % len(raw))
        check(await until(lambda: unit.conn is None, 15), "the unit sees the daemon's link drop")

        await unit.forget_bonds()                   # the unit's Bluetooth reset; BlueZ keeps its bond
        t2, _, snap2 = await pair_once(adapter, unit)
        check(snap2["state"] == "bonded",
              "re-pair after the unit forgot its bonds (stale-bond self-heal): %s" % snap2)
        check((snap2["address"] or "").upper() == IDENTITY, "re-pair caches the identity: %s" % snap2["address"])
        check(await until(lambda: unit.conn is None, 15), "the unit sees the re-pair link drop")
        raw = await device.CamperDevice(IDENTITY, adapter=adapter).read_all(funcs)
        check(len(raw) >= 5, "daemon reads %d functions right after the wizard (no manual disconnect)"
              % len(raw))
        check(await until(lambda: unit.conn is None, 15), "the unit sees the daemon's link drop")

        # An ASLEEP unit must never cost the bond: another client keeps discovery on while the unit
        # is still heard (so BlueZ keeps its RSSI), then the unit sleeps (no adverts, no links) and
        # the owner opens the wizard remotely. It must end connect_failed/timeout, bond + cache kept.
        keys = await bond_keys(unit)
        other = await hold_discovery(adapter)
        await asyncio.sleep(2)
        await unit.device.stop_advertising()
        unit.refuse_connections = True
        unit.passkey_shown.clear()
        t4 = BluezTransport(adapter_path="/org/bluez/" + adapter)
        runner4 = PairingRunner(t4)
        t4.on_event = runner4.handle
        await runner4.start()
        snap4 = await until_state(runner4, {"error", "bonded", "waiting_passkey"}, 120)
        print("  .. %s" % snap4, flush=True)
        check(snap4["state"] == "error" and snap4["error"] in ("connect_failed", "timeout"),
              "wizard against an asleep unit ends connect_failed/timeout: %s" % snap4)
        check(await bluez_bonded(adapter) is True, "BlueZ still holds the bond after the asleep run")
        check(CACHE.exists() and json.loads(CACHE.read_text())["address"].upper() == IDENTITY,
              "pairing.json kept after the asleep run")
        check(not unit.passkey_shown.is_set() and await bond_keys(unit) == keys,
              "the unit's bond is untouched by the asleep run")
        other.disconnect()

        unit.refuse_connections = False                 # the unit wakes
        await unit._advertise()
        await asyncio.sleep(2)
        tw, _, snapw = await rerun_over_valid_bond(adapter, unit)
        check(snapw["state"] == "bonded", "after waking, the wizard over the kept bond ends bonded: %s"
              % snapw)
        check(not unit.passkey_shown.is_set() and await bond_keys(unit) == keys,
              "after waking, no new passkey and the unit's bond is unchanged")
        check(await until(lambda: unit.conn is None, 15), "the unit sees the post-wake link drop")

        other = await hold_discovery(adapter)       # "another BlueZ client" (e.g. HA Bluetooth)
        t3 = BluezTransport(adapter_path="/org/bluez/" + adapter)
        runner3 = PairingRunner(t3)
        t3.on_event = runner3.handle
        await runner3.start()
        busy = await until(lambda: runner3.snapshot()["radio_busy"], 30)
        check(busy is True, "radio_busy while another client keeps discovering: %s" % runner3.snapshot())
        await runner3.cancel()
        other.disconnect()
    print("ALL PASS", flush=True)


async def bluez_bonded(adapter):
    """Whether BlueZ (a fresh D-Bus client, like the daemon after a restart) still holds a bond for
    the unit's identity on ``adapter``: ``Device1.Bonded`` (or ``Paired`` on an older BlueZ)."""
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        obj = bus.get_proxy_object("org.bluez", "/", await bus.introspect("org.bluez", "/"))
        objects = await obj.get_interface("org.freedesktop.DBus.ObjectManager").call_get_managed_objects()
        for path, ifaces in objects.items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev or not path.startswith("/org/bluez/%s/" % adapter):
                continue
            if str(dev["Address"].value).upper() != IDENTITY:
                continue
            flag = dev.get("Bonded") or dev.get("Paired")
            return bool(flag.value) if flag is not None else False
        return False
    finally:
        bus.disconnect()


async def hold_discovery(adapter):
    """Open a SECOND system-bus client and start discovery on ``adapter`` — BlueZ keeps scanning
    for as long as this client's session lives, exactly like a co-resident scanner."""
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    path = "/org/bluez/" + adapter
    obj = bus.get_proxy_object("org.bluez", path, await bus.introspect("org.bluez", path))
    await obj.get_interface("org.bluez.Adapter1").call_start_discovery()
    await asyncio.sleep(1)
    return bus


if __name__ == "__main__":
    asyncio.run(main())
