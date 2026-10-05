"""tools/applab/walk.py + scenarios.py without an emulator: the step dispatcher, its failure paths
and the recording format, with adb, the FIFOs and the ESP faked."""

import json
import os
import re
from datetime import date
from pathlib import Path

import pytest

from calictl import control, overrides, protocol
from tools import capture_diff
from tools.applab import pair_wizard, scenarios, walk
from tools.applab.scenarios import fifo, logwait, pair, ui, wait, xy

HERE = Path(__file__).resolve().parent


class FakeLab:
    """walk.Lab's interface with no device behind it."""

    def __init__(self, screens=(["Home"],), screen=scenarios.XY_SCREEN, pair_ok=True, log=b""):
        self.screens = [list(s) for s in screens]
        self._i = 0
        self._screen = screen
        self.pair_ok = pair_ok
        self.log = log
        self.log_mark = 0
        self.calls = []
        self._t = 0.0

    def texts(self):
        s = self.screens[min(self._i, len(self.screens) - 1)]
        self._i += 1
        return s

    def tap(self, rx):
        self.calls.append(("tap", rx))
        return any(re.search(rx, t) for t in self.texts())

    def screen(self):
        return self._screen

    def fifo(self, cmd):
        self.calls.append(("fifo", cmd))
        return None

    def xy(self, pt):
        self.calls.append(("xy", pt))

    def adb(self, *a):
        self.calls.append(("adb", a))

    def pair(self):
        self.calls.append(("pair",))
        return self.pair_ok

    def log_since_mark(self):
        return self.log[self.log_mark :]

    def shot(self, name):
        self.calls.append(("shot", name))
        return "/shots/%s.png" % name

    def now(self):
        return self._t

    def sleep(self, s):
        self._t += s


def _run(steps, lab, **kw):
    return walk.run_steps("s", steps, lab, clock=lab.now, **kw)


def test_steps_become_step_events_with_their_expectation():
    lab = FakeLab(screens=[["Go"]])
    evs = _run([ui("^Go$", expect=("cooler", "power", "on")), fifo("set cooler State=0")], lab)
    assert [(e["n"], e["verb"], e["arg"]) for e in evs] == [
        (1, "ui", ["^Go$"]),
        (2, "fifo", ["set cooler State=0"]),
    ]
    assert evs[0]["expect"] == ["cooler", "power", "on"] and "expect" not in evs[1]
    assert [c for c in lab.calls if c[0] == "shot"] == [("shot", "s-01-ui"), ("shot", "s-02-fifo")]
    assert evs[1]["t"] >= evs[0]["t"] + walk.SETTLE_S  # the action's write + flush land before the next step


def test_a_missing_ui_target_stops_the_run_and_names_the_last_screenshot():
    with pytest.raises(walk.StepFailed) as e:
        _run([ui("^Nowhere$")], FakeLab(screens=[["Other"]]))
    assert e.value.n == 1 and e.value.shot == "/shots/s-01-ui.png"
    assert "last screenshot: /shots/s-01-ui.png" in str(e.value)


def test_wait_times_out():
    with pytest.raises(walk.StepFailed, match="timeout after 3 s"):
        _run([wait("Never", 3)], FakeLab())


def test_pair_wizard_reports_a_missing_prompt(monkeypatch):
    monkeypatch.setattr(pair_wizard, "tree", lambda: "")
    monkeypatch.setattr(pair_wizard, "notif_has", lambda text: False)
    monkeypatch.setattr(pair_wizard, "sh", lambda *a: "")
    monkeypatch.setattr(pair_wizard.time, "sleep", lambda s: None)
    assert pair_wizard.main() is False


def test_a_pairing_that_never_prompts_fails_the_step():
    with pytest.raises(walk.StepFailed, match="pairing did not complete"):
        _run([pair()], FakeLab(pair_ok=False))


def test_xy_refuses_a_different_screen():
    lab = FakeLab(screen=("1080x1920", 320))
    with pytest.raises(walk.StepFailed, match="re-measure"):
        _run([ui("Home"), xy("vehicle_tab")], lab)
    assert not [c for c in lab.calls if c[0] in ("xy", "tap")]  # refused before anything ran


def test_an_unmeasured_xy_point_fails(monkeypatch):
    monkeypatch.setattr(walk, "XY", {"p": None})
    with pytest.raises(walk.StepFailed, match="not measured"):
        _run([xy("p")], FakeLab())


