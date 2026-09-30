"""The Python twin of the ESP32 WiFi provisioning/connectivity SM (#154 Task 3).

``tools.wifi_sm_ref`` is the behavioural source of truth: it generates
``tests/vectors/wifi_sm.json`` (``tools.gen_wifi_vectors``), which the C twin
(``firmware/components/cali_core/wifi_sm.c``) must replay exactly
(``tests/firmware/test_wifi_sm_parity.py``). Every case below asserts the exact
``(state, actions)`` — state is ``(st, ap_up, joined_once, retry_ms, since_ms, next_try_ms)``.

.. test:: WiFi SM Python twin transition rules + golden-vector replay
   :id: T_WIFI_SM_REF
   :links: R_FW_WIFI_PROVISION
"""

import json
from pathlib import Path

from tools import wifi_sm_ref as W
from tools.wifi_consts import CONSTS

VECTORS = Path(__file__).parent / "vectors" / "wifi_sm.json"
MIN, MAX = CONSTS["NET_RETRY_MIN_MS"], CONSTS["NET_RETRY_MAX_MS"]
AP_CLOSE, SETUP_AFTER = CONSTS["NET_AP_CLOSE_MS"], CONSTS["NET_SETUP_AFTER_MS"]


def run(start, *events):
    """Step through ``(ev, arg)`` pairs; return the list of ``(state-list, action-lists)``."""
    s, out = W.WifiState(*start), []
    for ev, arg in events:
        s, acts = W.step(s, ev, arg)
        out.append((list(s), [list(a) for a in acts]))
    return out


def test_pinned_enum_values():
    assert (
        W.WIFI_UNPROVISIONED,
        W.WIFI_SETUP_AP,
        W.WIFI_CONNECTING,
        W.WIFI_ONLINE,
        W.WIFI_RETRYING,
        W.WIFI_SETUP_AP_RETRYING,
    ) == (0, 1, 2, 3, 4, 5)
    assert (
        W.WEV_BOOT_WITH_CREDS,
        W.WEV_BOOT_NO_CREDS,
        W.WEV_CREDS_SET,
        W.WEV_CREDS_FORGET,
        W.WEV_GOT_IP,
        W.WEV_LOST,
        W.WEV_FAILED,
        W.WEV_AP_STARTED,
        W.WEV_TICK,
    ) == tuple(range(9))
    assert (
        W.WACT_AP_START,
        W.WACT_AP_STOP,
        W.WACT_STA_START,
        W.WACT_STA_STOP,
        W.WACT_MDNS,
        W.WACT_CLEAR_CREDS,
        W.WACT_LOG_REASON,
    ) == tuple(range(7))
    assert W.MAX_ACTIONS == 3


def test_fresh_boot_opens_the_setup_ap():
    assert run([0, 0, 0, 0, 0, 0], (W.WEV_BOOT_NO_CREDS, 0), (W.WEV_AP_STARTED, 0)) == [
        ([1, 1, 0, 0, 0, 0], [[W.WACT_AP_START, 0]]),
        ([1, 1, 0, 0, 0, 0], []),  # AP_STARTED: stays SETUP_AP
    ]


def test_boot_with_creds_joins_without_the_ap():
    assert run(
        [0, 0, 0, 0, 0, 0],
        (W.WEV_BOOT_WITH_CREDS, 0),
        (W.WEV_GOT_IP, 0),
        (W.WEV_TICK, 5000),
        (W.WEV_TICK, 5000 + AP_CLOSE),
    ) == [
        ([2, 0, 1, 0, 0, 0], [[W.WACT_STA_START, 0]]),  # R6: stored creds count as joined
        ([3, 0, 1, 0, 0, 0], [[W.WACT_MDNS, 0]]),
        ([3, 0, 1, 0, 5000, 0], []),  # first TICK stamps since_ms
        ([3, 0, 1, 0, 5000, 0], []),  # no AP up -> nothing to close
    ]


def test_first_join_failure_clears_credentials():
    """A typo'd first join never bricks setup: CONNECTING (never joined) + FAILED(auth)
    goes back to SETUP_AP, logs the reason and clears the bad creds (twin + vectors)."""
    auth = 15
    assert run([W.WIFI_CONNECTING, 1, 0, 0, 0, 0], (W.WEV_FAILED, auth)) == [
        ([W.WIFI_SETUP_AP, 1, 0, 0, 0, 0], [[W.WACT_LOG_REASON, auth], [W.WACT_CLEAR_CREDS, 0]]),
    ]
    case = next(
        c
        for c in json.loads(VECTORS.read_text())["cases"]
        if c["id"] == "first-join-failure-clears-credentials"
    )
    assert case["start"] == [W.WIFI_CONNECTING, 1, 0, 0, 0, 0]
    assert case["steps"] == [
        {
            "ev": W.WEV_FAILED,
            "arg": auth,
            "state": [W.WIFI_SETUP_AP, 1, 0, 0, 0, 0],
            "actions": [[W.WACT_LOG_REASON, auth], [W.WACT_CLEAR_CREDS, 0]],
        }
    ]


