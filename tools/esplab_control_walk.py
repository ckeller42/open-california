#!/usr/bin/env python3
"""Replay the app-recorded control actions against an ESP firmware and a fake unit.

For every ``app`` case of ``tests/vectors/control.json`` (one per app-recorded write the ESP
carries): put the fake unit in the state the app saw (the recorded state frames of the gating
functions, pushed as notifications; for lighting first the unit's own config frames that make the
firmware latch the case's wake-up / door-contact / favourite config), POST the action to the
firmware's ``/api/command`` (a wake-up carries the case's ``local_now``, the page's clock), and check
that the fake unit received exactly calictl's writes (``expect.frames``: the frame, the lighting
commit, a save's SET_COLOR preface; for a wake-up edit refused for want of its config, the
REQUEST_CONFIG pull the firmware sends before refusing) byte for byte, each delayed frame at least its ``delay_ms``
after the previous write — nothing else, never the roof's ``1401`` — and that the answer is
calictl's shape with ``applied`` never true. Used by ``tests/firmware/test_control_e2e.py`` (host
tier) and, as a CLI, on the CoreS3 bench:

    FAKE_UNIT_RECORD=~/unit.jsonl FAKE_UNIT_FIFO=~/unit.in python tools/applab/fake_unit_ble.py hci-socket:N &
    python tools/esplab_control_walk.py --url http://calictl-esp.local --fifo ~/unit.in --record ~/unit.jsonl

Restart the fake unit between walks (same keystore, FIFO and passkey; wait for the firmware to
reconnect by bond): the firmware's wake-up latch and the mock's stored wake-up survive a walk, so a
second walk against the same mock fails ``lighting-wakeup.jsonl:239`` (it expects no config known).
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
# Fields the unit's own clock moves every tick (the mock recomputes them from its RTC at once, so a
# pushed value never "holds"); no gate or builder reads them, so the walk does not wait for them.
CLOCK_FIELDS = frozenset(
    {"TimerCounterHour", "TimerCounterMin", "RunningTimeinAction", "AgeOneBattValuesMinutes"}
)


def _doc() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def app_cases() -> list[dict]:
    """The ``app`` cases of the control vectors (frames, or a wake-up edit refused after the config pull)."""
    return _doc()["app"]


def config_pull() -> dict:
    """``{"reason", "frames"}``: the REQUEST_CONFIG pull sent before a wake-up edit whose config is unknown."""
    return _doc()["config_pull"]


def expected_frames(case: dict) -> list[dict]:
    """The frame dicts calictl (and the ESP) write for ``case``: its frames, the pull for a refusal
    for want of the wake-up config, else none."""
    e = case["expect"]
    if e["kind"] == "frames":
        return e["frames"]
    pull = config_pull()
    return pull["frames"] if e["kind"] == "refused" and e["reason"] == pull["reason"] else []


def expected_writes(case: dict) -> list[tuple[str, str]]:
    """``[(char, hex)]`` written for ``case``, in order."""
    return [(f["char"], f["hex"]) for f in expected_frames(case)]


def latch_frames(case: dict) -> list[str]:
    """Hex 1502 frames that make a firmware latch the case's ``latch`` — the unit's own config frames
    (Mode 12 reply, Mode 16 / PN 8, Mode 20). The favourite bits default to all-stored (0x7f): the same
    answer as "unknown" for every gate and builder, and it overwrites bits a previous case left latched."""
    from calictl import overrides, protocol
    from tools.mock_unit import _pack_state

    funcs = protocol.load()
    overrides.apply(funcs)
    f, lat = funcs["lighting"], case.get("latch") or {}
    out = [_pack_state(f, {"Mode": 12, "ProfileNumber": 0, "LightValue": lat.get("FavouritesStored", 0x7F)})]
    if "DoorContact" in lat:
        out.append(_pack_state(f, {"Mode": 16, "ProfileNumber": 8, "LightValue": lat["DoorContact"]}))
    if "WakeupTimestamp" in lat:
        out.append(
            _pack_state(
                f,
                {
                    "Mode": 20,
                    "ProfileNumber": 14,
                    "Timestamp": lat["WakeupTimestamp"],
                    "LightValue": lat["WakeupLightValue"],
                },
            )
        )
    return [b.hex() for b in out]


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


def gate_state(case: dict) -> dict:
    """``{fn: decoded fields}`` of the case's recorded gate frames — what the firmware's ``/api/state``
    ``fn`` must hold before the command is sent (less :data:`CLOCK_FIELDS`)."""
    from calictl import overrides, protocol

    funcs = protocol.load()
    overrides.apply(funcs)
    out = {
        fn: {k: v for k, v in protocol.decode(funcs[fn], bytes.fromhex(hx)).items() if k not in CLOCK_FIELDS}
        for fn, hx in case["frames_hex"].items()
        if fn in GATE_FUNCTIONS
    }
    if case["function"] == "lighting":  # the latch keys the pushed config frames (latch_frames) set
        out.setdefault("lighting", {}).update({"FavouritesStored": 0x7F, **(case.get("latch") or {})})
    return out


def held_state(get_fn, want: dict, timeout: float = 15.0, every: float = 0.2) -> bool:
    """Poll ``get_fn()`` (the firmware's ``/api/state`` ``fn``) until it holds every field of ``want``
    (``{fn: {field: value}}``; fields not in ``want`` are not compared) on two polls in a row (a push still in flight from the previous command cannot land after the
    check); ``False`` if that does not happen within ``timeout`` seconds."""
    end, held = time.monotonic() + timeout, 0
    while True:
        fn = get_fn()
        ok = all(fn.get(f, {}).get(k) == v for f, fields in want.items() for k, v in fields.items())
        held = held + 1 if ok else 0
        if held == 2:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(every)


def walk(cases, inject, post, writes) -> list[str]:
    """Run ``cases`` in order. ``inject(case)`` puts the fake unit in the case's state and returns
    once the firmware holds it (``False``: it never did — reported, the case is skipped); ``post(body) -> (status, json)``; ``writes()`` -> the unit's control
    writes so far (:func:`unit_writes`). Returns one message per problem (empty = every case
    passed)."""
    problems = []
    for case in cases:
        if inject(case) is False:
            problems.append("%s: the firmware never held the pushed state" % case["id"])
            continue
        before = len(writes())
        body = {
            "function": case["function"],
            "what": case["what"],
            "value": case["value"],
            "confirm": True,
            **({"local_now": case["local_now"]} if "local_now" in case else {}),
        }
        status, ans = post(body)
        got = writes()[before:]
        want = expected_writes(case)
        exp = case["expect"]
        if exp["kind"] == "frames":
            ok = status == 200 and ans.get("ok") is True and ans.get("applied") is None
        else:  # refused: elsewhere (no write) or for want of the wake-up config (after the pull)
            ok = status == 200 and ans.get("refused") == exp["reason"] and ans.get("applied") is False
        if not ok:
            problems.append("%s: answer %s %s" % (case["id"], status, ans))
        if [(c, h) for c, h, _ in got] != want:
            problems.append(
                "%s: unit got %s, calictl sends %s" % (case["id"], [(c, h) for c, h, _ in got], want)
            )
        else:
            for i, f in enumerate(expected_frames(case)):
                gap_ms = (got[i][2] - got[i - 1][2]) * 1000 if i else None
                if i and f["delay_ms"] and gap_ms < f["delay_ms"]:
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


def _get_fn(url) -> dict:
    with urllib.request.urlopen(url.rstrip("/") + "/api/state", timeout=15) as r:
        return json.loads(r.read())["fn"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="replay the app-recorded control actions against an ESP",
        epilog="Restart the fake unit between walks (same keystore, FIFO, passkey; wait for the ESP's "
        "link): its stored wake-up and the ESP's latch survive a walk, so a second walk fails "
        "lighting-wakeup.jsonl:239.",
    )
    ap.add_argument("--url", required=True, help="the firmware, e.g. http://calictl-esp.local")
    ap.add_argument("--fifo", required=True, help="the fake unit's scenario FIFO (FAKE_UNIT_FIFO)")
    ap.add_argument("--record", required=True, help="the fake unit's FAKE_UNIT_RECORD file")
    ap.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for the pushed state")
    a = ap.parse_args(argv)

    def inject(case):
        with open(a.fifo, "w", encoding="utf-8") as f:
            if case["function"] == "lighting":
                for hx in latch_frames(case):
                    f.write("raw lighting %s\n" % hx)
            for fn, hx in case["frames_hex"].items():
                if fn in GATE_FUNCTIONS:
                    f.write("raw %s %s\n" % (fn, hx))
        return held_state(lambda: _get_fn(a.url), gate_state(case), a.timeout)

    cases = app_cases()
    problems = walk(cases, inject, _post(a.url), lambda: unit_writes(a.record))
    print(json.dumps({"cases": len(cases), "problems": problems}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.path.insert(0, str(VECTORS.parents[2]))  # run as a script: sys.path[0] is tools/, not the repo
    sys.exit(main())
