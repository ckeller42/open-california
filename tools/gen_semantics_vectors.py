#!/usr/bin/env python3
"""Generate ``tests/vectors/semantics.json`` — golden vectors pinning ``calictl/webui/semantics.js``
(the browser twin the ESP32 satellite UI runs) to :mod:`calictl.semantics` (the authority).

Inputs: the mock unit's seed (:data:`tools.mock_unit.DEFAULT_SEED`, packed and decoded like a real
read), every decode vector of ``tests/vectors/camper_codec.json`` (one-hot, mixed and TRUNCATED
frames: a field past the end is absent), an empty frame per function, and hand cases for the
interpretation edges (starter current ``0x81``, the 511/254 not-fitted sentinels, SoC 11-15, RTC
day 0, ASCII versions incl. non-printable and >= 0x80 bytes, every InfoPopUp/ErrorCode value, the
camping lights/USB polarity matrix). Outputs: :func:`calictl.semantics.interpret` per case; for
whole states also :func:`calictl.semantics.apply_sw_corrections`, :func:`calictl.anchors.check`
and ``ServeBackend._firmware_meta``; and Python ``round()`` over every x0.1 / /100 value in range
plus exact .x5 ties (``pyRound`` must reproduce round-half-even).

Only :data:`UI_FUNCTIONS` (what the web UI renders) are twinned; semantics.js shows the rest
through its generic fallback. Deterministic. Regenerate after any semantics/anchors change:

    python3 -m tools.gen_semantics_vectors           # rewrite
    python3 -m tools.gen_semantics_vectors --check   # exit 1 if stale
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

from calictl import anchors, overrides, protocol, semantics
from calictl.serve import ServeBackend

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "vectors" / "semantics.json"
CODEC_VECTORS = ROOT / "tests" / "vectors" / "camper_codec.json"
# The functions the calictl web UI renders (app.js ORDER, plus general via _meta.firmware).
UI_FUNCTIONS = (
    "airheater",
    "campingmode",
    "cooler",
    "energy",
    "general",
    "lighting",
    "roof",
    "vehicle",
    "water",
)
_ZONES = (
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "OneZero",
    "OneOne",
    "OneTwo",
    "OneThree",
    "OneFour",
    "OneFive",
    "OneSix",
)


def _ascii(s: str) -> int:
    """4 chars -> the big-endian u32 the unit packs AmbSwVersion/CmSwVersion into."""
    return int.from_bytes(s.encode("latin-1"), "big")


def decoded_seed() -> dict:
    """The mock unit's seed, packed into frames and decoded like a real read."""
    from tools.mock_unit import DEFAULT_SEED, _pack_state

    funcs = protocol.load()
    overrides.apply(funcs)
    return {
        fn: protocol.decode(funcs[fn], _pack_state(funcs[fn], vals))
        for fn, vals in sorted(DEFAULT_SEED.items())
    }


