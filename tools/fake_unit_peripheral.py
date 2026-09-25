"""The fake VW California camper unit as a Bumble BLE peripheral — ONE implementation reached three
ways: the Android app lab (netsim, local), calictl's pairing tests over a Bumble ``LocalLink`` (CI),
and real BlueZ through a vhci controller inside a CI VM. The app lab validates it against the real
app, so "the app accepts it" carries over to "calictl passes against it".

.. req:: Fake unit fidelity
   :id: R_FAKE_UNIT_FIDELITY

   Advertises ``VWCAMPER`` from a rotating resolvable private address over a fixed identity + IRK;
   pairs with LE Secure Connections passkey entry where the unit DISPLAYS a fresh code per attempt;
   refuses new bonds while its pairing screen is closed; holds one connection at a time. Evidence per
   behaviour: docs/business-logic/protocol-crosscheck-applab.md ("Pairing").
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets

from bumble.att import Attribute, AttributeValue
from bumble.core import UUID, AdvertisingData
from bumble.device import Device, DeviceConfiguration
from bumble.gatt import (
    GATT_CHARACTERISTIC_USER_DESCRIPTION_DESCRIPTOR,
    Characteristic,
    Descriptor,
    Service,
)
from bumble.hci import Address, OwnAddressType
from bumble.pairing import PairingConfig, PairingDelegate

from calictl import overrides, protocol
from tools.mock_unit import MockCamperUnit, MockDisconnect, _pack_state

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "-6c77-4b7d-bbf6-a5e587701f3d"
NAME = "VWCAMPER"
IDENTITY = "C0:FF:EE:CA:11:F0"
IRK = bytes.fromhex("865F81FF5A8B486EAAE29A27AD9F77DC")
BASELINE = os.path.join(REPO, "tests", "scenarios", "firmware", "baseline-0410.json")
log = logging.getLogger("fake_unit")


def cu(slot: str) -> str:
    return f"0000{slot}{BASE}"


class UnitDelegate(PairingDelegate):
    """The unit DISPLAYS a passkey; the central types it. A fresh code per attempt unless
    ``unit.fixed_passkey`` pins one. ``unit.pairing_mode`` mirrors the unit's "Gerät verbinden"
    screen: while it is off, a new pairing request is refused (SMP Pairing Not Supported)."""

    def __init__(self, unit: FakeUnit):
        super().__init__(io_capability=PairingDelegate.IoCapability.DISPLAY_OUTPUT_ONLY)
        self._unit = unit

    async def accept(self) -> bool:
        return self._unit.pairing_mode

    async def generate_passkey(self) -> int:
        u = self._unit
        code = u.fixed_passkey if u.fixed_passkey is not None else secrets.randbelow(1_000_000)
        u.last_passkey = code
        u.passkey_shown.set()
        print(f"\n### PASSKEY {code:06d} — the unit shows this; type it on the central ###\n", flush=True)
        return code

    async def display_number(self, number: int, digits: int) -> None:
        print(f"\n### DISPLAY {number:0{digits}d} ###\n", flush=True)


class FakeUnit:
    def __init__(self, vin: str = ""):
        self.funcs = protocol.load()
        overrides.apply(self.funcs)
        self.vin_fingerprint = hashlib.sha256(vin.encode()).digest()[-16:]
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
        self.seen_beat = False               # a beat arrived on the current link (watchdog arms)
        self.conn = None                     # current Bumble connection (single-link unit)
        self.pairing_mode = True           # the unit's "Gerät verbinden" screen is open
        self.refuse_connections = False    # test knob: drop every link at once
        self.fixed_passkey: int | None = None
        self.last_passkey: int | None = None
        self.passkey_shown = asyncio.Event()
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
        self.seen_beat = True

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
                           self.vin_fingerprint, [desc("VIN")]),
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
        """Read scenario commands from a FIFO in a background thread and apply them on the loop.

        Reads a named FIFO (``FAKE_UNIT_FIFO``, default ``$TMPDIR/applab/fake_unit.in``) rather than
        stdin: asyncio's stdin pipe reader raises ``OSError: [Errno 22]`` on macOS when stdin is a
        FIFO or is redirected under ``nohup``/``labctl``, and the failure fires in a later callback
        that a try/except around the setup can't catch. A blocking thread on a FIFO path sidesteps
        the selector entirely and works no matter how the process was launched.
        """
        import threading

        loop = asyncio.get_event_loop()
        path = os.environ.get(
            "FAKE_UNIT_FIFO", os.path.join(os.environ.get("TMPDIR", "/tmp"), "applab", "fake_unit.in"))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                os.mkfifo(path)
        except OSError as e:
            log.info("scenario console disabled (no FIFO at %s: %s)", path, e)
            return
        log.info("scenario console: echo commands into %s "
                 "(set <fn> F=v | raw <fn> <hex> | show <fn> | pair on|off | rotate | forget | q)", path)

        def pump():
            while True:
                with open(path, encoding="utf-8") as fifo:   # reopen after each writer closes (EOF)
                    for line in fifo:
                        loop.call_soon_threadsafe(self._console_line, line)

        threading.Thread(target=pump, daemon=True).start()

    def _console_line(self, line: str):
        parts = line.strip().split()
        if parts:
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
                elif parts[0] == "pair":          # pair on | pair off — the unit's pairing screen
                    self.pairing_mode = parts[1] == "on"
                    print(f"pairing mode {'ON' if self.pairing_mode else 'off'}", flush=True)
                elif parts[0] == "rotate":
                    self.tasks.append(asyncio.get_running_loop().create_task(self.rotate_address()))
                    self.tasks = [t for t in self.tasks if not t.done()]
                elif parts[0] == "forget":        # like "Bluetooth zurücksetzen"
                    self.tasks.append(asyncio.get_running_loop().create_task(self.forget_bonds()))
                    self.tasks = [t for t in self.tasks if not t.done()]
                    print("bonds forgotten", flush=True)
                else:
                    print("commands: set <fn> F=v ... | raw <fn> <hex> | show <fn> | pair on|off | rotate | forget | q", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"error: {e}", flush=True)

    # --- link-level behaviour ----------------------------------------------------------
    def _on_connection(self, conn) -> None:
        if self.refuse_connections:
            # Keep the task reference (as schedule_notify does) — an orphaned task can be
            # garbage-collected before it runs, silently dropping the refusal.
            self.tasks.append(asyncio.get_event_loop().create_task(conn.disconnect()))
            self.tasks = [t for t in self.tasks if not t.done()]
            return
        self.conn = conn
        conn.on("disconnection", lambda reason, c=conn: self._on_disconnection(c))

    def _on_disconnection(self, conn) -> None:
        if self.conn is conn:
            self.conn = None

    async def _advertise(self) -> None:
        adv = bytes(AdvertisingData([
            (AdvertisingData.FLAGS, bytes([0x06])),
            (AdvertisingData.COMPLETE_LOCAL_NAME, NAME.encode()),
        ]))
        scan_rsp = bytes(AdvertisingData([
            (AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS, UUID(cu("1000")).to_bytes()),
        ]))
        # Legacy connectable advertising stops by itself when a central connects and auto_restart
        # resumes it on disconnect -> exactly one connection slot, like the real unit.
        await self.device.start_advertising(
            auto_restart=True, advertising_data=adv, scan_response_data=scan_rsp,
            own_address_type=OwnAddressType.RESOLVABLE_OR_RANDOM)

    async def start(self) -> None:
        await self.device.power_on()
        await self._advertise()

    async def rotate_address(self) -> None:
        """Advertise from a fresh resolvable private address. Bumble keeps the advertising address
        until advertising restarts, so rotation is explicit (the lab CLI calls this on a timer).
        A no-op while connected — the unit is not advertising then.

        Bumble-only workaround: ``HCI_LE_Set_Advertising_Set_Random_Address_Command`` (extended
        advertising, what ``_advertise()`` uses) only updates that advertising SET's address;
        ``Controller.random_address`` — a separate legacy attribute the simulated ``LocalLink``
        uses to stamp the SOURCE address on every ACL/GATT packet once connected — is never synced
        to it. Left stale, a central that connects under the new RPA gets every GATT response
        misrouted ("no connection for <stale-address>", silently dropped): ``discover_services()``
        hangs forever on the very first request after a rotate + reconnect. Found via
        ``tests/test_pairing_link.py::test_rotation_between_scan_and_connect_recovers`` (no real
        unit rotates this way, so real hardware is unaffected — this only matters for the Bumble
        harness). Sync it explicitly so ACL routing tracks the address we actually advertise under.
        """
        if self.conn is not None:
            return
        await self.device.stop_advertising()
        await self.device.update_rpa()
        self.device.host.controller.random_address = self.device.random_address
        await self._advertise()

    async def forget_bonds(self) -> None:
        """Like the unit's "Bluetooth zurücksetzen": drop every stored bond."""
        await self.device.keystore.delete_all()

    async def next_passkey(self, timeout: float = 10.0) -> int:
        """Wait for the code the unit shows for the current pairing attempt (test/rig hook)."""
        await asyncio.wait_for(self.passkey_shown.wait(), timeout)
        self.passkey_shown.clear()
        return self.last_passkey

    @property
    def advertising_address(self) -> str:
        return str(self.device.random_address)


