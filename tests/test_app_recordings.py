"""Replay every committed app recording (``tests/vectors/app/*.jsonl``) against calictl.

Recordings are made by ``tools/applab/walk.py`` in the app lab (the real CaliforniaOnTour app in an
emulator against the fake unit) and committed by hand per APK version. Each control write the app
made is attributed to the scenario step that caused it and must equal ``control.build`` on the
fields the app targets; the app's 500 ms neutral flush and its known app-only frames are skipped.
The second half pins the replay rules on synthetic recordings, so they hold before the first
recording lands.

.. test:: App recordings replay against calictl
   :id: T_APP_RECORDINGS
   :links: R_APP_FIDELITY
"""

import json
from pathlib import Path

import pytest

from calictl import control, overrides, protocol
from tools import capture_diff

HERE = Path(__file__).resolve().parent
RECORDINGS = sorted((HERE / "vectors" / "app").glob("*.jsonl"))
CASES = [pytest.param(p, id=p.stem) for p in RECORDINGS] or [
    pytest.param(
        None,
        id="none",
        marks=pytest.mark.skip(reason="no app recordings yet — record one with tools/applab/walk.py"),
    )
]
BASE = json.loads((HERE / "scenarios" / "firmware" / "baseline-0410.json").read_text())["raw_frames_hex"]
HEADER = {"recorded_by": "app 5.0.8.3028", "date": "2026-10-02", "avd": "lab34", "scenario": "t"}


@pytest.mark.parametrize("path", CASES)
def test_recording_header_and_hygiene(path):
    assert capture_diff.recording_hygiene(path) == []


@pytest.mark.parametrize("path", CASES)
def test_recording_writes_match_calictl(path):
    checks = capture_diff.check_recording(path)
    problems = ["%s:%d: %s" % (path.name, c.line, c.problem) for c in checks if c.problem]
    assert not problems, "\n".join(problems)


# --- the replay rules, on synthetic recordings ------------------------------------------------


def _funcs():
    f = protocol.load()
    overrides.apply(f)
    return f


def _ev(t, ev, **kw):
    return {"t": t, "ev": ev, "conn": 1, "t_ms": int(t * 1000), **kw}


def _rec(tmp_path, events, header=HEADER):
    p = tmp_path / "r.jsonl"
    lines = ([header] if header is not None else []) + events
    p.write_text("".join(json.dumps(e) + "\n" for e in lines))
    return p


def _step(t, n, expect):
    return {"t": t, "ev": "step", "n": n, "verb": "xy", "arg": ["p"], "expect": expect}


def _cooler_read():
    return _ev(0.5, "read", char="1102", fn="cooler", hex=BASE["cooler"])


def test_the_apps_neutral_flush_is_not_a_second_action(tmp_path):
    p = _rec(
        tmp_path,
        [
            _ev(0.0, "connect"),
            _cooler_read(),
            _step(1.0, 1, ["cooler", "power", "off"]),
            _ev(1.1, "write", char="1101", fn="cooler", hex="fc771e3e1f1f"),  # the app's OFF
            _ev(1.6, "write", char="1101", fn="cooler", hex="ff771e3e1f1f"),  # its 500 ms neutral frame
            _ev(1.7, "write", char="1003", fn="heartbeat", hex="00000007"),
        ],
    )
    checks = capture_diff.check_recording(p)
    assert [c.kind for c in checks] == ["action", "flush"]
    assert [c.problem for c in checks] == [None, None]


def test_a_second_real_action_under_one_step_fails(tmp_path):
    p = _rec(
        tmp_path,
        [
            _cooler_read(),
            _step(1.0, 1, ["cooler", "power", "off"]),
            _ev(1.1, "write", char="1101", fn="cooler", hex="fc771e3e1f1f"),
            _ev(1.2, "write", char="1101", fn="cooler", hex="fd771e3e1f1f"),  # ON: not what the step did
        ],
    )
    problems = [c.problem for c in capture_diff.check_recording(p) if c.problem]
    assert len(problems) == 1 and "State app=1 calictl=0" in problems[0]


def test_a_write_without_a_calictl_builder_fails(tmp_path):
    p = _rec(
        tmp_path,
        [
            _ev(1.0, "write", char="1302", fn="water", hex="00"),
            _ev(1.1, "write", char="1905", fn=None, hex="00"),
        ],
    )
    problems = [c.problem for c in capture_diff.check_recording(p)]
    assert len(problems) == 2
    assert "char 1302" in problems[0] and "no control builder" in problems[0]
    assert "char 1905" in problems[1]


def test_an_unattributed_action_fails(tmp_path):
    p = _rec(tmp_path, [_ev(1.0, "write", char="1601", fn="energy", hex="10")])
    (c,) = capture_diff.check_recording(p)
    assert c.kind == "error" and "unattributed energy write 10" in c.problem


