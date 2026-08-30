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
FOLLOW_DELAY_S = float(os.environ.get("CALICTL_FOLLOW_DELAY_S", "0.3"))  # gap before a commit/follow frame
# A plain read returns whatever is LATCHED in a state characteristic, which goes STALE: the
# fresh-water level char read 1 L while the true level was 11 L. The 1003 liveness heartbeat is
# what drives the unit's sensor-measurement loop AND keeps the link up (an un-heartbeated link is
# dropped after ~15 s) — the app ticks it continuously (~0.7 s) the whole time it's connected, not
# only for actuation. So the read path runs the same heartbeat: this warm-up lets the measurement
# refresh before the read pass. Live-verified on-device 2026-07-09 (1 L -> 11 L, link held 64 s).
HEARTBEAT_WARMUP_S = float(os.environ.get("CALICTL_HEARTBEAT_WARMUP_S", "2.0"))
# Water is push-only on 00001302 (qg/b never writes, no 1301 control char — traced 2026-08-18); a
# bare read returns the stale latch. HYPOTHESIS (from 2026-07-14 notes): the 1003 liveness heartbeat
# drives the unit to re-measure + push a fresh 1302 notification, so keeping it ticking and waiting
# for the push would refresh water. UNVALIDATED — a 2026-08-18 on-van test was inconclusive because
# both tanks were empty (0), so there was no changing level to fetch (fresh reads the ~1 L
# empty-floor which the unit displays as 0). DISABLED by default until validated with a non-empty,
# changing tank; set CALICTL_WATER_PUSH_WAIT_S>0 to try it. See value-freshness.md.
WATER_PUSH_WAIT_S = float(os.environ.get("CALICTL_WATER_PUSH_WAIT_S", "0"))

# Functions whose state char is PUSH-ONLY for freshness: a bare read returns a stale latch and
# the true value arrives only as a notification (water 1302, decompile-confirmed 2026-07-14).
# For all other chars the persistent session live-reads each poll (a stale sticky push would
# otherwise pin them). NB: whether water re-pushes under the subscribe-once persistent session
# (vs per-op which re-subscribes each poll) is UNVERIFIED on-device — if water pins stale in
# persistent mode, a periodic re-subscribe is the follow-up. See value-freshness.md.
PUSH_ONLY_FUNCS = frozenset({"water"})

# roof move (SAFETY-SENSITIVE, NOT-LIVE-VERIFIED). A single roof frame won't complete travel:
# the app streams the OPEN/CLOSE move frame continuously until STOP. Decompile of the app engine
# (`w8/a`, 2026-07-13) settled the model: motion is driven IMMEDIATELY — there is NO STOP-hold /
# confirmation-frame phase. The STOP frames seen in one capture were just the press-and-hold
# dead-man (the on-screen button wasn't held while the user read the safety dialog), not protocol.
ROOF_MOVE_PERIOD_S = float(os.environ.get("CALICTL_ROOF_PERIOD_S", "0.5"))  # ~500 ms counter cadence
ROOF_MAX_TRAVEL_S = float(os.environ.get("CALICTL_ROOF_MAX_TRAVEL_S", "30.0"))  # hard cap on travel
# SafetyCounter (frame bytes 1-4, 32-bit BE) is APP-GENERATED and purely time-derived — it does
# NOT echo the unit's counter. The app engine seeds a random int in [1, 1_000_000] and sets
# value = seed + floor(elapsed_ms / 500), i.e. a monotonic +1 per ~500 ms of wall-clock. The unit
# only checks liveness/monotonicity and reports a SafetyCounterValid bit (char 1402, bit 7); it
# gates motor actuation until the counter validates. The app arms a 3 s dead-man: if the counter
# is still invalid after 3 s it errors ("SafetyCounter is invalid after 3 seconds").
ROOF_SAFETY_TICK_MS = 500                 # counter increments +1 per this many ms of wall-clock
ROOF_SAFETY_VALIDATE_S = 3.0              # dead-man: counter must validate within ~3 s, else error
ROOF_SAFETY_SEED_MAX = 1_000_000          # app seeds a random SafetyCounter in [1, this]


