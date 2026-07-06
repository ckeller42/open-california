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
    "airheater": {  # same shared-model layout
        "NightTimerHourOff": (20, 4),
        "TimerHour": (24, 8),
        "TimerMin": (32, 8),
        "NightTimerHourOn": (40, 8),
    },
}

# control frame lengths confirmed on-device / from the model (bytes)
CONTROL_FRAME_BYTES = {"cooler": 6, "airheater": 6}


def apply(funcs: dict) -> None:
    """Fill in the manually-resolved offsets on the loaded Function objects."""
    for fn, fields in CONTROL_OFFSETS.items():
        f = funcs.get(fn)
        if not f:
            continue
        for cf in f.control_fields:
            if cf.name in fields and not cf.placed:
                cf.offset, cf.width = fields[cf.name]