def _hand_cases() -> list:
    """(id, function, fields): the interpretation edges."""
    c = []
    for v in (0x00, 0x7F, 0x80, 0x81, 0xFF):  # starter current: 0x81 = not measured, else signed 8-bit
        c.append(
            (
                "energy/starter-%02x" % v,
                "energy",
                {"IOneBattBemAfs": v, "UOneBattBemAfs": 125, "AgeOneBattValuesMinutes": 3},
            )
        )
    for u, i in ((0, 0), (141, 0), (125, 0xFFFB), (135, 0x8000), (129, 0x7FFF), (3, 5)):
        c.append(("energy/leisure-%d-%04x" % (u, i), "energy", {"UTwoBattBemAfs": u, "ITwoBattBemAfs": i}))
    for inst in (0, 1):  # not-fitted sentinels: 511 currents / 254 powers -> null / 0 unless installed
        c.append(
            (
                "energy/sources-installed-%d" % inst,
                "energy",
                {
                    "DcdcInstalled": inst,
                    "LadInstalled": inst,
                    "PvInstalled": inst,
                    "IDcdcAfs": 0xFFFE,
                    "ILandAfs": 511,
                    "IPvAfs": 511,
                    "PDcdcAfs": 0xFF,
                    "PLandAfs": 254,
                    "PPvAfs": 254,
                },
            )
        )
    for lvl in range(16):
        c.append(("energy/soc-%d" % lvl, "energy", {"SocOneBattAfs": lvl, "SocTwoBattAfs": lvl}))
    for st in (0, 1, 2, 3, 6, 7):
        c.append(
            (
                "energy/source-state-%d" % st,
                "energy",
                {"StateDcdcAfs": st, "StateLandAfs": st, "StatePvAfs": st},
            )
        )
    for age in (0, 254, 255):
        c.append(("energy/age-%d" % age, "energy", {"AgeOneBattValuesMinutes": age}))
    faults = dict.fromkeys(
        (
            "SystemError",
            "DcdcDefect",
            "PvDefect",
            "LandDefect",
            "LandNotAvailable",
            "TwoBattNotCharged",
            "TwoBattSwitchAtCharging",
            "TwoBattSwitchAtWorkshop",
            "WarningLevelTwo",
            "WarningLevelActive",
            "SleepWarning",
            "CurrentDeratingTemperature",
            "EnergyModeNotSelectable",
        ),
        1,
    )
    c.append(
        (
            "energy/all-faults",
            "energy",
            {**faults, "EnergyMode": 2, "tTwoBattRemainingh": 58, "tTwoBattRemainingmin": 30},
        )
    )
    for unit in (0, 1):
        c.append(
            (
                "water/unit-%d" % unit,
                "water",
                {
                    "Installed": 1,
                    "FreshWaterUnit": unit,
                    "FreshWaterLevel": 11,
                    "FreshWaterVolume": 29,
                    "WasteWaterUnit": unit,
                    "WasteWaterLevel": 37,
                    "WasteWaterVolume": 22,
                },
            )
        )
        c.append(
            (
                "water/capacity-0-unit-%d" % unit,
                "water",
                {"FreshWaterUnit": unit, "FreshWaterLevel": 5, "FreshWaterVolume": 0},
            )
        )
    for p in range(16):
        c.append(("water/popup-%d" % p, "water", {"FreshWaterInfoPopUp": p, "WasteWaterInfoPopUp": p}))
    for inst, err in itertools.product((0, 1), range(4)):
        c.append(
            (
                "cooler/installed-%d-error-%d" % (inst, err),
                "cooler",
                {"Installed": inst, "Error": err, "State": 0},
            )
        )
    for mode in range(8):
        c.append(
            (
                "cooler/mode-%d" % mode,
                "cooler",
                {
                    "Installed": 1,
                    "State": 1,
                    "Mode": mode,
                    "Level": 3,
                    "NightTimerHourOn": 22,
                    "NightTimerHourOff": 6,
                    "TimerState": mode % 2,
                    "TimerHourSet": 7,
                    "TimerMinSet": 30,
                },
            )
        )
    for err in range(8):
        c.append(("airheater/error-%d" % err, "airheater", {"Installed": 1, "ErrorCode": err}))
    for op in range(4):
        c.append(("airheater/opmode-%d" % op, "airheater", {"Installed": 1, "OperationModeAirHeater": op}))
    for n, p in itertools.product((0, 1), repeat=2):
        c.append(
            ("airheater/run-%d%d" % (n, p), "airheater", {"NormalOperation": n, "PermanentOperation": p})
        )
    for st, usb, il, ol in itertools.product(
        (0, 1), repeat=4
    ):  # the polarity matrix (lights inverted+combined)
        c.append(
            (
                "campingmode/s%d-u%d-i%d-o%d" % (st, usb, il, ol),
                "campingmode",
                {
                    "Installed": 1,
                    "State": st,
                    "UsbCharger": usb,
                    "InteriorLight": il,
                    "OutsideLight": ol,
                    "Enable": st,
                },
            )
        )
    c.append(("campingmode/master-only", "campingmode", {"State": 1}))
    for inst, v in itertools.product((0, 1), range(16)):
        c.append(
            (
                "roof/installed-%d-%d" % (inst, v),
                "roof",
                {"Installed": inst, "Position": v, "InfoPopUp": v, "SafetyCounterValid": v % 2},
            )
        )
    c.append(("roof/no-position", "roof", {"Installed": 1}))
    c.append(("lighting/all-13", "lighting", {"BrightnessL" + z: 13 for z in _ZONES}))
    c.append(
        ("lighting/zone9-only", "lighting", {"BrightnessL" + z: (5 if z == "Nine" else 0) for z in _ZONES})
    )
    for lvl, vol in (
        (50, 29),
        (99, 29),
        (1, 29),
        (50, 45),
        (67, 15),
    ):  # percent unit: litres floor, not round
        c.append(
            (
                "water/pct-floor-%d-%d" % (lvl, vol),
                "water",
                {"FreshWaterUnit": 0, "FreshWaterLevel": lvl, "FreshWaterVolume": vol},
            )
        )
    for sentinel in (13, 14):  # only a not-equipped / leave-unchanged zone: any_on must stay false
        c.append(
            (
                "lighting/only-%d" % sentinel,
                "lighting",
                {"BrightnessL" + z: (sentinel if z == "Four" else 0) for z in _ZONES},
            )
        )
        c.append(
            (
                "lighting/only-%d-others-13" % sentinel,
                "lighting",
                {"BrightnessL" + z: (sentinel if z == "Four" else 13) for z in _ZONES},
            )
        )
    c.append(
        (
            "lighting/sentinels",
            "lighting",
            {
                "BrightnessLOne": 14,
                "BrightnessLTwo": 13,
                "BrightnessLOneTwo": 11,
                "ProfileNumber": 3,
                "Mode": 4,
            },
        )
    )
    for amb in ("0410", "0409", "0411", "04\x1f0", "041\x7f", "041\xff"):
        for comm in (None, 1, 2):
            f = {"AmbSwVersion": _ascii(amb), "CmSwVersion": _ascii("0207")}
            if comm is not None:
                f["CommunicationVersion"] = comm
            c.append(("general/amb-%s-comm-%s" % (amb.encode("unicode_escape").decode(), comm), "general", f))
    c.append(("general/comm-only-3", "general", {"CommunicationVersion": 3}))
    clock = {
        "CarTimeYear": 126,
        "CarTimeMonth": 6,
        "CarTimeDay": 8,
        "CarTimeHour": 19,
        "CarTimeMinute": 30,
        "CarTimeSecond": 5,
    }
    c.append(
        ("vehicle/clock", "vehicle", {**clock, "TerminalOneFive": 1, "CarVariant": 4, "CarLevelPopUp": 1})
    )
    c.append(("vehicle/rtc-day-0", "vehicle", {**clock, "CarTimeDay": 0}))
    c.append(
        ("vehicle/rtc-epoch", "vehicle", {**clock, "CarTimeYear": 0, "CarTimeMonth": 0, "CarTimeDay": 1})
    )
    for roll, pitch in ((0xFF93, 35), (0x8000, 0x7FFF), (0xFFFB, 5), (9001, (-9050) & 0xFFFF), (0, 0)):
        c.append(
            (
                "vehicle/level-%04x-%04x" % (roll, pitch),
                "vehicle",
                {"CarLevelRoll": roll, "CarLevelPitch": pitch},
            )
        )
    return c