def _roof_safety_counter(seed: int, elapsed_ms: float, tick_ms: int = ROOF_SAFETY_TICK_MS) -> int:
    """The app's time-derived roof SafetyCounter: ``seed + floor(elapsed_ms / tick_ms)`` as a
    32-bit big-endian-able unsigned int (monotonic +1 per ``tick_ms`` of wall-clock)."""
    return (seed + int(elapsed_ms // tick_ms)) & 0xFFFFFFFF


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


async def _read_char_with_retry(client, name, char):
    """Read one state characteristic with the standard 3x / 0.8 s-backoff retry and a mid-loop
    disconnect abort. The single home for the read retry/abort/log policy, shared by
    :meth:`CamperDevice._read_all_on` and :meth:`PersistentSession.read_all` (which previously kept
    two byte-identical copies of this loop).

    :returns: ``(data, link_up)`` — ``data`` is the raw bytes on success, or ``None`` when the 3
        attempts were exhausted (caller skips that func). ``link_up`` reports the client's ACTUAL
        connection state at return (on every path, incl. a successful read), so the caller's
        ``if not up: break`` faithfully reproduces the originals' unconditional post-read
        ``if not is_connected: break`` — a read can succeed while the disconnect callback has already
        flipped ``is_connected`` (asyncio race), and the cycle must still abort before the next func.

    .. req:: One shared state-read retry policy
       :id: R_READ_RETRY_SHARED
       :status: implemented
       :tags: ble, reads

       The per-op and persistent read paths shall share one retry/abort/log implementation, so the
       "3 tries, 0.8 s backoff, abort the cycle on disconnect" behaviour cannot drift between them.
    """
    for attempt in range(3):
        try:
            data = bytes(await client.read_gatt_char(char))
            return data, getattr(client, "is_connected", False)   # report the post-read link state
        except Exception as e:
            if not getattr(client, "is_connected", False):
                # link dropped mid-session: don't burn ~30 s of futile retries under the serve lock
                print("read_all: link dropped at %s; aborting cycle" % name, flush=True)
                return None, False
            if attempt == 2:
                print("read_all: %s failed after retries: %r" % (name, e), flush=True)
            await asyncio.sleep(0.8)
    return None, getattr(client, "is_connected", False)


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
            "no BLE session to %s after retries (%s). Causes: the unit deep-slept (parked/idle — "
            "wakes on door/ignition), the phone app holds the single connection slot, or the unit's "
            "Bluetooth is DISABLED in its settings (persistent DeviceNotFound that won't self-resolve "
            "until re-enabled)." % (self.addr, type(last).__name__))

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
        notif: dict[str, bytes] = {}                  # pushed values, keyed by lowercased char UUID
        await self._subscribe_all(client, notif)      # water (1302) is push-only for freshness
        stop = asyncio.Event()
        beat = asyncio.ensure_future(self._heartbeat(client, stop))
        try:
            await asyncio.sleep(HEARTBEAT_WARMUP_S)   # let the liveness register + sensors refresh
            for name, f in funcs.items():
                if not f.state_char:
                    continue
                pushed = notif.get(str(f.state_char).lower())
                if pushed is not None:                # a fresh notification beats the stale latch
                    out[name] = pushed
                    continue
                data, up = await _read_char_with_retry(client, name, f.state_char)
                if data is not None:
                    out[name] = data
                if not up:
                    break
            # Push-only freshness: water's true level is delivered as a heartbeat-driven 1302
            # NOTIFICATION, not the instantaneous read (which returns the stale latch). If water only
            # yielded a read, keep the heartbeat running and wait for the fresh push to land.
            await self._await_water_push(client, funcs, out, notif)
        finally:
            stop.set()
            try:
                await beat
            except Exception:
                pass
            await self._safe_disconnect(client)
        return out

    async def _await_water_push(self, client, funcs, out, notif):
        """Wait (heartbeat still ticking) for a FRESH water 1302 push, replacing the stale read.

        Exits as soon as a new notification arrives (the freshly-measured level) or after
        ``WATER_PUSH_WAIT_S``. A no-op when water isn't in the table or the wait is disabled. Never
        raises — on any trouble the earlier (stale) read stands."""
        import time as _t
        wait_s = float(os.environ.get("CALICTL_WATER_PUSH_WAIT_S", WATER_PUSH_WAIT_S))  # read live (testable)
        wf = funcs.get("water")
        if wf is None or not wf.state_char or wait_s <= 0 or not client.is_connected:
            return
        key = str(wf.state_char).lower()
        seen0 = notif.get(key)                  # the latch push (if any) from the initial subscribe
        deadline = _t.monotonic() + wait_s
        try:
            while _t.monotonic() < deadline and client.is_connected:
                await asyncio.sleep(0.5)
                p = notif.get(key)
                if p is not None and p != seen0:   # a NEW push = the fresh re-measurement
                    out["water"] = p
                    return
        except Exception as e:
            print("water push-wait aborted: %r" % e, flush=True)

    async def read(self, func) -> bytes:
        """Read one function's state char under a live 1003 heartbeat (fresh, not the stale
        latch — see :meth:`read_all`). Prefers a pushed notification when one arrives (water is
        push-only for freshness — see :meth:`_subscribe_all`)."""
        client = await self._session()
        notif: dict[str, bytes] = {}
        await self._subscribe_all(client, notif)
        stop = asyncio.Event()
        beat = asyncio.ensure_future(self._heartbeat(client, stop))
        try:
            await asyncio.sleep(HEARTBEAT_WARMUP_S)
            pushed = notif.get(str(func.state_char).lower())
            return pushed if pushed is not None else bytes(await client.read_gatt_char(func.state_char))
        finally:
            stop.set()
            try:
                await beat
            except Exception:
                pass
            await self._safe_disconnect(client)

    async def actuate(self, func, frame: bytes, *, verify=True,
                      follow: bytes | None = None) -> dict | None:
        """Connect, arm with a 1003 heartbeat, write a control frame, disconnect (one BLE
        session — the CLI/per-op path). See :meth:`_actuate_on` for the shared write core.

        .. req:: Arm the unit with a 1003 heartbeat before a control write
           :id: R_ACTUATE_ARM
           :status: implemented
           :tags: ble, control

           ``calictl`` shall, in one BLE session, replay the connect handshake and tick the
           1003 liveness counter before writing a control frame (and any ``follow`` commit
           frame), so the unit's arm gate honours the write. Diagram: :need:`S_SEQ_ACTUATE`.
        """
        if not func.control_char:
            raise ValueError("%s has no control characteristic" % func.name)
        client = await self._session()
        try:
            return await self._actuate_on(client, func, frame, follow=follow, verify=verify,
                                          arm=True)
        finally:
            await self._safe_disconnect(client)

    async def _handshake(self, client, label, noun):
        """Replay the connect handshake (version + auth reads, subscribe-all) so a write is honoured
        and notifications flow. NO heartbeat, NO arm-delay — that's the caller's choice. ``label``/
        ``noun`` tag the weak-handshake warning."""
        auth_ok = 0
        for uuid in (VERSION_CHAR, AUTH_CHAR):
            try:
                await client.read_gatt_char(uuid); auth_ok += 1
            except Exception:
                pass
        n = await self._subscribe_all(client)
        if auth_ok < 2 or n == 0:
            print("%s: weak handshake (auth-reads=%d/2, subscribed=%d) — %s may be ignored"
                  % (label, auth_ok, n, noun), flush=True)

    async def _arm(self, client, stop, label, noun):
        """Handshake, then start the 1003 liveness heartbeat and wait ``ARM_DELAY_S`` so the unit
        registers it before the first write. Returns the heartbeat task. This is the
        ``R_ACTUATE_ARM`` prologue (issue #2's 1003 gate) for the CONTROL actuation path.

        NB the ROOF path does NOT use this: the app streams its SafetyCounter immediately with no
        1003 heartbeat and no pre-write sleep (the counter itself is the roof's liveness proof, and
        a 3 s pre-arm gap would make the unit see a fresh counter and withhold the motor another
        ~3 s — verified against the decompiled roof driver 2026-08-30). Roof uses ``_handshake``.
        """
        await self._handshake(client, label, noun)
        beat = asyncio.create_task(self._heartbeat(client, stop))
        await asyncio.sleep(ARM_DELAY_S)   # let the unit register the heartbeat
        return beat

    async def _actuate_on(self, client, func, frame: bytes, *, follow: bytes | None = None,
                          verify: bool = True, arm: bool = True) -> dict | None:
        """Write a control frame over an ALREADY-CONNECTED client. Never connects/disconnects.

        ``arm=True`` (cold session, e.g. the CLI): replay the handshake (VERSION/AUTH reads +
        subscribe-all), start a +1 heartbeat on 1003, wait ``ARM_DELAY_S`` so the unit registers
        liveness, then write. ``arm=False`` (persistent session): the caller's heartbeat is already
        ticking, so skip the handshake/heartbeat/arm-delay and write immediately -- the < 1 s path.

        :param follow: optional second frame written to the same control char right after ``frame``
            (the lighting commit; see ``control.commit_for``).
        :returns: the post-write ``protocol.decode`` when ``verify`` and the func has a state char.
        """
        from . import protocol  # lazy
        if not func.control_char:
            raise ValueError("%s has no control characteristic" % func.name)
        stop = asyncio.Event()
        beat = None
        try:
            if arm:
                beat = await self._arm(client, stop, "actuate", "write")
            await client.write_gatt_char(func.control_char, frame, response=True)
            if follow is not None:
                await asyncio.sleep(FOLLOW_DELAY_S)
                await client.write_gatt_char(func.control_char, follow, response=True)
            if not verify or not func.state_char:
                return None
            await asyncio.sleep(SETTLE_S)
            raw = bytes(await client.read_gatt_char(func.state_char))
            return protocol.decode(func, raw)
        finally:
            stop.set()
            if beat is not None:
                try:
                    await beat
                except Exception:
                    pass

    async def actuate_roof(self, func, move_frame: bytes, stop_frame: bytes, *,
                           max_duration_s: float = ROOF_MAX_TRAVEL_S,
                           period_s: float = ROOF_MOVE_PERIOD_S,
                           validate_s: float = ROOF_SAFETY_VALIDATE_S,
                           counter_seed: int | None = None,
                           stop_event=None,
                           verify: bool = True) -> dict | None:
        """Drive a roof OPEN/CLOSE move by streaming move frames, then force STOP.

        **SAFETY-SENSITIVE.** The wire protocol is **live-verified** against the app's real
        roof drive (HCI capture 2026-07-14, char 1401): direction bytes 0x01 open / 0x04 close /
        0x00 stop, a **free-running** monotonic +1/500 ms SafetyCounter (the app runs it
        continuously from connect, never echoing the unit), press-and-hold with no STOP-hold
        phase, and Position decoding closed(0)→middle(2)→closed against a real ~30 cm move with
        SafetyCounterValid staying set. **calictl driving the roof itself is still unproven** —
        the frames match the app, but we have not yet had calictl actuate the motor end-to-end.
        Physical roof motion can pinch/collide — a caller must have confirmed the roof is clear.
        Nothing here runs automatically.

        Model (settled by the app-engine decompile, ``w8/a``, 2026-07-13): a single roof
        frame will not complete travel, so the app **streams the move frame immediately**
        — there is NO STOP-hold / confirmation phase (the STOP frames seen in a capture
        were the press-and-hold dead-man, not protocol). Each frame is ``[direction byte]
        [4-byte BE SafetyCounter]``. The SafetyCounter is **app-generated and purely
        time-derived**: a random seed in ``[1, ROOF_SAFETY_SEED_MAX]`` plus ``+1`` per
        ``ROOF_SAFETY_TICK_MS`` of wall-clock (:func:`_roof_safety_counter`) — it does
        NOT echo the unit's counter. The unit withholds motor actuation until that
        counter validates (reported as ``SafetyCounterValid``, char 1402 bit 7), which
        takes up to a ~3 s dead-man; if still invalid after ``validate_s`` the app treats
        it as an error, and so do we (log + STOP early).

        Runs in ONE BLE session (single-owner model, held under the ``serve`` lock):
        connect, replay the connect handshake (version + auth reads, subscribe-all),
        start the 1003 arm heartbeat, then re-send ``move_frame`` (with the live
        time-derived counter) every ``period_s`` for at most ``max_duration_s`` — a
        bounded travel cap — and then send ``stop_frame``. The unit self-gates the first
        ~3 s via counter validation, so early frames may not move the roof.

        The final STOP is **best-effort**: on a clean end it is written and confirmed,
        but on a mid-move link drop the STOP write itself fails and is swallowed. The real
        stop guarantee in that case is the hardware's own dead-man behaviour — the unit is
        expected to halt when the move frames cease — which is plausible but **UNVERIFIED
        on this unit**. Do not rely on the software STOP reaching the unit after a link
        drop. Returns the post-STOP state decode when ``verify``, else None.

        :param func: the roof Function (needs ``control_char`` + ``state_char``).
        :param move_frame: the OPEN or CLOSE frame (``control.roof_frame``); byte 0 is the
            direction, bytes 1-4 are overwritten with the live time-derived SafetyCounter.
        :param stop_frame: the STOP frame (byte 0 == 0), sent to end travel unconditionally.
        :param max_duration_s: hard cap on the move duration (safety bound).
        :param period_s: frame cadence (~500 ms, ``ROOF_MOVE_PERIOD_S``).
        :param validate_s: dead-man window — if ``SafetyCounterValid`` is still false after this
            many seconds the move is aborted (STOP) as the app does. ``None`` disables the check.
        :param counter_seed: optional explicit SafetyCounter seed (else a random app-style seed);
            handy for deterministic tests.
        :param verify: read the state char back after STOP.
        :returns: post-STOP state decode, or None when ``verify`` is false.

        .. req:: Drive roof travel with a bounded, time-derived-counter move stream
           :id: R_ROOF_ACTUATE
           :status: implemented
           :tags: ble, control, roof, safety

           ``calictl`` shall, in one armed BLE session, stream the roof move frame with a
           monotonic app-generated SafetyCounter for a bounded maximum duration and then
           unconditionally send a STOP frame — so roof travel is completed but never left
           running unbounded — and shall abort (STOP) if the unit does not validate the
           SafetyCounter within the ~3 s dead-man window.
        """
        import random
        import time

        from . import protocol  # lazy
        if not func.control_char:
            raise ValueError("%s has no control characteristic" % func.name)
        client = await self._session()
        stop = asyncio.Event()
        beat = None
        seed = counter_seed if counter_seed is not None else random.randint(1, ROOF_SAFETY_SEED_MAX)
        start = None                       # set once the move stream begins (arms the counter clock)

        async def _send(direction_byte: bytes) -> bool:
            """Write one 5-byte roof frame (direction + the live time-derived SafetyCounter).
            Returns False only on a genuine link drop (so the caller breaks to the final STOP)."""
            elapsed_ms = 0.0 if start is None else (time.monotonic() - start) * 1000.0
            ctr = _roof_safety_counter(seed, elapsed_ms)
            frame = direction_byte[:1] + ctr.to_bytes(4, "big")
            try:
                await client.write_gatt_char(func.control_char, frame, response=True)
                return True
            except Exception:
                if not client.is_connected:
                    return False
                return True   # transient write error on a live link: keep driving

        try:
            # App-faithful roof arm (decompile-verified 2026-08-30): handshake ONLY — no 1003
            # heartbeat, no ARM_DELAY_S pre-arm sleep. The app streams the SafetyCounter immediately
            # on button press; a pre-arm gap would make the unit see a fresh counter and withhold
            # the motor another ~3 s. The counter IS the roof's liveness proof.
            await self._handshake(client, "actuate_roof", "move")

            # Stream the move frame with the live time-derived counter until the safety cap. The
            # unit self-gates motion for the first ~validate_s until the counter validates; we
            # abort (-> STOP) if it never does, mirroring the app's 3 s dead-man error.
            start = time.monotonic()
            deadline = start + max_duration_s
            validate_checked = False
            # stop_event lets a hold-to-move UI (or a 'stop' command) interrupt travel:
            # set lock-free by another coroutine, checked each frame -> break -> STOP.
            while time.monotonic() < deadline and not (stop_event is not None and stop_event.is_set()):
                if not await _send(move_frame):
                    print("actuate_roof: link dropped mid-move — sending STOP", flush=True)
                    break
                if (validate_s is not None and not validate_checked and func.state_char
                        and (time.monotonic() - start) >= validate_s):
                    validate_checked = True
                    if not await self._roof_counter_valid(client, func):
                        print("actuate_roof: SafetyCounter invalid after %.0fs — aborting move "
                              "(STOP)" % validate_s, flush=True)
                        break
                await asyncio.sleep(period_s)
            # ALWAYS force a STOP (best-effort even if the link is flaky), with the live counter.
            await _send(stop_frame)
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

    @staticmethod
    async def _roof_counter_valid(client, func) -> bool:
        """Read the roof state char and return the ``SafetyCounterValid`` bit (char 1402 bit 7).
        Best-effort: a read/decode failure is treated as *valid* (True) so a flaky read never
        force-aborts a move on its own — the bounded cap + STOP remain the real safety net."""
        from . import protocol  # lazy
        try:
            raw = bytes(await client.read_gatt_char(func.state_char))
            dec = protocol.decode(func, raw)
            return bool(dec.get("SafetyCounterValid", 1))
        except Exception:
            return True

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
            except TimeoutError:
                pass

    @staticmethod
    async def _subscribe_all(client, sink: dict | None = None, on_push=None) -> int:
        """start_notify on every notifiable/indicatable characteristic — the app
        subscribes all status chars before control writes are honoured. Returns the
        count; individual failures are swallowed (best-effort handshake).

        When ``sink`` is given, pushed notifications are captured into it keyed by the
        lowercased char UUID. Some chars are **push-only for freshness** — notably water
        (``1302``): a bare read returns a stale latch, and the true level arrives only as a
        notification (decompile-confirmed 2026-07-14). Readers prefer ``sink`` over a bare read.

        When ``on_push(uuid_lower, data)`` is given, it is called for EVERY notification (in
        addition to the sink) — the daemon uses it to log camping/ignition changes the instant
        the unit pushes them (the app subscribes camping state on ``1202``, decompile-confirmed),
        rather than waiting for the next poll. It must be sync + fast; its exceptions are swallowed.
        """
        def _handler(sender, data):
            u = str(getattr(sender, "uuid", sender)).lower()
            b = bytes(data)
            if sink is not None:
                sink[u] = b
            if on_push is not None:
                try:
                    on_push(u, b)
                except Exception:
                    pass
        n = 0
        for svc in client.services:
            for ch in svc.characteristics:
                if "notify" in ch.properties or "indicate" in ch.properties:
                    try:
                        await client.start_notify(ch.uuid, _handler)
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


class PersistentSession:
    """One long-lived connected client with the 1003 heartbeat ticking continuously — the
    daemon's fast path. Poll reads and command writes run over the live link (no per-op connect,
    subscribe, or 3 s arm). CLI code keeps using CamperDevice.actuate/read_all.

    .. req:: Hold a persistent armed BLE session for fast actuation
       :id: R_PERSISTENT_SESSION
       :status: implemented
       :tags: ble, control, latency

       While the van is reachable, the daemon shall keep one connected client with the 1003
       heartbeat ticking, so control writes apply in well under a second (no connect/subscribe/
       arm-delay per action), and reads stay fresh continuously.
    """

    def __init__(self, dev: CamperDevice, on_push=None):
        self._dev = dev
        self._client = None
        self._notif: dict[str, bytes] = {}
        self._on_push = on_push        # optional sync callback(uuid_lower, data) fired on each push
        self._stop = None
        self._beat = None

    @property
    def is_up(self) -> bool:
        return bool(self._client is not None and getattr(self._client, "is_connected", False)
                    and self._beat is not None and not self._beat.done())

    async def start(self) -> None:
        client = await self._dev._session()
        for uuid in (VERSION_CHAR, AUTH_CHAR):
            try:
                await client.read_gatt_char(uuid)
            except Exception:
                pass
        self._notif = {}
        await self._dev._subscribe_all(client, self._notif, on_push=self._on_push)
        self._stop = asyncio.Event()
        self._beat = asyncio.create_task(self._dev._heartbeat(client, self._stop))
        await asyncio.sleep(HEARTBEAT_WARMUP_S)     # let the arm + first measurement register
        self._client = client

    async def read_all(self, funcs: dict) -> dict[str, bytes]:
        """Live-read every function's state char over the already-connected client.

        Mirrors :meth:`CamperDevice._read_all_on`'s retry/logging behaviour: a char not
        satisfied by a push is retried up to 3x (0.8 s backoff) before being skipped, with a
        failure logged, and the cycle aborts early if the client itself disconnects mid-loop.

        Only :data:`PUSH_ONLY_FUNCS` (water) may be served from the persistent notification
        cache (``self._notif``) indefinitely — it is push-only-for-freshness, so a bare read
        would return a stale latch. Every other notifiable char is live-read each poll: this
        cache lives for the lifetime of the session, so trusting it for an ordinary char would
        pin whatever value was captured at subscribe time forever.
        """
        out: dict[str, bytes] = {}
        client = self._client
        for name, f in funcs.items():
            if not f.state_char:
                continue
            if name in PUSH_ONLY_FUNCS:
                pushed = self._notif.get(str(f.state_char).lower())
                if pushed is not None:
                    out[name] = pushed
                    continue
            data, up = await _read_char_with_retry(client, name, f.state_char)
            if data is not None:
                out[name] = data
            if not up:
                break
        return out

    async def read_one(self, func) -> bytes:
        return bytes(await self._client.read_gatt_char(func.state_char))

    async def actuate(self, func, frame: bytes, *, follow: bytes | None = None,
                      verify: bool = True) -> dict | None:
        return await self._dev._actuate_on(self._client, func, frame, follow=follow,
                                           verify=verify, arm=False)

    async def aclose(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._beat is not None:
            try:
                await self._beat
            except Exception:
                pass
        if self._client is not None:
            await self._dev._safe_disconnect(self._client)
        self._client = None
        self._beat = None
