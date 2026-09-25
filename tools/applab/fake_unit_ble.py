#!/usr/bin/env python3
"""A fake VW California camper unit as a Bumble BLE peripheral, for the Android emulator.

Thin CLI wrapper over ``tools.fake_unit_peripheral.build_unit`` — the GATT surface, scenario
console, mock state model and pairing delegate live there (shared with the CI pairing tests over
a Bumble ``LocalLink``). This module adds only what is specific to the app lab: the netsim
transport, the link watchdog (drop a link with no 1003 heartbeat), and periodic address rotation.
Pairing = LE passkey, the unit DISPLAYS a passkey (fresh per attempt unless ``FAKE_UNIT_PASSKEY``
pins one); the phone types it. See ``tools/applab/README.md``.

Run (emulator started with ``-packet-streamer-endpoint default``; needs ``bumble[android]``):

    FAKE_UNIT_VIN=<vin typed into the app> python tools/applab/fake_unit_ble.py [android-netsim]

Then on stdin, live scenario control (each change notifies subscribers):

    set airheater NormalOperation=1 RunningTimeinAction=42
    set roof InfoPopUp=5
    raw cooler 0943000000001606       # replace a whole state frame
    show airheater                    # decoded state
    pair on | pair off                # the unit's "Gerät verbinden" screen
    rotate                            # advertise from a fresh private address now
    forget                            # drop every stored bond
    q
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

REPO = os.environ.get("OC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from bumble.transport import open_transport  # noqa: E402

from tools.fake_unit_peripheral import build_unit  # noqa: E402

HEARTBEAT_TIMEOUT_S = float(os.environ.get("FAKE_UNIT_HEARTBEAT_TIMEOUT_S", "15"))
PAIRING_GRACE_S = float(os.environ.get("FAKE_UNIT_PAIRING_GRACE_S", "90"))   # before the first beat
# The unit's char 1002 is NOT an opaque id: it is the LAST 16 bytes of SHA-256(VIN string) — the app
# hashes the VIN the user typed and compares (ny/c.java case 8: st.u.f(vin).c("SHA-256"), tail 16).
VIN = os.environ.get("FAKE_UNIT_VIN", "")      # the VIN you type into the app; never committed (PII rule)
# Stable identity + bond store so a fake restart does NOT force an app re-pair (see main()).
FAKE_UNIT_ADDR = os.environ.get("FAKE_UNIT_ADDR", "C0:FF:EE:CA:11:F0")
KEYSTORE_PATH = os.environ.get(
    "FAKE_UNIT_KEYSTORE", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fake_unit_keys.json"))
log = logging.getLogger("fake_unit")

FIXED = os.environ.get("FAKE_UNIT_PASSKEY")                # unset: a fresh code per attempt
RPA_S = float(os.environ.get("FAKE_UNIT_RPA_S", "600"))     # the real unit rotated every ~10 min


async def link_watchdog(unit, conn) -> None:
    """The real unit drops a link that carries no 1003 heartbeat for ~15 s — armed only after the
    first beat, with PAIRING_GRACE_S before it (a human answers the passkey in 20–40 s)."""
    import time
    unit.last_beat_t = time.monotonic()
    unit.seen_beat = False
    while unit.conn is conn:
        await asyncio.sleep(1.0)
        limit = HEARTBEAT_TIMEOUT_S if unit.seen_beat else PAIRING_GRACE_S
        if time.monotonic() - unit.last_beat_t > limit:
            print(f"### no 1003 heartbeat for {limit:.0f}s — dropping link", flush=True)
            try:
                await conn.disconnect()
            except Exception as e:  # noqa: BLE001
                log.warning("disconnect failed: %s", e)
            return


async def rotator(unit) -> None:
    while True:
        await asyncio.sleep(RPA_S)
        await unit.rotate_address()
        print(f"### rotated: now advertising from {unit.advertising_address}", flush=True)


async def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "android-netsim"
    async with await open_transport(spec) as hci:
        unit = build_unit(hci.source, hci.sink, identity=FAKE_UNIT_ADDR, keystore=KEYSTORE_PATH,
                          vin=VIN, fixed_passkey=int(FIXED) if FIXED else None)
        loop = asyncio.get_event_loop()

        def on_conn(c):
            print(f"### CONNECTED from {c.peer_address}", flush=True)
            unit.tasks.append(loop.create_task(link_watchdog(unit, c)))
            c.on("disconnection", lambda r: print(f"### DISCONNECTED reason={r}", flush=True))

        unit.device.on("connection", on_conn)
        await unit.start()
        print(f"### VWCAMPER advertising from {unit.advertising_address} (identity {FAKE_UNIT_ADDR}, "
              f"keys {KEYSTORE_PATH})", flush=True)
        for coro in (unit.console(), unit.clock(), rotator(unit)):
            unit.tasks.append(loop.create_task(coro))

        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        term = asyncio.ensure_future(hci.source.terminated)
        await asyncio.wait([term, asyncio.ensure_future(stop.wait())], return_when=asyncio.FIRST_COMPLETED)
        print("### shutting down — deregistering radio", flush=True)
        try:
            await unit.device.power_off()
        except Exception as e:  # noqa: BLE001
            log.warning("power_off failed: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("BUMBLE_LOGLEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
