"""calictl/webui/semantics.js (the satellite UI's browser twin) equals calictl.semantics.

``tools.gen_semantics_vectors`` writes ``tests/vectors/semantics.json`` from the Python oracle
(:mod:`calictl.semantics`, :func:`calictl.anchors.check`, ``ServeBackend._firmware_meta``); this runs
``node`` over the same vectors through semantics.js — loaded as the browser loads it, a classic
script whose top-level names are globals — and asserts every output deep-equal (compared in Python,
so 12 == 12.0 and -0.0 == 0.0). Skipped only when node is absent (CI runners have it).

.. test:: The JS semantics twin equals the Python semantics on the golden vectors
   :id: T_SEMANTICS_JS_PARITY
   :links: R_FW_SHARED_UI
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from calictl import anchors, semantics
from calictl.serve import ServeBackend
from tools import gen_semantics_vectors
from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parent.parent
SEM_JS = ROOT / "calictl" / "webui" / "semantics.js"
VECTORS = ROOT / "tests" / "vectors" / "semantics.json"

# argv: semantics.js, a JS expression evaluated in its context, optional JSON file bound to __V
_HARNESS = (
    'const fs=require("fs"),vm=require("vm");const ctx=vm.createContext({});'
    'vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),ctx,{filename:"semantics.js"});'
    'if(process.argv[3])ctx.__V=JSON.parse(fs.readFileSync(process.argv[3],"utf8"));'
    "process.stdout.write(JSON.stringify(vm.runInContext(process.argv[2],ctx)));"
)


def same(a, b):
    """Deep equality that tells bool from number (``True != 1``) but not int from float (JSON has one number)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def test_same_distinguishes_bool_from_number():
    assert not same(True, 1) and not same({"k": [0]}, {"k": [False]}) and not same({"a": 1}, {"a": 1, "b": 1})
    assert same(12, 12.0) and same({"k": [True, None, -0.0]}, {"k": [True, None, 0.0]})


def node_eval(expr, data_path=None):
    """Evaluate ``expr`` in a fresh context holding semantics.js; returns the JSON-decoded result."""
    node = shutil.which("node")
    if not node:  # a missing node must not silently skip the gate in CI
        (pytest.fail if os.environ.get("CI") else pytest.skip)("node not available")
    args = [node, "-e", _HARNESS, str(SEM_JS), expr] + ([str(data_path)] if data_path else [])
    out = subprocess.run(args, capture_output=True, text=True, check=True, timeout=60)
    return json.loads(out.stdout)


_RUN_VECTORS = """({
  interpret: __V.interpret.map((c) => interpret(c.function, c.fields)),
  states: __V.states.map((c) => {
    const st = {};
    for (const [k, f] of Object.entries(c.fn)) st[k] = interpret(k, f);
    applySwCorrections(st);
    return { state: st, anchors: anchorsCheck(st), firmware: firmwareMeta(st.general) };
  }),
  round: __V.round.map((c) => pyRound(c.x, c.nd)),
  SAT_OFFLINE_S,
})"""


@pytest.fixture(scope="module")
def vectors():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def js():
    return node_eval(_RUN_VECTORS, VECTORS)


def test_vectors_are_fresh(vectors):
    assert gen_semantics_vectors.build() == vectors


def test_interpret_matches_python(js, vectors):
    for case, got in zip(vectors["interpret"], js["interpret"], strict=True):
        assert same(got, case["expect"]), case["id"]


def test_whole_states_match_python(js, vectors):
    for case, got in zip(vectors["states"], js["states"], strict=True):
        assert same(got, case["expect"]), case["id"]


def test_py_round_matches_python_round(js, vectors):
    for case, got in zip(vectors["round"], js["round"], strict=True):
        assert same(got, case["expect"]), case


def test_sat_offline_s_is_three_kicked_reconnect_periods(js):
    """The page's "van asleep" threshold tracks the session's kicked-reconnect pacing (the parked
    unit terminates the held link ~15-20 s after each connect; a paced reconnect refreshes data
    ~every 45-50 s — field 2026-10-09, #264): three periods of slack before claiming sleep."""
    header = (
        Path(__file__).resolve().parents[1]
        / "firmware/components/cali_core/include/cali_session.h"
    ).read_text()
    kicked_ms = int(re.search(r"#define CALI_SESSION_KICKED_RECONNECT_MS (\d+)u", header).group(1))
    assert js["SAT_OFFLINE_S"] == 3 * kicked_ms / 1000


