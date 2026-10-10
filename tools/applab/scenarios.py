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
    # Cooler (measured 2026-10-05). The Timer / Quiet-mode rows expand inline (an accordion: opening
    # one collapses the other) and the page scrolls, so each point names its layout: "top" = page
    # unscrolled; the rest = after `_SCROLL_END` (the swipe clamps at the page end, so it is stable).
    "cooler_power": (905, 1770),  # top: the Refrigerator box on/off switch
    "cooler_level_5": (990, 1440),  # top: cooling-level slider (1-5) at level 5
    "cooler_power_quiet_open": (905, 738),  # Quiet mode expanded, scrolled to the end: the on/off switch
    "cooler_manual_quiet": (905, 1486),  # Quiet mode expanded, scrolled to the end: Manual quiet switch
    "cooler_auto_quiet": (905, 1680),  # Quiet mode expanded, scrolled to the end: Automatic quiet switch
    "cooler_timer": (905, 1632),  # Timer expanded (Quiet collapsed), scrolled to the end: Timer switch
    "heater_immediate": (
        900,
        1640,
    ),  # Immediate heating toggle (measured 2026-10-05; the 905,1558 map was ~80px high)
    "heater_temp_8": (790, 1104),  # Heating Temperature slider (1-9,HI) at level 8
    "heater_runtime_60": (499, 1386),  # Run Time slider (10-120) at 60 min
    "heater_permanent": (905, 2015),  # Permanent Heating toggle (greyed: "only in the vehicle")
    "camping_master": (905, 1010),  # Camping mode detail: the master toggle switch
    "lighting_all": (905, 457),  # All lights master toggle (OFF->ON writes 0c10 = profile LIGHTS_ON)
    "lighting_kitchen_row": (540, 1175),  # tap the collapsed Kitchen zone row to expand its lamps
    "lighting_cooking_50": (582, 1690),  # Kitchen > "Cooking" slider at 50 % = BrightnessLSeven (L7) = 5
    "lighting_background_50": (
        582,
        1450,
    ),  # Kitchen > "Background Lighting" slider at 50 % = BrightnessLFive (L5)
    "lighting_profile_a_hold": (148, 640, 2500),  # press-and-hold profile tile A = save the current lighting
    # The roof rocker ("roof switch", bounds [376,1397][704,2132]): its upper half held 16 s. Recorded
    # against the old stepped mock roof; the mock now follows the real unit (CAPTURE 2026-10-10): the
    # first open press raises the pre-open checklist (0302, no motion) and full travel takes ~28 s
    # (tools/mock_unit.py ROOF_*), so a re-recording needs the dialog's OK + a longer hold.
    "roof_open_hold": (540, 1580, 16000),
    "wakeup_hour_wheel": (469, 1666, 469, 1521),  # the wake-up sheet's hour wheel: one row up = +1 h
    "wakeup_switch": (958, 381),  # Wake-up Light page, unscrolled: the "Wake-up light" enable switch
    "wakeup_sheet_handle": (
        540,
        1603,
    ),  # the time sheet's drag handle when it opens half-expanded (tap = expand)
    "door_contact_switch": (590, 1808),  # Lighting & sliding door page: the "Opening sliding door ..." row
    "lighting_profile_a": (148, 640),  # profile tile A (= favourite 1), short tap (= activate once stored)
}

TILE = {
    "cooler": r"^Refrigerator Box$",
    "airheater": r"[Aa]ir heater",  # the tile reads "Auxiliary air heater" on app 5.0.8.3028
    "lighting": r"^Lighting$",
    "campingmode": r"^Camping mode$",
    "roof": r"^Pop-up roof$",
    "energy": r"^(Second battery|Energy)$",
}


