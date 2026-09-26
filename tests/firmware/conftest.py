"""Host-firmware e2e harness: a Bumble virtual controller exposed as HCI over TCP (the firmware's
NimBLE socket transport connects to it), linked to the fake unit's controller."""
import asyncio
import importlib.util
import json
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HOST_DIR = Path(__file__).resolve().parents[2] / "firmware" / "host"
# The firmware-side controller's public address. Bumble's Controller defaults to 00:00:00:00:00:00,
# which NimBLE treats as "no public address" (BLE_OWN_ADDR_PUBLIC would fail with BLE_HS_ENOADDR).
FW_PUBLIC_ADDRESS = "F0:F1:F2:F3:F4:F6"


def _skip_reason():
    if not sys.platform.startswith("linux"):
        return "host firmware build is Linux-only (NimBLE NPL linux port)"
    if importlib.util.find_spec("bumble") is None:
        return "bumble not installed"
    return None


def pytest_collection_modifyitems(config, items):
    """Skip (not error) this directory off Linux / without Bumble. A module-level ``pytest.skip`` in a
    conftest aborts the whole run when the directory is the command-line target, so skip per item;
    Bumble and the fake unit are imported lazily for the same reason."""
    reason = _skip_reason()
    if reason:
        here = Path(__file__).resolve().parent
        for item in items:
            if here in Path(str(item.fspath)).resolve().parents:
                item.add_marker(pytest.mark.skip(reason=reason))


# One LE connection event at the minimum connection interval (7.5 ms), rounded up.
ACL_LATENCY_S = 0.010


def _radio_link():
    """A Bumble ``LocalLink`` whose LE ACL data takes a connection event to arrive.

    Bumble delivers ACL with zero latency. NimBLE's host drains its whole ACL RX queue in one go
    (``ble_hs_process_rx_data_queue``) while HCI events wait on the event queue behind it, so on a
    zero-latency link the unit's first key-distribution PDU (sent the instant its side sees the
    encryption change) is processed BEFORE our Encryption Change event -> SMP "Unspecified reason".
    On a real radio that PDU needs at least one more connection event (>= 7.5 ms). LL control PDUs
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
            loop.call_at(at, LocalLink.send_acl_data, self, sender_controller, destination_address,
                         transport, data)

    return RadioLink()


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
        self.fw_controller = Controller("fw", host_source=self._transport.source,
                                        host_sink=self._transport.sink, link=self.link,
                                        public_address=FW_PUBLIC_ADDRESS)
        # Bumble's Controller answers even a LEGACY scan (HCI_LE_Set_Scan_Enable, what NimBLE sends
        # with BLE_EXT_ADV=0) with LE *Extended* Advertising Reports whenever it advertises the
        # LE_EXTENDED_ADVERTISING feature; NimBLE drops those, so it never sees the unit. A real
        # controller reports in the format of the scan command used; dropping the feature bit on the
        # firmware-side controller makes Bumble emit legacy reports.
        self.fw_controller.le_features = (Controller.le_features
                                          & ~LeFeatureMask.LE_EXTENDED_ADVERTISING)
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
        self.p = subprocess.Popen(args + list(extra), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.lines: queue.Queue[str] = queue.Queue()
        self.log: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.log.append(line.rstrip())
            self.lines.put(line.rstrip())

    def send(self, line):
        self.p.stdin.write(line + "\n"); self.p.stdin.flush()

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
            rest = line[len(prefix):].strip()
            try:
                val = json.loads(rest) if rest[:1] in "{[" else rest
            except ValueError:
                val = rest
            if pred(val):
                return val
        raise AssertionError("no %r matching line in %.0fs; log tail:\n%s"
                             % (prefix, timeout, "\n".join(self.log[-40:])))

    def stop(self):
        if self.p.poll() is None:
            try:
                self.send("quit")
                self.p.wait(5)
            except Exception:
                self.p.kill()
