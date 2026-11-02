"""The at-the-van heater runner: start counting, the sampled fields, and the write gate (no daemon)."""

import pytest

from tools import van_heater_test as v


def test_count_start_only_when_running_rises_from_a_known_false():
    assert v.count_start({"running": False}, {"running": True})
    assert not v.count_start({"running": True}, {"running": True})
    assert not v.count_start({}, {"running": True})  # unknown before: not a start
    assert not v.count_start(None, {"running": True})
    # continuous -> timed hand-over: running stays True while normal rises
    prev = {"running": True, "permanent": True, "normal": False}
    assert not v.count_start(prev, {"running": True, "permanent": False, "normal": True})


def test_sample_of_reads_the_heater_battery_and_ignition_fields():
    st = {
        "airheater": {"running": True, "normal": True, "level": 5, "running_time_remaining": 12},
        "energy": {"batt2_current": -9.1, "batt2_v": 12.9},
        "vehicle": {"ignition_on": False},
        "_meta": {"age_s": 3},
    }
    s = v.sample_of(st)
    assert (s["running"], s["level"], s["remaining"], s["batt2_current"], s["age_s"]) == (
        True,
        5,
        12,
        -9.1,
        3,
    )


def test_lone_runtime_write_needs_a_running_heater_and_a_typed_yes(monkeypatch):
    sent = []

    class Dae:
        def post(self, path, body):
            sent.append(body)
            return {"ok": True}

    class Rec:
        last = {"running": False}

        def check(self):
            pass

        def write(self, rec):
            pass

    assert v.lone_runtime_write(Dae(), Rec(), 30, "w") == {"skipped": "not running"}
    Rec.last = {"running": True, "remaining": 20}
    monkeypatch.setattr(v, "ask", lambda _p: "yes")  # anything but the exact YES refuses
    assert v.lone_runtime_write(Dae(), Rec(), 30, "w") == {"skipped": "no consent"}
    assert sent == []


def test_rejected_write_is_skipped_without_a_watch(monkeypatch):
    class Dae:
        def post(self, path, body):
            return {"error": "read_only"}

    class Rec:
        last = {"running": True, "remaining": 20}

        def check(self):
            pass

        def write(self, rec):
            pass

    monkeypatch.setattr(v, "ask", lambda _p: "YES")
    monkeypatch.setattr(v, "wait_minutes", lambda *_: (_ for _ in ()).throw(AssertionError("watched")))
    out = v.lone_runtime_write(Dae(), Rec(), 30, "w")
    assert out["skipped"] == "write rejected"


def test_unwritable_evidence_file_stops_the_run_and_blocks_the_write(tmp_path, capsys):
    sent = []

    class Dae:
        def post(self, path, body):
            sent.append(body)
            return {"ok": True}

    rec = v.Recorder(Dae(), str(tmp_path / "no-such-dir" / "e.jsonl"), 1)
    rec.write({"ev": "x"})  # does not raise
    rec.last = {"running": True}
    with pytest.raises(v.RecordingUnavailable):
        rec.check()
    with pytest.raises(v.RecordingUnavailable):
        v.lone_runtime_write(Dae(), rec, 30, "w")
    assert sent == []
    monkey = pytest.MonkeyPatch()
    monkey.setattr(v, "preflight", lambda *_: None)
    monkey.setattr(v.time, "sleep", lambda _s: None)
    try:
        assert v.main(["--run", "baseline", "--out", str(tmp_path / "no-such-dir" / "e.jsonl")]) == 2
    finally:
        monkey.undo()
    assert "STOP" in capsys.readouterr().err
