"""Scripted app walks for ``tools/applab/walk.py`` — one list of steps per scenario.

A step is a verb with arguments, optionally with ``expect=(function, what, value)``: the calictl
action (``control.build(funcs, function, what, value, state)``) that the app's next non-neutral
write must equal on the fields it targets (``tools.capture_diff.check_recording``).

Verbs: ``ui(rx)`` taps the first node whose text or content-desc matches; ``fifo(cmd)`` sends a
fake-unit console line (``set``/``raw`` go to both fakes under ``--live``); ``wait(rx, s)`` polls
the screen texts; ``xy(point)`` taps (``(x, y)``), long-presses (``(x, y, ms)``) or drags
(``(x, y, x2, y2)``, the time wheels) at a named point of :data:`XY`; ``adb(*args)`` runs any adb
command (background, foreground, force-stop); ``idle(s)``; ``pair()`` runs ``pair_wizard``;
``logwait(rx, s)`` waits for a line in the fake's log.

Selectors come from ``ui/screens/*.yaml`` and the 2026-09-16/27 lab sessions; the XY points are
measured on the AVD ``lab34`` whose screen is :data:`XY_SCREEN` (``walk.py`` refuses any other).
``None`` = not measured yet — ``tools/applab/README.md`` "Measuring XY points".
"""

from __future__ import annotations

from typing import NamedTuple


class Step(NamedTuple):
    verb: str
    args: tuple
    expect: tuple | None = None


def ui(rx: str, expect: tuple | None = None) -> Step:
    return Step("ui", (rx,), expect)


def fifo(cmd: str, expect: tuple | None = None) -> Step:
    return Step("fifo", (cmd,), expect)


def wait(rx: str, timeout_s: float = 15.0) -> Step:
    return Step("wait", (rx, timeout_s))


def xy(point: str, expect: tuple | None = None) -> Step:
    return Step("xy", (point,), expect)


def adb(*args: str) -> Step:
    return Step("adb", args)


def idle(s: float) -> Step:
    return Step("idle", (s,))


def pair() -> Step:
    return Step("pair", ())


def logwait(rx: str, timeout_s: float = 30.0) -> Step:
    return Step("logwait", (rx, timeout_s))


APP_ID = "de.volkswagen.CaliforniaOnTour"
APP_ACTIVITY = "de.volkswagen.caliontour.development.CaliforniaOnTourMainActivity"
XY_SCREEN = ("1080x2400", 420)  # `adb shell wm size` / `wm density` of the AVD the points were measured on

XY: dict[str, tuple[int, ...] | None] = {
    "vehicle_tab": (745, 2290),  # Vehicle tab (the app-lab runbook, pixel_6 lab34 on the Mac)
    "cooler_power": None,
    "cooler_level_5": None,  # cooling-level slider, right end (level 5)
    "cooler_manual_quiet": None,
    "cooler_auto_quiet": None,
    "cooler_timer": None,
    "heater_immediate": None,
    "heater_temp_8": None,  # Heating Temperature slider at level 8
    "heater_runtime_60": None,  # Run Time slider at 60 min
    "heater_permanent": None,
    "camping_master": None,
    "lighting_all": None,
    "lighting_kitchen_50": None,  # kitchen zone slider at 50 %
    "lighting_profile_a_hold": None,  # (x, y, 2000): press-and-hold profile tile A = edit
    "roof_open_hold": None,  # (x, y, 12000): the upper roof button held 12 s, then released
    "wakeup_hour_wheel": None,  # (x, y, x, y - 145): one row of the wake-up hour wheel
}

TILE = {
    "cooler": r"^Refrigerator Box$",
    "airheater": r"^(Air heater|Parking heater|Heater)$",
    "lighting": r"^Lighting$",
    "campingmode": r"^Camping mode$",
    "roof": r"^Pop-up roof$",
    "energy": r"^(Second battery|Energy)$",
}


def _open_app() -> list[Step]:
    """App to the front, Vehicle tab, reconnect on the stored bond (every walk starts a fresh fake)."""
    return [
        adb("shell", "am", "start", "-n", f"{APP_ID}/{APP_ACTIVITY}"),
        xy("vehicle_tab"),
        ui(r"^Connect$"),
        wait(r"Remote Control", 30),
    ]


