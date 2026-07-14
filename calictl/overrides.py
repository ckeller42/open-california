"""Manually-resolved control-frame offsets.

The auto-extractor conservatively flags the cooler/air-heater timer-value fields
`MERGED_AMBIGUOUS` (the two functions share one control model). Their placement
is in fact unambiguous in the app's frame builder `sf/a.java` f() (lines 110-157,
read directly), so we supply it here to complete the 6-byte control frame.

These are the only manual offsets in calictl; everything else comes from the
auto-generated dictionary.
"""
from __future__ import annotations

# function -> {field_name: (offset, width)}   (source: sf/a.java f())
CONTROL_OFFSETS = {
    "cooler": {  # 1101; sf/a.java f() DEFAULT branch (Z=1). Contiguous, no bit-hole after Level.
        # Was wrong: the previous entry pasted the airheater (case-0) layout — which has a 4-bit
        # hole at 16-19 — onto cooler, shifting all four timers +4. Corrected 2026-07-12.
        "TimerHour": (16, 8),
        "TimerMin": (24, 8),
        "NightTimerHourOn": (32, 8),
        "NightTimerHourOff": (40, 8),
    },
    "airheater": {  # 1701; from sf/a.java f() (airheater ctor branch, read directly).
        # NOT the cooler layout — airheater's four MERGED_AMBIGUOUS control fields differ.
        "OperationModeCombined": (20, 4),
        "RunningTime": (24, 8),
        "TimerHour": (32, 8),
        "TimerMin": (40, 8),
    },
    # 1-byte camping control frame (control char 1201), from lg/a.java f()
    # default branch. State@6 is already placed by the extractor; the other
    # three 2-bit fields were left MERGED_AMBIGUOUS because lg/a also serves
    # roof-AC.
    "campingmode": {
        "InteriorLight": (0, 2),
        "OutsideLight": (2, 2),
        "UsbCharger": (4, 2),
    },
    # lighting Timestamp: sg.a() no-arg 32-bit field @16 (eg/a.java f() boolArr[16..47]),
    # between Mode@8 and LightValue@48. Default 0 — part of the packet, not a validated clock.
    "lighting": {
        "Timestamp": (16, 32),
    },
    # The following resolve the remaining MERGED_AMBIGUOUS control offsets, read directly
    # from each function's f() builder (2026-07-08). Not installed on this van (except roof),
    # so `set` isn't wired for them yet; this just completes the frame layout. Enum value
    # semantics remain UNVERIFIED. See docs/business-logic/re-gap-inventory.md §A3.
    "roof": {                       # jg/a.java f() — 5-byte frame (Up@6/Down@4 already placed)
        "SafetyCounter": (8, 32),   # app->unit echo/handshake; roof move needs a ~1 Hz heartbeat
    },
    "roofaircondition": {           # lg/a.java f() case0 — 3-byte frame (State@6 already placed)
        "Mode": (8, 4), "FanSpeed": (12, 4), "Temperature": (16, 8),
    },
    "stairs": {                     # pg/a.java f() case0 — 1-byte frame
        "OperationMode": (6, 2), "Movement": (4, 2),
    },
    "livingroomheater": {           # gg/a.java f() case0 — 3-byte frame (Mode@12 already placed)
        "TemperatureWater": (2, 2), "StateWater": (4, 2),
        "StateAir": (6, 2), "TemperatureAir": (16, 8),
    },
    "satelliteantenna": {           # gg/a.java f() default branch — 2-byte frame (SatelliteSelection@12 placed)
        "Dish": (0, 2), "System": (2, 2), "Wlan": (6, 1), "DishStop": (7, 1),
    },
}

# control frame lengths confirmed on-device / from the model (bytes)
CONTROL_FRAME_BYTES = {"cooler": 6, "airheater": 6, "campingmode": 1, "lighting": 16,
                       "roof": 5, "roofaircondition": 3, "stairs": 1, "livingroomheater": 3,
                       "satelliteantenna": 2}

# Semantic value constraints beyond the field's bit-width. The unit's firmware
# range-VALIDATES control writes and drops the ATT link with 0x0E on an out-of-range
# value (observed live 2026-07-07: cooler State=3, lighting ProfileNumber=14+Mode=4),
# so calictl checks these before sending. A value is either an inclusive (lo, hi) range
# or a set of allowed values. Only add entries backed by on-device evidence or an app
# enum — a wrong constraint would reject a legitimate write. NOTE: the "leave-unchanged"
# sentinel 3 IS valid for the campingmode 2-bit fields (full-packet resend), so those are
# deliberately left width-bounded (0..3), not restricted here.
CONTROL_RANGES = {
    # State=3 (the dict default, a leave-unchanged sentinel) is NOT valid for cooler —
    # it was rejected 0x0E on-device. A full-packet cooler write must send 0=off / 1=on.
    "cooler": {"State": {0, 1}},
    # lighting Mode enum (dg/n.java, full set decoded 2026-07-12): 0=NO_MODE, 4=SET_BRIGHTNESS,
    # 6=SET_COLOR, 8=SET_DOUBLE, 12=REQUEST_CONFIG, 16=SET_PROFILE, 20=WAKEUP_TIME, 24=SYSTEM_TIME,
    # 28=PREVIEW — all legitimate firmware modes (guard against out-of-enum values only).
    # (ProfileNumber is intentionally NOT constrained: valid range depends on Mode.)
    "lighting": {"Mode": {0, 4, 6, 8, 12, 16, 20, 24, 28}},
    # HeatingLevel is a 4-bit field but the app exposes only levels 1-9 + "HI"(=10); it uses 11
    # as the post-set leave-unchanged/commit value (verified at the van 2026-07-14 via HCI capture:
    # lowest set -> 1, "HI" -> 10, a HeatingLevel=11 follow frame after every set). 0 and 12-15 are
    # never emitted. Allow {1..10} (user) plus 11 (the sentinel the builder defaults to).
    "airheater": {"HeatingLevel": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}},
}


def apply(funcs: dict) -> None:
    """Fill in the manually-resolved offsets + semantic value constraints on the
    loaded Function objects."""
    for fn, fields in CONTROL_OFFSETS.items():
        f = funcs.get(fn)
        if not f:
            continue
        for cf in f.control_fields:
            if cf.name in fields and not cf.placed:
                cf.offset, cf.width = fields[cf.name]
    for fn, ranges in CONTROL_RANGES.items():
        f = funcs.get(fn)
        if not f:
            continue
        for cf in f.control_fields:
            if cf.name in ranges:
                cf.valid = ranges[cf.name]
