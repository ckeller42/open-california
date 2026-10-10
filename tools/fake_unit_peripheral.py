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
import time

from bumble import core
from bumble.att import Attribute, AttributeValue
from bumble.core import UUID, AdvertisingData
from bumble.device import Device, DeviceConfiguration, Peer
from bumble.gatt import (
    GATT_CHARACTERISTIC_USER_DESCRIPTION_DESCRIPTOR,
    Characteristic,
    Descriptor,
    Service,
)
from bumble.hci import Address, OwnAddressType
from bumble.pairing import PairingConfig, PairingDelegate

from calictl import overrides, protocol
from calictl.trace import REDACTED_VIN_HASH, Tracer, char_short
from tools.mock_unit import MockCamperUnit, MockDisconnect, _pack_state

UNIT_MTU_REQUEST = 247  # Client RX MTU in the unit's own Exchange MTU Request (btmon 2026-10-10)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "-6c77-4b7d-bbf6-a5e587701f3d"
NAME = "VWCAMPER"
IDENTITY = "C0:FF:EE:CA:11:F0"
IRK = bytes.fromhex("865F81FF5A8B486EAAE29A27AD9F77DC")
BASELINE = os.path.join(REPO, "tests", "scenarios", "firmware", "baseline-0410.json")
log = logging.getLogger("fake_unit")


