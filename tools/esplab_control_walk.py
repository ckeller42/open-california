#!/usr/bin/env python3
"""Replay the app-recorded control actions against an ESP firmware and a fake unit.

For every ``app`` case of ``tests/vectors/control.json`` (one per app-recorded write the ESP
carries): put the fake unit in the state the app saw (the recorded state frames of the gating
functions, pushed as notifications), POST the action to the firmware's ``/api/command``, and check
that the fake unit received exactly calictl's writes (``expect.frames``: the frame, the lighting
commit, a save's SET_COLOR preface) byte for byte, each delayed frame at least its ``delay_ms``
after the previous write — nothing else, never the roof's ``1401`` — and that the answer is
calictl's shape with ``applied`` never true. Used by ``tests/firmware/test_control_e2e.py`` (host
tier) and, as a CLI, on the CoreS3 bench:

    FAKE_UNIT_RECORD=~/unit.jsonl FAKE_UNIT_FIFO=~/unit.in python tools/applab/fake_unit_ble.py hci-socket:N &
    python tools/esplab_control_walk.py --url http://calictl-esp.local --fifo ~/unit.in --record ~/unit.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VECTORS = Path(__file__).resolve().parent.parent / "tests" / "vectors" / "control.json"
# The functions whose state the gates read (control.command_precondition on the ESP's five + roof).
GATE_FUNCTIONS = ("airheater", "campingmode", "cooler", "energy", "lighting", "roof")
SKIP_CHARS = ("1003", "f000")


def app_cases() -> list[dict]:
    """The ``app`` cases of the control vectors (31: 27 frames + 4 wake-up "elsewhere")."""
    return json.loads(VECTORS.read_text(encoding="utf-8"))["app"]


def expected_writes(case: dict) -> list[tuple[str, str]]:
    """``[(char, hex)]`` calictl writes for ``case``, in order (empty for a refusal)."""
    e = case["expect"]
    return [(f["char"], f["hex"]) for f in e["frames"]] if e["kind"] == "frames" else []


def unit_writes(path) -> list[tuple[str, str, float]]:
    """``[(char, hex, t)]``: the control writes the fake unit recorded (``FAKE_UNIT_RECORD``),
    ``1003`` heartbeats and ``f000`` excluded; ``t`` is the unit's receive time (epoch s)."""
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("ev") == "write" and ev.get("char") not in SKIP_CHARS:
            out.append((ev["char"], ev["hex"], ev["t"]))
    return out


def walk(cases, inject, post, writes) -> list[str]:
    """Run ``cases`` in order. ``inject(case)`` puts the fake unit in the case's state and returns
    once the firmware holds it; ``post(body) -> (status, json)``; ``writes()`` -> the unit's control
    writes so far (:func:`unit_writes`). Returns one message per problem (empty = every case
    passed)."""
    problems = []
    for case in cases:
        inject(case)
        before = len(writes())
        body = {"function": case["function"], "what": case["what"], "value": case["value"], "confirm": True}
        status, ans = post(body)
        got = writes()[before:]
        want = expected_writes(case)
        exp = case["expect"]
        if exp["kind"] == "frames":
            ok = status == 200 and ans.get("ok") is True and ans.get("applied") is None
        else:  # elsewhere: refused without a write
            ok = status == 200 and ans.get("refused") == exp["reason"] and ans.get("applied") is False
        if not ok:
            problems.append("%s: answer %s %s" % (case["id"], status, ans))
        if [(c, h) for c, h, _ in got] != want:
            problems.append(
                "%s: unit got %s, calictl sends %s" % (case["id"], [(c, h) for c, h, _ in got], want)
            )
        else:
            for i, f in enumerate(exp.get("frames", [])):
                gap_ms = (got[i][2] - got[i - 1][2]) * 1000 if i else None
                if f["delay_ms"] and gap_ms < f["delay_ms"]:
                    problems.append(
                        "%s: frame %d came %.0f ms after the previous write, calictl waits %d"
                        % (case["id"], i, gap_ms, f["delay_ms"])
                    )
        if any(c == "1401" for c, _, _ in got):
            problems.append("%s: a ROOF write reached the unit" % case["id"])
    return problems


def _post(url):
    def post(body):
        req = urllib.request.Request(
            url.rstrip("/") + "/api/command",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    return post


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="replay the app-recorded control actions against an ESP")
    ap.add_argument("--url", required=True, help="the firmware, e.g. http://calictl-esp.local")
    ap.add_argument("--fifo", required=True, help="the fake unit's scenario FIFO (FAKE_UNIT_FIFO)")
    ap.add_argument("--record", required=True, help="the fake unit's FAKE_UNIT_RECORD file")
    ap.add_argument("--settle", type=float, default=2.0, help="seconds after a state push")
    a = ap.parse_args(argv)

    def inject(case):
        with open(a.fifo, "w", encoding="utf-8") as f:
            for fn, hx in case["frames_hex"].items():
                if fn in GATE_FUNCTIONS:
                    f.write("raw %s %s\n" % (fn, hx))
        time.sleep(a.settle)

    cases = app_cases()
    problems = walk(cases, inject, _post(a.url), lambda: unit_writes(a.record))
    print(json.dumps({"cases": len(cases), "problems": problems}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