def _connect() -> list[Step]:
    """Cold-start the app on the Vehicle tab and establish a session to this walk's fresh fake unit.

    A silent *reconnect* over the stored bond stalls on this emulator/netsim (the app connects, polls
    ``vehicle`` at 1 Hz and never completes the subscribe/read-all — see ``README.md`` "Reconnect /
    ghost radios"); a fresh *pair* is the path that completes. So this drops the fake's bond
    (``forget``) and re-pairs through the passkey wizard, exactly as the ``session`` scenario does."""
    return [
        adb("shell", "am", "force-stop", APP_ID),
        fifo("forget"),  # the fake drops its bond so Connect triggers a fresh pair, not a stalling reconnect
        adb("shell", "am", "start", "-n", f"{APP_ID}/{APP_ACTIVITY}"),
        idle(8),  # let the cold start finish rendering before tapping the Vehicle tab
        xy("vehicle_tab"),
        wait(r"^Connect$", 45),
        ui(r"^Connect$"),
        pair(),
        wait(r"Disconnect", 60),
    ]


_SCROLL_END = adb(
    "shell", "input", "swipe", "540", "1900", "540", "900", "1000"
)  # slow: no fling; clamps at the page end


def _open_app() -> list[Step]:
    """:func:`_connect`, then scroll the *Remote Control* tiles into view."""
    return [
        *_connect(),
        adb("shell", "input", "swipe", "540", "1800", "540", "600", "400"),  # reveal the tiles
        wait(r"Remote Control", 15),
    ]


