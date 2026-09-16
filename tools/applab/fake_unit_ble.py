#!/usr/bin/env python3
"""A fake VW California camper unit as a Bumble BLE peripheral, for the Android emulator.

Serves the unit's GATT surface (service 0xNN00 per function: NN01 control/write, NN02
state/read+notify; 1001 versions, 1002 VIN fingerprint, 1003 liveness counter, 1004 auth-gated
vehicle state) on top of calictl's own protocol dictionary + ``tools.mock_unit`` state model,
seeded from the committed firmware-0410 raw-frame baseline. Pairing = LE passkey, we DISPLAY a
fixed passkey (the real unit shows one on its screen; the phone types it). A link that carries
no 1003 heartbeat for 15 s is dropped, like the real unit does. See ``tools/applab/README.md``.

Run (emulator started with ``-packet-streamer-endpoint default``; needs ``bumble[android]``):

    FAKE_UNIT_VIN=<vin typed into the app> python tools/applab/fake_unit_ble.py [android-netsim]

Then on stdin, live scenario control (each change notifies subscribers):

    set airheater NormalOperation=1 RunningTimeinAction=42
    set roof InfoPopUp=5
    raw cooler 0943000000001606       # replace a whole state frame
    show airheater                    # decoded state
    q
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys

REPO = os.environ.get("OC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from bumble.att import Attribute, AttributeValue  # noqa: E402
from bumble.core import UUID, AdvertisingData  # noqa: E402
from bumble.device import Device, DeviceConfiguration  # noqa: E402
from bumble.gatt import (  # noqa: E402
    GATT_CHARACTERISTIC_USER_DESCRIPTION_DESCRIPTOR,
    Characteristic,
    Descriptor,
    Service,
)
from bumble.hci import Address  # noqa: E402
from bumble.pairing import PairingConfig, PairingDelegate  # noqa: E402
from bumble.transport import open_transport  # noqa: E402

from calictl import overrides, protocol  # noqa: E402
from tools.mock_unit import MockCamperUnit, MockDisconnect, _pack_state  # noqa: E402

BASE = "-6c77-4b7d-bbf6-a5e587701f3d"
PASSKEY = int(os.environ.get("FAKE_UNIT_PASSKEY", "123456"))
HEARTBEAT_TIMEOUT_S = float(os.environ.get("FAKE_UNIT_HEARTBEAT_TIMEOUT_S", "15"))
# The unit's char 1002 is NOT an opaque id: it is the LAST 16 bytes of SHA-256(VIN string) — the app
# hashes the VIN the user typed and compares (ny/c.java case 8: st.u.f(vin).c("SHA-256"), tail 16).
VIN = os.environ.get("FAKE_UNIT_VIN", "")      # the VIN you type into the app; never committed (PII rule)
VIN_FINGERPRINT = hashlib.sha256(VIN.encode()).digest()[-16:]
BASELINE = os.path.join(REPO, "tests", "scenarios", "firmware", "baseline-0410.json")
log = logging.getLogger("fake_unit")


def cu(slot: str) -> str:
    return f"0000{slot}{BASE}"


class UnitDelegate(PairingDelegate):
    """The unit DISPLAYS a passkey; the phone types it."""

    def __init__(self):
        super().__init__(io_capability=PairingDelegate.IoCapability.DISPLAY_OUTPUT_ONLY)

    async def generate_passkey(self) -> int:
        print(f"\n### PASSKEY {PASSKEY:06d} — type this on the phone ###\n", flush=True)
        return PASSKEY

    async def display_number(self, number: int, digits: int) -> None:
        print(f"\n### DISPLAY {number:0{digits}d} ###\n", flush=True)


class FakeUnit:
    def __init__(self):
        self.funcs = protocol.load()
        overrides.apply(self.funcs)
        base = json.load(open(BASELINE))["raw_frames_hex"]
        self.raw: dict[str, bytes] = {fn: bytes.fromhex(h) for fn, h in base.items()}
        # Seed the mock's decoded state from the baseline frames; keep the raw frame as the
        # read value until a function is touched (write or scenario), so unmodelled bits survive.
        seed = {}
        for fn, frame in self.raw.items():
            if fn in self.funcs:
                seed[fn] = protocol.decode(self.funcs[fn], frame)
        self.unit = MockCamperUnit(seed=seed)
        self.unit.armed = True             # the emulator app keeps its own heartbeat; don't gate
        self.dirty: set[str] = set()
        self.by_state: dict[str, str] = {}   # state char uuid -> fn
        self.chars: dict[str, Characteristic] = {}
        self.device: Device | None = None
        self.tasks: list = []                # keep task refs (else GC kills them)
        self.last_beat_t: float = 0.0        # monotonic time of the last 1003 write
        self.conn = None                     # current Bumble connection (single-link unit)
        for fn, f in self.funcs.items():
            if f.state_char:
                self.by_state[f.state_char.lower()] = fn
            if fn in seed:
                repack = _pack_state(f, seed[fn])
                if repack != self.raw[fn]:
                    log.warning("%s: dictionary does not round-trip the baseline frame (%s vs %s); "
                                "serving raw until modified", fn, repack.hex(), self.raw[fn].hex())

    # --- reads / writes -------------------------------------------------------------
    def read_state(self, fn: str) -> bytes:
        if fn in self.dirty or fn not in self.raw:
            v = self.unit.read(self.funcs[fn].state_char)
        else:
            v = self.raw[fn]
        log.info("READ %s -> %s", fn, v.hex())
        return v

    def on_write(self, fn: str, data: bytes) -> None:
        f = self.funcs[fn]
        try:
            self.unit.write(f.control_char, bytes(data))
        except MockDisconnect as e:
            log.warning("REJECTED write to %s: %s", fn, e)
            return
        self.dirty.add(fn)
        log.info("WRITE %s %s -> state %s", fn, bytes(data).hex(), self.unit.decoded(fn))
        self.schedule_notify(fn)

    def on_beat(self, data: bytes) -> None:
        import time
        self.unit.beat(data)
        self.last_beat_t = time.monotonic()

    async def link_watchdog(self, conn) -> None:
        """The real unit drops a link that carries no 1003 liveness heartbeat for ~15 s (and a
        connected peripheral cannot advertise, so a stale link also hides the unit from the app's
        scan). Mirror it: disconnect when no beat arrived within HEARTBEAT_TIMEOUT_S."""
        import time
        self.last_beat_t = time.monotonic()
        while self.conn is conn:
            await asyncio.sleep(1.0)
            if time.monotonic() - self.last_beat_t > HEARTBEAT_TIMEOUT_S:
                print(f"### no 1003 heartbeat for {HEARTBEAT_TIMEOUT_S:.0f}s — dropping link", flush=True)
                try:
                    await conn.disconnect()
                except Exception as e:  # noqa: BLE001
                    log.warning("disconnect failed: %s", e)
                return

    async def clock(self) -> None:
        """Drive the mock's clock once a second (RTC, countdowns, roof travel, ignition coupling —
        see ``MockCamperUnit.tick``) and notify what changed. The real unit does NOT stream: a
        buspi trace (2026-09-16) showed each subscribed char notified exactly once right after its
        CCCD write and then only on change, so pushes here are change-driven too (plus the
        on-subscribe push wired in build_services)."""
        while True:
            await asyncio.sleep(1.0)
            changed = self.unit.tick(1.0)
            self.dirty.update(changed)
            for fn in changed:
                self.schedule_notify(fn)

    def schedule_notify(self, fn: str) -> None:
        ch = self.chars.get(fn)
        if ch is not None and self.device is not None:
            self.tasks.append(asyncio.get_event_loop().create_task(self.device.notify_subscribers(ch)))
            self.tasks = [t for t in self.tasks if not t.done()]

    # --- GATT --------------------------------------------------------------------------
    def build_services(self) -> list[Service]:
        services = []
        desc = lambda s: Descriptor(GATT_CHARACTERISTIC_USER_DESCRIPTION_DESCRIPTOR,  # noqa: E731
                                    Attribute.READABLE, s.encode())
        groups: dict[str, list[Characteristic]] = {}
        for fn, f in self.funcs.items():
            slot = f.state_char[4:8]              # e.g. "1102"
            svc = slot[:2] + "00"
            if fn == "general":                   # 1001 = versions, read-only (no notify)
                groups.setdefault(svc, []).append(Characteristic(
                    cu("1001"), Characteristic.Properties.READ, Attribute.READABLE,
                    AttributeValue(read=lambda c, fn=fn: self.read_state(fn)), [desc("Info")]))
                continue
            props = Characteristic.Properties.READ | Characteristic.Properties.NOTIFY
            perms = Attribute.READABLE
            if fn == "vehicle":                   # the auth-gated read that forces bonding
                perms = Attribute.READABLE | Attribute.READ_REQUIRES_AUTHENTICATION
            st = Characteristic(f.state_char, props, perms,
                                AttributeValue(read=lambda c, fn=fn: self.read_state(fn)),
                                [desc("State")])
            # The unit pushes the current value once as soon as a client enables notifications
            # (observed on buspi 2026-09-16: one notify per char right after each CCCD write).
            st.on(Characteristic.EVENT_SUBSCRIPTION,
                  lambda conn, notify, indicate, fn=fn: notify and self.schedule_notify(fn))
            self.chars[fn] = st
            lst = groups.setdefault(svc, [])
            if f.control_char:
                lst.append(Characteristic(
                    f.control_char,
                    Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
                    Attribute.WRITEABLE,
                    AttributeValue(write=lambda c, v, fn=fn: self.on_write(fn, v)), [desc("Control")]))
            lst.append(st)
        # service 1000 extras: 1002 opaque vehicle id, 1003 liveness counter (write)
        groups.setdefault("1000", []).extend([
            Characteristic(cu("1002"), Characteristic.Properties.READ, Attribute.READABLE,
                           VIN_FINGERPRINT, [desc("VIN")]),
            Characteristic(cu("1003"),
                           Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
                           Attribute.WRITEABLE,
                           AttributeValue(write=lambda c, v: self.on_beat(bytes(v))), [desc("Counter")]),
        ])
        # 1900 extras the app may read on the SAT/system page
        groups.setdefault("1900", []).extend([
            Characteristic(cu("1903"), Characteristic.Properties.READ, Attribute.READABLE, b"0410\x000207\x00"),
            Characteristic(cu("1904"), Characteristic.Properties.READ, Attribute.READABLE, b"California"),
            Characteristic(cu("1905"), Characteristic.Properties.READ, Attribute.READABLE, b"********"),
        ])
        # f000 generic write
        groups.setdefault("f000", []).append(Characteristic(
            cu("f000"), Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
            Attribute.WRITEABLE, AttributeValue(write=lambda c, v: log.info("f000 write %s", bytes(v).hex()))))
        for svc, chars in sorted(groups.items()):
            # sort control before state before extras, by slot
            chars.sort(key=lambda ch: str(ch.uuid))
            services.append(Service(cu(svc), chars))
        return services

    # --- scenario console ------------------------------------------------------------
    async def console(self):
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                await asyncio.sleep(3600)
                continue
            parts = line.decode().strip().split()
            if not parts:
                continue
            try:
                if parts[0] == "q":
                    os._exit(0)
                elif parts[0] == "set":
                    fn = parts[1]
                    st = self.unit.state.setdefault(fn, {})
                    for kv in parts[2:]:
                        k, v = kv.split("=")
                        st[k] = int(v)
                    self.dirty.add(fn)
                    print(f"{fn} -> {self.unit.decoded(fn)}", flush=True)
                    self.schedule_notify(fn)
                elif parts[0] == "raw":
                    fn, hx = parts[1], parts[2]
                    self.raw[fn] = bytes.fromhex(hx)
                    self.unit.state[fn] = protocol.decode(self.funcs[fn], self.raw[fn])
                    self.dirty.discard(fn)
                    print(f"{fn} raw -> {self.raw[fn].hex()}", flush=True)
                    self.schedule_notify(fn)
                elif parts[0] == "show":
                    fn = parts[1]
                    print(f"{fn} raw={self.read_state(fn).hex()} decoded={protocol.decode(self.funcs[fn], self.read_state(fn))}", flush=True)
                else:
                    print("commands: set <fn> F=v ... | raw <fn> <hex> | show <fn> | q", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"error: {e}", flush=True)


async def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "android-netsim"
    unit = FakeUnit()
    async with await open_transport(spec) as hci:
        cfg = DeviceConfiguration(
            name="VWCAMPER",
            address=Address("C0:FF:EE:CA:11:F0"),
            keystore="JsonKeyStore",
            irk=bytes.fromhex("865F81FF5A8B486EAAE29A27AD9F77DC"),
            advertising_interval_min=100, advertising_interval_max=100,
        )
        dev = Device.from_config_with_hci(cfg, hci.source, hci.sink)
        unit.device = dev
        dev.add_services(unit.build_services())
        dev.pairing_config_factory = lambda conn: PairingConfig(
            sc=True, mitm=True, bonding=True, delegate=UnitDelegate())
        def on_conn(c):
            print(f"### CONNECTED from {c.peer_address}", flush=True)
            unit.conn = c
            unit.tasks.append(asyncio.get_event_loop().create_task(unit.link_watchdog(c)))
            c.on("disconnection", lambda r: print(f"### DISCONNECTED reason={r}", flush=True))
        dev.on("connection", on_conn)
        for a in dev.gatt_server.attributes:
            log.debug("%s", a)
        await dev.power_on()
        adv = bytes(AdvertisingData([
            (AdvertisingData.FLAGS, bytes([0x06])),
            (AdvertisingData.COMPLETE_LOCAL_NAME, b"VWCAMPER"),
        ]))
        scan_rsp = bytes(AdvertisingData([
            (AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS, UUID(cu("1000")).to_bytes()),
        ]))
        await dev.start_advertising(auto_restart=True, advertising_data=adv, scan_response_data=scan_rsp)
        print("### VWCAMPER advertising — passkey", f"{PASSKEY:06d}", flush=True)
        unit.tasks.append(asyncio.get_event_loop().create_task(unit.console()))
        unit.tasks.append(asyncio.get_event_loop().create_task(unit.clock()))
        await hci.source.terminated


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("BUMBLE_LOGLEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
