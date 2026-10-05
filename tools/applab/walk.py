#!/usr/bin/env python3
"""Record the real app walking a scripted scenario against the fake unit.

    ~/esp-venv/bin/python tools/applab/walk.py cooler airheater
    ~/esp-venv/bin/python tools/applab/walk.py --live --esp-fifo /tmp/esplab/fake_unit.in cooler

Per scenario: SIGTERM the lab's fake unit (pid file), start a fresh one on ``android-netsim`` with
``FAKE_UNIT_RECORD`` set to a scratch file, run the steps of
:data:`tools.applab.scenarios.SCENARIOS` (screenshot after each), SIGTERM the fake, and write
``tests/vectors/app/<scenario>.jsonl``: the header line, then the fake's events merged by time with
this runner's ``step`` events — and, with ``--live``, per step the app's visible texts
(``app_screen``) and the ESP satellite's ``/api/state`` + page texts (``esp_state``). A failed step
stops the run, leaves the fake running for inspection and names the last screenshot; nothing is
retried. Needs the emulator up (``labctl.sh up``), ``$LAB_DIR/env.sh`` sourced, and the app set up
for the VIN in ``FAKE_UNIT_VIN`` (read from the app's own data when unset; never printed).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.applab import adbui, pair_wizard  # noqa: E402
from tools.applab.scenarios import APP_ID, SCENARIOS, SCREEN, XY, XY_SCREEN, Step  # noqa: E402

STATE = Path(os.environ.get("TMPDIR", "/tmp")) / "applab"
AVD = "lab34"
SETTLE_S = 2.0  # after a tap / drag / console line: the write and the app's 500 ms flush land first
VERBS = frozenset({"ui", "fifo", "wait", "xy", "adb", "idle", "pair", "logwait"})
STATE_COMMANDS = ("set", "raw")  # mirrored to the ESP's fake under --live; link commands are not
TEST_IDENTITY = "C0:FF:EE:CA:11:F0"
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")


class StepFailed(RuntimeError):
    """A step could not be done; ``shot`` is the screenshot taken right after it."""

    def __init__(self, n: int, step: Step, why: str, shot: str):
        super().__init__("step %d %s%r: %s (last screenshot: %s)" % (n, step.verb, step.args, why, shot))
        self.n, self.step, self.why, self.shot = n, step, why, shot


def redact_texts(texts: list[str]) -> list[str]:
    """Screen texts with VIN-shaped tokens and every MAC but the fake's identity replaced."""
    out = []
    for t in texts:
        t = _VIN_RE.sub("<vin>", t)
        t = _MAC_RE.sub(lambda m: m.group(0) if m.group(0).upper() == TEST_IDENTITY else "<mac>", t)
        out.append(t)
    return out


class Lab:
    """The real side effects of a walk: adb, the console FIFOs, the fake's log, screenshots.

    :param fifos: the app fake's FIFO first, then (``--live``) the ESP fake's.
    :param fake_log: the app fake's log (``logwait`` and the passkey).
    :param shots: the screenshot directory (scratch; never the repo).
    """

    def __init__(self, fifos: list[str], fake_log: Path, shots: Path):
        self.fifos, self.fake_log = fifos, Path(fake_log)
        self.log_mark = self.fake_log.stat().st_size if self.fake_log.exists() else 0
        Path(shots).mkdir(parents=True, exist_ok=True)
        adbui.SHOTS = str(shots)

    def tap(self, rx: str) -> bool:
        return not adbui.tap(rx).startswith("NOT FOUND")

    def texts(self) -> list[str]:
        return adbui.texts(adbui.tree())

    def screen(self) -> tuple[str, int]:
        size = re.search(r"(\d+x\d+)", adbui.sh("shell", "wm", "size"))
        dens = re.search(r"(\d+)", adbui.sh("shell", "wm", "density"))
        return (size.group(1) if size else "?", int(dens.group(1)) if dens else 0)

    def fifo(self, cmd: str) -> str | None:
        if not cmd.strip():
            raise ValueError("empty fifo command")
        targets = self.fifos if cmd.split()[0] in STATE_COMMANDS else self.fifos[:1]
        for path in targets:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as e:
                return "FIFO %s: %s (is that fake unit running?)" % (path, e.strerror)
            try:
                os.write(fd, (cmd + "\n").encode())
            finally:
                os.close(fd)
        return None

    def xy(self, pt: tuple[int, ...]) -> None:
        a = [str(v) for v in pt]
        if len(pt) == 2:
            adbui.sh("shell", "input", "tap", *a)
        elif len(pt) == 3:  # a long press is a swipe that does not move; adb returns on release
            adbui.sh("shell", "input", "swipe", a[0], a[1], a[0], a[1], a[2])
        else:  # time wheels turn with draganddrop, not swipe (app-lab runbook)
            adbui.sh("shell", "input", "draganddrop", *a, "1500")

    def adb(self, *args: str) -> None:
        adbui.sh(*args)

    def pair(self) -> bool:
        pair_wizard.FAKE_LOG = str(self.fake_log)
        return bool(pair_wizard.main())

    def log_since_mark(self) -> bytes:
        with open(self.fake_log, "rb") as f:
            f.seek(self.log_mark)
            return f.read()

    def shot(self, name: str) -> str:
        return adbui.shot(name)

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, s: float) -> None:
        time.sleep(s)


