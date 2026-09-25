"""Real-BlueZ pairing rig — run ONLY by the `pairing-real-stack` CI job inside a VM whose kernel has
Bluetooth. A Bumble virtual controller appears to BlueZ as a new adapter (vhci), linked to a second
Bumble controller hosting the fake unit; calictl's real PairingRunner + BluezTransport pair with it.
Prints one line per check and exits non-zero on the first failure. Needs root + a running bluetoothd.

Not collected by pytest (not named ``test_*.py``): it needs root, /dev/vhci, dbus, bluetoothd, bleak
and dbus_fast — see ``tests/realstack/vm.sh`` (runner side) and ``in_vm.sh`` (guest side).

.. test:: Real BlueZ pairs with the fake unit (CI VM)
   :id: T_PAIRING_REALSTACK
   :links: R_PAIRING_SM, R_FAKE_UNIT_FIDELITY
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

        rpa1 = unit.advertising_address
        t, runner, snap = await pair_once(adapter, unit)
        check(snap["state"] == "bonded", "wizard bonds over real BlueZ: %s" % snap)
        check((snap["address"] or "").upper() == IDENTITY,
              "identity cached, not the RPA %s: %s" % (rpa1, snap["address"]))
        check(json.loads(CACHE.read_text())["address"].upper() == IDENTITY, "pairing cache holds the identity")
        await t.disconnect()
        check(await until(lambda: unit.conn is None, 15), "the unit sees the wizard's link drop")

        await unit.rotate_address()
        check(unit.advertising_address != rpa1,
              "unit rotated its RPA %s -> %s" % (rpa1, unit.advertising_address))
        funcs = protocol.load()
        overrides.apply(funcs)
        raw = await device.CamperDevice(IDENTITY, adapter=adapter).read_all(funcs)
        check(len(raw) >= 5, "daemon reads %d functions over the bond after rotation" % len(raw))
        check(await until(lambda: unit.conn is None, 15), "the unit sees the daemon's link drop")

        await unit.forget_bonds()                   # the unit's Bluetooth reset; BlueZ keeps its bond
        t2, _, snap2 = await pair_once(adapter, unit)
        check(snap2["state"] == "bonded",
              "re-pair after the unit forgot its bonds (AlreadyExists self-heal): %s" % snap2)
        check((snap2["address"] or "").upper() == IDENTITY, "re-pair caches the identity: %s" % snap2["address"])
        await t2.disconnect()
        check(await until(lambda: unit.conn is None, 15), "the unit sees the re-pair link drop")

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