_DEVICE = {
    "pairing": {"state": "bonded", "address": "C0:FF:EE:CA:11:F0"},
    "link": {"up": True, "last_snap_age_ms": 1200},
    "wifi": {"mode": "station", "ssid": "HomeNet", "ip": "192.168.1.57", "rssi": -58},
    "uptime_ms": 5000,
    "fw": "abc1234",
}


def _body(fn=None, **device):
    seed = gen_semantics_vectors.decoded_seed() if fn is None else fn
    return {"t": 5000, "fn": seed, "device": {**_DEVICE, **device}}


def _adapt(body, now_ms=1_000_000):
    return node_eval("adaptSatellite(%s, %d)" % (json.dumps(body), now_ms))


def test_adapter_interprets_like_python_and_synthesizes_meta():
    body = _body()
    got = _adapt(body)
    py = {k: semantics.interpret(k, dict(f)) for k, f in body["fn"].items()}
    semantics.apply_sw_corrections(py)
    meta = got.pop("_meta")
    assert same(got, json.loads(json.dumps(py)))
    assert same(
        meta,
        {
            "online": True,
            "age_s": 1.2,
            "last_seen": 1000 - 1.2,
            "paired": True,
            "read_only": True,
            "session": "off",
            "session_mode": "off",
            "satellite": True,
            "firmware": ServeBackend._firmware_meta(py["general"]),
            "anchors": anchors.check(py),
        },
    )


def test_adapter_wires_anchor_violations():
    """Raw fields that trip anchors reach _meta.anchors exactly as calictl.anchors.check reports them."""
    fn = gen_semantics_vectors.decoded_seed()
    fn["energy"] = {**fn["energy"], "UTwoBattBemAfs": 50}  # 5.0 V, below 8-16 V
    fn["cooler"] = {**fn["cooler"], "Installed": 1, "Level": 7}
    body = _body(fn=fn)
    py = {k: semantics.interpret(k, dict(f)) for k, f in fn.items()}
    semantics.apply_sw_corrections(py)
    want = anchors.check(py)
    assert len(want) >= 2
    assert same(_adapt(body)["_meta"]["anchors"], want)


@pytest.mark.parametrize(
    "up,age_ms,online",
    [
        (True, 90000, True),  # exactly the threshold: still live
        (True, 90001, False),
        (True, None, False),
        (False, 500, True),  # link momentarily down (parked kick cycle) with seconds-old data: live
        (False, 90001, False),
        (True, 0, True),
    ],
)
def test_online_is_data_age_not_link_state(up, age_ms, online):
    """The offline ("van asleep") banner keys on DATA AGE only: during the parked unit's kick/
    reconnect cycle the link flaps while data stays seconds old (field 2026-10-09, #264) — that
    must not read as sleep. Only data older than SAT_OFFLINE_S does."""
    meta = _adapt(_body(link={"up": up, "last_snap_age_ms": age_ms}))["_meta"]
    assert meta["online"] is online
    if age_ms is None:
        assert meta["age_s"] is None and meta["last_seen"] is None


def test_unpaired_device_is_not_paired():
    assert _adapt(_body(pairing={"state": "idle", "address": None}))["_meta"]["paired"] is False


@pytest.mark.parametrize(
    "control,read_only", [(None, True), ({"writes": False}, True), ({"writes": True}, False)]
)
def test_adapter_read_only_follows_the_firmwares_control_flag(control, read_only):
    """Read-only-ness comes from ``device.control.writes`` (station mode), not from "is a satellite":
    an older firmware without the field stays display-only."""
    body = _body() if control is None else _body(control=control)
    assert _adapt(body)["_meta"]["read_only"] is read_only


def test_adapter_with_no_functions():
    got = _adapt(_body(fn={}, link={"up": True, "last_snap_age_ms": None}))
    assert list(got) == ["_meta"]
    assert got["_meta"]["firmware"] == ServeBackend._firmware_meta(None) and got["_meta"]["anchors"] == []


@pytest.mark.parametrize(
    "body,want",
    [
        ({"t": 1, "fn": {}, "device": {}}, True),
        ({"cooler": {"installed": True}, "_meta": {"online": True}}, False),
        ({"fn": {}, "device": {}, "_meta": {}}, False),
        ({"fn": {}}, False),
        ({"error": "state_failed"}, False),
        (None, False),
        ("x", False),
    ],
)
def test_is_satellite_body(body, want):
    assert node_eval("isSatelliteBody(%s)" % json.dumps(body)) is want