class Recorder(Tracer):
    """The app-recording tap: :mod:`calictl.trace`'s writer and schema, seen from the unit's side of
    the link. Off unless built with a path (``FAKE_UNIT_RECORD``, read by
    ``tools/applab/fake_unit_ble.py`` only).

    Adds to the trace schema: ``t_ms`` (ms since this connection's ``connect``), ``conn`` (1-based
    connection number; 0 before the first), and the events ``mtu``, ``subscribe`` (``hex`` ``0100``
    notify / ``0200`` indicate / ``0000`` off) and ``pair`` (``state`` ``passkey_shown`` / ``bonded``
    / ``failed`` — never the code). ``1002`` is written as ``"<vin-hash>"``; the ``1003`` heartbeat
    is recorded. A write error logs once and turns recording off; the unit keeps serving.

    Unlike a long-lived :class:`~calictl.trace.Tracer`, this tap intentionally forgoes any size cap
    or file rotation: a recording spans one scripted scenario (seconds, a few hundred frames), so the
    file stays small and is overwritten per scenario by ``walk.py``.

    :param path: the JSONL file to append to, or ``None`` (recording off).
    """

    def __init__(self, path: str | None):
        super().__init__(path, heartbeat=True)
        self.conn_n = 0
        self._t0: float | None = None

    def connected(self) -> None:
        """A central's link came up: number it and restart ``t_ms``."""
        self.conn_n += 1
        self._t0 = time.monotonic()
        self.event("connect")

    def event(self, ev: str, **extra) -> None:
        """A non-I/O event (connect, disconnect, mtu, subscribe, pair)."""
        if self.path:
            self._emit({"t": time.time(), "ev": ev, **extra})

    def _emit(self, rec: dict) -> None:
        if rec.get("char") == "1002":
            rec["hex"] = REDACTED_VIN_HASH
        rec["conn"] = self.conn_n
        rec["t_ms"] = None if self._t0 is None else round((time.monotonic() - self._t0) * 1000)
        path = self.path
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError as e:
            log.error("FAKE_UNIT_RECORD: cannot write %s (%s); recording disabled for this run", path, e)
            self.path = None


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

    async def confirm(self, auto: bool = False) -> bool:
        # Bumble asks here only for Just Works (a NoInputNoOutput central, no passkey, no MITM).
        # The real unit hangs up on that request (btmon, buspi 2026-09-26): refuse it too.
        return False

    async def generate_passkey(self) -> int:
        u = self._unit
        code = u.fixed_passkey if u.fixed_passkey is not None else secrets.randbelow(1_000_000)
        u.last_passkey = code
        u.passkey_shown.set()
        u.rec.event("pair", state="passkey_shown")
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
        self.unit.event_sink = lambda fn, frame: self.schedule_notify(fn, frame)  # acks/echoes, one-off
        self.unit.armed = True  # the emulator app keeps its own heartbeat; don't gate
        self.dirty: set[str] = set()
        self.by_state: dict[str, str] = {}  # state char uuid -> fn
        self.chars: dict[str, Characteristic] = {}
        self.device: Device | None = None
        self.tasks: list = []  # keep task refs (else GC kills them)
        self.last_beat_t: float = 0.0  # monotonic time of the last 1003 write
        self.seen_beat = False  # a beat arrived on the current link (watchdog arms)
        self.beats = 0  # 1003 writes seen since start (test hook)
        self.control_writes = 0  # control-char writes seen since start (test hook)
        self.conn = None  # current Bumble connection (single-link unit)
        self.pairing_mode = True  # the unit's "Gerät verbinden" screen is open
        self.refuse_connections = False  # test knob: drop every link at once
        self.drop_on_read: str | None = None  # test knob: hang up on the next GATT read of this fn
        self.fixed_passkey: int | None = None
        self.last_passkey: int | None = None
        self.passkey_shown = asyncio.Event()
        self.rec = Recorder(None)  # build_unit(record=...) replaces it; off by default
        self.subscribed: set[str] = set()  # fns whose state char the current central subscribed
        for fn, f in self.funcs.items():
            if f.state_char:
                self.by_state[f.state_char.lower()] = fn
            if fn in seed:
                repack = _pack_state(f, seed[fn])
                if repack != self.raw[fn]:
                    log.warning(
                        "%s: dictionary does not round-trip the baseline frame (%s vs %s); "
                        "serving raw until modified",
                        fn,
                        repack.hex(),
                        self.raw[fn].hex(),
                    )

    # --- reads / writes -------------------------------------------------------------
    def read_state(self, fn: str) -> bytes:
        if fn in self.dirty or fn not in self.raw:
            v = self.unit.read(self.funcs[fn].state_char)
        else:
            v = self.raw[fn]
        log.info("READ %s -> %s", fn, v.hex())
        return v

    def gatt_read(self, fn: str):
        """A central's GATT read of ``fn``'s state char (notifications do not come through here).
        With ``drop_on_read == fn`` (one-shot) the unit hangs up instead of answering: the read
        never completes — a link lost mid read-all.
        (Bumble awaits an awaitable read value; this one returns only after the link is gone.)"""
        if self.drop_on_read != fn or self.conn is None:
            v = self.read_state(fn)
            self.rec.read(self.funcs[fn].state_char, v)
            return v
        self.drop_on_read = None
        conn = self.conn

        async def hang_up() -> bytes:
            log.info("READ %s -> dropping the link", fn)
            await conn.disconnect()
            return b""  # nobody left to answer

        return hang_up()

    def static_read(self, slot: str, value: bytes) -> bytes:
        """A central's read of a fixed aux char (``1002`` VIN hash, ``1903``-``1905``), recorded."""
        self.rec.read(cu(slot), value)
        return value

    def on_write(self, fn: str, data: bytes) -> None:
        self.control_writes += 1
        f = self.funcs[fn]
        self.rec.write(f.control_char, bytes(data))
        try:
            self.unit.write(f.control_char, bytes(data))
        except MockDisconnect as e:
            log.warning("REJECTED write to %s: %s", fn, e)
            return
        self.dirty.add(fn)
        log.info("WRITE %s %s -> state %s", fn, bytes(data).hex(), self.unit.decoded(fn))
        self.schedule_notify(fn)

    def on_beat(self, data: bytes) -> None:
        self.rec.write(cu("1003"), data)
        self.unit.beat(data)
        self.last_beat_t = time.monotonic()
        self.seen_beat = True
        self.beats += 1

    def on_f000(self, data: bytes) -> None:
        """The generic ``f000`` write (unmodelled), recorded."""
        self.rec.write(cu("f000"), data)
        log.info("f000 write %s", data.hex())

    def on_subscription(self, fn: str, notify: bool, indicate: bool) -> None:
        """A CCCD write on ``fn``'s state char. The unit pushes the current value once as soon as
        notifications are enabled (buspi 2026-09-16: one notify per char right after its CCCD write)."""
        self.rec.event(
            "subscribe",
            char=char_short(self.funcs[fn].state_char),
            fn=fn,
            hex="0100" if notify else "0200" if indicate else "0000",
        )
        if notify or indicate:
            self.subscribed.add(fn)
        else:
            self.subscribed.discard(fn)
        if notify:
            self.schedule_notify(fn)

    def set_raw(self, fn: str, frame: bytes, notify: bool = True) -> None:
        """Serve ``frame`` verbatim for ``fn`` from now on (reads and notifications), e.g. a frame
        shorter than the dictionary's; with ``notify`` push it to a subscribed central at once."""
        self.raw[fn] = bytes(frame)
        self.unit.state[fn] = protocol.decode(self.funcs[fn], self.raw[fn])
        self.dirty.discard(fn)
        if notify:
            self.schedule_notify(fn)

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

    def schedule_notify(self, fn: str, value: bytes | None = None) -> None:
        """Notify ``fn``'s state char: the stored state, or an explicit one-off ``value`` (an ack/echo)."""
        ch = self.chars.get(fn)
        if ch is not None and self.device is not None:
            # the value explicitly: Bumble would otherwise fetch it through the char's GATT read
            # callback, which is the central-read path (gatt_read, drop_on_read)
            value = self.read_state(fn) if value is None else value
            if self.conn is not None and fn in self.subscribed:
                self.rec.notify(self.funcs[fn].state_char, value)
            self.tasks.append(asyncio.get_event_loop().create_task(self.device.notify_subscribers(ch, value)))
            self.tasks = [t for t in self.tasks if not t.done()]

    # --- GATT --------------------------------------------------------------------------
    def build_services(self) -> list[Service]:
        services = []
        desc = lambda s: Descriptor(  # noqa: E731
            GATT_CHARACTERISTIC_USER_DESCRIPTION_DESCRIPTOR,
            Attribute.READABLE,
            s.encode(),
        )
        groups: dict[str, list[Characteristic]] = {}
        for fn, f in self.funcs.items():
            slot = f.state_char[4:8]  # e.g. "1102"
            svc = slot[:2] + "00"
            if fn == "general":  # 1001 = versions, read-only (no notify)
                groups.setdefault(svc, []).append(
                    Characteristic(
                        cu("1001"),
                        Characteristic.Properties.READ,
                        Attribute.READABLE,
                        AttributeValue(read=lambda c, fn=fn: self.gatt_read(fn)),
                        [desc("Info")],
                    )
                )
                continue
            props = Characteristic.Properties.READ | Characteristic.Properties.NOTIFY
            perms = Attribute.READABLE
            if fn == "vehicle":  # the auth-gated read that forces bonding
                perms = Attribute.READABLE | Attribute.READ_REQUIRES_AUTHENTICATION
            st = Characteristic(
                f.state_char,
                props,
                perms,
                AttributeValue(read=lambda c, fn=fn: self.gatt_read(fn)),
                [desc("State")],
            )
            # The unit pushes the current value once as soon as a client enables notifications
            # (observed on buspi 2026-09-16: one notify per char right after each CCCD write).
            st.on(
                Characteristic.EVENT_SUBSCRIPTION,
                lambda conn, notify, indicate, fn=fn: self.on_subscription(fn, notify, indicate),
            )
            self.chars[fn] = st
            lst = groups.setdefault(svc, [])
            if f.control_char:
                lst.append(
                    Characteristic(
                        f.control_char,
                        Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
                        Attribute.WRITEABLE,
                        AttributeValue(write=lambda c, v, fn=fn: self.on_write(fn, v)),
                        [desc("Control")],
                    )
                )
            lst.append(st)
        # service 1000 extras: 1002 opaque vehicle id, 1003 liveness counter (write)
        groups.setdefault("1000", []).extend(
            [
                Characteristic(
                    cu("1002"),
                    Characteristic.Properties.READ,
                    Attribute.READABLE,
                    AttributeValue(read=lambda c: self.static_read("1002", self.vin_fingerprint)),
                    [desc("VIN")],
                ),
                Characteristic(
                    cu("1003"),
                    Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
                    Attribute.WRITEABLE,
                    AttributeValue(write=lambda c, v: self.on_beat(bytes(v))),
                    [desc("Counter")],
                ),
            ]
        )
        # 1900 extras the app may read on the SAT/system page
        groups.setdefault("1900", []).extend(
            [
                Characteristic(
                    cu(slot),
                    Characteristic.Properties.READ,
                    Attribute.READABLE,
                    AttributeValue(read=lambda c, slot=slot, value=value: self.static_read(slot, value)),
                )
                for slot, value in (
                    ("1903", b"0410\x000207\x00"),
                    ("1904", b"California"),
                    ("1905", b"********"),
                )
            ]
        )
        # f000 generic write
        groups.setdefault("f000", []).append(
            Characteristic(
                cu("f000"),
                Characteristic.Properties.WRITE | Characteristic.Properties.WRITE_WITHOUT_RESPONSE,
                Attribute.WRITEABLE,
                AttributeValue(write=lambda c, v: self.on_f000(bytes(v))),
            )
        )
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
            "FAKE_UNIT_FIFO", os.path.join(os.environ.get("TMPDIR", "/tmp"), "applab", "fake_unit.in")
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                os.mkfifo(path)
        except OSError as e:
            log.info("scenario console disabled (no FIFO at %s: %s)", path, e)
            return
        log.info(
            "scenario console: echo commands into %s "
            "(set <fn> F=v | raw <fn> <hex> | show <fn> | pair on|off | rotate | forget | q)",
            path,
        )

        def pump():
            while True:
                with open(path, encoding="utf-8") as fifo:  # reopen after each writer closes (EOF)
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
                    self.set_raw(fn, bytes.fromhex(hx), notify=False)
                    print(f"{fn} raw -> {self.raw[fn].hex()}", flush=True)
                    self.schedule_notify(fn)
                elif parts[0] == "show":
                    fn = parts[1]
                    print(
                        f"{fn} raw={self.read_state(fn).hex()} decoded={protocol.decode(self.funcs[fn], self.read_state(fn))}",
                        flush=True,
                    )
                elif parts[0] == "pair":  # pair on | pair off — the unit's pairing screen
                    self.pairing_mode = parts[1] == "on"
                    print(f"pairing mode {'ON' if self.pairing_mode else 'off'}", flush=True)
                elif parts[0] == "rotate":
                    self.tasks.append(asyncio.get_running_loop().create_task(self.rotate_address()))
                    self.tasks = [t for t in self.tasks if not t.done()]
                elif parts[0] == "forget":  # like "Bluetooth zurücksetzen"
                    self.tasks.append(asyncio.get_running_loop().create_task(self.forget_bonds()))
                    self.tasks = [t for t in self.tasks if not t.done()]
                    print("bonds forgotten", flush=True)
                else:
                    print(
                        "commands: set <fn> F=v ... | raw <fn> <hex> | show <fn> | pair on|off | rotate | forget | q",
                        flush=True,
                    )
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
        # A central reached us, so the unit is awake: undo the mock's own heartbeat-lapse drop()
        # (online=False), which this peripheral never models as "not advertising". Without this every
        # one-off ack/echo is swallowed on a re-paired link (app lab 2026-10-06).
        self.unit.wake()
        self.rec.connected()
        conn.on("disconnection", lambda reason, c=conn: self._on_disconnection(c, reason))
        conn.on("connection_att_mtu_update", lambda c=conn: self.rec.event("mtu", mtu=c.att_mtu))
        conn.on("pairing", lambda keys: self.rec.event("pair", state="bonded"))
        conn.on("pairing_failure", lambda reason: self.rec.event("pair", state="failed", reason=int(reason)))
        self.tasks.append(asyncio.get_event_loop().create_task(self._unit_mtu_exchange(conn)))
        self.tasks = [t for t in self.tasks if not t.done()]

    async def _unit_mtu_exchange(self, conn) -> None:
        """The real unit is also a GATT client: on every link it sends the central an ATT Exchange MTU
        Request (Client RX MTU 247, btmon 2026-10-10) and hangs up with HCI 0x13 when its 30 s ATT
        transaction timeout expires unanswered — what a central without a GATT server (the ESP before
        its NimBLE GATT server was enabled) suffered every ~30 s. BlueZ and Android answer it."""
        try:
            await Peer(conn).request_mtu(UNIT_MTU_REQUEST)
        except core.TimeoutError:  # Bumble's GATT_REQUEST_TIMEOUT, 30 s like the unit's ATT timeout
            if self.conn is conn:
                self.rec.event("mtu", timeout=True)
                await conn.disconnect()  # Bumble's default reason = 0x13 REMOTE USER TERMINATED
        except Exception:  # noqa: BLE001 - an error response or a dropped link: the real unit's reaction is unknown
            return

    def _on_disconnection(self, conn, reason=None) -> None:
        self.rec.event("disconnect", reason=None if reason is None else int(reason))
        if self.conn is conn:
            self.conn = None
            self.subscribed.clear()

    async def _advertise(self) -> None:
        adv = bytes(
            AdvertisingData(
                [
                    (AdvertisingData.FLAGS, bytes([0x06])),
                    (AdvertisingData.COMPLETE_LOCAL_NAME, NAME.encode()),
                ]
            )
        )
        scan_rsp = bytes(
            AdvertisingData(
                [
                    (
                        AdvertisingData.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                        UUID(cu("1000")).to_bytes(),
                    ),
                ]
            )
        )
        # Legacy connectable advertising stops by itself when a central connects and auto_restart
        # resumes it on disconnect -> exactly one connection slot, like the real unit.
        await self.device.start_advertising(
            auto_restart=True,
            advertising_data=adv,
            scan_response_data=scan_rsp,
            own_address_type=OwnAddressType.RESOLVABLE_OR_RANDOM,
        )

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


