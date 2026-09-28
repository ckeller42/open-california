"""wifi_sm_ref.py — the Python twin of the ESP32 firmware's WiFi provisioning/connectivity state
machine (#154 Task 3). Behavioural source of truth: ``tools.gen_wifi_vectors`` runs scripted event
sequences through :func:`step` into ``tests/vectors/wifi_sm.json``, and the C twin
(``firmware/components/cali_core/wifi_sm.c``) must replay them exactly
(``tests/firmware/test_wifi_sm_parity.py``). The enum values below are pinned — the C port gets
them only through the generated ``csrc/net_consts.h`` (``tools.gen_c_dict.generate_net``); never
renumber.

Pure: no strings, no radio, no clock. The radio is driven only through the returned ``WACT_*``
actions; time enters **only** as ``WEV_TICK``'s ``arg`` (``now_ms``, monotonic ms since boot,
64-bit). Every other event carries no time, so a transition that "starts a clock" (join, loss,
join-failure-after-a-join) sets ``since_ms = 0`` = *unstamped*, and the next TICK in that state
stamps ``since_ms = now`` (and, while retrying, ``next_try_ms = now + retry_ms``) without firing
anything. On the firmware's 100 ms tick that costs at most one tick of precision and keeps the
event args single-purpose (``WEV_FAILED``'s ``arg`` is the reason, ``WEV_TICK``'s is ``now_ms``).
Timings come from :data:`tools.wifi_consts.CONSTS`.

State is ``(st, ap_up, joined_once, retry_ms, since_ms, next_try_ms)`` (:class:`WifiState`).
``ap_up`` tracks the SM's *intent*: it is set when the SM emits ``WACT_AP_START`` and cleared
when it emits ``WACT_AP_STOP`` (``WEV_AP_STARTED`` is informational and changes nothing).
"""
from typing import NamedTuple

from tools.wifi_consts import CONSTS

(WIFI_UNPROVISIONED, WIFI_SETUP_AP, WIFI_CONNECTING, WIFI_ONLINE, WIFI_RETRYING,
 WIFI_SETUP_AP_RETRYING) = range(6)
(WEV_BOOT_WITH_CREDS, WEV_BOOT_NO_CREDS, WEV_CREDS_SET, WEV_CREDS_FORGET, WEV_GOT_IP, WEV_LOST,
 WEV_FAILED, WEV_AP_STARTED, WEV_TICK) = range(9)
(WACT_AP_START, WACT_AP_STOP, WACT_STA_START, WACT_STA_STOP, WACT_MDNS, WACT_CLEAR_CREDS,
 WACT_LOG_REASON) = range(7)
MAX_ACTIONS = 3   # CREDS_FORGET emits 3 (STA_STOP, CLEAR_CREDS, AP_START); nothing emits more

_AP_CLOSE_MS = CONSTS["NET_AP_CLOSE_MS"]
_RETRY_MIN_MS = CONSTS["NET_RETRY_MIN_MS"]
_RETRY_MAX_MS = CONSTS["NET_RETRY_MAX_MS"]
_SETUP_AFTER_MS = CONSTS["NET_SETUP_AFTER_MS"]


class WifiState(NamedTuple):
    st: int
    ap_up: int
    joined_once: int
    retry_ms: int
    since_ms: int
    next_try_ms: int


def _tick_retrying(s, now):
    """RETRYING / SETUP_AP_RETRYING + TICK(now): stamp, retry on schedule, open the AP after 5 min."""
    if s.since_ms == 0:
        return s._replace(since_ms=now, next_try_ms=now + s.retry_ms), []
    acts, st, ap_up, retry, nxt = [], s.st, s.ap_up, s.retry_ms, s.next_try_ms
    if now >= nxt:
        acts.append((WACT_STA_START, 0))
        retry = min(retry * 2, _RETRY_MAX_MS)
        nxt = now + retry
    if st == WIFI_RETRYING and now - s.since_ms >= _SETUP_AFTER_MS:
        st, ap_up = WIFI_SETUP_AP_RETRYING, 1
        acts.append((WACT_AP_START, 0))
    return WifiState(st, ap_up, s.joined_once, retry, s.since_ms, nxt), acts