def _states(seed: dict) -> list:
    """(id, {fn: fields}) whole states for apply_sw_corrections + anchors + firmware meta."""

    def with_(fn, **fields):
        s = {k: dict(v) for k, v in seed.items()}
        s[fn].update(fields)
        return s

    anchors_case = with_("energy", UTwoBattBemAfs=50)
    anchors_case["cooler"].update(Installed=1, Level=7, NightTimerHourOn=25, NightTimerHourOff=24)
    anchors_case["vehicle"].update(CarLevelRoll=9100, CarLevelPitch=(-9050) & 0xFFFF)
    return [
        ("seed", seed),
        ("seed-amb-0411", with_("general", AmbSwVersion=_ascii("0411"))),
        ("seed-dcdc-not-fitted", with_("energy", DcdcInstalled=0)),
        ("no-general", {k: v for k, v in seed.items() if k != "general"}),
        ("anchors", anchors_case),
        ("empty", {}),
    ]


def _state_case(case_id: str, fn: dict) -> dict:
    fn = {k: f for k, f in fn.items() if k in UI_FUNCTIONS}
    st = {k: semantics.interpret(k, dict(f)) for k, f in fn.items()}
    semantics.apply_sw_corrections(st)
    return {
        "id": case_id,
        "fn": fn,
        "expect": {
            "state": st,
            "anchors": anchors.check(st),
            "firmware": ServeBackend._firmware_meta(st.get("general")),
        },
    }


def _round_cases() -> list:
    xs = [(n * 0.1, 1) for n in range(1024)] + [(s / 100.0, 2) for s in range(-400, 401)]
    xs += [
        (0.125, 2),
        (0.375, 2),
        (2.5, 0),
        (3.5, 0),
        (0.25, 1),
        (-0.25, 1),
        (2.675, 2),
        (1.005, 2),
        (12.35, 1),
        (-0.04, 1),
    ]
    return [{"x": x, "nd": nd, "expect": round(x, nd)} for x, nd in xs]


def build() -> dict:
    """The whole vector document (JSON-normalised)."""
    seed = decoded_seed()
    codec = json.loads(CODEC_VECTORS.read_text(encoding="utf-8"))["decode"]
    cases = [("seed/" + fn, fn, f) for fn, f in seed.items() if fn in UI_FUNCTIONS]
    cases += [
        ("codec/" + v["id"], v["function"], v["expect"]) for v in codec if v["function"] in UI_FUNCTIONS
    ]
    cases += [("empty/" + fn, fn, {}) for fn in UI_FUNCTIONS]
    cases += _hand_cases()
    doc = {
        "version": 1,
        "generated_by": "tools/gen_semantics_vectors.py",
        "interpret": [
            {"id": i, "function": fn, "fields": f, "expect": semantics.interpret(fn, dict(f))}
            for i, fn, f in cases
        ],
        "states": [_state_case(i, fn) for i, fn in _states(seed)],
        "round": _round_cases(),
    }
    return json.loads(json.dumps(doc))


def _text() -> str:
    return json.dumps(build(), indent=1, sort_keys=True) + "\n"


def main() -> int:
    text = _text()
    if "--check" in sys.argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print("STALE: %s — run python3 -m tools.gen_semantics_vectors" % OUT, file=sys.stderr)
            return 1
        print("fresh: %s" % OUT)
        return 0
    OUT.write_text(text, encoding="utf-8")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
