"""Robust BLE transport to the Camper Unit.

Hard-won connection lessons baked in:
- Use a single `async with BleakClient(...)` per operation batch. A manual
  connect-retry loop re-triggers `le-connection-abort-by-local` (the kernel
  aborts a new connect while a prior one is still tearing down).
- On the abort/EOFError cascade, recover by power-cycling the adapter and
  re-scanning (BlueZ must re-acquire the device's rotating address).
- The unit allows ONE connection: if the phone app is connected (e.g. ignition
  on), the Pi is locked out — surfaced as ConnectionUnavailable, not retried
  forever.

`bleak` is imported lazily so protocol/semantics stay importable without it.
"""
from __future__ import annotations

import asyncio
import subprocess

DEFAULT_ADDR = "AA:BB:CC:DD:EE:FF"


# --- non-function GATT chars (deliberately absent from dictionary.yaml) --------
# 1003 is the write-only "Counter" liveness char (decompiled `ag/b.java`). Ticking
# a monotonic +1 counter on it ARMS actuation: while it beats, the unit honours
# control writes to the normal control chars — the missing precondition behind the
# "writes ACK but do nothing" gate (issue #2, confirmed on-device 2026-07-07). The
# load latches once actuated (one-shot arm), so the heartbeat only needs to run
# across the write window, not indefinitely. 1001/1004 are the version +
# authenticated reads the app performs on connect (bonding / liveness gate).
def _aux_uuid(short: str) -> str:
    return "0000%s-6c77-4b7d-bbf6-a5e587701f3d" % short

HEARTBEAT_CHAR = _aux_uuid("1003")
VERSION_CHAR = _aux_uuid("1001")
AUTH_CHAR = _aux_uuid("1004")
HEARTBEAT_START = 0x00100000      # arbitrary high start (no read-seed / challenge)
HEARTBEAT_PERIOD_S = 0.6          # proven cadence; ~10 beats span the arm window
ARM_DELAY_S = 3.0                 # let the unit register the heartbeat before writing


def _beat_bytes(ctr: int) -> bytes:
    """The 4-byte big-endian liveness value written to HEARTBEAT_CHAR each tick."""
    return (ctr & 0xFFFFFFFF).to_bytes(4, "big")


class ConnectionUnavailable(RuntimeError):
    """Could not establish a BLE session (device unreachable or slot held)."""


def _adapter_reset(adapter: str = "hci0") -> None:
    """Power-cycle the local BT adapter and re-scan — clears the abort cascade."""
    for cmd in ("power off", "power on"):
        subprocess.run(["bluetoothctl", cmd.split()[0], cmd.split()[1]],
                       capture_output=True, timeout=10)
        _sleep_blocking(2)
    subprocess.run(["bluetoothctl", "--timeout", "5", "scan", "on"],
                   capture_output=True, timeout=9)


def _sleep_blocking(sec: float) -> None:
    import time
    time.sleep(sec)