def test_logwait_consumes_the_line_it_matched():
    lab = FakeLab(log=b"### DISCONNECTED reason=19\n### CONNECTED from x\n")
    _run([logwait("DISCONNECTED", 5), logwait("CONNECTED", 5)], lab)
    with pytest.raises(walk.StepFailed, match="timeout"):
        _run([logwait("DISCONNECTED", 2)], lab)


def test_lab_fifo_mirrors_state_commands_only(tmp_path, monkeypatch):
    monkeypatch.setattr(walk.adbui, "SHOTS", str(tmp_path))
    a, b = tmp_path / "app.in", tmp_path / "esp.in"
    os.mkfifo(a)
    os.mkfifo(b)
    ra = os.open(a, os.O_RDONLY | os.O_NONBLOCK)
    rb = os.open(b, os.O_RDONLY | os.O_NONBLOCK)
    try:
        lab = walk.Lab([str(a), str(b)], tmp_path / "fake.log", tmp_path)
        assert lab.fifo("set cooler Level=3") is None
        assert lab.fifo("forget") is None  # a link command: never to the ESP's fake (its bond)
        got_a, got_b = os.read(ra, 4096), os.read(rb, 4096)
    finally:
        os.close(ra)
        os.close(rb)
    assert got_a == b"set cooler Level=3\nforget\n"
    assert got_b == b"set cooler Level=3\n"
    err = walk.Lab([str(a)], tmp_path / "fake.log", tmp_path).fifo("set cooler Level=3")
    assert "is that fake unit running" in err


class FakeEsp:
    def state(self):
        return {
            "t": 1,
            "fn": {"cooler": {"State": 1, "Level": 3}},
            "device": {"link": {"up": True}, "pairing": {"state": "bonded"}, "wifi": {"ssid": "home"}},
        }

    def texts(self, screen):
        return ["Cooler", "Level 3", "C0:FF:EE:CA:11:F0"]


def test_live_steps_record_both_screens_redacted():
    vin_like = "ABCDEFGH" + "J1234567" + "8"
    lab = FakeLab(screens=[["Go", "VIN " + vin_like]])
    evs = walk.run_steps("cooler", [ui("^Go$")], lab, esp=FakeEsp(), clock=lab.now)
    app = next(e for e in evs if e["ev"] == "app_screen")
    esp = next(e for e in evs if e["ev"] == "esp_state")
    assert app["step"] == 1 and "VIN <vin>" in app["texts"]
    assert esp["screen"] == "cooler" and esp["fn"] == {"cooler": {"State": 1, "Level": 3}}
    assert esp["pairing"] == "bonded" and "wifi" not in esp and "device" not in esp
    assert "C0:FF:EE:CA:11:F0" in esp["texts"]


def test_the_recording_is_header_first_then_merged_by_time(tmp_path):
    hdr = walk.recording_header("5.0.8.3028", "cooler", today=date(2026, 10, 2))
    assert hdr == {
        "recorded_by": "app 5.0.8.3028",
        "date": "2026-10-02",
        "avd": "lab34",
        "scenario": "cooler",
    }
    assert set(hdr) == capture_diff.HEADER_KEYS
    out = tmp_path / "cooler.jsonl"
    fake = [
        {"t": 2.0, "ev": "connect", "conn": 1, "t_ms": 0},
        {"t": 4.0, "ev": "disconnect", "conn": 1, "t_ms": 2000},
    ]
    steps = [
        {"t": 1.0, "ev": "step", "n": 1, "verb": "idle", "arg": [1]},
        {"t": 3.0, "ev": "step", "n": 2, "verb": "idle", "arg": [1]},
    ]
    walk.write_recording(out, hdr, fake, steps)
    header, events = capture_diff.load_recording(out)
    assert header == hdr
    assert [e["t"] for _, e in events] == [1.0, 2.0, 3.0, 4.0]