SCENARIOS: dict[str, list[Step]] = {
    "session": [
        adb("shell", "am", "force-stop", APP_ID),
        fifo("forget"),  # the unit drops its bond: the app pairs afresh on Connect
        adb("shell", "am", "start", "-n", f"{APP_ID}/{APP_ACTIVITY}"),
        idle(8),  # cold-start render
        xy("vehicle_tab"),
        wait(r"^Connect$", 45),
        ui(r"^Connect$"),
        pair(),
        wait(r"Disconnect", 60),  # connected (first connection)
        idle(20),
        adb("shell", "input", "keyevent", "KEYCODE_HOME"),
        logwait(r"### DISCONNECTED", 120),  # background drops the link
        adb("shell", "am", "start", "-n", f"{APP_ID}/{APP_ACTIVITY}"),
        idle(5),
        xy("vehicle_tab"),
        wait(r"^Connect$", 45),
        ui(r"^Connect$"),  # the app does not auto-reconnect on foreground; tap to reconnect
        logwait(r"### CONNECTED", 60),  # second connection
        idle(10),
    ],
    "cooler": [
        # The tile opens behind a one-time 3-page "COOLING LEVEL" coachmark: skip it once in the session
        # before recording (README "coachmarks"). Quiet mode needs the box ON, the timer needs it OFF
        # (control.refusal mirrors both), so: power on -> level -> quiet modes -> power off -> timer.
        *_open_app(),
        fifo("set cooler State=0 Mode=0 Level=3"),
        ui(TILE["cooler"]),
        wait(r"Cooling level"),
        xy("cooler_power", expect=("cooler", "power", "on")),
        xy("cooler_level_5", expect=("cooler", "level", 5)),
        _SCROLL_END,
        ui(r"^Quiet mode"),  # expand the Quiet mode row (client-side; no write)
        wait(r"Manual quiet mode"),
        _SCROLL_END,
        xy("cooler_manual_quiet", expect=("cooler", "mode", "quiet")),
        idle(2),  # a re-tap of the same switch inside the app's write window is swallowed
        xy("cooler_manual_quiet", expect=("cooler", "mode", "normal")),
        xy("cooler_auto_quiet", expect=("cooler", "mode", "timer_quiet")),
        xy("cooler_power_quiet_open", expect=("cooler", "power", "off")),
        ui(r"^Timer: "),  # expand the Timer row (collapses Quiet mode)
        wait(r"As soon as the timer is activated"),
        _SCROLL_END,
        xy("cooler_timer", expect=("cooler", "timer_start", None)),  # the timer is offered with the box off
        # The first tap on the switch after arming writes nothing (lab 2026-10-05, 3 of 3: the switch
        # shows ON but the first tap is absorbed, at 3 s or at 20 s alike); the second tap cancels.
        idle(2),
        xy("cooler_timer"),
        xy("cooler_timer", expect=("cooler", "timer_cancel", None)),
    ],
    "airheater": [
        # Core immediate-heating controls (power/level/runtime). The timer-time frame is already
        # APP-OBSERVED (evidence-ledger, `3f7b007f091f`) and permanent-OFF is covered by
        # airheater-permanent-on, so this records the live power/level/runtime frames only. The tile
        # The tile opens behind a one-time 4-page "IMMEDIATE HEATING" info coachmark (Next/Skip);
        # dismiss it once in the app before recording (it does not reappear in the same session, like
        # the other tiles' coachmarks — see README "coachmarks").
        # Set temperature + run time while INACTIVE (both sliders adjustable, stable layout), then
        # activate Immediate heating LAST: once active the app shows "Active • N min", locks the run-time
        # slider and shifts the toggle, so a run-time or power-off change afterwards writes no frame.
        *_open_app(),
        fifo("set airheater NormalOperation=0 PermanentOperation=0 OperationModeAirHeater=0"),
        ui(TILE["airheater"]),
        wait(r"Heating Temperature"),
        xy("heater_temp_8", expect=("airheater", "level", 8)),
        xy("heater_runtime_60", expect=("airheater", "runtime", 60)),
        xy("heater_immediate", expect=("airheater", "power", "on")),
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
        # The app models each zone as a tap-to-expand row of named lamps, not one slider: lab 2026-10-05
        # proved Kitchen = "Background Lighting" (BrightnessLFive=L5) + "Cooking" (BrightnessLSeven=L7),
        # so calictl "kitchen"=L7 is the Cooking lamp. A one-time info coachmark covers the controls first.
        *_open_app(),
        ui(TILE["lighting"]),
        ui(r"^Close$"),  # dismiss the one-time lighting info coachmark sheet
        wait(r"All lights|Alle Lichter"),
        xy("lighting_all", expect=("lighting", "power", "on")),  # master OFF->ON = 0c10 (LIGHTS_ON)
        xy("lighting_kitchen_row"),  # expand the Kitchen zone (client-side; no BLE write)
        xy("lighting_cooking_50", expect=("lighting", "kitchen", 5)),  # Cooking = BrightnessLSeven (L7) = 5
    ],
    "roof-hold": [
        *_open_app(),
        fifo("set vehicle TerminalOneFive=1"),
        fifo("set roof Position=0 InfoPopUp=0"),
        ui(TILE["roof"]),
        wait(r"press and hold the upper button"),
        xy("roof_open_hold", expect=("roof", "open", None)),  # adb returns on release (16 s)
        wait(r"roof is open", 20),
    ],
    "energy-mode": [  # ECO needs PvInstalled=1 and shows only on a 2nd visit (protocol-crosscheck-applab.md).
        # Energy Mode is NOT a Remote Control tile: it is a dropdown under Vehicle Information >
        # Charging (app 5.0.8.3028). The readback echo keeps EnergyMode at Normal after a write, so
        # the fake is nudged to Max before the Normal tap or the app treats Normal as a no-op.
        *_connect(),
        ui(r"See all info"),
        wait(r"Vehicle Information", 20),
        adb("shell", "input", "swipe", "540", "1700", "540", "700", "400"),  # reveal Charging
        ui(r"Energy Mode"),  # expand the dropdown (it stays open across selections + pushes)
        ui(r"^Max$", expect=("energy", "mode", "max_charge")),
        fifo("set energy EnergyMode=1"),  # readback = Max so the Normal tap is a real change, not a no-op
        idle(3),  # let the push re-render the current value before the next tap
        ui(r"^Normal$", expect=("energy", "mode", "normal")),
    ],
    "lighting-profile": [
        # Save = press-and-hold a profile tile: the app writes SET_BRIGHTNESS with ProfileNumber = the
        # favorite and every equipped zone at its current level (no Save button). Run after the lighting
        # coachmark was closed once (lighting-zone dismisses it). Activating it: `lighting-favourite`.
        *_open_app(),
        ui(TILE["lighting"]),
        wait(r"All lights|Alle Lichter"),
        xy("lighting_all", expect=("lighting", "power", "on")),
        xy("lighting_kitchen_row"),  # expand Kitchen (client-side)
        xy("lighting_cooking_50", expect=("lighting", "kitchen", 5)),
        xy("lighting_profile_a_hold", expect=("lighting", "save_profile", 1)),
    ],
    "lighting-wakeup": [
        # Time first (no wake-up config reported yet: the app's write carries enabled=off), then the
        # switch on, then a second time edit WITH the switch on (ruling R3: the edit keeps the
        # unit-reported enabled=1), then the switch off. Each write resends the config the app read
        # back from the mock's Mode-20 echo (dg/h F0). The emulator runs in UTC (the replay pins it).
        *_open_app(),
        ui(TILE["lighting"]),
        wait(r"All lights|Alle Lichter"),
        ui(r"^Functions & Settings$"),  # expand (client-side)
        _SCROLL_END,
        ui(r"^Wake-up Light$"),
        wait(r"Wake-up time"),
        ui(r"^\d\d:\d\d$"),  # the Time field opens the wheel sheet
        wait(r"Wake-up time:"),
        *[xy("wakeup_hour_wheel") for _ in range(7)],  # 00 -> 07
        ui(r"^OK$", expect=("lighting", "wakeup", "07:00")),
        idle(2),
        xy("wakeup_switch", expect=("lighting", "wakeup", "07:00 on")),
        # A switch tap compares the phone clock with the unit RTC (1004; the fake's is the baseline's
        # 2026-08-28): "Different time settings." App/Vehicle dialog, OK dismisses it.
        wait(r"Different time settings", 10),
        ui(r"^OK$"),
        idle(2),
        ui(r"^\d\d:\d\d$"),
        wait(r"Wake-up time:"),
        idle(1),
        xy("wakeup_sheet_handle"),  # the second open lands half-expanded (OK off screen): expand it
        idle(1),
        xy("wakeup_hour_wheel"),  # 07 -> 08
        ui(r"^OK$", expect=("lighting", "wakeup", "08:00")),
        idle(2),
        xy("wakeup_switch", expect=("lighting", "wakeup", "08:00 off")),
        wait(r"Different time settings", 10),  # the switch shows the clock dialog both ways
        ui(r"^OK$"),
        idle(2),
        adb("shell", "input", "keyevent", "BACK"),  # reopen: the page shows the time it read back
        idle(2),
        ui(r"^Wake-up Light$"),
        wait(r"^08:00$"),
    ],
    "door-contact": [
        *_open_app(),
        ui(TILE["lighting"]),
        wait(r"All lights|Alle Lichter"),
        ui(r"^Functions & Settings$"),
        _SCROLL_END,
        ui(r"[Ss]liding [Dd]oor"),  # the "Lighting & sliding door" entry
        wait(r"Opening sliding door"),
        xy("door_contact_switch", expect=("lighting", "door_contact", "on")),
        idle(2),
        xy("door_contact_switch", expect=("lighting", "door_contact", "off")),
        idle(2),
    ],
    "lighting-favourite": [
        # Save A (press-and-hold) then activate A (tap): the mock stores favourites and answers the
        # app's post-save REQUEST_CONFIG with favourite 1's bit, so tile A is no longer "+".
        *_open_app(),
        ui(TILE["lighting"]),
        wait(r"All lights|Alle Lichter"),
        xy("lighting_all", expect=("lighting", "power", "on")),
        xy("lighting_kitchen_row"),
        xy("lighting_cooking_50", expect=("lighting", "kitchen", 5)),
        xy("lighting_profile_a_hold", expect=("lighting", "save_profile", 1)),
        idle(3),
        # After the save tile A is the ACTIVE profile and a tap on it is a no-op (dg/h u0 caller hi/f.j):
        # switch all lights off first so the tap activates.
        xy("lighting_all", expect=("lighting", "power", "off")),
        idle(2),
        xy("lighting_profile_a", expect=("lighting", "profile", 1)),
        idle(2),
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