def build_unit(hci_source, hci_sink, *, identity: str = IDENTITY, irk: bytes = IRK,
               keystore: str | None = None, vin: str = "", fixed_passkey: int | None = None,
               pairing_mode: bool = True, rpa_timeout_s: int = 900) -> FakeUnit:
    """Build (not start) the fake unit on any Bumble HCI pair: an ``open_transport`` source/sink
    (netsim, vhci) or a ``Controller`` passed as both (``LocalLink`` tests).

    :param keystore: a JSON keystore path to persist bonds across restarts (the app lab); ``None``
        keeps them in memory (tests — nothing is written to disk).
    :returns: the :class:`FakeUnit`; call ``await unit.start()`` to power on and advertise.
    """
    unit = FakeUnit(vin=vin)
    unit.fixed_passkey = fixed_passkey
    unit.pairing_mode = pairing_mode
    cfg = DeviceConfiguration(
        name=NAME, address=Address(identity), irk=irk,
        le_privacy_enabled=True, le_rpa_timeout=rpa_timeout_s,
        advertising_interval_min=100, advertising_interval_max=100,
        keystore=f"JsonKeyStore:{keystore}" if keystore else None)
    dev = Device.from_config_with_hci(cfg, hci_source, hci_sink)
    unit.device = dev
    dev.add_services(unit.build_services())
    dev.pairing_config_factory = lambda conn: PairingConfig(
        sc=True, mitm=True, bonding=True, delegate=UnitDelegate(unit))
    dev.on("connection", unit._on_connection)
    return unit