def step(state, ev, arg=0):
    """Advance the WiFi SM by one event.

    :param state: current :class:`WifiState` (or any 6-sequence in its field order)
    :param ev: one of the ``WEV_*`` constants; unknown events are no-ops
    :param arg: ``now_ms`` for ``WEV_TICK``, the reason code for ``WEV_FAILED``; unused otherwise
    :returns: ``(new_state, actions)`` — ``actions`` is a list of at most :data:`MAX_ACTIONS`
        ``(WACT_*, arg)`` pairs to run, in order (only ``WACT_LOG_REASON`` carries an arg, the
        reason truncated to 32 bits like the C twin's ``uint32_t``)
    """
    s = WifiState(*state)
    st = s.st
    if ev == WEV_CREDS_FORGET:
        return (WifiState(WIFI_SETUP_AP, 1, 0, 0, 0, 0),
                [(WACT_STA_STOP, 0), (WACT_CLEAR_CREDS, 0), (WACT_AP_START, 0)])
    if st == WIFI_UNPROVISIONED:
        if ev == WEV_BOOT_NO_CREDS:
            return WifiState(WIFI_SETUP_AP, 1, 0, 0, 0, 0), [(WACT_AP_START, 0)]
        if ev == WEV_BOOT_WITH_CREDS:
            return WifiState(WIFI_CONNECTING, s.ap_up, 0, 0, 0, 0), [(WACT_STA_START, 0)]
    elif st == WIFI_SETUP_AP:
        if ev == WEV_CREDS_SET:
            return s._replace(st=WIFI_CONNECTING), [(WACT_STA_START, 0)]
    elif st == WIFI_CONNECTING:
        if ev == WEV_GOT_IP:
            return WifiState(WIFI_ONLINE, s.ap_up, 1, 0, 0, 0), [(WACT_MDNS, 0)]
        if ev == WEV_FAILED:
            reason = arg & 0xFFFFFFFF
            if not s.joined_once:   # a typo never bricks setup: back to the AP, bad creds gone
                acts = [(WACT_LOG_REASON, reason), (WACT_CLEAR_CREDS, 0)]
                if not s.ap_up:     # booted with creds -> the AP was never started
                    acts.append((WACT_AP_START, 0))
                return WifiState(WIFI_SETUP_AP, 1, 0, 0, 0, 0), acts
            return (WifiState(WIFI_RETRYING, s.ap_up, 1, _RETRY_MIN_MS, 0, 0),
                    [(WACT_LOG_REASON, reason)])
    elif st == WIFI_ONLINE:
        if ev == WEV_LOST:
            return (WifiState(WIFI_RETRYING, s.ap_up, s.joined_once, _RETRY_MIN_MS, 0, 0),
                    [(WACT_STA_STOP, 0)])
        if ev == WEV_TICK:
            if s.since_ms == 0:
                return s._replace(since_ms=arg), []
            if s.ap_up and s.since_ms + _AP_CLOSE_MS <= arg:
                return s._replace(ap_up=0), [(WACT_AP_STOP, 0)]
    elif st in (WIFI_RETRYING, WIFI_SETUP_AP_RETRYING):
        if ev == WEV_GOT_IP:
            return WifiState(WIFI_ONLINE, s.ap_up, 1, 0, 0, 0), [(WACT_MDNS, 0)]
        if ev == WEV_CREDS_SET and st == WIFI_SETUP_AP_RETRYING:
            return WifiState(WIFI_CONNECTING, s.ap_up, 0, 0, 0, 0), [(WACT_STA_START, 0)]
        if ev == WEV_TICK:
            return _tick_retrying(s, arg)
    return s, []
