"""Host-firmware e2e harness: a Bumble virtual controller exposed as HCI over TCP (the firmware's
NimBLE socket transport connects to it), linked to the fake unit's controller."""

import asyncio
import importlib.util
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

HOST_DIR = Path(__file__).resolve().parents[2] / "firmware" / "host"
# The firmware-side controller's public address. Bumble's Controller defaults to 00:00:00:00:00:00,
# which NimBLE treats as "no public address" (BLE_OWN_ADDR_PUBLIC would fail with BLE_HS_ENOADDR).
FW_PUBLIC_ADDRESS = "F0:F1:F2:F3:F4:F6"


def _toolchain_missing():
    """Why the 32-bit host build cannot run here (``None`` if it can). NimBLE's Linux port builds with
    ``-m32``, so this links a trivial C and C++ program with ``$CROSS_COMPILE{gcc,g++} -m32`` — the
    same compilers the Makefile uses (gcc-multilib/g++-multilib on x86_64, or an i686 cross gcc)."""
    for tool in ("make", "git"):
        if shutil.which(tool) is None:
            return "%s not found" % tool
    prefix = os.environ.get("CROSS_COMPILE", "")
    with tempfile.TemporaryDirectory() as tmp:
        for comp, ext in (("gcc", "c"), ("g++", "cc")):
            src = Path(tmp) / ("probe." + ext)
            src.write_text("int main(void) { return 0; }\n" if ext == "c" else "int main() { return 0; }\n")
            cmd = [prefix + comp, "-m32", str(src), "-o", str(Path(tmp) / "probe"), "-lstdc++"]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except OSError as e:
                return "%s%s not usable (%s)" % (prefix, comp, e)
            if r.returncode != 0:
                return (
                    "no 32-bit toolchain: `%s -m32` cannot link (install gcc-multilib g++-multilib, "
                    "or set CROSS_COMPILE=i686-linux-gnu-)" % (prefix + comp)
                )
    return None


def _skip_reason():
    if not sys.platform.startswith("linux"):
        return "host firmware build is Linux-only (NimBLE NPL linux port)"
    if importlib.util.find_spec("bumble") is None:
        return "bumble not installed"
    return _toolchain_missing()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "linux_only: BLE e2e test needing the NimBLE Linux host build "
        "(skipped off Linux, without Bumble, or without a 32-bit toolchain — see _skip_reason()).",
    )


def pytest_collection_modifyitems(config, items):
    """Skip (not error) tests marked ``linux_only`` off Linux, without Bumble, or without a 32-bit
    toolchain. A module-level ``pytest.skip`` in a conftest aborts the whole run when the directory
    is the command-line target, so skip per item; Bumble and the fake unit are imported lazily for
    the same reason. Only the tests needing the NimBLE host build carry the marker (the BLE e2e
    test_host_e2e.py and the bond-store test test_ble_store_kv.py) — the
    pure-C pairing-SM parity test (test_pairing_sm_parity.py) has no BLE/NimBLE
    dependency and runs on any host with a C compiler, macOS included."""
    marked = [it for it in items if it.get_closest_marker("linux_only")]
    reason = _skip_reason() if marked else None
    if reason:
        for item in marked:
            item.add_marker(pytest.mark.skip(reason=reason))


# One LE connection event at the interval NimBLE actually asks for: ble_gap_connect with NULL params
# uses BLE_GAP_INITIAL_CONN_ITVL_MIN = 30 ms (max 50 ms). 10 ms (the 7.5 ms spec minimum) was not
# enough under qemu-i386: the unit's Identity Information still overtook our Encryption Change.
ACL_LATENCY_S = 0.030


def _radio_link():
    """A Bumble ``LocalLink`` whose LE ACL data takes a connection event to arrive.

    Bumble delivers ACL with zero latency. NimBLE's host drains its whole ACL RX queue in one go
    (``ble_hs_process_rx_data_queue``) while HCI events wait on the event queue behind it, so on a
    zero-latency link the unit's first key-distribution PDU (sent the instant its side sees the
    encryption change) is processed BEFORE our Encryption Change event -> SMP "Unspecified reason".
    On a real radio that PDU needs at least one more connection event (the negotiated interval,
    >= 30 ms for NimBLE's default connection parameters). LL control PDUs
    (which drive the encryption change) stay immediate; ACL order is preserved.
    """
    import asyncio

    from bumble.link import LocalLink

    class RadioLink(LocalLink):
        _last_at = 0.0

        def send_acl_data(self, sender_controller, destination_address, transport, data):
            loop = asyncio.get_running_loop()
            at = max(loop.time() + ACL_LATENCY_S, self._last_at + 1e-6)  # strictly increasing: FIFO
            self._last_at = at
            loop.call_at(
                at, LocalLink.send_acl_data, self, sender_controller, destination_address, transport, data
            )

    return RadioLink()