def test_boot_with_creds_and_router_down_keeps_the_creds():
    """Ruling R6: creds loaded from flash were validated when saved. Boot with the router down
    -> CONNECTING -> FAILED -> RETRYING (backoff) -> SETUP_AP_RETRYING after NET_SETUP_AFTER_MS;
    CLEAR_CREDS never fires; the router comes back -> ONLINE and the AP closes 30 s later."""
    steps = run(
        [0, 0, 0, 0, 0, 0],
        (W.WEV_BOOT_WITH_CREDS, 0),
        (W.WEV_FAILED, 201),
        (W.WEV_TICK, 1000),
        (W.WEV_TICK, 2000),
        (W.WEV_TICK, 1000 + SETUP_AFTER - 1),
        (W.WEV_TICK, 1000 + SETUP_AFTER),
        (W.WEV_FAILED, 201),
        (W.WEV_GOT_IP, 0),
        (W.WEV_TICK, 400000),
        (W.WEV_TICK, 400000 + AP_CLOSE),
    )
    sta = [W.WACT_STA_START, 0]
    assert steps == [
        ([2, 0, 1, 0, 0, 0], [sta]),
        ([4, 0, 1, MIN, 0, 0], [[W.WACT_LOG_REASON, 201]]),
        ([4, 0, 1, MIN, 1000, 2000], []),
        ([4, 0, 1, 2000, 1000, 4000], [sta]),
        ([4, 0, 1, 4000, 1000, SETUP_AFTER + 4999], [sta]),  # overdue retry fires
        ([5, 1, 1, 4000, 1000, SETUP_AFTER + 4999], [[W.WACT_AP_START, 0]]),
        ([5, 1, 1, 4000, 1000, SETUP_AFTER + 4999], []),  # retry failure: no wipe
        ([3, 1, 1, 0, 0, 0], [[W.WACT_MDNS, 0]]),
        ([3, 1, 1, 0, 400000, 0], []),
        ([3, 0, 1, 0, 400000, 0], [[W.WACT_AP_STOP, 0]]),
    ]
    case = next(
        c
        for c in json.loads(VECTORS.read_text())["cases"]
        if c["id"] == "boot-with-creds-router-down-keeps-creds"
    )
    assert case["steps"][0]["state"][2] == 1
    assert all([W.WACT_CLEAR_CREDS, 0] not in s["actions"] for s in case["steps"])
    assert any(s["state"][0] == W.WIFI_SETUP_AP_RETRYING for s in case["steps"])
    assert case["steps"][-1]["state"][0] == W.WIFI_ONLINE


def test_failure_after_a_join_retries_instead():
    assert run([W.WIFI_CONNECTING, 1, 1, 0, 0, 0], (W.WEV_FAILED, 3), (W.WEV_TICK, 2000)) == [
        ([4, 1, 1, MIN, 0, 0], [[W.WACT_LOG_REASON, 3]]),
        ([4, 1, 1, MIN, 2000, 2000 + MIN], []),
    ]


def test_lost_retries_with_backoff_doubling_to_the_cap():
    steps = run(
        [W.WIFI_ONLINE, 0, 1, 0, 1000, 0],
        (W.WEV_LOST, 0),
        (W.WEV_TICK, 10000),
        (W.WEV_TICK, 10999),
        (W.WEV_TICK, 11000),
        (W.WEV_TICK, 13000),
        (W.WEV_TICK, 17000),
        (W.WEV_TICK, 25000),
        (W.WEV_TICK, 41000),
        (W.WEV_TICK, 73000),
        (W.WEV_TICK, 133000),
    )
    assert steps[0] == ([4, 0, 1, MIN, 0, 0], [[W.WACT_STA_STOP, 0]])
    assert steps[1] == ([4, 0, 1, MIN, 10000, 11000], [])  # stamp
    assert steps[2] == ([4, 0, 1, MIN, 10000, 11000], [])  # not yet due
    sta = [[W.WACT_STA_START, 0]]
    assert steps[3:] == [
        ([4, 0, 1, 2000, 10000, 13000], sta),
        ([4, 0, 1, 4000, 10000, 17000], sta),
        ([4, 0, 1, 8000, 10000, 25000], sta),
        ([4, 0, 1, 16000, 10000, 41000], sta),
        ([4, 0, 1, 32000, 10000, 73000], sta),
        ([4, 0, 1, MAX, 10000, 133000], sta),
        ([4, 0, 1, MAX, 10000, 193000], sta),  # capped
    ]