class CamperDevice:
    def __init__(self, addr: str = DEFAULT_ADDR, *, adapter: str = "hci0",
                 connect_timeout: float = 30.0):
        self.addr = addr
        self.adapter = adapter
        self.connect_timeout = connect_timeout

    async def _session(self, reset_on_fail: bool | None = None):
        """Yield a connected BleakClient, retrying on the abort cascade.

        Adapter power-cycle recovery is OPT-IN (env CALICTL_ADAPTER_RESET=1),
        default OFF: on buspi hci0 is shared with the Anker/Victron/Govee readers,
        so resetting it would disrupt them. Plain retries (plus bleak-retry-connector
        in solix-env) handle the cascade without touching the adapter.
        """
        import os
        from bleak import BleakClient  # lazy
        if reset_on_fail is None:
            reset_on_fail = os.environ.get("CALICTL_ADAPTER_RESET") == "1"
        last = None
        for attempt in range(3):
            try:
                client = BleakClient(self.addr, timeout=self.connect_timeout)
                await client.connect()
                return client
            except Exception as e:  # BleakError / EOFError / dbus abort
                last = e
                if reset_on_fail and attempt == 0:
                    await asyncio.to_thread(_adapter_reset, self.adapter)
                await asyncio.sleep(4)
        raise ConnectionUnavailable(
            "no BLE session to %s after retries (%s). If ignition is on, the "
            "phone app may hold the single connection slot." % (self.addr, type(last).__name__))

    async def read_all(self, funcs: dict) -> dict[str, bytes]:
        """One session: read every function's state characteristic.
        Returns {function_name: raw_bytes}; missing/failed reads are omitted.
        """
        client = await self._session()
        out: dict[str, bytes] = {}
        try:
            for name, f in funcs.items():
                if not f.state_char:
                    continue
                for _ in range(3):
                    try:
                        out[name] = bytes(await client.read_gatt_char(f.state_char))
                        break
                    except Exception:
                        await asyncio.sleep(0.8)
        finally:
            await self._safe_disconnect(client)
        return out

    async def read(self, func) -> bytes:
        client = await self._session()
        try:
            return bytes(await client.read_gatt_char(func.state_char))
        finally:
            await self._safe_disconnect(client)

    async def write_control(self, func, frame: bytes, *, verify=True) -> dict | None:
        """Write a control frame; optionally read the state char back and return
        the post-write decode for the caller to verify."""
        from . import protocol
        if not func.control_char:
            raise ValueError("%s has no control characteristic" % func.name)
        client = await self._session()
        try:
            try:
                await client.write_gatt_char(func.control_char, frame, response=True)
            except Exception:
                await client.write_gatt_char(func.control_char, frame, response=False)
            if not verify or not func.state_char:
                return None
            await asyncio.sleep(1.5)
            raw = bytes(await client.read_gatt_char(func.state_char))
            return protocol.decode(func, raw)
        finally:
            await self._safe_disconnect(client)

    async def actuate(self, func, frame: bytes, *, verify=True) -> dict | None:
        """Arm the unit with a 1003 liveness heartbeat, then write a control frame.

        One BLE session end-to-end (the single-slot rule holds — callers hold the
        serve lock around this): connect, replay the app's connect handshake
        (version + authenticated reads, subscribe every notifiable char), start a
        +1 counter heartbeat on 1003, and only then write `frame`. Actuation
        latches (one-shot arm), so the heartbeat is torn down right after the write.
        Returns the post-write state decode when `verify`, else None.
        """
        from . import protocol  # lazy
        if not func.control_char:
            raise ValueError("%s has no control characteristic" % func.name)
        client = await self._session()
        stop = asyncio.Event()
        beat = None
        try:
            # connect handshake: version + authenticated reads (bonding/liveness gate)
            for uuid in (VERSION_CHAR, AUTH_CHAR):
                try:
                    await client.read_gatt_char(uuid)
                except Exception:
                    pass
            await self._subscribe_all(client)
            beat = asyncio.create_task(self._heartbeat(client, stop))
            await asyncio.sleep(ARM_DELAY_S)   # let the unit register the heartbeat
            await client.write_gatt_char(func.control_char, frame, response=True)
            if not verify or not func.state_char:
                return None
            await asyncio.sleep(2.5)           # let the actuation take effect
            raw = bytes(await client.read_gatt_char(func.state_char))
            return protocol.decode(func, raw)
        finally:
            stop.set()
            if beat is not None:
                try:
                    await beat
                except Exception:
                    pass
            await self._safe_disconnect(client)

    async def _heartbeat(self, client, stop) -> None:
        """Write a monotonic +1 4-byte big-endian counter to HEARTBEAT_CHAR every
        HEARTBEAT_PERIOD_S until `stop` is set — the actuation arm (issue #2)."""
        ctr = HEARTBEAT_START
        while not stop.is_set():
            try:
                await client.write_gatt_char(HEARTBEAT_CHAR, _beat_bytes(ctr), response=True)
                ctr += 1
            except Exception:
                pass  # keep beating; a dropped tick is tolerable within the arm window
            try:
                await asyncio.wait_for(stop.wait(), HEARTBEAT_PERIOD_S)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    async def _subscribe_all(client) -> int:
        """start_notify on every notifiable/indicatable characteristic — the app
        subscribes all status chars before control writes are honoured. Returns the
        count; individual failures are swallowed (best-effort handshake)."""
        def _noop(_sender, _data):
            pass
        n = 0
        for svc in client.services:
            for ch in svc.characteristics:
                if "notify" in ch.properties or "indicate" in ch.properties:
                    try:
                        await client.start_notify(ch.uuid, _noop)
                        n += 1
                    except Exception:
                        pass
        return n

    @staticmethod
    async def _safe_disconnect(client) -> None:
        try:
            await client.disconnect()
        except Exception:
            pass  # disconnect frequently throws EOFError after a good read
