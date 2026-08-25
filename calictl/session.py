"""Persistent-BLE-session supervisor — the daemon's fast-path connection lifecycle.

Extracted from :class:`serve.Server` (which held ~6 session fields + 4 methods for it). The van
allows ONE BLE connection at a time, shared with the phone app, so this holds a persistent armed
session only while the web UI is ACTIVE and RELEASES the slot when it goes idle, backing off while
the van is unreachable. The trickiest concurrency in the daemon — hence its own named home.

Ownership / injection: the supervisor owns the session STATE (the live session, its state string,
the backoff counter, the manual-release mode, the last-UI-activity stamp) and the wake event. The
``_ble`` asyncio.Lock is SHARED with the daemon's poll/on_command/actuate (the van's single-owner
rule), so the daemon creates it inside ``run()``'s loop and hands it here via :meth:`attach` (the
lock + this supervisor's wake event are loop-created — they cannot exist at ``__init__``).

.. req:: One owner for the persistent-session lifecycle
   :id: R_SESSION_SUPERVISOR
   :status: implemented
   :tags: ble, session, concurrency

   Holding/releasing the single BLE slot (UI-active → hold; idle → release for the phone app),
   the reconnect backoff, and the keep-warm nudge shall live in one unit that shares the daemon's
   ``_ble`` lock but owns the session state.
"""
from __future__ import annotations

import asyncio
import time


class SessionSupervisor:
    """Holds the persistent session while the web UI is active; releases the slot when idle; backs
    off while unreachable. Actuation/poll code asks :meth:`live_session` for the fast path.

    :param dev: the :class:`device.CamperDevice`.
    :param interval: the poll interval (seconds) — also the supervisor's idle re-check cadence.
    :param persistent: whether the persistent fast path is enabled (else always cold per-op).
    :param on_push: the notification callback handed to each :class:`device.PersistentSession`.
    :param ui_idle_s: seconds of UI inactivity after which the slot is released.
    """

    SESSION_BACKOFF = (5, 10, 30, 60)   # reconnect backoff seconds, capped
    ASLEEP_AFTER = 4                    # consecutive fails at cap -> label 'asleep'

    def __init__(self, dev, *, interval, persistent, on_push, ui_idle_s):
        self._dev = dev
        self.interval = interval
        self._persistent = persistent
        self._on_push = on_push
        self._ui_idle_s = ui_idle_s
        self._session = None
        self.session_state = "off"         # off|connecting|up|degraded|asleep
        self._backoff_fails = 0
        self._session_mode = None          # None = auto (activity-scoped); "release" = user disconnected
        self._last_ui_activity = None
        self._ble = None                   # the SHARED lock, set by attach() in run()'s loop
        self._wake = None                  # asyncio.Event(), created in attach() (loop-bound)

    def attach(self, ble_lock):
        """Bind the loop-created shared ``_ble`` lock and create the wake event. Call from ``run()``
        (inside the event loop) before starting :meth:`supervise`."""
        self._ble = ble_lock
        self._wake = asyncio.Event()

    @property
    def mode(self):
        """The session mode for the UI: ``"auto"`` (activity-scoped) or ``"release"`` (user disconnected)."""
        return self._session_mode or "auto"

    def live_session(self):
        """The persistent session if it's currently up, else None (caller uses the per-op path)."""
        s = self._session
        return s if (self._persistent and s is not None and s.is_up) else None

    def note_activity(self):
        """Mark the web UI active NOW (a browser poll or a command). While active, the daemon holds
        the fast path; going idle releases it. Called from the web thread AND the loop — a plain
        float write is atomic under the GIL."""
        self._last_ui_activity = time.time()

    def _ui_active(self):
        """True while the UI has been used within ``ui_idle_s``. A manual "release" forces inactive
        regardless of polling, so the slot goes to the phone app until Connect / a command."""
        if self._session_mode == "release":
            return False
        a = self._last_ui_activity
        return a is not None and (time.time() - a) < self._ui_idle_s

    async def set_mode(self, action):
        """Explicit Connect/Disconnect from the web UI. ``connect`` clears any manual release, marks
        active, and nudges the supervisor up NOW; ``disconnect`` sets the release + wakes the
        supervisor to drop the session. Returns the mode + current session state for the UI."""
        if action == "connect":
            self._session_mode = None
            self.note_activity()
            self.nudge()
        elif action == "disconnect":
            self._session_mode = "release"
            if self._wake is not None:
                self._wake.set()   # wake the supervisor to drop the session immediately
        return {"ok": True, "mode": self.mode, "session": self.session_state}

    def nudge(self):
        """Keep-warm: a command wants to actuate. If no session is up, reset the backoff the
        supervisor grew while the van slept and wake it to reconnect NOW. No-op when a session is
        already live."""
        if (self._persistent and self._wake is not None and self.live_session() is None):
            self._backoff_fails = 0
            self._wake.set()

    async def _connect_once(self):
        """One connect attempt. Updates session_state + _backoff_fails. Returns True if it came up.
        The CALLER holds ``_ble`` around this (single-owner)."""
        from . import device  # lazy
        self.session_state = "connecting"
        if self._session is not None:
            # Close the outgoing session before replacing it: else its heartbeat task loops forever
            # and its BleakClient is never disconnected — an unbounded leak per drop->reconnect.
            try:
                await self._session.aclose()
            except Exception:
                pass
            self._session = None
        sess = device.PersistentSession(self._dev, on_push=self._on_push)
        try:
            await sess.start()
        except Exception as e:
            self._backoff_fails += 1
            self._session = None
            self.session_state = "asleep" if self._backoff_fails >= self.ASLEEP_AFTER else "degraded"
            print("session connect failed (%d): %s" % (self._backoff_fails, e), flush=True)
            return False
        self._session = sess
        self.session_state = "up"
        self._backoff_fails = 0
        print("persistent session up", flush=True)
        return True

    async def supervise(self):
        """Hold the persistent session while the web UI is ACTIVE; release the BLE slot when idle (so
        the phone app can connect); back off while the van is unreachable. Runs forever on the loop."""
        while True:
            if not self._ui_active():
                # web UI idle -> release the slot for the phone app; the daemon falls back to brief
                # cold polls, which coexist with the app far better than a permanently-held link.
                if self._session is not None:
                    async with self._ble:
                        await self._session.aclose()
                    self._session = None
                    print("persistent session released (web UI idle)", flush=True)
                self.session_state = "off"
                # wake early when a command/poll marks activity, else re-check each interval
                waiter = asyncio.ensure_future(self._wake.wait())
                try:
                    await asyncio.wait({waiter}, timeout=self.interval)
                finally:
                    waiter.cancel()
                    self._wake.clear()
                continue
            if self._session is not None and self._session.is_up:
                await asyncio.sleep(self.interval)
                continue
            async with self._ble:
                ok = await self._connect_once()
            if not ok:
                idx = min(self._backoff_fails, len(self.SESSION_BACKOFF)) - 1
                # sleep the backoff, but wake IMMEDIATELY if a command nudges us (keep-warm): the user
                # reaching for a control means the van is likely awake now, so don't make them wait
                # out a backoff that grew during a long deep-sleep.
                sleeper = asyncio.ensure_future(asyncio.sleep(self.SESSION_BACKOFF[max(0, idx)]))
                waker = asyncio.ensure_future(self._wake.wait())
                try:
                    await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    sleeper.cancel()
                    waker.cancel()
                    self._wake.clear()
