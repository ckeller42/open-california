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
import os
import subprocess

# The vehicle's BLE identity address is owner-specific PII — never hardcode a real one.
# Set CALICTL_ADDR in the environment (buspi: /etc/buspi/calictl.env) or pass --addr;
# the repo default is a non-functional placeholder.
DEFAULT_ADDR = os.environ.get("CALICTL_ADDR", "AA:BB:CC:DD:EE:FF")


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
# Timing (env-overridable). Defaults are the proven on-device values; the mock/e2e harness
# shrinks them so a run is fast while still exercising the multi-step arm→write→settle window
# (and thus the UI's in-flight feedback). Do NOT lower the defaults for real hardware.
HEARTBEAT_PERIOD_S = float(os.environ.get("CALICTL_HEARTBEAT_PERIOD_S", "0.6"))  # ~10 beats span the arm window
ARM_DELAY_S = float(os.environ.get("CALICTL_ARM_DELAY_S", "3.0"))   # let the unit register the heartbeat before writing
SETTLE_S = float(os.environ.get("CALICTL_SETTLE_S", "2.5"))         # let the actuation take effect before readback
# A plain read returns whatever is LATCHED in a state characteristic, which goes STALE: the
# fresh-water level char read 1 L while the true level was 11 L. The 1003 liveness heartbeat is
# what drives the unit's sensor-measurement loop AND keeps the link up (an un-heartbeated link is
# dropped after ~15 s) — the app ticks it continuously (~0.7 s) the whole time it's connected, not
# only for actuation. So the read path runs the same heartbeat: this warm-up lets the measurement
# refresh before the read pass. Live-verified on-device 2026-07-09 (1 L -> 11 L, link held 64 s).
HEARTBEAT_WARMUP_S = float(os.environ.get("CALICTL_HEARTBEAT_WARMUP_S", "2.0"))

