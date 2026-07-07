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
    "cooler": {
        "NightTimerHourOff": (20, 4),
        "TimerHour": (24, 8),
        "TimerMin": (32, 8),
        "NightTimerHourOn": (40, 8),
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
}

# control frame lengths confirmed on-device / from the model (bytes)
CONTROL_FRAME_BYTES = {"cooler": 6, "airheater": 6, "campingmode": 1, "lighting": 16}

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
    # lighting Mode enum (dg/n.java): 0=default/no-op, 4=SET_BRIGHTNESS, 16=SET_PROFILE.
    # (ProfileNumber is intentionally NOT constrained: valid range depends on Mode —
    #  must be 0 with SET_BRIGHTNESS but selects a profile with SET_PROFILE.)
    "lighting": {"Mode": {0, 4, 16}},
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
