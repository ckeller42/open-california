#!/usr/bin/env python3
"""Generate the ESP control golden vectors (``tests/vectors/control.json``) from calictl.control.

The language-neutral specification of the ESP32's C control twin
(``firmware/components/cali_core/control.c``): for every case, what calictl answers —

- ``elsewhere``: a command the ESP does not carry (``gen_c_dict.ESP_CONTROL_FUNCTIONS`` lists the
  five it does; ``lighting wakeup`` is excluded: it needs the unit-reported wake-up config and a
  local wall clock the ESP has neither of — plan decision 3);
- ``refused``: ``control.command_precondition``'s text (checked FIRST, as serve does);
- ``bad``: ``control.build`` raised ``CommandError`` (or ``TypeError``: calictl answers that one with
  a 500, the ESP with a 400 — both refuse; plan decision 6);
- ``none``: ``control.build`` returned ``None``;
- ``frames``: the writes in ``calictl.device.actuate``'s order — ``preface_for`` (if any), then the
  frame, each followed by ``commit_for`` (lighting's flush) ``FOLLOW_MS`` after the previous ACK.

Inputs: a value grid per (function, what) over named state variants (each gating field both ways),
and every app-recorded action in ``tests/vectors/app/*.jsonl`` (``tools.capture_diff``), with the
raw state frames the app saw. Values are JSON strings, integers or null — never booleans (the ESP
refuses them, plan decision 5). Deterministic; regenerate after any calictl.control change:

    python3 -m tools.gen_control_vectors          # rewrite
    python3 -m tools.gen_control_vectors --check  # exit 1 if stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from calictl import control, overrides, protocol
from tools import capture_diff
from tools.gen_c_dict import ESP_CONTROL_FUNCTIONS, ESP_ELSEWHERE_REASON, _env_default
from tools.mock_unit import DEFAULT_SEED, _pack_state

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "vectors" / "control.json"
APP_DIR = ROOT / "tests" / "vectors" / "app"
FOLLOW_MS = round(_env_default("CALICTL_FOLLOW_DELAY_S") * 1000)
ESP_ELSEWHERE = frozenset({("lighting", "wakeup")})

_ONOFF = ["on", "off", " ON ", 1, 0, None, "x"]
GRID: dict[str, dict[str, list]] = {
    "cooler": {
        "power": _ONOFF,
        # int() edge cases: `_` only between digits, one sign, "-0" is 0 (out of range here)
        "level": [
            1,
            5,
            0,
            6,
            "3",
            " 4 ",
            "+2",
            "5_0",
            "0_5",
            "_5",
            "5_",
            "1__0",
            "-0",
            "+-3",
            "0x3",
            "abc",
            None,
            10**30,
            -1,
        ],
        "mode": ["normal", "quiet", "timer_quiet", "Quiet ", "bogus", None],
        # the last one is >= CALI_CTL_VALUE_MAX (64) chars: the C twin must refuse, never truncate
        "timer_set": [
            "07:30",
            "7:5",
            "24:00",
            "07:60",
            "0730",
            " 06 : 15 ",
            "1:2:3",
            "07:",
            None,
            "5:30" + " " * 60 + ":7",
        ],
        "timer_start": [None],
        "timer_cancel": [None],
        "night_on": [0, 23, 24, -1, "22"],
        "night_off": [7, "x"],
        "bogus": [1],
    },
    "campingmode": {
        **{w: ["on", "off", 1, 0, None, "maybe"] for w in ("lights", "usb", "master")},
        "bogus": ["on"],
    },
    "energy": {"mode": ["normal", "max_charge", "eco", "ECO", " eco ", "turbo", None], "bogus": ["eco"]},
    "airheater": {
        "power": ["on", "off"],
        "level": [1, 10, 0, 11, "5"],
        "runtime": [0, 120, 121, "60", None],
        "timer": ["06:45", "25:00", None],
        "timer_start": [None],
        "timer_cancel": [None],
        "permanent": ["off", "on", "OFF", "0", "false", None],
        "bogus": [1],
    },
    "lighting": {
        "power": ["on", "off"],
        "all": [0, 10, 11, 12, -1, "5", None],
        "profile": [0, 1, 3, 7, 8, 13, 14, "3", None],
        "save_profile": [
            "1",
            "7",
            "3 amber",
            "3 Dark_Blue",
            "3 purple",
            "0",
            "8",
            "1 2 3",
            "",
            None,
            5,
            "3 red" + " " * 62 + " extra",
        ],
        "door_contact": ["on", "off", "1", "0", "true", "false", "maybe", None],
        "color": ["red"],
        "nonexistent": [3],
        "BrightnessLNine": [3],
        "BrightnessLOneSix": [3],
        "roof-reading": [0, 5, "x"],
        "wakeup": ["07:00 on", "off"],
        **{z: [0, 5, 11, 12] for z in control.LIGHT_ZONES if z != "roof-reading"},
    },
    "roof": {"open": [None], "close": [None], "stop": [None]},
    "stairs": {"move": ["extend"]},
    "roofaircondition": {"power": ["on"]},
    "livingroomheater": {"air": ["on"]},
}
# State variants per function (names into _states()); every gating field both ways.
VARIANTS = {
    "cooler": ["seed", "empty", "fridge_on", "fridge_off", "cooler_schedule"],
    "campingmode": ["seed", "empty", "camping_off", "camping_on"],
    "energy": ["seed", "empty", "energy_locked"],
    "airheater": ["seed", "empty"],
    "lighting": [
        "seed",
        "empty",
        "roof_closed",
        "roof_closed_14",
        "roof_open",
        "fav_3_empty",
        "fav_request_echo",
        "lighting_levels",
    ],
}


def _funcs():
    f = protocol.load()
    overrides.apply(f)
    return f


def _states(funcs) -> dict[str, dict]:
    seed = {
        fn: protocol.decode(funcs[fn], _pack_state(funcs[fn], vals))
        for fn, vals in DEFAULT_SEED.items()
        if funcs[fn].state_char
    }

    def v(**over):
        s = json.loads(json.dumps(seed))
        for fn, fields in over.items():
            s[fn] = {**s.get(fn, {}), **fields}
        return s

    return {
        "seed": seed,
        "empty": {},
        "fridge_on": v(cooler={"State": 1}),
        "fridge_off": v(cooler={"State": 0}),
        "cooler_schedule": v(
            cooler={
                "State": 1,
                "Mode": 4,
                "Level": 2,
                "NightTimerSet": 1,
                "NightTimerHourOn": 22,
                "NightTimerHourOff": 7,
                "TimerHourSet": 6,
                "TimerMinSet": 30,
            }
        ),
        "camping_off": v(campingmode={"State": 0}),
        "camping_on": v(campingmode={"State": 1}),
        "energy_locked": v(energy={"EnergyModeNotSelectable": 1}),
        "roof_closed": v(roof={"Position": 0}),
        "roof_closed_14": v(roof={"Position": 14}),
        "roof_open": v(roof={"Position": 1}),
        "fav_3_empty": v(lighting={"Mode": 12, "ProfileNumber": 0, "LightValue": 0b1111011}),
        "fav_request_echo": v(lighting={"Mode": 12, "ProfileNumber": 13, "LightValue": 0}),
        "lighting_levels": v(
            lighting={
                "BrightnessLOne": 5,
                "BrightnessLNine": 11,
                "BrightnessLOneZero": 7,
                "BrightnessLTwo": 13,
            }
        ),
    }


def expect(funcs, fn: str, what: str, value, states: dict) -> dict:
    """calictl's answer to ``set fn what value`` over decoded ``states`` (see the module docstring).

    :param funcs: the loaded + overridden function table.
    :param fn: the target function.
    :param what: the control key.
    :param value: a JSON string, integer or ``None`` (never a bool).
    :param states: function -> decoded state.
    :returns: the ``expect`` object of one vector.
    """
    assert not isinstance(value, bool), "booleans are refused by the ESP (plan decision 5)"
    if fn not in ESP_CONTROL_FUNCTIONS or (fn, what) in ESP_ELSEWHERE:
        return {"kind": "elsewhere", "reason": ESP_ELSEWHERE_REASON}
    reason = control.command_precondition(fn, what, value, states)
    if reason:
        return {"kind": "refused", "reason": reason}
    last = states.get(fn, {})
    try:
        frame = control.build(funcs, fn, what, value, last)
    except (control.CommandError, TypeError):
        return {"kind": "bad"}
    if frame is None:
        return {"kind": "none"}
    char = str(funcs[fn].control_char)[4:8]
    commit = control.commit_for(fn)
    pre = control.preface_for(funcs, fn, what, value, last)
    frames = []
    for f in ([pre] if pre is not None else []) + [frame]:  # device.actuate's write loop
        frames.append({"char": char, "delay_ms": 0, "hex": f.hex()})
        if commit is not None:
            frames.append({"char": char, "delay_ms": FOLLOW_MS, "hex": commit.hex()})
    return {"kind": "frames", "frames": frames}


def _cases(funcs, states):
    out = []
    for fn, whats in GRID.items():
        for what, values in whats.items():
            for value in values:
                for sname in VARIANTS.get(fn, ["seed"]):
                    out.append(
                        {
                            "id": "%s/%s/%s@%s" % (fn, what, json.dumps(value), sname),
                            "function": fn,
                            "what": what,
                            "value": value,
                            "state": sname,
                            "expect": expect(funcs, fn, what, value, states[sname]),
                        }
                    )
    return out


def _app(funcs):
    out = []
    for path in sorted(APP_DIR.glob("*.jsonl")):
        for w in capture_diff.check_recording(path, funcs=funcs):
            if w.kind != "action" or w.fn not in ESP_CONTROL_FUNCTIONS:
                continue
            fn, what, value = w.expect
            states = {f: protocol.decode(funcs[f], bytes.fromhex(h)) for f, h in w.frames.items()}
            exp = expect(funcs, fn, what, value, states)
            if exp["kind"] not in ("frames", "elsewhere"):
                raise SystemExit(
                    "%s:%d: calictl %s the recorded %s/%s=%r" % (path.name, w.line, exp, fn, what, value)
                )
            out.append(
                {
                    "id": "%s:%d" % (path.name, w.line),
                    "function": fn,
                    "what": what,
                    "value": value,
                    "frames_hex": dict(sorted(w.frames.items())),
                    "app_hex": w.hex,
                    "expect": exp,
                }
            )
    return out


def render() -> str:
    """The full vector document (deterministic JSON text)."""
    funcs = _funcs()
    states = _states(funcs)
    doc = {
        "follow_delay_ms": FOLLOW_MS,
        "elsewhere_reason": ESP_ELSEWHERE_REASON,
        "states": states,
        "cases": _cases(funcs, states),
        "app": _app(funcs),
    }
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print("STALE: %s — run python3 -m tools.gen_control_vectors" % OUT, file=sys.stderr)
            return 1
        print("fresh: %s" % OUT)
        return 0
    OUT.write_text(text, encoding="utf-8")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
