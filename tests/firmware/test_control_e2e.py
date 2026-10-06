"""Host tier: the firmware's control path end to end — real HTTP core, real NimBLE host, the Bumble
fake unit (#154 B).

``cali-host --http`` pairs, reads, joins the scripted WiFi; then every app-recorded action the ESP
carries (``tests/vectors/control.json`` ``app``) goes through ``POST /api/command`` with the fake
unit in the recorded state, and the unit must receive exactly calictl's writes, follow-up commits
included and spaced as calictl spaces them (``tools/esplab_control_walk.py``, shared with the
CoreS3 bench). Roof, wake-up and unknown commands never reach the unit — and a roof, heartbeat,
wrong-length or empty frame handed straight to the NimBLE transport's ``write`` (cali-host's
test-only ``twrite`` line) is refused at that choke point. The console ``set`` reaches the unit
too; a POST outside station mode is ``403``; the ``1003`` heartbeat keeps ticking through
commands; a write the unit NACKs is ``502`` with no commit after it, one it ACKs and ignores is
``applied: null``; a second command while one is on the air is ``409 busy``.

.. test:: Each /api/command produces calictl's (= the app-recorded) frames at the unit
   :id: T_FW_CONTROL_E2E
   :links: R_FW_CONTROL_TWIN, R_FW_CONTROL_API, R_FW_WRITE_ALLOWLIST
"""

import json
import time

import pytest

from calictl import control, protocol
from tools import esplab_control_walk as walker

from .test_host_e2e import _beats_seen, _funcs, _pair, _serve_raw
from .test_web_e2e import PSK, _request, _wifi_script, get_json

# The walker's own logic runs anywhere; the host-tier tests carry linux_only themselves.
pytestmark = [pytest.mark.xdist_group("firmware-host-build")]
HOST = pytest.mark.linux_only

ARM_S = 3.5  # CODEC_ARM_DELAY_MS + margin
WIFI = "ap minsel -55 1\njoin minsel ok 192.168.1.42\n"
HEARTBEAT_S = 0.6  # CODEC_HEARTBEAT_PERIOD_MS
ELSEWHERE = "Only via buspi or the app"


@pytest.fixture
def rec_unit(tmp_path):
    """(HciUnit, its FAKE_UNIT_RECORD path)."""
    from .conftest import HciUnit

    rec = tmp_path / "unit.jsonl"
    hu = HciUnit(record=str(rec))
    yield hu, rec
    hu.close()


def _online(host_fw, hu, tmp_path):
    """A paired, read, station-mode firmware whose link is armed (up for CODEC_ARM_DELAY_MS)."""
    fw = host_fw(hu, http=True, fake_wifi=_wifi_script(tmp_path, WIFI))
    _pair(fw, hu)
    fw.expect("SNAP", timeout=40)
    fw.send("wifi set minsel %s" % PSK)
    fw.expect("LOG", lambda line: line == "wifi: online 192.168.1.42")
    time.sleep(ARM_S)
    return fw


def _post(fw):
    def post(body):
        r = _request(fw, "POST", "/api/command", body, timeout=15)
        return r.status, json.loads(r.body)

    return post


def _wait_state(fw, want, timeout=15.0):
    """Poll /api/state until its ``fn`` holds every decoded frame of ``want`` on two polls in a row
    (a push still in flight from the previous command cannot land after the check)."""
    end, held = time.monotonic() + timeout, 0
    while True:
        fn = get_json(fw, "/api/state")["fn"]
        held = held + 1 if all(fn.get(k) == v for k, v in want.items()) else 0
        if held == 2:
            return
        assert time.monotonic() < end, ({k: fn.get(k) for k in want}, want)
        time.sleep(0.2)


def _inject(fw, hu):
    funcs = _funcs()

    def inject(case):
        frames = {fn: hx for fn, hx in case["frames_hex"].items() if fn in walker.GATE_FUNCTIONS}
        for fn, hx in frames.items():
            hu.call(_serve_raw, hu.unit, fn, bytes.fromhex(hx), True)
        _wait_state(fw, {fn: protocol.decode(funcs[fn], bytes.fromhex(hx)) for fn, hx in frames.items()})

    return inject


def _case(case_id):
    return next(c for c in walker.app_cases() if c["id"] == case_id)


def _beats(rec):
    """Epoch times of the 1003 heartbeat writes the unit recorded."""
    lines = rec.read_text(encoding="utf-8").splitlines() if rec.is_file() else []
    evs = [json.loads(line) for line in lines]
    return [e["t"] for e in evs if e.get("ev") == "write" and e.get("char") == "1003"]


async def _ack_after(unit, seconds):
    """The unit answers every control write ``seconds`` late (it still records it on arrival)."""
    import asyncio

    orig = type(unit).on_write

    async def late(fn, data):
        orig(unit, fn, data)
        await asyncio.sleep(seconds)

    unit.on_write = late


