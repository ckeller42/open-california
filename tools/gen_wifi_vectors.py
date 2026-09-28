#!/usr/bin/env python3
"""Generate ``tests/vectors/wifi_sm.json`` — the WiFi SM golden vectors (#154 Task 3).

Each case is a start state plus a scripted ``(ev, arg)`` sequence; the expected state and actions
after every step come from running :func:`tools.wifi_sm_ref.step`, the behavioural source of
truth. The C twin (``firmware/components/cali_core/wifi_sm.c``) must replay the file exactly
(``tests/firmware/test_wifi_sm_parity.py``). Checked in; never hand-edit:

    python3 -m tools.gen_wifi_vectors            # rewrite the file
    python3 -m tools.gen_wifi_vectors --check    # exit 1 if the checked-in file is stale
"""

import json
import sys
from pathlib import Path

from tools import wifi_sm_ref as W
from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "vectors" / "wifi_sm.json"

_MIN, _MAX = CONSTS["NET_RETRY_MIN_MS"], CONSTS["NET_RETRY_MAX_MS"]
_CLOSE, _AFTER = CONSTS["NET_AP_CLOSE_MS"], CONSTS["NET_SETUP_AFTER_MS"]
_ZERO = (W.WIFI_UNPROVISIONED, 0, 0, 0, 0, 0)
T, F = W.WEV_TICK, W.WEV_FAILED


# (id, start, ev): each must leave the state unchanged and emit nothing
STRAY = (
    ("stray-unprovisioned-tick", _ZERO, T),
    ("stray-unprovisioned-creds-set", _ZERO, W.WEV_CREDS_SET),
    ("stray-connecting-tick", (W.WIFI_CONNECTING, 1, 0, 0, 0, 0), T),
    ("stray-connecting-creds-set", (W.WIFI_CONNECTING, 1, 0, 0, 0, 0), W.WEV_CREDS_SET),
    ("stray-online-creds-set", (W.WIFI_ONLINE, 0, 1, 0, 5, 0), W.WEV_CREDS_SET),
    ("stray-online-unknown-event", (W.WIFI_ONLINE, 0, 1, 0, 5, 0), 99),
    ("stray-retrying-creds-set", (W.WIFI_RETRYING, 0, 1, 4000, 10000, 17000), W.WEV_CREDS_SET),
    ("stray-retrying-failed", (W.WIFI_RETRYING, 0, 1, _MIN, 5, 1005), F),
    ("stray-setup-ap-retrying-failed", (W.WIFI_SETUP_AP_RETRYING, 1, 1, _MAX, 10000, 370000), F),
)