def _fw_controller_class():
    """The firmware-side Bumble ``Controller`` with the three controller duties the firmware relies
    on and Bumble leaves out (harness-only; README "Host build notes" 10):

    * **Resolving list (LL privacy).** NimBLE writes a bonded peer's identity + IRK to the
      controller (``LE Add Device To Resolving List``, on bond and at every boot) and reconnects by
      the *identity* address; the controller must match that against the peer's current resolvable
      private address. Bumble accepts the command but ignores it, so a unit that advertises from an
      RPA (as the real one does) is never found. Here an advertisement whose RPA resolves with a
      listed IRK completes a pending connection to that identity, reported (``LE Connection
      Complete``) with the identity address — what NimBLE looks the bond up by.
    * **LE Create Connection Cancel** ends the pending connection with a Connection Complete
      (Unknown Connection Identifier), Vol 4 Part E 7.8.13. Bumble answers success and keeps the
      pending connection, so NimBLE's connect procedure (its 10 s timeout, or our disconnect())
      never ends and every later GAP procedure is refused.
    * **Losing the host ends the links.** A rebooting ESP32 takes its controller with it and the
      unit sees a supervision timeout; Bumble's controller outlives the TCP host and keeps the link
      up, so the unit (one connection slot) would never advertise again. ``host_gone()`` drops
      every link (Connection Timeout to the peer) and forgets the pending connection and the list.
    """
    import asyncio

    from bumble import hci, ll
    from bumble.controller import Controller
    from bumble.smp import AddressResolver

    class FirmwareController(Controller):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.resolving_list: dict[bytes, tuple[bytes, object]] = {}  # identity -> (irk, addr)
            self._resolved: tuple[object, object] | None = None  # (rpa, identity)
            self.hold_connects = False  # test knob: a pending LE connection never completes
            # test knobs for a cancel of a held connection: its completion arrives this much later
            # (a real controller ends the procedure at a connection event), or the connection
            # completes first and the cancel is refused (the race a real radio can lose)
            self.cancel_delay_s = 0.0
            self.connect_on_cancel = False
            self._last_rpa = None  # the latest resolvable advertiser address seen

        # -- resolving list ----------------------------------------------------------------
        def on_hci_le_add_device_to_resolving_list_command(self, command):
            ident = command.peer_identity_address
            self.resolving_list[bytes(ident)] = (command.peer_irk, ident)
            return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.SUCCESS)

        def on_hci_le_remove_device_from_resolving_list_command(self, command):
            self.resolving_list.pop(bytes(command.peer_identity_address), None)
            return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.SUCCESS)

        def on_hci_le_clear_resolving_list_command(self, command):
            self.resolving_list.clear()
            return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.SUCCESS)

        def on_advertising_pdu(self, pdu):
            pending = self.pending_le_connection
            if pdu.advertiser_address.is_resolvable:
                self._last_rpa = pdu.advertiser_address
            if pending and self.hold_connects:  # scan reports only; the connect stays pending
                self.pending_le_connection = None
                try:
                    super().on_advertising_pdu(pdu)
                finally:
                    self.pending_le_connection = pending
                return
            adv = pdu.advertiser_address
            entry = self.resolving_list.get(bytes(pending.peer_address)) if pending else None
            if entry and adv.is_resolvable:
                irk, ident = entry
                if AddressResolver([(irk, ident)]).resolve(adv) is not None:
                    self._resolved = (adv, pending.peer_address)
                    pending.peer_address = adv  # connect to the RPA on the air ...
            super().on_advertising_pdu(pdu)

        def send_hci_packet(self, packet):
            if (
                isinstance(packet, hci.HCI_LE_Connection_Complete_Event)
                and self._resolved
                and packet.peer_address == self._resolved[0]
            ):
                ident = self._resolved[1]  # ... report the identity to the host
                self._resolved = None
                packet = hci.HCI_LE_Connection_Complete_Event(
                    status=packet.status,
                    connection_handle=packet.connection_handle,
                    role=packet.role,
                    peer_address_type=ident.address_type,
                    peer_address=ident,
                    connection_interval=packet.connection_interval,
                    peripheral_latency=packet.peripheral_latency,
                    supervision_timeout=packet.supervision_timeout,
                    central_clock_accuracy=packet.central_clock_accuracy,
                )
            super().send_hci_packet(packet)

        # -- create connection cancel --------------------------------------------------------
        def on_hci_le_create_connection_cancel_command(self, command):
            pending = self.pending_le_connection
            if pending is None:
                return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.COMMAND_DISALLOWED_ERROR)
            if self.connect_on_cancel and self._last_rpa is not None:
                # the connection completes before the cancel is processed: refuse the cancel
                self.connect_on_cancel = self.hold_connects = False
                self._resolved = (self._last_rpa, pending.peer_address)
                pending.peer_address = self._last_rpa
                self.create_le_connection(self._last_rpa)  # its Connection Complete goes first
                return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.COMMAND_DISALLOWED_ERROR)
            self.pending_le_connection = None
            self._resolved = None
            done = hci.HCI_LE_Connection_Complete_Event(
                status=hci.HCI_ErrorCode.UNKNOWN_CONNECTION_IDENTIFIER_ERROR,
                connection_handle=0,
                role=hci.Role.CENTRAL,
                peer_address_type=pending.peer_address.address_type,
                peer_address=pending.peer_address,
                connection_interval=0,
                peripheral_latency=0,
                supervision_timeout=0,
                central_clock_accuracy=0,
            )
            # after this command's Command Complete (send_hci_packet itself defers with call_soon)
            asyncio.get_running_loop().call_later(self.cancel_delay_s, self.send_hci_packet, done)
            return hci.HCI_StatusReturnParameters(hci.HCI_ErrorCode.SUCCESS)

        # -- the host went away (process exit = ESP reboot) -----------------------------------
        def host_gone(self):
            for addr, conn in list(self.le_connections.items()):
                conn.send_ll_control_pdu(ll.TerminateInd(hci.HCI_ErrorCode.CONNECTION_TIMEOUT_ERROR))
                del self.le_connections[addr]
            self.pending_le_connection = None
            self._resolved = None
            self.resolving_list.clear()

    return FirmwareController