async def _nack_writes(unit):
    """The unit records every control write and answers it with an ATT error (Write Not Permitted)."""
    from bumble import att

    orig = type(unit).on_write

    def nack(fn, data):
        orig(unit, fn, data)
        raise att.ATT_Error(att.ATT_WRITE_NOT_PERMITTED_ERROR)

    unit.on_write = nack


# -- the walker itself (any host) ------------------------------------------------------------------


def test_walker_flags_wrong_missing_and_early_frames():
    """``walk`` passes calictl's frames and flags a wrong byte, a missing commit, a commit sent
    before its delay, a roof write and a wrong answer — the checks the host tier and the bench rely
    on."""
    case = _case("lighting-zone.jsonl:143")  # kitchen 5: the zone frame, then the commit 300 ms on
    (c1, h1), (c2, h2) = walker.expected_writes(case)
    ok = (200, {"ok": True, "applied": None})

    def run(sent, answer=ok):
        log = []
        return walker.walk([case], lambda c: None, lambda b: (log.extend(sent), answer)[1], lambda: list(log))

    assert run([(c1, h1, 0.0), (c2, h2, 0.31)]) == []
    assert run([(c1, h1[:-2] + "00", 0.0), (c2, h2, 0.31)])  # one byte off
    assert run([(c1, h1, 0.0)])  # no commit
    assert run([(c1, h1, 0.0), (c2, h2, 0.2)])  # commit 200 ms after the frame
    assert run([(c1, h1, 0.0), (c2, h2, 0.31), ("1401", "00", 0.4)])  # + a roof write
    assert run([(c1, h1, 0.0), (c2, h2, 0.31)], (200, {"ok": True, "applied": True}))  # never true


# -- the host tier ---------------------------------------------------------------------------------


@HOST
def test_app_recorded_actions_over_api_command(host_fw, rec_unit, tmp_path):
    """All 31 app cases: 27 produce calictl's frames byte-exact at the unit, 4 wake-ups are refused
    without a write; every answer has ``applied`` null (or false for a refusal), never true."""
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    cases = walker.app_cases()
    assert len(cases) == 31
    problems = walker.walk(cases, _inject(fw, hu), _post(fw), lambda: walker.unit_writes(rec))
    assert not problems, problems
    assert [c for c, _, _ in walker.unit_writes(rec)].count("1401") == 0


