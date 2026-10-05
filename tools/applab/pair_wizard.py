"""Drive the app's *Set up remote control* wizard to the pairing dialog and answer the passkey.

The pairing flow is several sheets deep and Android surfaces the passkey prompt as a
notification with a ~30 s SMP window, which is fiddly to do by hand. This walks whatever sheet is
open — ticks any confirmation checkbox, taps the primary button (*Set up Remote Control* /
*Continue with Remote Control* / *Next* / *Connect now*) — then waits for the "Pairing request"
notification, opens it, types the passkey and taps OK. It prints each step. It returns (exits) true/0 only when the passkey was typed and OK tapped.

Prereqs: the emulator is up with the app open (`labctl.sh up`), and the fake unit is advertising.
Only needed for a *fresh* pair; a persisted bond reconnects on its own (see the README).

    python3 tools/applab/pair_wizard.py                            # code read from the fake's log
    FAKE_UNIT_PASSKEY=123456 python3 tools/applab/pair_wizard.py   # fake started with the same pin

Env: FAKE_UNIT_PASSKEY (the code to type; the fake pins the same value only if it was STARTED with
it — unset, the fake shows a fresh random code per attempt, so this reads the last
``### PASSKEY nnnnnn`` line from the log instead), FAKE_UNIT_LOG (the fake unit's log, read for
that code and tailed for the result; default $TMPDIR/applab/fake_unit.log, matching labctl.sh).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ADBUI = [sys.executable, os.path.join(HERE, "adbui.py")]
PASSKEY = os.environ.get("FAKE_UNIT_PASSKEY")  # unset: read the fake's fresh code from its log
FAKE_LOG = os.environ.get(
    "FAKE_UNIT_LOG", os.path.join(os.environ.get("TMPDIR", "/tmp"), "applab", "fake_unit.log")
)
PRIMARY = ("Connect now", "Continue with Remote Control", "Set up Remote Control", "Next")


def sh(*a: str) -> str:
    return subprocess.run(list(a), capture_output=True, text=True).stdout


def tree() -> str:
    return sh(*ADBUI, "tree")


def bounds(line: str):
    m = re.search(r"\[(\d+), (\d+), (\d+), (\d+)\]", line)
    return tuple(int(x) for x in m.groups()) if m else None


def tap_bounds(b) -> None:
    sh("adb", "shell", "input", "tap", str((b[0] + b[2]) // 2), str((b[1] + b[3]) // 2))


def logged_passkey() -> str | None:
    """The last ``### PASSKEY nnnnnn`` the fake printed to FAKE_LOG, or None."""
    try:
        codes = re.findall(r"### PASSKEY (\d{6})", open(FAKE_LOG).read())
    except OSError:
        return None
    return codes[-1] if codes else None


def notif_has(text: str) -> bool:
    return text in sh("adb", "shell", "dumpsys", "notification", "--noredact")


def main() -> bool:
    for k in range(10):
        t = tree()
        for line in t.splitlines():  # tick any confirmation checkbox first
            if "CheckBox" in line and "chk=false" in line and bounds(line):
                tap_bounds(bounds(line))
                time.sleep(1)
                t = tree()
        btn = next((c for c in PRIMARY if re.search(r"'%s'" % re.escape(c), t)), None)
        title = re.findall(r"'([^']{6,70})'\s+TextView", t)
        print("[%d] %s -> %s" % (k, title[:2], btn), flush=True)
        if not btn:
            break
        sh(*ADBUI, "tap", "^%s$" % re.escape(btn))
        time.sleep(3)
        if btn == "Connect now":
            break

    for i in range(35):
        time.sleep(1)
        if notif_has("Pairing request"):
            print("pairing request after %ds" % (i + 1), flush=True)
            break
    else:
        print("no pairing request appeared", flush=True)
        return False

    sh("adb", "shell", "cmd", "statusbar", "expand-notifications")
    time.sleep(1.5)
    sh(*ADBUI, "tap", "Pairing request")
    time.sleep(2)
    t = tree()
    ed = next((bounds(l) for l in t.splitlines() if "EditText" in l), None)
    if not ed:
        print("no passkey field found", flush=True)
        return False
    passkey = PASSKEY or logged_passkey()
    if not passkey:
        print("no passkey: set FAKE_UNIT_PASSKEY or FAKE_UNIT_LOG (none in %s)" % FAKE_LOG, flush=True)
        return False
    tap_bounds(ed)
    time.sleep(0.5)
    sh("adb", "shell", "input", "text", passkey)
    time.sleep(0.7)
    ok = next((bounds(l) for l in tree().splitlines() if re.search(r"'OK'", l)), None)
    print("OK button:", ok, flush=True)
    if ok:
        tap_bounds(ok)
    time.sleep(10)
    sh("adb", "shell", "cmd", "statusbar", "collapse")
    try:
        tail = open(FAKE_LOG).read().splitlines()[-8:]
        print("== fake unit tail:")
        for line in tail:
            print("  " + line[:120])
    except OSError:
        pass
    return ok is not None


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