SCENARIOS: dict[str, list[Step]] = {
    "session": [
        adb("shell", "am", "force-stop", APP_ID),
        fifo("forget"),  # the unit drops its bond: the app pairs afresh on Connect
        adb("shell", "am", "start", "-n", f"{APP_ID}/{APP_ACTIVITY}"),
        xy("vehicle_tab"),
        ui(r"^Connect$"),
        pair(),
        wait(r"Remote Control", 45),
        idle(20),
        adb("shell", "input", "keyevent", "KEYCODE_HOME"),
        logwait(r"### DISCONNECTED", 120),
        adb("shell", "am", "start", "-n", f"{APP_ID}/{APP_ACTIVITY}"),
        logwait(r"### CONNECTED", 60),
        idle(10),
    ],
    "cooler": [
        *_open_app(),
        fifo("set cooler State=0 Mode=0 Level=3"),
        ui(TILE["cooler"]),
        wait(r"Manual quiet mode"),
        xy("cooler_power", expect=("cooler", "power", "on")),
        xy("cooler_level_5", expect=("cooler", "level", 5)),
        xy("cooler_manual_quiet", expect=("cooler", "mode", "quiet")),
        xy("cooler_manual_quiet", expect=("cooler", "mode", "normal")),
        xy("cooler_auto_quiet", expect=("cooler", "mode", "timer_quiet")),
        xy("cooler_power", expect=("cooler", "power", "off")),
        xy("cooler_timer", expect=("cooler", "timer_start", None)),  # the timer is offered with the box off
        xy("cooler_timer", expect=("cooler", "timer_cancel", None)),
    ],
    "airheater": [
        *_open_app(),
        fifo("set airheater NormalOperation=0 PermanentOperation=0 OperationModeAirHeater=0"),
        ui(TILE["airheater"]),
        wait(r"Heating Temperature"),
        xy("heater_immediate", expect=("airheater", "power", "on")),
        xy("heater_temp_8", expect=("airheater", "level", 8)),
        xy("heater_runtime_60", expect=("airheater", "runtime", 60)),
        xy("heater_immediate", expect=("airheater", "power", "off")),
        ui(r"^Start timer$", expect=("airheater", "timer_start", None)),
        ui(r"^Stop$", expect=("airheater", "timer_cancel", None)),
        fifo("set airheater PermanentOperation=1"),
        wait(r"[Cc]ontinuous heating"),
        xy("heater_permanent"),
        wait(r"Turn off continuous heating"),
        ui(r"^(Turn off|Switch off|Yes)$", expect=("airheater", "permanent", "off")),
    ],
    "campingmode": [
        *_open_app(),
        fifo("set vehicle TerminalOneFive=0"),
        fifo("set campingmode State=1"),
        ui(TILE["campingmode"]),
        wait(r"camping mode"),
        xy("camping_master", expect=("campingmode", "master", "off")),
        xy("camping_master", expect=("campingmode", "master", "on")),
    ],
    "lighting-zone": [
        *_open_app(),
        ui(TILE["lighting"]),
        wait(r"All lights|Alle Lichter"),
        xy("lighting_all", expect=("lighting", "power", "on")),
        xy("lighting_kitchen_50", expect=("lighting", "kitchen", 5)),
    ],
    "roof-hold": [
        *_open_app(),
        fifo("set vehicle TerminalOneFive=1"),
        fifo("set roof Position=0 InfoPopUp=0"),
        ui(TILE["roof"]),
        wait(r"press and hold the upper button"),
        xy("roof_open_hold", expect=("roof", "open", None)),
        wait(r"roof is open", 20),
    ],
    "energy-mode": [  # ECO is not offered on this profile (protocol-crosscheck-applab.md)
        *_open_app(),
        ui(TILE["energy"]),
        wait(r"Second battery|Charging"),
        ui(r"^Max", expect=("energy", "mode", "max_charge")),
        ui(r"^Normal", expect=("energy", "mode", "normal")),
    ],
    "lighting-profile": [
        *_open_app(),
        ui(TILE["lighting"]),
        wait(r"All lights|Alle Lichter"),
        ui(r"^A$", expect=("lighting", "profile", 1)),
        xy("lighting_profile_a_hold"),
        xy("lighting_kitchen_50", expect=("lighting", "kitchen", 5)),
        ui(r"^Save$", expect=("lighting", "save_profile", 1)),
    ],
    "lighting-wakeup": [
        *_open_app(),
        ui(TILE["lighting"]),
        wait(r"All lights|Alle Lichter"),
        ui(r"[Ww]ake.?up"),
        xy("wakeup_hour_wheel"),
        ui(r"^(OK|Save)$", expect=("lighting", "wakeup", "07:00")),
    ],
    "airheater-permanent-on": [  # expected: NO write — the switch is inert while continuous heating is off
        *_open_app(),
        fifo("set airheater PermanentOperation=0 NormalOperation=0"),
        ui(TILE["airheater"]),
        wait(r"Heating Temperature"),
        xy("heater_permanent"),
        wait(r"only in the vehicle", 10),
        idle(3),
    ],
}

# Which satellite view (calictl web UI `goto(view)`) shows the same function, for `walk.py --live`.
SCREEN = {
    "session": "home",
    "cooler": "cooler",
    "airheater": "airheater",
    "campingmode": "campingmode",
    "lighting-zone": "lighting",
    "energy-mode": "energy",
}