def _cases():
    """``(id, start, [(ev, arg), ...])`` scripts. Order is the file order."""
    cases = [
        (
            "fresh-boot-join-and-close-ap",
            _ZERO,
            [
                (W.WEV_BOOT_NO_CREDS, 0),
                (W.WEV_AP_STARTED, 0),
                (W.WEV_CREDS_SET, 0),
                (W.WEV_GOT_IP, 0),
                (T, 5000),
                (T, 5000 + _CLOSE - 1),
                (T, 5000 + _CLOSE),
                (T, 5000 + 2 * _CLOSE),
            ],
        ),
        (
            "boot-with-creds-joins-without-ap",
            _ZERO,
            [(W.WEV_BOOT_WITH_CREDS, 0), (W.WEV_GOT_IP, 0), (T, 5000), (T, 5000 + _CLOSE)],
        ),
        ("first-join-failure-clears-credentials", (W.WIFI_CONNECTING, 1, 0, 0, 0, 0), [(F, 15)]),
        # R6: stored creds + router down for > NET_SETUP_AFTER_MS -> retries, then the setup AP
        # while retrying, creds never cleared; the router comes back -> ONLINE, AP closes later
        (
            "boot-with-creds-router-down-keeps-creds",
            _ZERO,
            [
                (W.WEV_BOOT_WITH_CREDS, 0),
                (F, 201),
                (T, 1000),
                (T, 2000),
                (F, 201),
                (T, 4000),
                (T, 8000),
                (T, 16000),
                (T, 32000),
                (T, 64000),
                (T, 124000),
                (T, 184000),
                (T, 244000),
                (T, 1000 + _AFTER - 1),
                (T, 1000 + _AFTER),
                (F, 201),
                (T, 364000),
                (W.WEV_GOT_IP, 0),
                (T, 370000),
                (T, 370000 + _CLOSE),
            ],
        ),
        ("failure-after-join-retries", (W.WIFI_CONNECTING, 1, 1, 0, 0, 0), [(F, 3), (T, 2000), (T, 3000)]),
        (
            "lost-retry-backoff-doubles-to-cap",
            (W.WIFI_ONLINE, 0, 1, 0, 1000, 0),
            [
                (W.WEV_LOST, 0),
                (T, 10000),
                (T, 10999),
                (T, 11000),
                (T, 13000),
                (T, 17000),
                (T, 25000),
                (T, 41000),
                (T, 73000),
                (T, 133000),
                (T, 193000),
            ],
        ),
        (
            "five-min-retrying-opens-setup-ap",
            (W.WIFI_RETRYING, 0, 1, _MAX, 10000, 250000),
            [(T, 250000), (T, 10000 + _AFTER - 1), (T, 10000 + _AFTER), (T, 370000)],
        ),
        (
            "got-ip-while-setup-ap-retrying",
            (W.WIFI_SETUP_AP_RETRYING, 1, 1, _MAX, 10000, 370000),
            [(W.WEV_GOT_IP, 0), (T, 400000), (T, 400000 + _CLOSE - 1), (T, 400000 + _CLOSE)],
        ),
        (
            "got-ip-while-retrying",
            (W.WIFI_RETRYING, 0, 1, 4000, 10000, 17000),
            [(W.WEV_GOT_IP, 0), (T, 20000)],
        ),
        (
            "creds-set-while-setup-ap-retrying",
            (W.WIFI_SETUP_AP_RETRYING, 1, 1, _MAX, 10000, 370000),
            [(W.WEV_CREDS_SET, 0), (F, 2)],
        ),
        (
            "lost-then-regained",
            (W.WIFI_ONLINE, 0, 1, 0, 1000, 0),
            [(W.WEV_LOST, 0), (T, 2000), (T, 3000), (F, 201), (W.WEV_GOT_IP, 0), (T, 4000)],
        ),
        (
            "stray-events-are-noops",
            (W.WIFI_SETUP_AP, 1, 0, 0, 0, 0),
            [
                (W.WEV_GOT_IP, 0),
                (W.WEV_LOST, 0),
                (F, 9),
                (T, 99999),
                (W.WEV_AP_STARTED, 0),
                (W.WEV_BOOT_NO_CREDS, 0),
                (99, 7),
            ],
        ),
        (
            "large-now-crosses-32-bits",
            (W.WIFI_ONLINE, 1, 1, 0, (1 << 32) - 10, 0),
            [(T, (1 << 32) + 29989), (T, (1 << 32) + 29990)],
        ),
    ]
    # one stray (ignored) event per start state, so a wrong C transition there fails parity
    for cid, start, ev in STRAY:
        cases.append((cid, start, [(ev, 7)]))
    for st in range(6):
        cases.append(
            ("forget-from-state-%d" % st, (st, st & 1, 1, _MIN, 1234, 5678), [(W.WEV_CREDS_FORGET, 0)])
        )
    return cases


def generate() -> dict:
    """Run every scripted case through the twin.

    :returns: ``{"cases": [{"id", "start", "steps": [{"ev", "arg", "state", "actions"}]}]}``
    """
    out = []
    for cid, start, events in _cases():
        s, steps = W.WifiState(*start), []
        for ev, arg in events:
            s, acts = W.step(s, ev, arg)
            steps.append({"ev": ev, "arg": arg, "state": list(s), "actions": [list(a) for a in acts]})
        out.append({"id": cid, "start": list(start), "steps": steps})
    return {"version": 1, "generated_by": "tools/gen_wifi_vectors.py", "cases": out}


def render() -> str:
    """The exact checked-in file content for the current twin + constants."""
    return json.dumps(generate(), indent=1) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.is_file() or OUT.read_text() != text:
            print(
                "STALE: %s does not match a fresh regeneration — run python3 -m tools.gen_wifi_vectors" % OUT,
                file=sys.stderr,
            )
            return 1
        print("fresh: %s" % OUT)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