@HOST
def test_unit_never_sees_a_roof_or_unknown_write(host_fw, rec_unit, tmp_path):
    """Roof, stairs and wake-up are refused before any frame is built (API and console); an unknown
    control is ``400``. Then the NimBLE transport's own ``write`` (review M3), called directly through
    cali-host's ``twrite``: a roof frame, the ``1003`` heartbeat char, a wrong-length and an empty
    frame are all refused at its choke point — none reaches the unit — while an allowed frame does
    (so the refusals are the choke point's, not a dead hook)."""
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    post = _post(fw)
    for body in (
        {"function": "roof", "what": "open", "confirm": True},
        {"function": "roof", "what": "stop", "confirm": True},
        {"function": "stairs", "what": "move", "value": "extend"},
        {"function": "lighting", "what": "wakeup", "value": "07:00 on"},
    ):
        status, ans = post(body)
        assert (status, ans.get("refused"), ans.get("applied")) == (200, ELSEWHERE, False), body
    assert post({"function": "cooler", "what": "bogus", "value": "1"}) == (
        400,
        {"ok": False, "error": "unknown_control"},
    )
    fw.send("set roof close")
    fw.expect("LOG", lambda line: line == "control: roof/close refused: %s" % ELSEWHERE)

    roof = control.roof_frame(_funcs(), "open", 1).hex()
    zone = walker.expected_writes(_case("lighting-zone.jsonl:143"))[0][1]
    for char, hx, n in (
        ("1401", roof, len(roof) // 2),
        ("1003", "00000001", 4),
        ("1501", zone[:-2], 15),
        ("1501", "-", 0),
    ):
        fw.send("twrite %s %s" % (char, hx))
        fw.expect(
            "LOG",
            lambda line, c=char, n=n: (
                line == "ble: write %s/%d refused: not on the control allow-list" % (c, n)
            ),
        )
        fw.expect("LOG", lambda line, c=char, n=n: line.startswith("twrite: %s/%d rc=" % (c, n)))
    time.sleep(1)
    assert walker.unit_writes(rec) == []
    fw.send("twrite 1601 00")  # energy mode normal: on the allow-list, the transport sends it
    fw.expect("LOG", lambda line: line == "twrite: 1601/1 rc=0")
    end = time.monotonic() + 5
    while not walker.unit_writes(rec) and time.monotonic() < end:
        time.sleep(0.1)
    assert [(c, h) for c, h, _ in walker.unit_writes(rec)] == [("1601", "00")]


@HOST
def test_console_set_reaches_the_unit(host_fw, rec_unit, tmp_path):
    """The USB console's ``set`` drives the same sequencer: calictl's zone frame, then the commit
    at least CODEC_FOLLOW_DELAY_MS after it."""
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    fw.send("set lighting kitchen 5")
    fw.expect("LOG", lambda line: line == "control: lighting/kitchen sent", timeout=10)
    got = walker.unit_writes(rec)
    assert [(c, h) for c, h, _ in got] == walker.expected_writes(_case("lighting-zone.jsonl:143"))
    assert got[1][2] - got[0][2] >= control_follow_s()


def control_follow_s():
    return json.loads(walker.VECTORS.read_text(encoding="utf-8"))["follow_delay_ms"] / 1000


@HOST
def test_command_refused_in_setup_mode(host_fw, rec_unit, tmp_path):
    """Over the setup hotspot (no credentials yet) a command is ``403 setup_mode`` and nothing
    reaches the unit — even with an armed link."""
    hu, rec = rec_unit
    fw = host_fw(hu, http=True, fake_wifi=_wifi_script(tmp_path, "ap minsel -55 1\n"))
    _pair(fw, hu)
    fw.expect("SNAP", timeout=40)
    time.sleep(ARM_S)
    assert get_json(fw, "/api/wifi")["mode"] == "setup"
    status, ans = _post(fw)({"function": "cooler", "what": "power", "value": "on"})
    assert (status, ans) == (403, {"ok": False, "error": "setup_mode"})
    assert walker.unit_writes(rec) == []


@HOST
def test_heartbeat_keeps_ticking_through_commands(host_fw, rec_unit, tmp_path):
    """Five commands back to back: the unit's recording shows the ``1003`` heartbeat on its period
    all the way through the command window (no gap over two periods between beats, beats between
    the commands' writes), and the session never drops."""
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    mark = len(fw.log)
    before = hu.call(_beats_seen, hu.unit)
    post = _post(fw)
    for v in (2, 3, 4, 2, 3):  # cooler level has no gate: every one goes out
        assert post({"function": "cooler", "what": "level", "value": v})[0] == 200
        time.sleep(0.5)
    time.sleep(1)
    ctl = [t for _, _, t in walker.unit_writes(rec)]
    assert len(ctl) == 5
    beats = [t for t in _beats(rec) if ctl[0] - HEARTBEAT_S <= t <= ctl[-1] + HEARTBEAT_S]
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert len(beats) >= (ctl[-1] - ctl[0]) / HEARTBEAT_S, (beats, ctl)
    assert max(gaps) <= 2 * HEARTBEAT_S, gaps
    assert sum(1 for t in beats if ctl[0] < t < ctl[-1]) >= 3
    assert hu.call(_beats_seen, hu.unit) - before >= 8
    assert not [line for line in fw.log[mark:] if line.startswith("LOG session:")]


@HOST
def test_refused_writes_nack_is_502_ack_and_ignore_is_unconfirmed(host_fw, rec_unit, tmp_path):
    """An empty favourite (the mock ACKs and ignores ``profile 3``) is answered like any ACK:
    ``200`` with ``applied`` null, frame + commit sent (calictl's sequence). A write the unit answers
    with an ATT error is ``502 write_failed`` and the lighting commit is never sent after it."""
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    post = _post(fw)
    status, ans = post({"function": "lighting", "what": "profile", "value": 3})
    assert (status, ans["ok"], ans["applied"]) == (200, True, None), ans
    got = [h for _, h, _ in walker.unit_writes(rec)]
    assert got == ["0310000000000000eeeeeeeeeeeeeeee", "0e00000000000000eeeeeeeeeeeeeeee"], got

    hu.call(_nack_writes, hu.unit)
    n = len(walker.unit_writes(rec))
    assert post({"function": "lighting", "what": "kitchen", "value": 5}) == (
        502,
        {"ok": False, "error": "write_failed"},
    )
    fw.expect("LOG", lambda line: line == "control: lighting/kitchen failed: the unit refused the write")
    time.sleep(1.5)  # well past the commit's delay
    zone = walker.expected_writes(_case("lighting-zone.jsonl:143"))[0]
    assert [(c, h) for c, h, _ in walker.unit_writes(rec)[n:]] == [zone]


@HOST
def test_second_command_while_one_is_on_the_air_is_busy(host_fw, rec_unit, tmp_path):
    """The unit ACKs slowly; while the console's lighting command waits for its ACK a POST is
    ``409 busy`` (nothing written for it), and the running command still completes."""
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    hu.call(_ack_after, hu.unit, 1.0)
    fw.send("set lighting kitchen 5")
    fw.expect("LOG", lambda line: line == "control: lighting/kitchen sending")
    assert _post(fw)({"function": "cooler", "what": "level", "value": 3}) == (
        409,
        {"ok": False, "error": "busy"},
    )
    fw.expect("LOG", lambda line: line == "control: lighting/kitchen sent", timeout=10)
    assert [c for c, _, _ in walker.unit_writes(rec)] == ["1501", "1501"]