class EspProbe:
    """The ESP satellite as ``--live`` sees it: ``/api/state`` over HTTP and its page in Chromium
    (phone viewport 420x900, like ``tools/esplab_ui_load.py``)."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self._pw = None
        self._page = None

    def state(self) -> dict:
        try:
            with urllib.request.urlopen(self.url + "/api/state", timeout=5) as r:
                return json.load(r)
        except (OSError, ValueError) as e:
            return {"error": str(e)}

    def texts(self, screen: str) -> list[str]:
        if self._page is None:
            from playwright.sync_api import sync_playwright  # bench dep; lazy

            self._pw = sync_playwright().start()
            browser = self._pw.chromium.launch()
            self._page = browser.new_page(viewport={"width": 420, "height": 900})
            self._page.goto(self.url + "/", wait_until="load", timeout=30000)
            self._page.wait_for_function("() => !!(STATE._meta && STATE._meta.satellite)", timeout=30000)
        self._page.evaluate("v => goto(v)", screen)
        self._page.wait_for_timeout(1500)  # one /api/state poll
        return [line.strip() for line in self._page.inner_text("body").splitlines() if line.strip()]

    def close(self) -> None:
        if self._pw is not None:
            self._pw.stop()


def _poll(lab, cond, timeout_s: float, what: str) -> str | None:
    end = lab.now() + timeout_s
    while True:
        if cond():
            return None
        if lab.now() >= end:
            return "timeout after %g s waiting for %s" % (timeout_s, what)
        lab.sleep(1.0)


def dispatch(step: Step, lab) -> str | None:
    """Run one step.

    :returns: ``None`` on success, else why it failed.
    """
    v, a = step.verb, step.args
    if v == "ui":
        return None if lab.tap(a[0]) else "no node matches %r" % a[0]
    if v == "fifo":
        return lab.fifo(a[0])
    if v == "wait":
        return _poll(
            lab, lambda: any(re.search(a[0], t, re.I) for t in lab.texts()), a[1], "%r on screen" % a[0]
        )
    if v == "logwait":

        def seen() -> bool:
            m = re.search(a[0].encode(), lab.log_since_mark())
            if m:
                lab.log_mark += m.end()
            return bool(m)

        return _poll(lab, seen, a[1], "%r in the fake's log" % a[0])
    if v == "xy":
        pt = XY.get(a[0])
        if pt is None:
            return "XY point %r is not measured on %s yet (tools/applab/README.md: Measuring XY points)" % (
                a[0],
                XY_SCREEN,
            )
        lab.xy(pt)
        return None
    if v == "adb":
        lab.adb(*a)
        return None
    if v == "idle":
        lab.sleep(a[0])
        return None
    if v == "pair":
        return (
            None
            if lab.pair()
            else "pairing did not complete (no passkey prompt, field or OK — see pair_wizard output)"
        )
    return "unknown verb %r" % v


def live_events(n: int, lab, esp, screen: str, clock) -> list[dict]:
    """The ``app_screen`` + ``esp_state`` pair recorded after step ``n`` under ``--live``."""
    app = {"t": clock(), "ev": "app_screen", "step": n, "texts": redact_texts(lab.texts())}
    st = esp.state()
    dev = st.get("device") or {}
    esp_ev = {
        "t": clock(),
        "ev": "esp_state",
        "step": n,
        "screen": screen,
        "fn": st.get("fn"),
        "link": dev.get("link"),
        "pairing": (dev.get("pairing") or {}).get("state"),
        "error": st.get("error"),
        "texts": redact_texts(esp.texts(screen)),
    }
    return [app, esp_ev]


def run_steps(name: str, steps: list[Step], lab, *, esp=None, clock=time.time) -> list[dict]:
    """Run a scenario's steps.

    :returns: the runner's events (``step``, plus ``app_screen``/``esp_state`` with ``esp``).
    :raises StepFailed: at the first step that fails (step 0 = the screen does not match :data:`XY_SCREEN`).
    """
    if any(s.verb == "xy" for s in steps):
        got = lab.screen()
        if tuple(got) != tuple(XY_SCREEN):
            raise StepFailed(
                0,
                Step("xy", ()),
                "screen is %s@%ddpi but the XY points were measured on %s@%ddpi: re-measure them"
                % (got[0], got[1], XY_SCREEN[0], XY_SCREEN[1]),
                lab.shot("%s-00-screen" % name),
            )
    events: list[dict] = []
    for n, step in enumerate(steps, 1):
        ev = {"t": clock(), "ev": "step", "n": n, "verb": step.verb, "arg": list(step.args)}
        if step.expect:
            ev["expect"] = list(step.expect)
        events.append(ev)
        why = dispatch(step, lab)
        if why is None and step.verb in ("ui", "xy", "fifo"):
            lab.sleep(SETTLE_S)
        shot = lab.shot("%s-%02d-%s" % (name, n, step.verb))
        if why is not None:
            raise StepFailed(n, step, why, shot)
        if esp is not None:
            events.extend(live_events(n, lab, esp, SCREEN.get(name, "home"), clock))
    return events


def recording_header(app_ver: str, scenario: str, today: datetime.date | None = None) -> dict:
    """The recording's first line (``tools.capture_diff.HEADER_KEYS``)."""
    return {
        "recorded_by": "app %s" % app_ver,
        "date": (today or datetime.date.today()).isoformat(),
        "avd": AVD,
        "scenario": scenario,
    }