def build_unit(
    hci_source,
    hci_sink,
    *,
    identity: str = IDENTITY,
    irk: bytes = IRK,
    keystore: str | None = None,
    vin: str = "",
    fixed_passkey: int | None = None,
    pairing_mode: bool = True,
    rpa_timeout_s: int = 900,
    record: str | None = None,
) -> FakeUnit:
    """Build (not start) the fake unit on any Bumble HCI pair: an ``open_transport`` source/sink
    (netsim, vhci) or a ``Controller`` passed as both (``LocalLink`` tests).

    :param keystore: a JSON keystore path to persist bonds across restarts (the app lab); ``None``
        keeps them in memory (tests — nothing is written to disk).
    :param record: append every GATT/link event to this JSONL file (:class:`Recorder`); ``None``
        (default) records nothing. The lab CLI passes ``FAKE_UNIT_RECORD``; nothing here reads it.
    :returns: the :class:`FakeUnit`; call ``await unit.start()`` to power on and advertise.
    """
    unit = FakeUnit(vin=vin)
    unit.rec = Recorder(record)
    unit.fixed_passkey = fixed_passkey
    unit.pairing_mode = pairing_mode
    cfg = DeviceConfiguration(
        name=NAME,
        address=Address(identity),
        irk=irk,
        le_privacy_enabled=True,
        le_rpa_timeout=rpa_timeout_s,
        advertising_interval_min=100,
        advertising_interval_max=100,
        keystore=f"JsonKeyStore:{keystore}" if keystore else None,
    )
    dev = Device.from_config_with_hci(cfg, hci_source, hci_sink)
    unit.device = dev
    dev.add_services(unit.build_services())
    dev.pairing_config_factory = lambda conn: PairingConfig(
        sc=True, mitm=True, bonding=True, delegate=UnitDelegate(unit)
    )
    dev.on("connection", unit._on_connection)
    return unit
