"""Async runner: drives the pure pairing SM (`calictl.pairing`) against a transport.

.. req:: Transport runner drives the pairing SM
   :id: R_PAIRING_RUNNER

   Wraps ``pairing.step`` with asyncio glue: dispatches each ``ACT_*`` to a
   same-named coroutine on a pluggable transport, arms/cancels a per-state
   ``asyncio`` timeout timer from ``TIMEOUT_S``, and turns transport results
   (``verify()``/``persist_bond()``) and transport-call exceptions back into
   events. No dbus/bleak imports here — stdlib-only at import (Task 3 adds the
   concrete BlueZ transport in this same module, with its own lazy imports).
"""
import asyncio

from calictl import pairing
from calictl.pairing import (
    ACT_CONNECT,
    ACT_DISCONNECT,
    ACT_PAIR,
    ACT_PERSIST_BOND,
    ACT_REMOVE_BOND,
    ACT_SEND_PASSKEY,
    ACT_START_SCAN,
    ACT_STOP_SCAN,
    ACT_VERIFY,
    ERR_NAMES,
    ERR_NONE,
    EV_CANCEL,
    EV_PAIR_FAIL,
    EV_PASSKEY_ENTERED,
    EV_RESET,
    EV_RESET_DONE,
    EV_START,
    EV_TIMEOUT,
    EV_VERIFY_FAIL,
    EV_VERIFY_OK,
    IDLE,
    STATE_NAMES,
    PairingState,
    step,
)

# Actions whose transport-call failure means "the pairing attempt failed" (-> EV_PAIR_FAIL);
# ACT_VERIFY is handled separately since it also has a non-exception failure mode (None result).
_PAIR_FAIL_ACTS = (ACT_CONNECT, ACT_PAIR, ACT_SEND_PASSKEY)


class PairingRunner:
    """Drives :func:`calictl.pairing.step` against a transport; owns the asyncio timeout timer.

    :param transport: object with async ``start_scan/stop_scan/connect/pair/
        send_passkey(pk)/verify()->int|None/persist_bond()->str|None/disconnect/
        remove_bond``. Transport callbacks (device found, connected, passkey
        requested, pair result) feed back in through :meth:`handle`.
    """

    def __init__(self, transport):
        self._transport = transport
        self._ps = PairingState(IDLE, 0, ERR_NONE)
        self._timer_task = None
        self.address = None

    @property
    def state(self):
        return self._ps

    async def handle(self, ev, arg=0):
        """Advance the SM by one event, dispatch its actions, (re)arm the timeout timer."""
        old = self._ps
        self._ps, actions = step(self._ps, ev, arg)
        if self._ps != old:
            self._rearm_timer()
        for act, act_arg in actions:
            await self._dispatch(act, act_arg)

    def _rearm_timer(self):
        if self._timer_task is not None:
            self._timer_task.cancel()
            self._timer_task = None
        if self._ps.st in pairing.TIMEOUT_S:
            seconds = pairing.TIMEOUT_S[self._ps.st]
            self._timer_task = asyncio.ensure_future(self._timeout_after(self._ps, seconds))

    async def _timeout_after(self, armed_ps, seconds):
        await asyncio.sleep(seconds)
        if self._ps == armed_ps:  # guard: state must not have moved on since arming
            await self.handle(EV_TIMEOUT)

    async def _dispatch(self, act, arg):
        t = self._transport
        try:
            if act == ACT_START_SCAN:
                await t.start_scan()
            elif act == ACT_STOP_SCAN:
                await t.stop_scan()
            elif act == ACT_CONNECT:
                await t.connect()
            elif act == ACT_PAIR:
                await t.pair()
            elif act == ACT_SEND_PASSKEY:
                await t.send_passkey(arg)
            elif act == ACT_VERIFY:
                result = await t.verify()
            elif act == ACT_PERSIST_BOND:
                result = await t.persist_bond()
            elif act == ACT_DISCONNECT:
                await t.disconnect()
            elif act == ACT_REMOVE_BOND:
                await t.remove_bond()
        except Exception as e:
            print("pairing: transport action %d failed: %r" % (act, e), flush=True)
            if act in _PAIR_FAIL_ACTS:
                await self.handle(EV_PAIR_FAIL)
            elif act == ACT_VERIFY:
                await self.handle(EV_VERIFY_FAIL)
            # scan/disconnect/remove_bond errors: nothing else can retry them here, just log
            return

        if act == ACT_VERIFY:
            if result is not None:
                await self.handle(EV_VERIFY_OK, result)
            else:
                await self.handle(EV_VERIFY_FAIL)
        elif act == ACT_PERSIST_BOND:
            self.address = result

    async def start(self):
        await self.handle(EV_START)

    async def cancel(self):
        await self.handle(EV_CANCEL)

    async def reset(self):
        await self.handle(EV_RESET)
        await self.handle(EV_RESET_DONE)

    async def enter_passkey(self, pk):
        await self.handle(EV_PASSKEY_ENTERED, pk)

    def snapshot(self):
        return {
            "state": STATE_NAMES[self._ps.st],
            "attempts": self._ps.attempts,
            "error": ERR_NAMES[self._ps.error],
            "address": self.address,
        }