def _watch_host(sink, on_gone):
    """Call ``on_gone()`` when the TCP host of a Bumble ``tcp-server`` transport disconnects (its
    sink's ``transport`` goes from a connection to ``None``; Bumble has no other hook)."""

    class WatchedSink(type(sink)):
        @property
        def transport(self):
            return self.__dict__.get("_watched_transport")

        @transport.setter
        def transport(self, t):
            was = self.__dict__.get("_watched_transport")
            self.__dict__["_watched_transport"] = t
            if was is not None and t is None:
                on_gone()

    sink.__dict__["_watched_transport"] = sink.__dict__.pop("transport", None)
    sink.__class__ = WatchedSink


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HciUnit:
    """Runs the Bumble side on its own event loop thread for the lifetime of a test."""

    def __init__(self, **unit_kw):
        self.port = _free_port()
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._kw = unit_kw
        self.unit = None
        threading.Thread(target=self._run, daemon=True).start()
        assert self._ready.wait(10), "bumble side did not start"

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self._ready.set()
        self.loop.run_forever()

    async def _start(self):
        from bumble.controller import Controller
        from bumble.hci import Address, LeFeatureMask
        from bumble.transport import open_transport

        from tools.fake_unit_peripheral import build_unit

        self.link = _radio_link()
        uc = Controller("unit", link=self.link)
        self.unit = build_unit(uc, uc, **self._kw)
        await self.unit.start()
        self._transport = await open_transport(f"tcp-server:127.0.0.1:{self.port}")
        self.fw_controller = _fw_controller_class()(
            "fw",
            host_source=self._transport.source,
            host_sink=self._transport.sink,
            link=self.link,
            public_address=FW_PUBLIC_ADDRESS,
        )
        _watch_host(self._transport.sink, self.fw_controller.host_gone)
        # Bumble's Controller answers even a LEGACY scan (HCI_LE_Set_Scan_Enable, what NimBLE sends
        # with BLE_EXT_ADV=0) with LE *Extended* Advertising Reports whenever it advertises the
        # LE_EXTENDED_ADVERTISING feature; NimBLE drops those, so it never sees the unit. A real
        # controller reports in the format of the scan command used; dropping the feature bit on the
        # firmware-side controller makes Bumble emit legacy reports.
        self.fw_controller.le_features = Controller.le_features & ~LeFeatureMask.LE_EXTENDED_ADVERTISING
        # Bumble's LocalLink stamps every LE ACL packet with the SENDER controller's random_address,
        # even on a link the host opened with its public address (own_addr_type=0, what the firmware
        # uses). The unit's controller keys that link by our public address, so with the default
        # random_address (00:00:00:00:00:00) every packet we send is dropped ("no connection for
        # 00:00:00:00:00:00"). Mirror the public address, typed PUBLIC (Bumble's Address equality
        # includes the type), into random_address (harness-only).
        self.fw_controller.random_address = Address(FW_PUBLIC_ADDRESS, Address.PUBLIC_DEVICE_ADDRESS)

    def call(self, coro_fn, *a, timeout=10):
        """Run ``coro_fn(*a)`` on the bumble loop (e.g. ``unit.rotate_address``) and wait."""
        return asyncio.run_coroutine_threadsafe(coro_fn(*a), self.loop).result(timeout)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def hci_unit():
    hu = HciUnit()
    yield hu
    hu.close()