def write_recording(out: Path, header: dict, fake_events: list[dict], walk_events: list[dict]) -> None:
    """Header first, then both event streams merged by ``t`` (stable: the fake's first on a tie)."""
    merged = sorted(fake_events + walk_events, key=lambda e: e["t"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for rec in [header, *merged]:
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")


# --- the fake unit process --------------------------------------------------------------------


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop_pid(pid: int, timeout_s: float = 10.0) -> None:
    """SIGTERM (never SIGKILL: a hard kill leaves a ghost radio in netsim) and wait."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    end = time.monotonic() + timeout_s
    while _alive(pid) and time.monotonic() < end:
        time.sleep(0.2)
    if _alive(pid):
        raise SystemExit(
            "fake unit pid %d ignored SIGTERM for %g s; stop it by hand (never kill -9)" % (pid, timeout_s)
        )


def app_vin() -> str:
    """The VIN the app was set up with: ``FAKE_UNIT_VIN``, else read from the app's data (adb root).
    Never printed."""
    vin = os.environ.get("FAKE_UNIT_VIN")
    if vin:
        return vin
    adbui.sh("root")
    time.sleep(2)
    out = adbui.sh(
        "shell", "grep -rhoaE 'WV2[A-HJ-NPR-Z0-9]{14}' /data/data/%s/ 2>/dev/null | head -1" % APP_ID
    ).strip()
    if not out:
        raise SystemExit("no VIN: set FAKE_UNIT_VIN or set the app up first (tools/applab/README.md)")
    return out


def app_version() -> str:
    m = re.search(r"versionName=(\S+)", adbui.sh("shell", "dumpsys", "package", APP_ID))
    return m.group(1) if m else "unknown"


def start_fake(raw: Path, vin: str) -> subprocess.Popen:
    """A fresh fake unit on netsim recording into ``raw``; replaces the lab's fake (pid file)."""
    STATE.mkdir(parents=True, exist_ok=True)
    pidfile = STATE / "fake_unit.pid"
    if pidfile.exists():
        try:
            old = int(pidfile.read_text().strip())
        except ValueError:
            old = 0
        if old and _alive(old):
            print("walk: stopping the lab's fake unit (pid %d) with SIGTERM" % old, flush=True)
            _stop_pid(old)
    raw.unlink(missing_ok=True)
    log = STATE / "fake_unit.log"
    mark = log.stat().st_size if log.exists() else 0
    env = {
        **os.environ,
        "FAKE_UNIT_RECORD": str(raw),
        "FAKE_UNIT_FIFO": str(STATE / "fake_unit.in"),
        "FAKE_UNIT_VIN": vin,
    }
    with open(log, "ab") as out:
        proc = subprocess.Popen(
            [os.environ.get("BUMBLE_PY", sys.executable), "tools/applab/fake_unit_ble.py", "android-netsim"],
            cwd=REPO,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pidfile.write_text("%d\n" % proc.pid)
    for _ in range(60):
        time.sleep(0.5)
        with open(log, "rb") as f:
            f.seek(mark)
            if b"advertising from" in f.read():
                return proc
        if proc.poll() is not None:
            raise SystemExit("fake unit exited (%s); see %s" % (proc.returncode, log))
    raise SystemExit("fake unit not advertising after 30 s; see %s (left running, pid %d)" % (log, proc.pid))


def _stop_child(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        raise SystemExit(
            "fake unit pid %d ignored SIGTERM; stop it by hand (never kill -9)" % proc.pid
        ) from None
    (STATE / "fake_unit.pid").unlink(missing_ok=True)


def record(
    name: str,
    out_dir: Path,
    shots: Path,
    *,
    live: bool = False,
    esp_fifo: str | None = None,
    esp_url: str = "http://calictl-esp.local",
) -> int:
    """Record one scenario into ``out_dir/<name>.jsonl``.

    :returns: 0 when written, 1 when a step failed (the fake is left running).
    """
    from calictl.trace import read_events

    raw = STATE / ("%s.raw.jsonl" % name)
    proc = start_fake(raw, app_vin())
    esp = EspProbe(esp_url) if live else None
    notes: list[dict] = []
    if esp is not None:
        try:
            err = esp.state().get("error")
        except Exception as e:  # noqa: BLE001 - any probe failure means "unreachable"
            err = str(e)
        if err:
            msg = "live: ESP unreachable at %s — skipped (%s)" % (esp_url, err)
            print("walk %s: %s" % (name, msg), flush=True)
            notes.append({"t": time.time(), "ev": "note", "text": msg})
            esp.close()
            esp, live = None, False
    fifos = [str(STATE / "fake_unit.in")] + ([esp_fifo] if live and esp_fifo else [])
    lab = Lab(fifos, STATE / "fake_unit.log", shots)
    try:
        events = run_steps(name, SCENARIOS[name], lab, esp=esp)
    except StepFailed as e:
        print("walk %s: FAILED %s" % (name, e), flush=True)
        print(
            "walk %s: fake unit left running for inspection (pid %d); labctl.sh fake|down stops it"
            % (name, proc.pid),
            flush=True,
        )
        return 1
    finally:
        if esp is not None:
            esp.close()
    _stop_child(proc)
    out = out_dir / ("%s.jsonl" % name)
    write_recording(out, recording_header(app_version(), name), list(read_events(raw)), notes + events)
    print("walk %s: wrote %s" % (name, out), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="walk", description="record the real app walking a scenario")
    ap.add_argument("scenario", nargs="+", choices=sorted(SCENARIOS))
    ap.add_argument(
        "--live", action="store_true", help="also record the app's and the ESP satellite's screens per step"
    )
    ap.add_argument("--esp-fifo", help="the ESP-side fake unit's console FIFO (required with --live)")
    # Task 6 passes the bench route (the ESP is reached via the wlx stick, not mDNS on the wired path)
    ap.add_argument("--esp-url", default="http://calictl-esp.local")
    ap.add_argument("--out", type=Path, default=REPO / "tests" / "vectors" / "app")
    ap.add_argument("--shots", type=Path, default=STATE / "shots")
    a = ap.parse_args(argv)
    if a.live and not a.esp_fifo:
        ap.error("--live needs --esp-fifo")
    sdk = os.environ.get("ANDROID_SDK_ROOT")
    if sdk:  # pair_wizard calls plain `adb`
        os.environ["PATH"] = "%s/platform-tools:%s" % (sdk, os.environ.get("PATH", ""))
    for name in a.scenario:
        rc = record(name, a.out, a.shots, live=a.live, esp_fifo=a.esp_fifo, esp_url=a.esp_url)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
