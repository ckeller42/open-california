#!/usr/bin/env python3
"""At-the-van, human-in-the-loop evidence session for the auxiliary air heater.

The owner drives the heater from the real app (or the in-vehicle panel); this runner records what the
unit reports while that happens, and asks for one verdict per step. It answers the questions a cabin
thermostat needs before it is designed: what 1702 shows during glow, burn, run-on and a regulation
pause; whether a running unit accepts a lone RunningTime write (the rolling run-time idea); whether
an armed timer ends continuous heating.

HOW IT TALKS TO THE VAN
  Only over the running daemon's web API (GET /api/state, POST /api/session, POST /api/command). It
  never opens its own BLE connection — `serve` owns buspi's BLE link. Run it ON buspi
  (default http://localhost:8088). It keeps the daemon's fast session warm, samples the interpreted
  state every --every seconds (air heater, second-battery current and voltage, ignition) and writes
  every sample, mark and verdict to one JSONL file.

WRITES
  The runner writes to the unit in exactly one step, behind a typed YES: `R4` (one lone `runtime`
  write while the heater runs). It never pushes a run past the immediate-heating limits (VW's manual
  for the T6.1: 120 min per run, 180 min in total, then the heater locks until the van has been
  driven — ErrorCode 4). R4 needs the daemon with writes enabled: run the session through
  `bash tools/roof_test_session.sh --test tools.van_heater_test -- --run A`, which enables writes for
  the session and reverts to read-only on exit. Every other step only reads. Ctrl-C sends nothing:
  stop the heater from the app or the panel.

SAFETY
  The owner is at the van, the van is parked outdoors with the exhaust free. At most 3 heater starts
  per day; no OFF→ON within a minute; the runner counts the starts it sees (NormalOperation or
  PermanentOperation rising) in ~/.cache/calictl/heater_starts.json and warns at the budget.

USAGE
  python3 -m tools.van_heater_test --run baseline        # 10 min, no write
  python3 -m tools.van_heater_test --run A               # app start, levels, regulation, R4, link loss
  python3 -m tools.van_heater_test --run B               # the unit's timer starts the heater
  python3 -m tools.van_heater_test --run CD              # continuous heating + timer deadline
  python3 -m tools.van_heater_test --run A --out ~/heater-A.jsonl --url http://buspi:8088
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date

START_BUDGET_PER_DAY = 3
BUDGET_FILE = os.path.expanduser("~/.cache/calictl/heater_starts.json")
SESSION_KEEPALIVE_S = 15.0  # the daemon releases an idle fast session after ~25 s


class Daemon:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def state(self) -> dict:
        with urllib.request.urlopen(self.base + "/api/state", timeout=10) as r:
            return json.load(r)

    def post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return json.load(e)
            except Exception:
                return {"error": "http_%d" % e.code}
        except Exception as e:
            return {"error": "transport", "detail": repr(e)}


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def sample_of(st: dict) -> dict:
    """The fields the heater questions need, from one /api/state reply."""
    a, e, v, m = (st.get(k) or {} for k in ("airheater", "energy", "vehicle", "_meta"))
    return {
        "age_s": m.get("age_s"),
        "session": m.get("session"),
        "running": a.get("running"),
        "normal": a.get("normal"),
        "permanent": a.get("permanent"),
        "level": a.get("level"),
        "error_code": a.get("error_code"),
        "mode": a.get("mode"),
        "timer_armed": a.get("timer_armed"),
        "running_time": a.get("running_time"),
        "remaining": a.get("running_time_remaining"),
        "batt2_current": e.get("batt2_current"),
        "batt2_v": e.get("batt2_v"),
        "ignition_on": v.get("ignition_on"),
    }


def status_line(s: dict) -> str:
    return "heater %s%s lvl=%s err=%s mode=%s rt=%s left=%s | batt2 %s A %s V | age %ss" % (
        "ON" if s.get("running") else "off",
        " (continuous)" if s.get("permanent") else "",
        s.get("level"),
        s.get("error_code"),
        s.get("mode"),
        s.get("running_time"),
        s.get("remaining"),
        s.get("batt2_current"),
        s.get("batt2_v"),
        s.get("age_s"),
    )


def count_start(prev: dict | None, cur: dict) -> bool:
    """A start = NormalOperation or PermanentOperation rising from a known 0."""
    if not prev:
        return False
    return any(prev.get(k) is False and cur.get(k) is True for k in ("normal", "permanent"))


def starts_today() -> int:
    try:
        return int(json.load(open(BUDGET_FILE)).get(str(date.today()), 0))
    except (OSError, ValueError):
        return 0


def add_start() -> int:
    n = starts_today() + 1
    os.makedirs(os.path.dirname(BUDGET_FILE), exist_ok=True)
    with open(BUDGET_FILE, "w") as f:
        json.dump({str(date.today()): n}, f)
    return n


class Recorder:
    """Samples the daemon in a thread, keeps its session warm, appends everything to one JSONL."""

    def __init__(self, dae: Daemon, out: str, every: float):
        self.dae, self.out, self.every = dae, out, every
        self.last: dict = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def write(self, rec: dict) -> None:
        with self._lock, open(self.out, "a") as f:
            f.write(json.dumps({"t": round(time.time(), 2), **rec}, separators=(",", ":")) + "\n")

    def _run(self) -> None:
        keep = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - keep > SESSION_KEEPALIVE_S:
                self.dae.post("/api/session", {"action": "connect"})
                keep = now
            try:
                s = sample_of(self.dae.state())
            except Exception as e:
                self.write({"ev": "error", "detail": repr(e)})
            else:
                if count_start(self.last, s):
                    n = add_start()
                    self.write({"ev": "start_seen", "starts_today": n})
                    if n > START_BUDGET_PER_DAY:
                        print("\n  ⚠  %d heater starts today (budget %d)" % (n, START_BUDGET_PER_DAY))
                self.last = s
                self.write({"ev": "sample", **s})
            self._stop.wait(self.every)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


# --- steps -------------------------------------------------------------------
# kind: "do" = the owner acts, then Enter; "wait" = watch for `min` minutes; "r4" = the runner writes.
# `q` is the verdict question asked at the end of the step (empty = none).

RUNS: dict[str, list[dict]] = {
    "baseline": [
        {
            "id": "B0",
            "kind": "wait",
            "min": 10,
            "say": "No action. Idle 1702, idle current, temperatures.",
            "q": "",
        },
    ],
    "A": [
        {
            "id": "A1",
            "kind": "do",
            "say": "App: run time 40 min, level 5 (heater off).",
            "q": "Did the app show 40 min and level 5?",
        },
        {
            "id": "A2",
            "kind": "do",
            "say": "App: Immediate heating ON. Note when warm air comes out.",
            "q": "",
        },
        {
            "id": "A2w",
            "kind": "wait",
            "min": 10,
            "say": "Watch glow (≈ -9 A) then burn.",
            "q": "Warm air within the first 5 min?",
        },
        {"id": "A3", "kind": "do", "say": "App: level 1.", "q": ""},
        {
            "id": "A3w",
            "kind": "wait",
            "min": 10,
            "say": "Watch current and air flow at level 1.",
            "q": "Did the burner restart (new glow, ≈ -9 A)?",
        },
        {"id": "A4", "kind": "do", "say": "App: level 10 (HI).", "q": ""},
        {
            "id": "A4w",
            "kind": "wait",
            "min": 5,
            "say": "Watch current at HI.",
            "q": "Clearly more heat at HI?",
        },
        {"id": "A5", "kind": "do", "say": "App: level 1 again, then do nothing.", "q": ""},
        {
            "id": "A5w",
            "kind": "wait",
            "min": 7,
            "say": "KEY (Q1): does the burner pause by itself while 'running' stays on?",
            "q": "Did the heater go quiet / blow cold while the app still showed Active?",
        },
        {
            "id": "R4",
            "kind": "r4",
            "value": 30,
            "say": "Runner writes runtime 30 ALONE while the heater runs (the rolling run-time test).",
            "q": "Did the heater keep burning with no stop and no new glow?",
        },
        {"id": "A6", "kind": "do", "say": "Close the app and switch the phone's Bluetooth OFF.", "q": ""},
        {
            "id": "A7",
            "kind": "wait",
            "min": 35,
            "say": "The heater must continue and stop by itself when 'left' reaches 0; watch the run-on.",
            "q": "Did it stop by itself, with ~1 min run-on?",
        },
        {
            "id": "A8",
            "kind": "do",
            "say": "Phone Bluetooth ON, open the app.",
            "q": "Does the app show 'Inactive' and the right values?",
        },
    ],
    "B": [
        {
            "id": "B1",
            "kind": "do",
            "say": "App: run time 20 min; start time = now + 5 min; Start timer.",
            "q": "Status 'Inactive • Timer: HH:MM'?",
        },
        {"id": "B2", "kind": "do", "say": "Close the app. Press Enter.", "q": ""},
        {
            "id": "B2w",
            "kind": "wait",
            "min": 8,
            "say": "Does the unit start the heater by itself at HH:MM? (Q5)",
            "q": "Did the heater start at the timer time?",
        },
        {"id": "B3", "kind": "wait", "min": 8, "say": "Let it burn.", "q": ""},
        {"id": "B4", "kind": "do", "say": "Open the app, Immediate heating OFF.", "q": ""},
        {
            "id": "B4w",
            "kind": "wait",
            "min": 3,
            "say": "Watch the shutdown and run-on.",
            "q": "Normal stop with run-on?",
        },
    ],
    "CD": [
        {
            "id": "C1",
            "kind": "do",
            "say": "Panel in the van: start CONTINUOUS heating (app open). Note what the panel shows (level or °C?).",
            "q": "Does the panel show a temperature in °C?",
        },
        {
            "id": "C1w",
            "kind": "wait",
            "min": 5,
            "say": "Watch permanent=True and the level the panel set.",
            "q": "",
        },
        {
            "id": "C2",
            "kind": "do",
            "say": "Panel: change the heat setting by a few steps.",
            "q": "Did the app's slider follow?",
        },
        {
            "id": "C3",
            "kind": "do",
            "say": "App: level 3 while continuous heating runs (the ESP's future action).",
            "q": "Did the panel show the new setting?",
        },
        {
            "id": "D2",
            "kind": "do",
            "say": "App: run time 10 min, start time = now + 5 min, Start timer → confirm 'Yes, start the timer'.",
            "q": "Status 'Active • Continuous heating • Timer starts in …'?",
        },
        {
            "id": "D3",
            "kind": "wait",
            "min": 8,
            "say": "Close the app. At the timer time: does continuous end and a 10-min run take over?",
            "q": "Did permanent go off and a timed run start without a new glow?",
        },
        {
            "id": "D4",
            "kind": "wait",
            "min": 13,
            "say": "Does the heater stop by itself after the 10 min? (device-side deadline)",
            "q": "Did it stop by itself?",
        },
    ],
}


SCALE = 1.0  # --scale: shortens every wait for a dry run against the mock


def wait_minutes(rec: Recorder, minutes: float) -> None:
    end = time.monotonic() + minutes * 60 * SCALE
    while time.monotonic() < end:
        left = int(end - time.monotonic())
        print("\r  %3d:%02d  %s   " % (left // 60, left % 60, status_line(rec.last)), end="", flush=True)
        time.sleep(1)
    print()


def lone_runtime_write(dae: Daemon, rec: Recorder, value: int, warning: str) -> dict:
    s = rec.last
    if not s.get("running"):
        print("  the heater is not running — skipping (the write only makes sense during a run)")
        return {"skipped": "not running"}
    print("  ⚠  %s\n     now: %s" % (warning, status_line(s)))
    if ask("     Type YES to send `set airheater runtime %d`: " % value) != "YES":
        return {"skipped": "no consent"}
    before = s.get("remaining")
    res = dae.post(
        "/api/command", {"function": "airheater", "what": "runtime", "value": value, "confirm": True}
    )
    print("  answer:", json.dumps(res))
    rec.write({"ev": "write", "what": "runtime", "value": value, "answer": res, "remaining_before": before})
    print("  watching 3 min for 'left' to jump to %d …" % value)
    wait_minutes(rec, 3)
    return {"answer": res, "remaining_before": before, "remaining_after": rec.last.get("remaining")}


def run_step(dae: Daemon, rec: Recorder, step: dict) -> None:
    print("\n== %s  %s" % (step["id"], step["say"]))
    rec.write({"ev": "mark", "step": step["id"], "phase": "begin"})
    out: dict = {}
    if step["kind"] == "do":
        ask("  do it now, then press Enter (right after the tap) … ")
        rec.write({"ev": "mark", "step": step["id"], "phase": "done"})
    elif step["kind"] == "wait":
        wait_minutes(rec, step["min"])
    elif step["kind"] == "r4":
        out = lone_runtime_write(
            dae, rec, step["value"], "One lone RunningTime write during a run (never sent by the app)."
        )
    if step.get("q") and not out.get("skipped"):
        ans = ask("  %s [y/n]: " % step["q"]).lower()
        out.update(answer=ans, note=ask("  note (optional): "))
    rec.write({"ev": "verdict", "step": step["id"], **out})


def preflight(dae: Daemon, run: str) -> None:
    print("== Preflight ==")
    try:
        st = dae.state()
    except Exception as e:
        sys.exit("  daemon /api/state unreachable at %s (%r) — is `serve` up?" % (dae.base, e))
    meta, heater = st.get("_meta") or {}, st.get("airheater") or {}
    if not heater.get("installed"):
        sys.exit("  airheater.installed is false — nothing to test.")
    print(
        "  online=%s session=%s read_only=%s"
        % (meta.get("online"), meta.get("session"), meta.get("read_only"))
    )
    print("  " + status_line(sample_of(st)))
    needs_write = any(s["kind"] == "r4" for s in RUNS[run])
    if needs_write and meta.get("read_only"):
        print(
            "  ⚠  daemon is READ-ONLY: step R4 will be refused. For R4 run\n"
            "     bash tools/roof_test_session.sh --test tools.van_heater_test -- --run %s" % run
        )
    print("  heater starts seen today: %d of %d" % (starts_today(), START_BUDGET_PER_DAY))
    print(
        "  checklist: owner at the van · exhaust free · fuel > 1/4 · phone HCI snoop FULL + adb ·\n"
        "             buspi CALICTL_BLE_TRACE on · ESP BLE mode off · thermometers placed · clocks synced"
    )
    if ask("  Type GO to start run %s: " % run) != "GO":
        sys.exit("  not started.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, choices=sorted(RUNS))
    ap.add_argument("--url", default="http://localhost:8088")
    ap.add_argument("--out", default=None, help="JSONL evidence file [~/heater-<run>-<time>.jsonl]")
    ap.add_argument("--every", type=float, default=2.0, help="sample period, s")
    ap.add_argument("--scale", type=float, default=1.0, help="multiply every wait (dry runs only)")
    a = ap.parse_args(argv)
    global SCALE
    SCALE = a.scale
    dae = Daemon(a.url)
    steps = RUNS[a.run]
    out = a.out or os.path.expanduser("~/heater-%s-%s.jsonl" % (a.run, time.strftime("%Y%m%d-%H%M%S")))
    preflight(dae, a.run)
    rec = Recorder(dae, out, a.every)
    rec.write({"ev": "session", "run": a.run, "steps": [s["id"] for s in steps], "url": a.url})
    signal.signal(
        signal.SIGINT,
        lambda *_: (
            rec.write({"ev": "aborted"}),
            sys.exit("\n  aborted — nothing was sent; stop the heater from the app or the panel."),
        ),
    )
    rec.start()
    time.sleep(a.every + 1)
    for step in steps:
        run_step(dae, rec, step)
    rec.write({"ev": "end"})
    rec.stop()
    print("\n  evidence: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