class Firmware:
    """The host firmware as a subprocess; lines on stdout are queued for expect()."""

    def __init__(self, binary, port, store_dir=None, extra=()):
        args = [str(binary), "--hci-port", str(port)]
        if store_dir is not None:
            args += ["--store", str(store_dir)]
        self._spawn(args + list(extra))

    def _spawn(self, argv):
        """Start ``argv`` with stdin/stdout pipes (stderr folded into stdout) and pump its lines."""
        self.p = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.log: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.log.append(line.rstrip())
            self.lines.put(line.rstrip())

    def send(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def expect(self, prefix, pred=lambda v: True, timeout=30.0):
        """Wait for a ``PREFIX {json}`` (or bare ``PREFIX ...``) line whose payload satisfies pred."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                line = self.lines.get(timeout=max(0.05, end - time.monotonic()))
            except queue.Empty:
                break
            if not line.startswith(prefix):
                continue
            rest = line[len(prefix) :].strip()
            try:
                val = json.loads(rest) if rest[:1] in "{[" else rest
            except ValueError:
                val = rest
            if pred(val):
                return val
        raise AssertionError(
            "no %r matching line in %.0fs; log tail:\n%s" % (prefix, timeout, "\n".join(self.log[-40:]))
        )

    def stop(self):
        if self.p.poll() is None:
            try:
                self.send("quit")
                self.p.wait(5)
            except Exception:
                self.p.kill()
                self.p.wait(5)


QEMU_RUN = HOST_DIR.parent / "qemu" / "run_qemu.sh"
QEMU_BUILD = HOST_DIR.parent / "build-qemu"


class QemuFirmware(Firmware):
    """The esp32s3 QEMU-probe image (``firmware/build-qemu``) booted by ``firmware/qemu/run_qemu.sh``:
    the same line reader, on QEMU's UART0 (``-serial stdio``, no monitor on the stream)."""

    def __init__(self, flash, build_dir=QEMU_BUILD):
        self._spawn(["bash", str(QEMU_RUN), str(build_dir), str(flash)])

    def stop(self):
        """Power off: the device firmware has no ``quit`` (it ignores the line), so end QEMU. SIGTERM
        lets QEMU close the flash file; every probe write was committed (``nvs_commit``) before its
        ``LOG kvprobe ok``."""
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait(5)


@pytest.fixture
def qemu(tmp_path):
    """``qemu.boot()`` -> a running :class:`QemuFirmware`. Every boot of one test uses the same flash
    file (``tmp_path/"flash.bin"``, merged from the build on the first boot), so a second boot is a
    reboot with the NVS written by the first. Skipped unless ``CALI_QEMU=1``, and when
    ``qemu-system-xtensa`` is not on PATH (ESP-IDF's ``export.sh`` puts it there)."""
    if os.environ.get("CALI_QEMU") != "1":
        pytest.skip("QEMU boot tier: set CALI_QEMU=1 (CI job firmware-qemu)")
    if shutil.which("qemu-system-xtensa") is None:
        pytest.skip("qemu-system-xtensa not found (source $IDF_PATH/export.sh)")
    if not (QEMU_BUILD / "flash_args").is_file():
        pytest.fail("no QEMU-probe build in %s (see firmware/README.md)" % QEMU_BUILD)
    started: list[QemuFirmware] = []

    class _Qemu:
        flash = tmp_path / "flash.bin"

        def boot(self):
            for fw in started:  # one QEMU owns the flash file at a time
                fw.stop()
            fw = QemuFirmware(self.flash)
            started.append(fw)
            return fw

    yield _Qemu()
    for fw in started:
        fw.stop()


_BUILT: dict[str, Path] = {}


def build_host(binary="cali-host"):
    """Build a firmware/host Makefile target once per session (fetching the pinned NimBLE first)."""
    if binary not in _BUILT:
        subprocess.run(["bash", str(HOST_DIR / "fetch_nimble.sh")], check=True)
        subprocess.run(["make", "-C", str(HOST_DIR), binary], check=True)
        _BUILT[binary] = HOST_DIR / binary
    return _BUILT[binary]


@pytest.fixture
def host_fw(tmp_path):
    """Factory ``host_fw(hu, store_dir=None, extra=(), binary="cali-host")`` -> a running
    :class:`Firmware` on ``hu.port``. Without ``store_dir`` each firmware gets the per-test
    ``tmp_path/"store"`` (so two calls in one test share the bond store, like a reboot). Every
    firmware started is stopped at teardown."""
    started: list[Firmware] = []

    def make(hu, store_dir=None, extra=(), binary="cali-host"):
        store = Path(store_dir) if store_dir is not None else tmp_path / "store"
        fw = Firmware(build_host(binary), hu.port, store, extra)
        started.append(fw)
        return fw

    yield make
    for fw in started:
        fw.stop()