# roof move-heartbeat (SAFETY-SENSITIVE, NOT-LIVE-VERIFIED). A single roof frame
# won't complete travel: ig/c.java re-sends Up/Down at ~1 Hz until STOP. These bound
# how long actuate_roof drives the move before force-STOPping.
ROOF_MOVE_PERIOD_S = 1.0          # ~1 Hz move-heartbeat cadence (ig/c.java)
ROOF_MAX_TRAVEL_S = 30.0          # hard safety cap on a single open/close command
# HCI capture (2026-07-08) showed the app writes a LIVE +1 SafetyCounter (frame bytes 1-4,
# 32-bit BE) on every roof frame — idle and moving. A fixed counter=0 (our old behaviour)
# is not "live". actuate_roof increments it each resend, starting from an arbitrary high value.
ROOF_SAFETY_START = 0x00100000


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
        """One session: read every function's state characteristic, under a live 1003 heartbeat
        so the values are FRESH.

        :param funcs: the loaded function table (name -> Function with ``state_char``).
        :returns: ``{function_name: raw_bytes}``; functions with no state char (or whose read
            failed) are omitted.

        A bare read returns whatever is LATCHED in the characteristic, which decays to a stale
        value (the fresh-water char read 1 L while the true level was 11 L). The 1003 liveness
        heartbeat is what drives the unit's sensor-measurement loop and keeps the link alive — the
        app ticks it continuously the whole time it is connected, not only for actuation. So this
        runs the same ``_heartbeat`` for the duration of the read (with a short warm-up so the
        measurement refreshes first), all within the one session held under the ``serve`` BLE lock.

        .. req:: read_all keeps values fresh with a live 1003 heartbeat
            :id: R_READ_HEARTBEAT_REFRESH

            The poll must run the 1003 liveness heartbeat while reading, so the unit measures and
            the link stays up — otherwise a stale latched value (fresh-water 1 L vs the true 11 L)
            is surfaced and the link is dropped mid-cycle.
        """
        client = await self._session()
        return await self._read_all_on(client, funcs)

    async def _read_all_on(self, client, funcs: dict) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        stop = asyncio.Event()
        beat = asyncio.ensure_future(self._heartbeat(client, stop))
        try:
            await asyncio.sleep(HEARTBEAT_WARMUP_S)   # let the liveness register + sensors refresh
            for name, f in funcs.items():
                if not f.state_char:
                    continue
                for attempt in range(3):
                    try:
                        out[name] = bytes(await client.read_gatt_char(f.state_char))
                        break
                    except Exception as e:
                        if not client.is_connected:
                            # link dropped mid-session: don't burn ~30 s of futile retries
                            # (3 chars x 0.8 s x remaining funcs) under the serve BLE lock.
                            print("read_all: link dropped at %s; aborting cycle" % name, flush=True)
                            break
                        if attempt == 2:
                            print("read_all: %s failed after retries: %r" % (name, e), flush=True)
                        await asyncio.sleep(0.8)
                if not client.is_connected:
                    break
        finally:
            stop.set()
            try:
                await beat
            except Exception:
                pass
            await self._safe_disconnect(client)
        return out

    async def read(self, func) -> bytes:
        """Read one function's state char under a live 1003 heartbeat (fresh, not the stale
        latch — see :meth:`read_all`)."""
        client = await self._session()
        stop = asyncio.Event()
        beat = asyncio.ensure_future(self._heartbeat(client, stop))
        try:
            await asyncio.sleep(HEARTBEAT_WARMUP_S)
            return bytes(await client.read_gatt_char(func.state_char))
        finally:
            stop.set()
            try:
                await beat
            except Exception:
                pass
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
            auth_ok = 0
            for uuid in (VERSION_CHAR, AUTH_CHAR):
                try:
                    await client.read_gatt_char(uuid); auth_ok += 1
                except Exception:
                    pass
            n = await self._subscribe_all(client)
            if auth_ok < 2 or n == 0:
                # a weak handshake is the leading write-gate hypothesis — surface it rather
                # than silently proceed to a write the unit may ignore.
                print("actuate: weak handshake (auth-reads=%d/2, subscribed=%d) — write may "
                      "be ignored" % (auth_ok, n), flush=True)
            beat = asyncio.create_task(self._heartbeat(client, stop))
            await asyncio.sleep(ARM_DELAY_S)   # let the unit register the heartbeat
            await client.write_gatt_char(func.control_char, frame, response=True)
            if not verify or not func.state_char:
                return None
            await asyncio.sleep(SETTLE_S)      # let the actuation take effect
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

    async def actuate_roof(self, func, move_frame: bytes, stop_frame: bytes, *,
                           max_duration_s: float = ROOF_MAX_TRAVEL_S,
                           period_s: float = ROOF_MOVE_PERIOD_S,
                           verify: bool = True) -> dict | None:
        """Drive a roof OPEN/CLOSE move with the 1 Hz move-heartbeat, then force STOP.

        **SAFETY-SENSITIVE and NOT-LIVE-VERIFIED** (the pop-up roof is not installed
        on this van, so this path has never actuated real hardware; enum polarity +
        the SafetyCounter handshake are UNVERIFIED). Physical roof motion can pinch/
        collide — a caller must have confirmed the roof is clear. Nothing here runs
        automatically.

        Unlike the one-shot :meth:`actuate`, a single roof frame will not complete
        travel: the app's ``ig/c.java`` re-sends the Up/Down frame at ~1 Hz until a
        STOP. This runs the whole thing in ONE BLE session (single-owner model, held
        under the ``serve`` lock): connect, replay the connect handshake (version +
        auth reads, subscribe-all), start the 1003 arm heartbeat, then re-send
        ``move_frame`` every ``period_s`` for at most ``max_duration_s`` — a bounded
        safety cap — and ALWAYS send ``stop_frame`` at the end (also on a mid-move
        link drop). Returns the post-STOP state decode when ``verify``, else None.

        :param func: the roof Function (needs ``control_char`` + ``state_char``).
        :param move_frame: the OPEN or CLOSE frame (``control.roof_frame``).
        :param stop_frame: the STOP frame, sent to end travel unconditionally.
        :param max_duration_s: hard cap on move-heartbeat duration (safety bound).
        :param period_s: move-heartbeat cadence (~1 Hz).
        :param verify: read the state char back after STOP.
        :returns: post-STOP state decode, or None when ``verify`` is false.

        .. req:: Drive roof travel with a bounded move-heartbeat
           :id: R_ROOF_ACTUATE
           :status: implemented
           :tags: ble, control, roof, safety

           ``calictl`` shall, in one armed BLE session, re-send the roof move frame
           at ~1 Hz for a bounded maximum duration and then unconditionally send a
           STOP frame, so roof travel is completed but never left running unbounded.
        """
        import time
        from . import protocol  # lazy
        if not func.control_char:
            raise ValueError("%s has no control characteristic" % func.name)
        client = await self._session()
        stop = asyncio.Event()
        beat = None
        try:
            auth_ok = 0
            for uuid in (VERSION_CHAR, AUTH_CHAR):
                try:
                    await client.read_gatt_char(uuid); auth_ok += 1
                except Exception:
                    pass
            n = await self._subscribe_all(client)
            if auth_ok < 2 or n == 0:
                print("actuate_roof: weak handshake (auth-reads=%d/2, subscribed=%d) — move "
                      "may be ignored" % (auth_ok, n), flush=True)
            beat = asyncio.create_task(self._heartbeat(client, stop))
            await asyncio.sleep(ARM_DELAY_S)   # let the unit register the heartbeat
            # 1 Hz move-heartbeat: re-send the move frame with a LIVE +1 SafetyCounter (bytes
            # 1-4, from the HCI capture) until the safety cap, then STOP.
            ctr = ROOF_SAFETY_START
            deadline = time.monotonic() + max_duration_s
            while time.monotonic() < deadline:
                frame = move_frame[:1] + (ctr & 0xFFFFFFFF).to_bytes(4, "big")
                ctr += 1
                try:
                    await client.write_gatt_char(func.control_char, frame, response=True)
                except Exception:
                    if not client.is_connected:
                        print("actuate_roof: link dropped mid-move — sending STOP", flush=True)
                        break
                await asyncio.sleep(period_s)
            # ALWAYS force a STOP (best-effort even if the link is flaky), with the next counter.
            try:
                stop_live = stop_frame[:1] + (ctr & 0xFFFFFFFF).to_bytes(4, "big")
                await client.write_gatt_char(func.control_char, stop_live, response=True)
            except Exception:
                pass
            if not verify or not func.state_char:
                return None
            await asyncio.sleep(SETTLE_S)      # let the state settle after STOP
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