def test_a_lead_fails(tmp_path):
    f = _funcs()
    ours = control.build(f, "lighting", "kitchen", 5, {})
    vals = dict(control.decode_control(f["lighting"], ours))
    vals["LightValue"] = 1  # the app carries a field calictl sends as 0
    app = protocol.encode(f["lighting"], vals, frame_bytes=16).hex()
    p = _rec(
        tmp_path,
        [_step(1.0, 1, ["lighting", "kitchen", 5]), _ev(1.1, "write", char="1501", fn="lighting", hex=app)],
    )
    (c,) = capture_diff.check_recording(p)
    assert "LightValue" in c.problem and "LEAD" in c.problem


def test_an_expected_action_with_no_write_fails(tmp_path):
    p = _rec(tmp_path, [_step(1.0, 3, ["energy", "mode", "max_charge"])])
    (c,) = capture_diff.check_recording(p)
    assert "step 3" in c.problem and "wrote no such frame" in c.problem


def test_known_app_only_frames_are_reported_not_failed(tmp_path):
    p = _rec(
        tmp_path,
        [_ev(1.0, "write", char="1501", fn="lighting", hex="0d0c000000000000eeeeeeeeeeeeeeee")],
    )
    (c,) = capture_diff.check_recording(p)
    assert c.kind == "app-only" and c.problem is None


def test_an_open_gap_is_reported_and_a_closed_gap_fails(tmp_path):
    wake = "0914000000000000eeeeeeeeeeeeeeee"  # SET Mode 20 (WAKEUP_TIME) on profile 9
    p = _rec(
        tmp_path,
        [
            _step(1.0, 1, ["lighting", "wakeup", "07:00"]),
            _ev(1.1, "write", char="1501", fn="lighting", hex=wake),
        ],
    )
    (c,) = capture_diff.check_recording(p, gaps={("lighting", "wakeup"): "no builder yet"})
    assert c.kind == "gap" and c.problem is None
    q = _rec(
        tmp_path,
        [
            _step(1.0, 1, ["energy", "mode", "max_charge"]),
            _ev(1.1, "write", char="1601", fn="energy", hex="10"),
        ],
    )
    (c,) = capture_diff.check_recording(q, gaps={("energy", "mode"): "was unknown"})
    assert "is closed" in c.problem


def test_a_missing_header_or_unknown_event_fails_with_file_and_line(tmp_path):
    p = _rec(tmp_path, [_ev(1.0, "connect")], header=None)
    with pytest.raises(capture_diff.RecordingError, match=r"r\.jsonl:1: first line must be the header"):
        capture_diff.load_recording(p)
    q = _rec(tmp_path, [_ev(1.0, "connect"), _ev(1.1, "bogus")])
    with pytest.raises(capture_diff.RecordingError, match=r"r\.jsonl:3: unknown ev 'bogus'"):
        capture_diff.load_recording(q)


def test_hygiene_flags_vin_hash_vin_mac_and_passkey(tmp_path):
    vin_like = "ABCDEFGH" + "J1234567" + "8"  # 17 chars of the VIN alphabet, built so no line holds one
    p = _rec(
        tmp_path,
        [
            _ev(1.0, "read", char="1002", fn=None, hex="00" * 16),
            _ev(1.1, "app_screen", step=1, texts=["peer 12:34:56:78:9A:BC", vin_like]),
            _ev(1.2, "pair", state="passkey_shown", passkey=123456),
        ],
    )
    probs = capture_diff.recording_hygiene(p)
    assert any("1002" in x for x in probs)
    assert any("MAC 12:34:56:78:9A:BC" in x for x in probs)
    assert any("VIN-shaped" in x for x in probs)
    assert any("pair event carries ['passkey']" in x for x in probs)
    ok = _rec(tmp_path, [_ev(1.0, "app_screen", step=1, texts=["C0:FF:EE:CA:11:F0"])])
    assert capture_diff.recording_hygiene(ok) == []


def test_replay_pins_the_clock_for_every_build(monkeypatch):
    """The wake-up replay must not depend on the host's date or TZ: a "today" after the recording
    (and a far-east TZ) still reproduces the recorded 07:00 frame."""
    import datetime
    import time

    from calictl import control

    monkeypatch.setenv("TZ", "Pacific/Auckland")
    time.tzset()
    monkeypatch.setattr(control, "local_now", lambda: datetime.datetime(2030, 1, 1, 23, 59))
    try:
        checks = capture_diff.check_recording(str(HERE / "vectors" / "app" / "lighting-wakeup.jsonl"))
    finally:
        monkeypatch.undo()
        time.tzset()
    assert [c for c in checks if c.kind == "error"] == []
    assert any(c.kind == "action" and c.hex.startswith("0e146ac4") for c in checks)