def test_every_scenario_is_well_formed():
    funcs = protocol.load()
    overrides.apply(funcs)
    base = json.loads((HERE / "scenarios" / "firmware" / "baseline-0410.json").read_text())["raw_frames_hex"]
    assert set(scenarios.SCENARIOS) >= {
        "session",
        "cooler",
        "airheater",
        "campingmode",
        "lighting-zone",
        "roof-hold",
        "energy-mode",
        "lighting-profile",
        "lighting-wakeup",
        "airheater-permanent-on",
    }
    assert set(scenarios.SCREEN) <= set(scenarios.SCENARIOS)
    for name, steps in scenarios.SCENARIOS.items():
        for s in steps:
            assert s.verb in walk.VERBS, (name, s)
            if s.verb == "xy":
                assert s.args[0] in scenarios.XY, (name, s)
            if s.expect and tuple(s.expect[:2]) not in capture_diff.GAPS:
                fn, what, value = s.expect
                st = protocol.decode(funcs[fn], bytes.fromhex(base[fn]))
                assert control.build(funcs, fn, what, value, st) is not None, (name, s)


class DeadEsp:
    """An unreachable ESP: state() reports the error, texts() would raise like a Chromium goto."""

    closed = False

    def __init__(self, url=None):
        pass

    def state(self):
        return {"error": "timed out"}

    def texts(self, screen):
        raise RuntimeError("page.goto: net::ERR_NAME_NOT_RESOLVED")

    def close(self):
        DeadEsp.closed = True


def test_an_unreachable_esp_skips_the_live_capture_and_the_walk_completes(tmp_path, monkeypatch):
    stopped = []
    monkeypatch.setattr(walk, "STATE", tmp_path)
    monkeypatch.setattr(walk, "start_fake", lambda raw, vin: type("P", (), {"pid": 1})())
    monkeypatch.setattr(walk, "app_vin", lambda: "x")
    monkeypatch.setattr(walk, "app_version", lambda: "1.0")
    monkeypatch.setattr(walk, "_stop_child", lambda proc: stopped.append(proc))
    monkeypatch.setattr(walk, "EspProbe", DeadEsp)
    monkeypatch.setattr(walk, "Lab", lambda fifos, log, shots: FakeLab(screens=[["Go"]]))
    monkeypatch.setitem(walk.SCENARIOS, "t", [ui("^Go$")])
    import calictl.trace as trace

    monkeypatch.setattr(trace, "read_events", lambda p: iter(()))
    rc = walk.record("t", tmp_path, tmp_path, live=True, esp_fifo="/x", esp_url="http://esp.invalid")
    assert rc == 0 and len(stopped) == 1  # the fake was SIGTERM'd
    _, events = capture_diff.load_recording(tmp_path / "t.jsonl")
    evs = [e for _, e in events]
    assert any(
        e["ev"] == "note" and "ESP unreachable at http://esp.invalid" in e["text"] and "skipped" in e["text"]
        for e in evs
    )
    assert not [e for e in evs if e["ev"] in ("app_screen", "esp_state")]


def test_raw_mirrors_to_both_fifos_and_forget_to_the_app_only(tmp_path, monkeypatch):
    monkeypatch.setattr(walk.adbui, "SHOTS", str(tmp_path))
    paths = [tmp_path / "app.in", tmp_path / "esp.in"]
    fds = []
    for p in paths:
        os.mkfifo(p)
        fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
    try:
        lab = walk.Lab([str(p) for p in paths], tmp_path / "fake.log", tmp_path)
        lab.fifo("raw 1502 00")
        got = [os.read(fd, 4096) for fd in fds]
        lab.fifo("forget")
        got2 = [os.read(fd, 4096) for fd in fds]
    finally:
        for fd in fds:
            os.close(fd)
    assert got == [b"raw 1502 00\n", b"raw 1502 00\n"]
    assert got2 == [b"forget\n", b""]


def test_lab_pair_propagates_the_wizard_result(tmp_path, monkeypatch):
    monkeypatch.setattr(walk.adbui, "SHOTS", str(tmp_path))
    lab = walk.Lab(["/x"], tmp_path / "fake.log", tmp_path)
    monkeypatch.setattr(pair_wizard, "main", lambda: False)
    assert lab.pair() is False
    monkeypatch.setattr(pair_wizard, "main", lambda: True)
    assert lab.pair() is True


def test_an_empty_fifo_command_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(walk.adbui, "SHOTS", str(tmp_path))
    with pytest.raises(ValueError, match="empty"):
        walk.Lab(["/x"], tmp_path / "fake.log", tmp_path).fifo("  ")