def test_five_minutes_of_retrying_opens_the_setup_ap_and_keeps_retrying():
    t0 = 10000
    steps = run(
        [W.WIFI_RETRYING, 0, 1, MAX, t0, 250000],
        (W.WEV_TICK, 250000),
        (W.WEV_TICK, t0 + SETUP_AFTER - 1),
        (W.WEV_TICK, t0 + SETUP_AFTER),
        (W.WEV_TICK, 370000),
    )
    assert steps == [
        ([4, 0, 1, MAX, t0, 310000], [[W.WACT_STA_START, 0]]),
        ([4, 0, 1, MAX, t0, 310000], []),
        ([5, 1, 1, MAX, t0, 370000], [[W.WACT_STA_START, 0], [W.WACT_AP_START, 0]]),
        ([5, 1, 1, MAX, t0, 430000], [[W.WACT_STA_START, 0]]),  # same schedule, AP not re-started
    ]


def test_ap_closes_30s_after_join():
    steps = run(
        [W.WIFI_SETUP_AP, 1, 0, 0, 0, 0],
        (W.WEV_CREDS_SET, 0),
        (W.WEV_GOT_IP, 0),
        (W.WEV_TICK, 5000),
        (W.WEV_TICK, 5000 + AP_CLOSE - 1),
        (W.WEV_TICK, 5000 + AP_CLOSE),
        (W.WEV_TICK, 5000 + 2 * AP_CLOSE),
    )
    assert steps == [
        ([2, 1, 0, 0, 0, 0], [[W.WACT_STA_START, 0]]),  # AP stays up
        ([3, 1, 1, 0, 0, 0], [[W.WACT_MDNS, 0]]),
        ([3, 1, 1, 0, 5000, 0], []),
        ([3, 1, 1, 0, 5000, 0], []),
        ([3, 0, 1, 0, 5000, 0], [[W.WACT_AP_STOP, 0]]),
        ([3, 0, 1, 0, 5000, 0], []),  # once
    ]


def test_got_ip_while_setup_ap_retrying_goes_online_and_closes_the_ap_later():
    steps = run(
        [W.WIFI_SETUP_AP_RETRYING, 1, 1, MAX, 10000, 370000],
        (W.WEV_GOT_IP, 0),
        (W.WEV_TICK, 400000),
        (W.WEV_TICK, 400000 + AP_CLOSE),
    )
    assert steps == [
        ([3, 1, 1, 0, 0, 0], [[W.WACT_MDNS, 0]]),
        ([3, 1, 1, 0, 400000, 0], []),
        ([3, 0, 1, 0, 400000, 0], [[W.WACT_AP_STOP, 0]]),
    ]


def test_got_ip_while_retrying_goes_online():
    assert run([W.WIFI_RETRYING, 0, 1, 4000, 10000, 17000], (W.WEV_GOT_IP, 0)) == [
        ([3, 0, 1, 0, 0, 0], [[W.WACT_MDNS, 0]])
    ]


def test_creds_set_while_setup_ap_retrying_is_a_fresh_first_join():
    assert run(
        [W.WIFI_SETUP_AP_RETRYING, 1, 1, MAX, 10000, 370000], (W.WEV_CREDS_SET, 0), (W.WEV_FAILED, 2)
    ) == [
        ([2, 1, 0, 0, 0, 0], [[W.WACT_STA_START, 0]]),
        ([1, 1, 0, 0, 0, 0], [[W.WACT_LOG_REASON, 2], [W.WACT_CLEAR_CREDS, 0]]),
    ]


def test_forget_from_every_state():
    forget = [[W.WACT_STA_STOP, 0], [W.WACT_CLEAR_CREDS, 0], [W.WACT_AP_START, 0]]
    for st in range(6):
        assert run([st, st & 1, 1, MIN, 1234, 5678], (W.WEV_CREDS_FORGET, 0)) == [
            ([W.WIFI_SETUP_AP, 1, 0, 0, 0, 0], forget)
        ], st


def test_stray_events_are_noops():
    """The stray list lives in the vectors (so the C twin is checked too): each is unchanged."""
    from tools.gen_wifi_vectors import STRAY

    ids = {c["id"]: c for c in json.loads(VECTORS.read_text())["cases"]}
    assert "stray-retrying-creds-set" in ids and "stray-setup-ap-retrying-failed" in ids
    for cid, start, ev in STRAY:
        assert run(list(start), (ev, 7)) == [(list(start), [])], cid
        assert ids[cid]["steps"] == [{"ev": ev, "arg": 7, "state": list(start), "actions": []}]


def test_actions_never_exceed_max():
    for c in json.loads(VECTORS.read_text())["cases"]:
        for s in c["steps"]:
            assert len(s["actions"]) <= W.MAX_ACTIONS, c["id"]


def test_vectors_replay_and_are_fresh():
    from tools import gen_wifi_vectors

    assert VECTORS.read_text() == gen_wifi_vectors.render()
    cases = json.loads(VECTORS.read_text())["cases"]
    assert len(cases) >= 12
    for case in cases:
        s = W.WifiState(*case["start"])
        for i, st in enumerate(case["steps"]):
            s, acts = W.step(s, st["ev"], st["arg"])
            assert list(s) == st["state"], (case["id"], i)
            assert [list(a) for a in acts] == st["actions"], (case["id"], i)
