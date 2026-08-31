"""Platform-free pairing state machine: no strings, no clock, no addresses;
time only as EV_TIMEOUT; pinned values frozen for the C port.

.. req:: Platform-free pairing state machine
   :id: R_PAIRING_SM

   The pure SM core for guided BLE pairing (buspi web wizard now, ESP32
   touchscreen later, #154). No strings, no clock, no addresses in the SM —
   the transport injects ``EV_DEVICE_FOUND`` only after its own name filter
   matched; time enters only as ``EV_TIMEOUT`` from a platform timer; bond
   persistence is an opaque ``ACT_PERSIST_BOND``. Enum values below are
   pinned (a future C port + the sequence vectors in
   ``tests/vectors/pairing.json`` depend on them) — never renumber.
"""
from typing import NamedTuple

IDLE, SCANNING, CONNECTING, WAITING_PASSKEY, PAIRING, VERIFYING, BONDED, ERROR, RESETTING = range(9)
(EV_START, EV_DEVICE_FOUND, EV_CONNECTED, EV_PASSKEY_REQUESTED, EV_PASSKEY_ENTERED,
 EV_PAIR_OK, EV_PAIR_FAIL, EV_VERIFY_OK, EV_VERIFY_FAIL, EV_TIMEOUT, EV_CANCEL,
 EV_RESET, EV_RESET_DONE) = range(13)
(ACT_START_SCAN, ACT_STOP_SCAN, ACT_CONNECT, ACT_PAIR, ACT_SEND_PASSKEY, ACT_VERIFY,
 ACT_PERSIST_BOND, ACT_DISCONNECT, ACT_REMOVE_BOND) = range(9)
ERR_NONE, ERR_TIMEOUT, ERR_PAIR, ERR_VERIFY = range(4)
MAX_ATTEMPTS = 3
TIMEOUT_S = {SCANNING: 30, CONNECTING: 15, WAITING_PASSKEY: 60, PAIRING: 15,
             VERIFYING: 10, RESETTING: 10}
STATE_NAMES = {IDLE: "idle", SCANNING: "scanning", CONNECTING: "connecting",
               WAITING_PASSKEY: "waiting_passkey", PAIRING: "pairing",
               VERIFYING: "verifying", BONDED: "bonded", ERROR: "error",
               RESETTING: "resetting"}
ERR_NAMES = {ERR_NONE: None, ERR_TIMEOUT: "timeout", ERR_PAIR: "pairing_failed",
             ERR_VERIFY: "verify_failed"}


class PairingState(NamedTuple):
    st: int
    attempts: int
    error: int


def _cleanup(st):
    return [(ACT_STOP_SCAN, 0)] if st == SCANNING else \
        [(ACT_DISCONNECT, 0)] if st in (CONNECTING, WAITING_PASSKEY, PAIRING, VERIFYING) else []


def step(ps, ev, arg=0):
    """Advance the pairing SM by one event.

    :param ps: current :class:`PairingState`
    :param ev: one of the ``EV_*`` constants
    :param arg: event payload (passkey digits, verify readable-char count); unused by most events
    :returns: ``(new_state, actions)`` where ``actions`` is a list of ``(ACT_*, arg)`` pairs to run, in order
    """
    st = ps.st
    if ev == EV_CANCEL:
        return PairingState(IDLE, 0, ERR_NONE), _cleanup(st)
    if ev == EV_TIMEOUT and st in TIMEOUT_S:
        return PairingState(ERROR, ps.attempts, ERR_TIMEOUT), _cleanup(st)
    if ev == EV_RESET and st in (BONDED, ERROR, IDLE):
        return PairingState(RESETTING, 0, ERR_NONE), [(ACT_REMOVE_BOND, 0)]
    if st in (IDLE, ERROR) and ev == EV_START:
        return PairingState(SCANNING, 0, ERR_NONE), [(ACT_START_SCAN, 0)]
    if st == SCANNING and ev == EV_DEVICE_FOUND:
        return PairingState(CONNECTING, ps.attempts, ERR_NONE), [(ACT_STOP_SCAN, 0), (ACT_CONNECT, 0)]
    if st == CONNECTING and ev == EV_CONNECTED:
        return PairingState(PAIRING, ps.attempts, ERR_NONE), [(ACT_PAIR, 0)]
    if st == PAIRING and ev == EV_PASSKEY_REQUESTED:
        return PairingState(WAITING_PASSKEY, ps.attempts, ERR_NONE), []
    if st == WAITING_PASSKEY and ev == EV_PASSKEY_ENTERED:
        return PairingState(PAIRING, ps.attempts, ERR_NONE), [(ACT_SEND_PASSKEY, arg)]
    if st == PAIRING and ev == EV_PAIR_OK:
        return PairingState(VERIFYING, ps.attempts, ERR_NONE), [(ACT_VERIFY, 0)]
    if st == PAIRING and ev == EV_PAIR_FAIL:
        att = ps.attempts + 1
        if att < MAX_ATTEMPTS:
            return PairingState(SCANNING, att, ERR_NONE), [(ACT_DISCONNECT, 0), (ACT_START_SCAN, 0)]
        return PairingState(ERROR, att, ERR_PAIR), [(ACT_DISCONNECT, 0)]
    if st == VERIFYING and ev == EV_VERIFY_OK:
        return PairingState(BONDED, ps.attempts, ERR_NONE), [(ACT_PERSIST_BOND, 0)]
    if st == VERIFYING and ev == EV_VERIFY_FAIL:
        return PairingState(ERROR, ps.attempts, ERR_VERIFY), [(ACT_DISCONNECT, 0)]
    if st == RESETTING and ev == EV_RESET_DONE:
        return PairingState(IDLE, 0, ERR_NONE), []
    return ps, []
