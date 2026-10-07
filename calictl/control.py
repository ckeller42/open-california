"""Per-function control-frame builders (full-packet, dictionary-driven).

Shared by the CLI (`calictl set`) and the daemon (`serve.on_command`) so both
build identical frames. Cooler/camping frames are written under a 1003 liveness heartbeat
(`device.actuate` -> `_arm`), which is what arms actuation on-device (issue #2); the daemon's
lighting path writes bare on an awake unit (persistent session, `arm=False`); roof streams its
own SafetyCounter at once with the 1003 heartbeat ticking but no pre-arm delay
(`device.actuate_roof`, as the app does, #235)."""

from __future__ import annotations

import calendar
import datetime

from . import overrides, protocol, semantics

LIGHT_ON, LIGHT_OFF = 0, 1  # camping lights inverted (app K0 writes (!on)?1:0). VERIFY live.
SENTINEL = 3  # 2-bit "leave unchanged" (sg.a default)


_ON = ("on", "true", "1")
_OFF = ("off", "false", "0")


def is_onoff(value) -> bool:
    """Whether ``value`` spells on or off (``on/true/1`` / ``off/false/0``, case- and space-insensitive)."""
    return str(value).strip().lower() in _ON + _OFF


def _truthy(value) -> bool:
    """``True`` for on/true/1, ``False`` for off/false/0.

    :raises CommandError: anything else (ruling R3, 2026-10-06: ``null``/``""``/``"x"`` used to read
        as OFF — a buggy caller could switch the fridge off; unknown now refuses, never defaults).
    """
    tok = str(value).strip().lower()
    if tok in _ON:
        return True
    if tok in _OFF:
        return False
    raise CommandError("%s, got %r" % (REASON_NOT_ONOFF, value))


def _int_range(value, lo, hi, label):
    """Coerce ``value`` to int and require ``lo <= v <= hi``, else a uniform ValueError.

    Replaces ~10 hand-rolled ``int(value)`` + bound-check + bespoke-``ValueError`` blocks across the
    builders (cooler level, night hour, air-heater level/runtime, roof-A/C fan/mode/temp, …) with one
    validated helper, so the operator error is consistent. Sites whose message carries extra semantics
    (lighting brightness's ``0=off, 1-10=10%..100%``) keep their own inline check.

    .. req:: Uniform int-range validation for control values
       :id: R_CONTROL_INT_RANGE
       :status: implemented
       :tags: control, validation

       Numeric control arguments shall be coerced to int and rejected with a ValueError naming the
       field and the accepted range when out of bounds, via a single shared helper.

    :param value: the operator-supplied value (str/int).
    :param lo: inclusive lower bound.
    :param hi: inclusive upper bound.
    :param label: field name for the error message (e.g. ``"cooler level"``).
    :returns: the validated int.
    """
    v = int(value)
    if not lo <= v <= hi:
        raise ValueError("%s must be %d-%d, got %r" % (label, lo, hi, value))
    return v


def _hhmm(value):
    """Parse a time-of-day into (hour, minute). Accepts ``"HH:MM"``/``"H:M"`` or a 2-item
    (hour, minute) sequence. Raises ValueError on anything out of 0-23 / 0-59."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        hh, mm = int(value[0]), int(value[1])
    else:
        parts = str(value).strip().split(":")
        if len(parts) != 2:
            raise ValueError("time must be HH:MM, got %r" % value)
        hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("time out of range (0-23:0-59), got %r" % value)
    return hh, mm


class CommandError(ValueError):
    """A command the operator worded wrongly (grammar, range, retired target). Raised by
    :func:`build` before anything is written; the web API maps it — and only it — to HTTP 400."""


def local_now() -> datetime.datetime:
    """The local wall clock the wake-up builder counts from. Tests and the recording replay
    (``tools.capture_diff``) replace this module attribute to pin "now"."""
    return datetime.datetime.now()


def next_wakeup_epoch(hour: int, minute: int, now: datetime.datetime) -> int:
    """Seconds since 1970-01-01T00:00 of the next local ``hour:minute`` after ``now``, packed as
    if UTC — the app builds a ``LocalDateTime`` and converts it with ``TimeZone.UTC``
    (``dg/h.java:778-874``). Today if still ahead, else tomorrow (an exact match is tomorrow).
    Naive calendar arithmetic, so a DST change never shifts the wall-clock time.

    :param hour: 0-23.
    :param minute: 0-59.
    :param now: naive local time.
    :returns: the 32-bit ``Timestamp`` value.
    """
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if t <= now:
        t += datetime.timedelta(days=1)
    return calendar.timegm(t.timetuple())


def _wakeup_light_value(c) -> int:
    """Pack ``colour:4 | A4 A3 A2 A1 | brightness:4 | (ramp/10)<<1 | enabled`` (``dg/h.m0``)."""
    areas = sum(1 << (a - 1) for a in c["areas"])
    return c["colour"] << 12 | areas << 8 | c["brightness"] << 4 | (c["ramp"] // 10) << 1 | int(c["enabled"])


def wakeup_request(value, last):
    """The wake-up config a ``wakeup`` command asks for: ``value`` over the latched config.

    ``value`` is one string of words: ``HH:MM`` (time), ``on``/``off`` (the app's switch), and up to
    three positionals ``areas brightness ramp`` (areas = comma list of 1-4, brightness 0-10, ramp
    0/10/20/30 min). Missing time/areas/brightness/ramp/colour come from the wake-up config latched
    in ``last`` (:func:`calictl.semantics.lighting_config`), else :data:`WAKEUP_DEFAULT`. The
    enabled switch is inherited from the unit-reported config (off when none was reported) and
    changed only by ``on``/``off``.

    :param value: the command value.
    :param last: the decoded lighting state (may carry latch keys or be a Mode-20 frame).
    :returns: ``{"hour", "minute", "colour", "areas", "brightness", "ramp", "enabled"}``.
    :raises ValueError: malformed value, or ``on``/``off`` with no time known.
    """
    cur = semantics.wakeup_config(semantics.lighting_config(None, last or {}))
    tokens = str("" if value is None else value).split()
    if not tokens:
        raise ValueError("wakeup needs HH:MM and/or on|off")
    c = {k: v for k, v in (cur or WAKEUP_DEFAULT).items() if k != "time"}
    # An edit carries the enabled state the UNIT last reported (the app passes the whole current
    # config to dg/h.m0); only on/off changes it. Nothing reported -> off (the recorded 0x1100).
    c["enabled"] = bool(cur and cur["enabled"])
    pos, timed = [], False
    for tok in tokens:
        if tok.lower() in ("on", "off"):
            c["enabled"] = tok.lower() == "on"
        elif ":" in tok:
            c["hour"], c["minute"] = _hhmm(tok)
            timed = True
        else:
            pos.append(tok)
    if cur is None and not timed:
        raise ValueError("wake-up time not known yet (no wake-up frame seen): give it, e.g. wakeup 07:00 on")
    if len(pos) > 3:
        raise ValueError("wakeup takes HH:MM [areas] [brightness] [ramp] [on|off], got %r" % value)
    if pos:
        c["areas"] = sorted({_int_range(a, 1, 4, "wake-up area") for a in pos[0].split(",")})
    if len(pos) > 1:
        c["brightness"] = _int_range(pos[1], 0, 10, "wake-up brightness")
    if len(pos) > 2:
        c["ramp"] = int(pos[2])
        if c["ramp"] not in WAKEUP_RAMPS_MIN:
            raise ValueError("wake-up ramp must be one of %s min, got %r" % (WAKEUP_RAMPS_MIN, pos[2]))
    return c


def camping_values(**changes) -> dict:
    """Only the changed field is set; every other camping field stays at the
    leave-unchanged sentinel 3 (the lights are inverted, so carrying a state
    value would be wrong; 3 = no-op per the app's control model)."""
    vals = {"State": SENTINEL, "UsbCharger": SENTINEL, "InteriorLight": SENTINEL, "OutsideLight": SENTINEL}
    vals.update(changes)
    return vals


def _camping(funcs, what, value, last):
    # Matches the app's camping screen: ONE combined "lights" toggle (tf/a K0 writes
    # 0 to BOTH light fields for ON, inverted), USB (B2) + master (z2) normal.
    # lights/usb are only effective while master (camping mode) is on.
    on = _truthy(value)  # strips whitespace; "on " must not read as OFF
    if what == "lights":  # combined interior+outside, inverted
        v = LIGHT_ON if on else LIGHT_OFF
        ch = {"InteriorLight": v, "OutsideLight": v}
    elif what == "usb":  # rear USB ports, normal
        ch = {"UsbCharger": 1 if on else 0}
    elif what == "master":  # camping mode on/off, normal
        ch = {"State": 1 if on else 0}
    else:
        return None
    return protocol.encode(funcs["campingmode"], camping_values(**ch), frame_bytes=1)


# energy (char 1601, pg/a.java f() DEFAULT/energy branch) — set the power-management mode.
# The frame is 1 byte: EnergyModeSet @2/w2 carries the mode; DisplayRefresh @7/w1 stays 0 (the
# app's energy-VM mode setter, xf/d.java:389, only stages EnergyModeSet and writes DIRECT —
# a one-shot actuate, no commit/preamble). Mode ordinals match the read side (semantics.energy
# `energy_mode`, enum bf/c.java). DECOMPILE-DERIVED, not yet wire-captured / live-verified.
ENERGY_MODES = {"normal": 0, "max_charge": 1, "eco": 2}
ENERGY_MODE_UNCHANGED = 3  # 2-bit leave-unchanged sentinel (pg/a v())


def _energy(funcs, what, value, last):
    """Build the energy (char 1601) control frame. ``what`` == ``mode``; ``value`` is one of
    ``normal`` / ``max_charge`` / ``eco`` (case-insensitive). Returns None for anything else.

    Decompile-derived from the app's own energy setter (``xf/d.java:389`` stages
    ``EnergyModeSet`` = the mode ordinal, ``DisplayRefresh`` stays 0) and the shared
    ``EnergyMode`` enum (``bf/c.java``); NOT yet verified on the wire or on-device."""
    if what != "mode":
        return None
    key = str(value).strip().lower()
    if key not in ENERGY_MODES:
        return None
    vals = {"EnergyModeSet": ENERGY_MODES[key], "DisplayRefresh": 0}
    return protocol.encode(funcs["energy"], vals, frame_bytes=overrides.CONTROL_FRAME_BYTES["energy"])


def _cooler_neutral(funcs) -> dict:
    """The app's cooler frame for every field it does not target: each control field at its
    dictionary default — 2-bit 3, Level/Mode 7, TimerHour/Min 30/62, NightTimerHourOn/Off 31 — i.e.
    ``ff771e3e1f1f``, the app's neutral frame (APP-RECORDED, ``tests/vectors/app/cooler.jsonl``: power
    on ``fd771e3e1f1f``, level 5 ``ff751e3e1f1f``, timer start ``f7771e3e1f1f`` …). Ruling R1:
    calictl follows the app; the unit treats each default as leave-unchanged."""
    return {cf.name: cf.default for cf in funcs["cooler"].control_fields}


def _cooler_values(state: dict, **changes) -> dict:
    """Full-packet cooler control values that carry the CURRENT state: State/Mode/Level and the
    schedule (writing the current schedule back = no change), timer ACTION fields at no-op, then
    apply `changes`. Used only for ``night_on``/``night_off`` — no app recording shows what the app
    sends in the other schedule bytes there, and this exact carry is what was DEVICE-verified
    2026-08-26 (every other cooler command sends :func:`_cooler_neutral`, ruling R1)."""
    vals = dict(
        State=state.get("State", 1),
        Mode=state.get("Mode", 4),
        Level=state.get("Level", 3),
        # Timer ACTIONS stay at their no-op sentinels; the night-schedule VALUES must carry the
        # CURRENT state. LIVE-VERIFIED 2026-08-26 (issue #99 write test): the unit takes the hour
        # bytes LITERALLY — a night_off write that carried NightTimerHourOn=0 clobbered a just-set
        # quiet_from 22 back to 0 (1102 push: "quiet_from 22->0"). The old hard-coded zeros came
        # from the app's power-on capture, but that van had NO schedule set — 0 simply WAS the
        # current value there, so the capture couldn't distinguish "send 0" from "send current".
        # NightTimerSet is carried for the same reason (a literal 0 would disarm a set schedule).
        TimerStart=3,
        TimerCancel=3,
        NightTimerSet=state.get("NightTimerSet", 0),
        NightTimerHourOn=state.get("NightTimerHourOn", 0),
        NightTimerHourOff=state.get("NightTimerHourOff", 0),
        TimerHour=state.get("TimerHourSet", 0),
        TimerMin=state.get("TimerMinSet", 0),
    )
    vals.update(changes)
    return vals


# Cooler Mode enum (vf/c.java: readback J0()=Mode==2, P1()=Mode==4): 0=normal (quiet off),
# 2=manual quiet, 4=timer-based quiet. Staged by vf/c.f(this, <n>) via T1(0)/x0(2)/k0(4).
COOLER_MODES = {"normal": 0, "quiet": 2, "timer_quiet": 4}


def _cooler(funcs, what, value, last):
    # `power` on/off flips State (encode validates State in {0,1}); `level` sets the cooling
    # intensity 1-5; `mode` sets the quiet Mode enum; the timer/night branches arm the cooler's
    # scheduling (all decompile-verified from vf/c.java, NOT yet live-verified). Every untargeted
    # field rides at the app's leave-unchanged value (_cooler_neutral, byte-identical to the app's
    # recorded frames); only night_on/night_off still carry the current state (_cooler_values).
    if what == "power":
        ch = {"State": 1 if _truthy(value) else 0}
    elif what == "level":
        lvl = _int_range(value, 1, 5, "cooler level")
        ch = {"Level": lvl}
    elif what == "mode":  # quiet mode (vf/c.java T1/x0/k0 -> Mode 0/2/4)
        key = str(value).strip().lower()
        if key not in COOLER_MODES:
            return None
        ch = {"Mode": COOLER_MODES[key]}
    elif what == "timer_set":  # start-at TimerHour:TimerMin (vf/c.java:635 y0())
        hh, mm = _hhmm(value)
        ch = {"TimerHour": hh, "TimerMin": mm}
    elif what == "timer_start":  # arm cooling-start timer (vf/c.java:193 D())
        ch = {"TimerStart": 1}
    elif what == "timer_cancel":  # cancel it (vf/c.java:251 X0())
        ch = {"TimerCancel": 1}
    elif what in ("night_on", "night_off"):  # quiet-schedule hours (vf/c.java c0()/Y2()), 0-23
        hr = _int_range(value, 0, 23, "night timer hour")
        ch = {"NightTimerHourOn" if what == "night_on" else "NightTimerHourOff": hr}
        if not last:  # R4: the daemon/CLI gate (command_precondition) refuses first; keep the builder honest
            raise CommandError(REASON_COOLER_STATE_UNKNOWN)
        return protocol.encode(
            funcs["cooler"], _cooler_values(last, **ch), frame_bytes=overrides.CONTROL_FRAME_BYTES["cooler"]
        )
    # NB: to ARM scheduled ("Automatisch") quiet, use `mode timer_quiet` (Mode=4) — that is the app's
    # own path (the "Automatischer Flüstermodus" toggle stages Mode). There is deliberately NO
    # `night_set` command: the app NEVER writes the cooler NightTimerSet bit (verified 2026-08-26 —
    # the only writer of that shared frame slot is the AIR-HEATER VM rf/b.H3; on the cooler it's
    # read-only/unit-driven). An earlier night_set here rested on a mis-citation ("vf/c.X1") that is
    # actually setCoolingLevel — retracted rather than ship an off-protocol write.
    else:
        return None
    return protocol.encode(
        funcs["cooler"], {**_cooler_neutral(funcs), **ch}, frame_bytes=overrides.CONTROL_FRAME_BYTES["cooler"]
    )


# dg/n.java Mode enum (full, verified 2026-08-17): 0=NO_MODE, 4=SET_BRIGHTNESS, 6=SET_COLOR,
# 8=SET_DOUBLE, 12=REQUEST_CONFIG, 16=SET_PROFILE, 20=WAKEUP_TIME, 24=SYSTEM_TIME, 28=PREVIEW.
LIGHT_MODE_SET_BRIGHTNESS = 4
LIGHT_MODE_SET_COLOR = 6  # recolour the active profile: LightValue = palette index (1-10)
LIGHT_MODE_SET_PROFILE = 16  # switch active profile (payload carries the ProfileNumber)
LIGHT_MODE_REQUEST_CONFIG = 12  # d0(): the app's config pull; the reply carries the favourite bits
LIGHT_MODE_WAKEUP_TIME = 20  # m0(): wake-up light (Timestamp + packed LightValue)
LIGHT_PROFILE_DOOR_CONTACT = 8  # n4(): SET_PROFILE PN 8, LightValue 1/0 = sliding-door light on/off
WAKEUP_RAMPS_MIN = (0, 10, 20, 30)  # dg/k: WAKE_UP_{ON,OFF}[_10|_20|_30]
# The app's wake-up defaults when the unit has reported none (recorded 0x1100: warm white, area 1,
# brightness 0, no ramp, off) — its time picker sends exactly these with the chosen time.
WAKEUP_DEFAULT = {
    "hour": 0,
    "minute": 0,
    "colour": 1,
    "areas": [1],
    "brightness": 0,
    "ramp": 0,
    "enabled": False,
}
# Profile-number enum (dg/l.java mirrors ef/k.java): 0=LIGHTS_OFF, 1-7=FAVORITE1-7, 8=DOOR_CONTACT,
# 9=LIVE_VIEW (the per-zone-edit profile SET_BRIGHTNESS hardcodes), 10=WAKEUP_LIGHT,
# 11=INTERIOR_LIGHT, 12=LIGHTS_ON, 13=DEFAULT, 14=INIT sentinel.
LIGHT_PROFILE_ALL_ON = 12  # LIGHTS_ON  — the app's "Alle Lichter" master ON  (dg/h.java:323 Q())
LIGHT_PROFILE_ALL_OFF = 0  # LIGHTS_OFF — the app's "Alle Lichter" master OFF
# Colour palette (dg/j.java, decompile 2026-07-12): SET_COLOR carries ONE index in LightValue for
# the whole target profile — not RGB. Used by `save_profile N <colour>` (SET_COLOR preface) and the
# wake-up colour nibble. On-device apply is UNVERIFIED (colour UI not shown on this model).
LIGHT_COLORS = {
    "warm-white": 1,
    "blood-orange": 2,
    "amber": 3,
    "pistachio": 4,
    "peppermint": 5,
    "mint": 6,
    "azure": 7,
    "dark-blue": 8,
    "red": 9,
    "salmon": 10,
}
# Per-zone "leave unchanged" sentinel — CRACKED via HCI capture 2026-07-08: the app fills every
# zone it is NOT changing with 14 (0xe), not 0. 0 means "set this zone to 0"; 14 = "no change".
LIGHT_UNCHANGED = 14
# Brightness is the dg/i.java enum, NOT a raw 0-13 scale: 0=OFF, 1-10 = 10%..100% in 10% steps,
# 11=DEFAULT, 12 unused, 13=NOT_EQUIPPED (read-only marker for absent zones — writing it is
# garbage; we did exactly that for "power on" until the 2026-08-16 capture).
LIGHT_ON_BRIGHTNESS = 10  # "on" = 100% (enum PERCENTAGE_100)
LIGHT_MAX_SET = 11  # highest settable value (11 = DEFAULT brightness)
# Every SET_BRIGHTNESS frame the app sends hardcodes ProfileNumber=9 (LIVE_VIEW, the "live editing"
# profile) — `w(9, PENDING)` in dg/h.java:170-264 — NOT the currently-active profile. Echoing the
# live ProfileNumber instead (0 when the lights are off) is why our SET_BRIGHTNESS was ignored until
# a profile was activated; the real gate was always "PN=9 in the frame", not "a profile is active".
LIGHT_BRIGHTNESS_PROFILE = 9

# Lighting neutral FLUSH frame (HCI-seen 2026-07-13 after the app's SET_BRIGHTNESS / SET_PROFILE).
# NOT an app "commit": it is the builder's default frame — Mode 0 = NO_MODE, ProfileNumber + all
# zones = the 14 "unchanged" sentinel. The app self-flushes by streaming frames (~500 ms); calictl
# writes once, so it sends this neutral frame to flush its single SET. The real actuation gate is
# the unit's wake state (photon-verified 2026-08-16); readback is a write-through echo, never proof.
# Sent as the `follow` frame of device.actuate for every lighting write.
LIGHT_COMMIT = bytes.fromhex("0e00000000000000eeeeeeeeeeeeeeee")
# The app's REQUEST_CONFIG (Mode 12, PN 13; dg/h d0): the unit answers with its lighting configuration
# (favourite bits, wake-up, door contact) as 1502 frames. Only the daemon sends it, to learn the
# wake-up config before an edit (:data:`WAKEUP_UNKNOWN`).
LIGHT_REQUEST_CONFIG = bytes.fromhex("0d0c000000000000eeeeeeeeeeeeeeee")
WAKEUP_UNKNOWN = "wake-up config not known yet (the unit has not reported it): give on|off with the edit"

# Friendly zone key -> BrightnessL control field. Two provenance tiers:
#   CONFIRMED by the 2026-07-08 HCI capture (which nibble tracked each dragged slider):
#     reading trio L2/L1/L4 (Links/Rechts/Beifahrer — group confirmed, the split is best-effort),
#     "kitchen" = L7 (the Küche *Kochen*/cooking light), "roof-ambient" = L8, "outside-rear" = L3.
#   DEVICE-confirmed (2026-08-30 single-light isolation + owner-watched writes, evidence-ledger.md):
#     L5 = Küche Ambient, L6 = Küche Schrank, L9 = Dach Lesen, L12 = Eingang. The full real set is
#     semantics._REAL_LIGHT_ZONES = {1..9, 12}; the older "L5/L6 by elimination" inference is superseded.
LIGHT_ZONES = {
    "reading-1": "BrightnessLTwo",
    "reading-2": "BrightnessLOne",
    "reading-3": "BrightnessLFour",
    "kitchen": "BrightnessLSeven",
    "roof-ambient": "BrightnessLEight",
    "outside-rear": "BrightnessLThree",
    "kitchen-ambient": "BrightnessLFive",  # Küche Ambientelicht (capture 2026-08-16 + isolation)
    "kitchen-cabinet": "BrightnessLSix",  # Küche Schrank — DEVICE 2026-08-30 (owner-watched write
    #   lit the cabinet; matches decompile L6=KITCHEN_CABINETS/case16). Was mislabelled "roof-reading".
    "roof-reading": "BrightnessLNine",  # Dach Lesen (L9) — corrected: decompile case19
    #   ROOF_READING->BrightnessLNine, and L9 isolation lit the roof reading lamp 2026-08-30.
    "entrance": "BrightnessLOneTwo",  # Eingang (L12) — DEVICE 2026-08-30 isolation.
}


# --- physical preconditions for a write -------------------------------------
# Some actions are refused by the hardware/UI unless another subsystem is in a given state, so a
# frame the unit ACKs still does nothing. Refuse-with-reason beats a silent no-op. Two known gates
# (owner-confirmed 2026-08-30): the pop-top roof reading light (L9) is unpowered while the roof is
# down; the cooler cooling-timer can only be set while the fridge is OFF.
_ROOF_CLOSED_POSITIONS = (0, 14)  # matches semantics._ROOF_POS closed values
AIRHEATER_MAX_RUNTIME_MIN = 120  # the app's cap on immediate heating (its heating info page)
# OperationModeAirHeater is the departure-timer arm (app-observed 2026-09-16, tools/applab):
# 7 = the app's leave-unchanged sentinel in every frame that doesn't target it, 3 = timer armed
# (rf/b.java a2()), 0 = cancelled (j4()). OperationModeCombined names the device the timer drives
# (df/b.java enum, ordinal+1): 1 = AIR_HEATER on this van.
AIRHEATER_MODE_UNCHANGED = 7
AIRHEATER_MODE_TIMER_ARMED = 3
AIRHEATER_MODE_IDLE = 0
AIRHEATER_COMBINED_AIR_HEATER = 1
# The app's leave-unchanged sentinels for the wide heater fields (its `v()` defaults, all outside
# each field's valid range; seen in every observed heater frame, e.g. neutral `3f7b007f1f3f`).
AIRHEATER_LEVEL_UNCHANGED = 11
AIRHEATER_RUNTIME_UNCHANGED = 127
AIRHEATER_TIMER_HOUR_UNCHANGED = 31
AIRHEATER_TIMER_MIN_UNCHANGED = 63
# Terminal Position for each move direction (semantics._ROOF_POS: 1=open, 0/14=closed). When a move
# reaches its target limit, device.actuate_roof ceases the counter stream (app-faithful auto-stop) —
# best-effort courtesy on top of the unit's own limit switches; None = no known limit (don't poll).
_ROOF_LIMIT_POSITIONS = {"open": frozenset({1}), "close": frozenset(_ROOF_CLOSED_POSITIONS)}
# Alerts (semantics.roof()["alert"]) under which the app refuses a roof MOVE. Must stay in step with
# `ROOF_MOVE_BLOCK` in webui/app.js — the GUI greys open/close on exactly these. `sensor_error` is
# deliberately NOT here (the app still allows a move with it); "stop" is never blocked by anything.
ROOF_MOVE_BLOCK = frozenset(
    {
        "child_lock",
        "error",
        "driving",
        "emergency_locked",
        "not_possible",
        "low_battery",
        "in_use",
        "not_stationary",
    }
)


def roof_limit_positions(direction):
    """The set of terminal roof ``Position`` values for a move ``direction`` (open/close), or
    ``None`` for any other direction. Used to auto-stop travel when the roof reaches its limit."""
    return _ROOF_LIMIT_POSITIONS.get(direction)


# The gate texts (command_precondition), as constants: tools/gen_c_dict.py emits every REASON_* into
# csrc/control_consts.h, so the ESP's C twin refuses with the same words (ruling B: same reason texts).
REASON_NOT_ONOFF = (
    "the value must be on or off (or true/false, 1/0) — anything else is refused, never read as off"
)
REASON_COOLER_STATE_UNKNOWN = (
    "the cooler's current schedule is not known yet (no cooler state read) — refusing to overwrite it"
)
REASON_ROOF_READING = "the pop-top roof reading light needs the roof raised (roof is closed)"
REASON_COOLER_TIMER_NEEDS_FRIDGE_OFF = (
    "the cooling timer can only be set while the fridge is off (turn the cooler off first)"
)
REASON_QUIET_NEEDS_FRIDGE_ON = (
    "quiet mode can only be set while the fridge is on (switch the cooler on first)"
)
REASON_CAMPING_NEEDS_MASTER = "camping lights and USB need camping mode on (turn the camping master on first)"
REASON_ENERGY_LOCKED = "the unit currently does not allow changing the energy mode"
REASON_FAVOURITE_EMPTY = "this favourite is empty on the unit — save it first"
REASON_WAKEUP_NO_AREA = "the wake-up light needs at least one vehicle area"
# Every (function, what) whose value is an on/off token (ruling R3: anything else is refused up
# front, with REASON_NOT_ONOFF, before a builder could read it as OFF).
ONOFF_COMMANDS = frozenset(
    {
        ("cooler", "power"),
        ("campingmode", "master"),
        ("campingmode", "lights"),
        ("campingmode", "usb"),
        ("airheater", "power"),
        ("lighting", "power"),
        ("lighting", "door_contact"),
        ("roofaircondition", "power"),
        ("livingroomheater", "air"),
        ("livingroomheater", "water"),
    }
)


def command_precondition(function, what, value, states):
    """Return a human reason to refuse a control write, or ``None`` to allow it.

    :param function: target function (``"lighting"``, ``"cooler"``, ...).
    :param what: the targeted control key.
    :param value: requested value (context-dependent).
    :param states: mapping function-name -> DECODED state (e.g. ``serve._last`` or a fresh decode).
    :returns: a reason string when the write should be refused, else ``None``. Only blocks when the
        gating state is POSITIVELY wrong; unknown/absent state allows the write (can't prove it's
        blocked, and the write is otherwise harmless) — except where a default would be WRITTEN
        (:data:`REASON_NOT_ONOFF`, :data:`REASON_COOLER_STATE_UNKNOWN`).

    .. req:: An unknown value or unknown state refuses, never defaults
       :id: R_ONOFF_STRICT
       :status: implemented
       :tags: control, safety

       A command whose value is an on/off token shall accept only ``on/off``, ``true/false``,
       ``1/0`` (case- and space-insensitive) and refuse anything else (``null``, ``""``, ``"x"``)
       with :data:`REASON_NOT_ONOFF` — never read it as OFF (ruling R3). The cooler ``night_on`` /
       ``night_off`` edit, which carries the unit's current schedule, shall be refused with
       :data:`REASON_COOLER_STATE_UNKNOWN` while no cooler state is known — never send the
       default-filled frame (ruling R4). Both texts are emitted into ``control_consts.h`` for the
       ESP's C twin.
    """
    if (function, what) in ONOFF_COMMANDS and not is_onoff(value):
        return REASON_NOT_ONOFF
    if function == "lighting" and what == "roof-reading":
        try:
            on = int(value) > 0  # brightness 0-11; only an ON write is gated
        except (TypeError, ValueError):
            on = False
        pos = (states.get("roof") or {}).get("Position")
        if on and pos in _ROOF_CLOSED_POSITIONS:
            return REASON_ROOF_READING
    if function == "cooler" and what in ("timer_set", "timer_start"):
        if (states.get("cooler") or {}).get("State") == 1:  # fridge currently ON
            return REASON_COOLER_TIMER_NEEDS_FRIDGE_OFF
    # The mirror of the above: the quiet mode and its schedule are only settable while the fridge is
    # ON (the app greys those rows when it is off — APP-OBSERVED, `evidence-ledger.md`). Without this
    # the CLI/API/HA paths could send what the app never sends; the web UI already greys them.
    if function == "cooler" and what in ("mode", "night_on", "night_off"):
        if (states.get("cooler") or {}).get("State") == 0:  # fridge currently OFF
            return REASON_QUIET_NEEDS_FRIDGE_ON
    # night_on/night_off carry the CURRENT schedule (the hour bytes are literal, 2026-08-26 #99); with
    # no cooler state known the carry would be the default-filled frame that clobbers it. Ruling R4
    # (2026-10-06): unknown -> refuse, never default (same rule as the wake-up edit, R5).
    if function == "cooler" and what in ("night_on", "night_off"):
        if not states.get("cooler"):
            return REASON_COOLER_STATE_UNKNOWN
    # Camping lights + rear USB are only actionable while the camping master is ON: the rear USB is
    # physically dead without it (issue #111) and the light bits read back meaningless.
    if function == "campingmode" and what in ("lights", "usb"):
        if (states.get("campingmode") or {}).get("State") == 0:
            return REASON_CAMPING_NEEDS_MASTER
    # The unit can lock the energy-mode selector (EnergyModeNotSelectable); honour it off-UI too.
    if function == "energy" and what == "mode":
        if (states.get("energy") or {}).get("EnergyModeNotSelectable") == 1:
            return REASON_ENERGY_LOCKED
    # Roof MOVES (never "stop" — a stop must always get through; it is the safety action and the web
    # UI never greys it either) are refused under the same alert set the GUI blocks on
    # (`ROOF_MOVE_BLOCK` in webui/app.js) plus a Position the unit reports as `error`. The unit
    # enforces this itself (it withholds the motor), so this is a courtesy refusal that keeps the
    # off-UI paths honest — it is NOT the safety mechanism.
    if function == "roof" and what in ("open", "close"):
        roof = semantics.roof(states.get("roof") or {})
        if roof["alert"] in ROOF_MOVE_BLOCK:
            return "the unit reports the roof as %s — move refused" % roof["alert"].replace("_", " ")
        if roof["position_name"] == "error":
            return "the unit reports a roof position error — move refused"
    # Favourites: refuse only a slot the unit positively reported empty (the REQUEST_CONFIG reply's
    # FavoriteProfileModifiedState bits, latched by serve). Unknown bits allow (serve only sends
    # REQUEST_CONFIG before a wake-up edit, R5, so they are often unknown). The mock ACKs and ignores
    # an empty one.
    if function == "lighting" and what == "profile":
        stored = semantics.lighting_config(None, states.get("lighting") or {}).get("FavouritesStored")
        try:
            n = int(value)
        except (TypeError, ValueError):
            n = None
        if stored is not None and n is not None and 1 <= n <= 7 and not stored >> (n - 1) & 1:
            return REASON_FAVOURITE_EMPTY
    # Wake-up: the app's "no area chosen" dialog — enabling with no vehicle area is refused.
    if function == "lighting" and what == "wakeup":
        try:
            c = wakeup_request(value, states.get("lighting"))
        except ValueError:
            return None  # malformed: the builder reports it
        if c["enabled"] and not c["areas"]:
            return REASON_WAKEUP_NO_AREA
        # Ruling R5: an edit (no on/off) carries the enabled state the UNIT reported; with none
        # reported it would silently disarm, so it is refused. The daemon first pulls the config with
        # REQUEST_CONFIG (serve.on_command); the CLI has no latch, so it needs an explicit on|off.
        toks = str(value).lower().split()
        if "on" not in toks and "off" not in toks:
            if semantics.wakeup_config(semantics.lighting_config(None, states.get("lighting") or {})) is None:
                return WAKEUP_UNKNOWN
    # door_contact: deliberately NOT gated on vehicle.CarVariant — the app gates the sliding-door
    # page on its onboarding model, not on BLE, and this T7 reads CarVariant=4 (feature-availability.md).
    return None


# Back-compat alias (older callers used the lighting-only name).
def lighting_precondition(what, value, states):
    return command_precondition("lighting", what, value, states)


def _all_real_zones(zone_fields, b):
    """Value ``b`` for the REAL lamp zones (``semantics._REAL_LIGHT_ZONES`` = L1-L9 + L12), the
    unchanged sentinel elsewhere.

    ``power``/``all`` used to write every one of the 16 control zones — including the
    never-equipped ones (L10/L11, L13-L16), which only coincidentally looked right while "on" was
    the 13 NOT_EQUIPPED marker. The app's "Alle Lichter" frame is uncaptured, so stay
    conservative: never touch phantom zones.
    """
    from .semantics import _LZONES, _REAL_LIGHT_ZONES  # stdlib-only sibling; lazy to match style

    real = {"BrightnessL" + suf for suf, num in _LZONES.items() if num in _REAL_LIGHT_ZONES}
    return {z: (b if z in real else LIGHT_UNCHANGED) for z in zone_fields}


def _zone_fields(f):
    return [cf.name for cf in f.control_fields if cf.name.startswith("BrightnessL")]


def _saved_zones(zone_fields, last):
    """Every equipped (real) zone at its current level from ``last``; anything else 14 —
    the zones of the app's favourite save (``dg/h.l3`` step b)."""
    from .semantics import _LZONES, _REAL_LIGHT_ZONES  # stdlib-only sibling; lazy to match style

    real = {"BrightnessL" + suf for suf, num in _LZONES.items() if num in _REAL_LIGHT_ZONES}
    st = last or {}
    return {
        z: (
            st[z]
            if z in real and isinstance(st.get(z), int) and 0 <= st[z] <= LIGHT_MAX_SET
            else LIGHT_UNCHANGED
        )
        for z in zone_fields
    }


def _save_profile_args(value):
    """``"N"`` or ``"N <colour>"`` -> ``(N, palette index or None)``."""
    tokens = str(value).split()
    if not 1 <= len(tokens) <= 2:
        raise ValueError("save_profile takes N [colour], got %r" % value)
    n = _int_range(tokens[0], 1, 7, "save_profile favorite")
    if len(tokens) == 1:
        return n, None
    idx = LIGHT_COLORS.get(tokens[1].lower().replace("_", "-"))
    if idx is None:
        raise ValueError("unknown light colour %r; one of: %s" % (tokens[1], ", ".join(sorted(LIGHT_COLORS))))
    return n, idx


def _lighting(funcs, what, value, last):
    """Build a lighting control frame (char 1501). CRACKED via HCI capture 2026-07-08.

    The app's SET_BRIGHTNESS changes ONE zone at a time: the target zone carries the new
    brightness, every OTHER zone carries the leave-unchanged sentinel ``14`` (NOT 0 — 0 sets
    that zone to 0). ``ProfileNumber`` is hardcoded to ``9`` like the app (see
    ``LIGHT_BRIGHTNESS_PROFILE``), so a write applies with the lights off too. Verified byte-for-byte
    against the app (e.g. Kitchen=5 -> ``0904000000000000eeeeeee5eeeeeeee``).

    Grammar (``what``):
      * a zone key (``LIGHT_ZONES``) or a raw ``BrightnessL*`` field  -> SET_BRIGHTNESS that zone
        to ``value`` (0-11: 0=off, 1-10=10%..100%, 11=default); all other zones = 14 (unchanged).
      * ``"power"`` -> app-faithful master toggle: SET_PROFILE selecting LIGHTS_ON (12) / LIGHTS_OFF
        (0), like the app's "Alle Lichter" switch (dg/h.java:323 ``Q()``) — restores the saved
        on-state, not a forced 100%.
      * ``"all"`` -> set every REAL zone (``semantics._REAL_LIGHT_ZONES``: L1-L9 + L12) to
        ``value`` (a calictl convenience, not an app action); never-equipped zones always get the
        unchanged sentinel.
      * ``"wakeup"`` -> the wake-up light (Mode 20); ``value`` = :func:`wakeup_request` words.
      * ``"profile"`` -> SET_PROFILE (Mode 16); ``value`` = favourite 0-13 (not 8).
      * ``"door_contact"`` -> SET_PROFILE PN 8, LightValue 1/0 (``on``/``off``).
      * ``"save_profile"`` -> ``value`` = ``N`` or ``N <colour>`` (colour via :func:`preface_for`).

    .. req:: Build the app's door-contact frame
       :id: R_LIGHT_DOOR_CONTACT
       :status: implemented
       :tags: control, lighting

       ``door_contact on|off`` shall build ``dg/h.n4``: Mode 16, ProfileNumber 8, LightValue 1/0,
       zones unchanged.

    .. req:: Save and activate favourites like the app
       :id: R_LIGHT_FAVOURITE
       :status: implemented
       :tags: control, lighting

       ``profile N`` shall build one SET_PROFILE frame (``dg/h.u0``); ``save_profile N`` the
       recorded SET_BRIGHTNESS with ProfileNumber N and every equipped zone at its level
       (``dg/h.l3``), preceded by a SET_COLOR frame (Mode 6, PN N) when a colour is given. The
       standalone ``color`` command is retired.

    .. req:: Build the app's wake-up light frame
       :id: R_LIGHT_WAKEUP
       :status: implemented
       :tags: control, lighting

       ``wakeup`` shall build the app's Mode-20 frame (``dg/h.m0``): ProfileNumber 14,
       Timestamp = the next local HH:MM packed as UTC, LightValue = colour, areas, brightness and
       ``(ramp/10)<<1 | enabled``, zones unchanged — byte-identical to the app recording.
    """
    f = funcs["lighting"]
    zone_fields = _zone_fields(f)
    # SET_BRIGHTNESS/power/all hardcode ProfileNumber=9 like the app (writes land even with the
    # lights off / PN=0). SET_PROFILE overrides it with the target; SET_COLOR recolours that same 9.
    base = {
        "ProfileNumber": LIGHT_BRIGHTNESS_PROFILE,
        "Mode": LIGHT_MODE_SET_BRIGHTNESS,
        "Timestamp": 0,
        "LightValue": 0,
    }

    def _b(v):
        # settable brightness is the dg/i enum 0-11 (0=off, 1-10=10%..100%, 11=default);
        # 12 is unused, 13=NOT_EQUIPPED and 14=unchanged are markers, never valid to set.
        b = int(v)
        if not 0 <= b <= LIGHT_MAX_SET:
            raise ValueError(
                "lighting brightness must be 0-%d (0=off, 1-10=10%%..100%%, "
                "11=default), got %r" % (LIGHT_MAX_SET, v)
            )
        return b

    if what == "profile":
        # dg/h.u0: one SET_PROFILE frame, ProfileNumber = the target (FAVORITE_n = n).
        n = _int_range(value, 0, 13, "lighting profile")
        if n == LIGHT_PROFILE_DOOR_CONTACT:
            raise ValueError("profile 8 is the door-contact frame; use door_contact on|off")
        vals = {
            **base,
            "Mode": LIGHT_MODE_SET_PROFILE,
            "ProfileNumber": n,
            **{z: LIGHT_UNCHANGED for z in zone_fields},
        }
    elif what == "save_profile":
        # dg/h.l3 step (b): SET_BRIGHTNESS with ProfileNumber = the favourite N (NOT the live-view 9),
        # every equipped zone at its current level. APP-RECORDED (lighting-profile.jsonl). A colour
        # goes out FIRST as its own SET_COLOR frame (preface_for).
        n, _ = _save_profile_args(value)
        vals = {
            **base,
            "Mode": LIGHT_MODE_SET_BRIGHTNESS,
            "ProfileNumber": n,
            **_saved_zones(zone_fields, last),
        }
    elif what == "door_contact":
        # dg/h.n4: SET_PROFILE + ProfileNumber 8 (DOOR_CONTACT) staged, LightValue on?1:0 sent.
        vals = {
            **base,
            "Mode": LIGHT_MODE_SET_PROFILE,
            "ProfileNumber": LIGHT_PROFILE_DOOR_CONTACT,
            "LightValue": 1 if _truthy(value) else 0,
            **{z: LIGHT_UNCHANGED for z in zone_fields},
        }
    elif what == "wakeup":
        # dg/h.m0: Mode 20; ProfileNumber is not staged, so it keeps the buffer's 14 (recorded).
        c = wakeup_request(value, last)
        vals = {
            **base,
            "ProfileNumber": LIGHT_UNCHANGED,
            "Mode": LIGHT_MODE_WAKEUP_TIME,
            "Timestamp": next_wakeup_epoch(c["hour"], c["minute"], local_now()),
            "LightValue": _wakeup_light_value(c),
            **{z: LIGHT_UNCHANGED for z in zone_fields},
        }
    elif what == "power":
        # app-faithful master toggle (dg/h.java:323-337 Q()): a SET_PROFILE selecting LIGHTS_ON
        # (12) / LIGHTS_OFF (0), NOT per-zone brightness. Matches how the app's "Alle Lichter"
        # switch works, so it restores the user's saved on-state rather than forcing every lamp
        # to 100%. Zones carry the unchanged sentinel like every other profile-select frame.
        pn = LIGHT_PROFILE_ALL_ON if _truthy(value) else LIGHT_PROFILE_ALL_OFF
        vals = {
            **base,
            "Mode": LIGHT_MODE_SET_PROFILE,
            "ProfileNumber": pn,
            **{z: LIGHT_UNCHANGED for z in zone_fields},
        }
    elif what == "all":
        # not an app action (the app is per-zone) — our convenience: every REAL lamp to one level
        vals = {**base, **_all_real_zones(zone_fields, _b(value))}
    else:  # a single zone (friendly key or BrightnessL field)
        if what == "color":
            raise ValueError(
                "lighting color was retired: the app recolours a saved favourite — use save_profile N <colour>"
            )
        field = LIGHT_ZONES.get(what, what)
        if field not in zone_fields:
            raise ValueError("unknown lighting control %r" % what)
        vals = {**base, **{z: LIGHT_UNCHANGED for z in zone_fields}, field: _b(value)}
    return protocol.encode(f, vals, frame_bytes=overrides.CONTROL_FRAME_BYTES["lighting"])


def _airheater_values(state: dict, **changes) -> dict:
    """Full-packet airheater control values (char 1701, ``sf/a.java``).

    Every untargeted field is the app's own leave-unchanged sentinel (its ``v()`` model
    defaults, observed on the wire 2026-09-16 in every heater frame: 2-bit requests ``3``,
    ``AirDistribution`` 0, ``OperationModeAirHeater`` 7, ``HeatingLevel`` 11,
    ``OperationModeCombined`` 0, ``RunningTime`` 127, ``TimerHour`` 31, ``TimerMin`` 63);
    then ``changes`` is applied. Nothing is carried from ``state``: the unit ignores the
    sentinels, so calictl's frames equal the app's byte-for-byte — and carrying was WRONG
    twice over: a readback ``HeatingLevel`` outside the 1-11 set (the mock's idle seed reports
    0) made the encoder refuse the whole write, and re-sending the state's
    ``OperationModeAirHeater=0`` would cancel an armed departure timer on an unrelated write.

    :param state: kept for the builder signature; unused.
    """
    del state
    vals = dict(
        NormalOperationRequest=SENTINEL,
        PermanentOperationRequest=SENTINEL,
        PermanentOperationConfirmation=SENTINEL,
        AirDistribution=0,
        OperationModeAirHeater=AIRHEATER_MODE_UNCHANGED,
        HeatingLevel=AIRHEATER_LEVEL_UNCHANGED,
        OperationModeCombined=0,
        RunningTime=AIRHEATER_RUNTIME_UNCHANGED,
        TimerHour=AIRHEATER_TIMER_HOUR_UNCHANGED,
        TimerMin=AIRHEATER_TIMER_MIN_UNCHANGED,
    )
    vals.update(changes)
    return vals


def _airheater(funcs, what, value, last):
    """Build an airheater (parking heater) control frame.

    ``power`` on/off drives ``NormalOperationRequest`` (1=on, 0=off); ``level`` sets
    ``HeatingLevel`` 1-10 (10=HI). Full-packet, MSB-first, like cooler. VERIFIED against the real
    app by HCI capture (2026-07-08): calictl's on/off frames match byte-for-byte —
    on ``3d7b007f1f3f``, off ``3c7b007f1f3f`` (``NormalOperationRequest`` 1/0, other fields
    at their leave-unchanged sentinels).

    :param funcs: loaded + overridden Function map.
    :param what: ``"power"``, ``"level"``, ``"runtime"``, ``"timer"``, ``"timer_start"``,
        ``"timer_cancel"`` or ``"permanent"``.
    :param value: on/off token for power (off only for permanent), 1-10 for level, minutes
        for runtime, ``HH:MM`` for timer; ignored for ``timer_start``/``timer_cancel``.
    :param last: current decoded airheater state (carried into the frame).
    :returns: the 6-byte control frame, or ``None`` for an unknown target.

    .. req:: Build airheater control frame
       :id: R_AIRHEATER_SET
       :status: implemented
       :tags: ble, control, airheater

       ``calictl`` shall build a full-packet airheater (char 1701) control frame
       for ``power`` (via ``NormalOperationRequest`` = 1/0), ``level`` (via
       ``HeatingLevel`` 1-10), ``runtime`` (``RunningTime`` 0-120 min — the app's cap),
       ``timer`` (``TimerHour``/``TimerMin``), ``timer_start`` (arm the departure timer:
       ``OperationModeAirHeater`` = 3 + ``OperationModeCombined`` = 1, the app's
       ``3f3b017f1f3f``), ``timer_cancel`` (``OperationModeAirHeater`` = 0, the app's
       ``3f0b007f1f3f``) and ``permanent`` (OFF only: ``PermanentOperationRequest`` = 0; ON is
       refused because continuous heating can only be started from inside the vehicle),
       carrying current state for untargeted physical fields and the ``7`` sentinel for the
       timer-arm field.
    """
    if what == "power":
        ch = {"NormalOperationRequest": 1 if _truthy(value) else 0}
    elif what == "level":
        # App-settable range is 1-10 (rf/b.java:783 q4 stages HeatingLevel only if 0<i<=10; 11 is
        # the leave-unchanged/commit sentinel). 0-15 is the raw field WIDTH, not the exposed range.
        lvl = _int_range(value, 1, 10, "airheater HeatingLevel (10=HI)")
        ch = {"HeatingLevel": lvl}
    elif what == "runtime":
        # RunningTime, minutes (rf/b.java:199 D4() writes the raw int). The app caps the run time at
        # 120 min — its own heating info page says immediate heating is "limited to 120 minutes"
        # (the fuel-burning heater's emissions limit; the unit raises ErrorCode 4
        # HEATING_TIME_EXCEEDED past it). Enforce the same bound here so CLI/API/HA can't ask for
        # what the app never sends; 255 is the field WIDTH, not a valid request.
        rt = _int_range(value, 0, AIRHEATER_MAX_RUNTIME_MIN, "airheater runtime (minutes)")
        ch = {"RunningTime": rt}
    elif what == "timer":  # start-at TimerHour:TimerMin (rf/b.java:165 B0())
        hh, mm = _hhmm(value)
        ch = {"TimerHour": hh, "TimerMin": mm}
    elif what == "timer_start":
        # The heater page's "Start timer" (uh/d.java -> rf/b.java:274-308 a2(AIR_HEATER)): the
        # departure timer is ARMED by OperationModeAirHeater=3 with OperationModeCombined = the
        # device combo (AIR_HEATER -> 1; Truma/roof-A/C combos 2-7 are other-model equipment).
        # App-observed on the wire 2026-09-16: `3f3b017f1f3f`; the unit then shows the timer as
        # "Inactive • Timer: HH:MM" until TimerHour:TimerMin, when NormalOperation starts.
        ch = {
            "OperationModeAirHeater": AIRHEATER_MODE_TIMER_ARMED,
            "OperationModeCombined": AIRHEATER_COMBINED_AIR_HEATER,
        }
    elif what == "timer_cancel":
        # "Stop" on the same widget (rf/b.java:745-749 j4()): Mode back to 0 — `3f0b007f1f3f`.
        ch = {"OperationModeAirHeater": AIRHEATER_MODE_IDLE}
    elif what == "permanent":
        # Continuous heating ("Dauerbetrieb") is OFF-ONLY from outside the vehicle: the app's E3()
        # (rf/b.java:209-218) only ever writes PermanentOperationRequest=0 — it can only be
        # STARTED from the in-vehicle controls, and no ON write site exists anywhere in the app.
        # Mirror that exactly: accept "off", refuse "on" (never guess a write that arms a
        # fuel-burning heater).
        if str(value).strip().lower() not in ("off", "false", "0"):
            raise ValueError(
                "continuous heating can only be started from inside the vehicle; only 'off' is accepted"
            )
        ch = {"PermanentOperationRequest": 0}
    else:
        return None
    return protocol.encode(
        funcs["airheater"],
        _airheater_values(last, **ch),
        frame_bytes=overrides.CONTROL_FRAME_BYTES["airheater"],
    )


# --- roof / roof-A/C / stairs / LR-heater ------------------------------------
# ALL FOUR ARE NOT-LIVE-VERIFIED (none installed on this van; see AGENTS.md "Known state").
# Their frames are offset-resolved in overrides.py; encode() validates bit-widths only. Enum
# meanings vary in confidence: roof-A/C Mode (jf/c.java: 0=AUTOMATIC/1=MANUAL_COOLING/
# 2=MANUAL_HEATING/3=VENTING) + FanSpeed (jf/b.java: 0-4=LEVEL_0..AUTO) are STATICALLY VERIFIED
# against the app getters (2026-07-12, readback clamp kg/b.java:318-326) — only Temperature's unit
# and live actuation are unverified. Stairs/LR-heater enum polarity is likewise statically checked
# (see semantics.py). "Statically transcribed, meaning unverified" applies to the raw carried
# fields, not these confirmed enums.


def _roofac_values(state: dict, **changes) -> dict:
    """Full-packet roof-A/C values (char 2001, ``lg/a.java`` case0). Carry current
    State/Mode/FanSpeed/Temperature so a targeted change leaves the rest as-is; then
    apply ``changes``. NOTE: the state decode names the fan field ``Fanspeed`` while
    the control field is ``FanSpeed``."""
    vals = dict(
        State=state.get("State", 0),
        Mode=state.get("Mode", 0),
        FanSpeed=state.get("Fanspeed", 0),
        Temperature=state.get("Temperature", 0),
    )
    vals.update(changes)
    return vals


def _roofaircondition(funcs, what, value, last):
    """Build a roof-A/C (char 2001) control frame. NOT-LIVE-VERIFIED (not installed).

    ``power`` on/off -> ``State`` 1/0; ``fanspeed`` 0-4; ``mode`` 0-3;
    ``temperature`` 0-255 (raw, scale UNVERIFIED). Untargeted fields carry current
    state (mirrors ``_cooler``). Setter values from re-gap §A3."""
    if what == "power":
        ch = {"State": 1 if _truthy(value) else 0}
    elif what == "fanspeed":
        v = _int_range(value, 0, 4, "roofaircondition fanspeed")
        ch = {"FanSpeed": v}
    elif what == "mode":
        v = _int_range(value, 0, 3, "roofaircondition mode")
        ch = {"Mode": v}
    elif what == "temperature":
        v = _int_range(value, 0, 255, "roofaircondition temperature")
        ch = {"Temperature": v}
    else:
        return None
    return protocol.encode(
        funcs["roofaircondition"],
        _roofac_values(last, **ch),
        frame_bytes=overrides.CONTROL_FRAME_BYTES["roofaircondition"],
    )


# og/b.java:289 h1() emits only Movement 2 or 1 (z11?2:1); 0 is the field's power-up/sentinel
# default (never an app-sent action). So extend=2/retract=1 are app-confirmed VALUES; only the
# extend-vs-retract LABEL is unverified, and stop=0 is inferred (the sentinel), not app-emitted.
STAIRS_MOVEMENT = {"extend": 2, "retract": 1, "stop": 0}


def _stairs_values(state: dict, **changes) -> dict:
    """Full-packet stairs values (char 1801, ``pg/a.java`` case0). ``OperationMode``
    defaults to the leave-unchanged sentinel 3; ``Movement`` defaults to 0=stop; then
    apply ``changes``."""
    vals = dict(OperationMode=SENTINEL, Movement=0)
    vals.update(changes)
    return vals


def _stairs(funcs, what, value, last):
    """Build a stairs (char 1801) control frame. NOT-LIVE-VERIFIED (not installed).

    ``move`` extend/retract/stop -> ``Movement`` 2/1/0; ``mode`` 0-3 ->
    ``OperationMode``. Setter values from re-gap §A3. Enum polarity UNVERIFIED."""
    if what == "move":
        v = str(value).strip().lower()
        if v not in STAIRS_MOVEMENT:
            raise ValueError("stairs move must be extend/retract/stop, got %r" % value)
        ch = {"Movement": STAIRS_MOVEMENT[v]}
    elif what == "mode":
        m = _int_range(value, 0, 3, "stairs mode")
        ch = {"OperationMode": m}
    else:
        return None
    return protocol.encode(
        funcs["stairs"], _stairs_values(last, **ch), frame_bytes=overrides.CONTROL_FRAME_BYTES["stairs"]
    )


def _lrheater_values(state: dict, **changes) -> dict:
    """Full-packet living-room-heater values (char 2101, ``gg/a.java`` case0). Carry
    current StateAir/StateWater/TemperatureWater/Mode/TemperatureAir; then apply
    ``changes``."""
    vals = dict(
        StateAir=state.get("StateAir", 0),
        StateWater=state.get("StateWater", 0),
        TemperatureWater=state.get("TemperatureWater", 0),
        Mode=state.get("Mode", 5),
        TemperatureAir=state.get("TemperatureAir", 0),
    )
    vals.update(changes)
    return vals


def _livingroomheater(funcs, what, value, last):
    """Build a living-room-heater (char 2101) control frame. NOT-LIVE-VERIFIED
    (not installed).

    ``air`` on/off -> ``StateAir`` 1/0; ``water`` on/off -> ``StateWater`` 1/0;
    ``temperature`` 0-255 -> ``TemperatureAir`` (raw, scale UNVERIFIED). Untargeted
    fields carry current state (mirrors ``_cooler``). Setter values from re-gap §A3."""
    if what == "air":
        ch = {"StateAir": 1 if _truthy(value) else 0}
    elif what == "water":
        ch = {"StateWater": 1 if _truthy(value) else 0}
    elif what in ("temperature", "temperatureair"):
        v = _int_range(value, 0, 255, "livingroomheater temperature")
        ch = {"TemperatureAir": v}
    else:
        return None
    return protocol.encode(
        funcs["livingroomheater"],
        _lrheater_values(last, **ch),
        frame_bytes=overrides.CONTROL_FRAME_BYTES["livingroomheater"],
    )


# roof (char 1401, jg/a.java f()) — SAFETY-SENSITIVE + NOT-LIVE-VERIFIED.
# Press-and-hold: while held, a single frame will NOT complete travel, so the move frame is
# re-sent until STOP on release. The re-send is driven by device.actuate_roof, NOT device.actuate.
#   OPEN = Up1/Down0    CLOSE = Up0/Down1    STOP = Up0/Down0
# The app runs TWO timers on the 1401 char (verified 2026-08-17, w8/a + b1/d + ig/c):
#   (1) the PRIMARY frame pump is a ~500 ms SafetyCounter timer (w8/a, ctor 500=tick-ms /
#       450-550=fire-period jitter): each fire writes a frame carrying counter +1;
#   (2) a secondary 1000 ms timer (ig/c jn.a(1000L)) only RE-AFFIRMS the direction — it does
#       not touch the counter. Net ~3 frames/s, consecutive counter deltas 0 or +1, NEVER +2.
# SafetyCounter (32-bit) is APP-GENERATED, not echoed: a monotonic BE-uint32 = seed +
# floor(elapsed_ms/500) (b1/d.java:352), a pure wall-clock rule. device.actuate_roof streams at
# ~500 ms with +1/frame — exactly the app's counter-timer sub-stream (we simply omit the 1000 ms
# duplicate re-sends); protocol-correct, same counter trajectory the unit validates. A fixed 0 is
# valid only for a lone STOP. The unit withholds the motor until SafetyCounterValid (1402 bit 7);
# a one-shot 3000 ms dead-man (ig/c) flags an error if it never validates.
ROOF_MOVES = {"open": (1, 0), "close": (0, 1), "stop": (0, 0)}  # -> (Up, Down)


def roof_frame(funcs, direction: str, counter: int = 0) -> bytes:
    """Build one roof (char 1401) move frame. SAFETY-SENSITIVE + NOT-LIVE-VERIFIED.

    ``direction`` is ``open``/``close``/``stop`` (Up/Down per ``ROOF_MOVES``);
    ``counter`` fills the 32-bit ``SafetyCounter`` (app-generated monotonic BE-uint32,
    ~+1 per 500 ms of elapsed time; advanced by device.actuate_roof). Raises ValueError
    on an unknown direction."""
    if direction not in ROOF_MOVES:
        raise ValueError("roof direction must be open/close/stop, got %r" % direction)
    up, down = ROOF_MOVES[direction]
    vals = {"Up": up, "Down": down, "SafetyCounter": counter}
    return protocol.encode(funcs["roof"], vals, frame_bytes=overrides.CONTROL_FRAME_BYTES["roof"])


def _roof(funcs, what, value, last):
    """Generic builder entry for roof: ``what`` is the direction (open/close/stop);
    ``value`` is unused. Returns ONE move frame — device.actuate_roof re-sends it at
    ~500 ms with a +1/frame SafetyCounter while held (the app's counter-timer sub-stream).
    SAFETY-SENSITIVE + NOT-LIVE-VERIFIED."""
    if what not in ROOF_MOVES:
        return None
    return roof_frame(funcs, what)


BUILDERS = {
    "campingmode": _camping,
    "cooler": _cooler,
    "lighting": _lighting,
    "airheater": _airheater,
    "roofaircondition": _roofaircondition,
    "stairs": _stairs,
    "livingroomheater": _livingroomheater,
    "roof": _roof,
    "energy": _energy,
}


def build(funcs, function, what, value, last_decoded):
    """Build the control frame for ``set function what value`` over the decoded state.

    :raises CommandError: the value is malformed or out of range (nothing has been written yet,
        so every ``ValueError`` a builder raises is the operator's input error).
    """
    b = BUILDERS.get(function)
    try:
        return b(funcs, what, value, last_decoded) if b else None
    except CommandError:
        raise
    except ValueError as e:
        raise CommandError(str(e)) from e


def commit_for(function):
    """The follow/flush frame a function needs after a SET, or None. Lighting is the only one:
    :data:`LIGHT_COMMIT` is the neutral NO_MODE default frame that flushes calictl's single
    SET_BRIGHTNESS/SET_PROFILE write (the app self-flushes by streaming; HCI-seen 2026-07-13).
    Actuation itself is gated on the unit being awake, and is confirmed by the ``1502`` Mode-4
    push — not by the readback echo. Callers pass this as
    ``device.actuate(..., follow=commit_for(fn))``.

    .. req:: Flush lighting changes with the neutral follow frame
       :id: R_LIGHT_COMMIT
       :status: implemented
       :tags: control, lighting

       For lighting, ``calictl`` shall follow every SET_BRIGHTNESS/SET_PROFILE with the neutral
       flush frame :data:`LIGHT_COMMIT` (``0e00…``, NO_MODE + all-unchanged), standing in for the
       app's continuous frame stream. Diagram: :need:`S_SEQ_LIGHT_COMMIT`.
    """
    return LIGHT_COMMIT if function == "lighting" else None


def preface_for(funcs, function, what, value, last):
    """The frame the app sends BEFORE the main one, or ``None``. Only ``lighting save_profile N
    <colour>``: ``dg/h.l3`` step (a), a SET_COLOR (Mode 6) with ProfileNumber N, LightValue = the
    palette index and the zones of the save that follows. DECOMPILE-only (the colour UI is not shown
    on this model, so the app cannot be recorded doing it).

    :returns: the SET_COLOR frame, or ``None`` when the action has no preface.
    """
    if function != "lighting" or what != "save_profile":
        return None
    n, idx = _save_profile_args(value)
    if idx is None:
        return None
    f = funcs["lighting"]
    vals = {
        "ProfileNumber": n,
        "Mode": LIGHT_MODE_SET_COLOR,
        "Timestamp": 0,
        "LightValue": idx,
        **_saved_zones(_zone_fields(f), last),
    }
    return protocol.encode(f, vals, frame_bytes=overrides.CONTROL_FRAME_BYTES["lighting"])


# Screen-open config pull. The app opens its Lighting screen with a REQUEST_CONFIG
# (0d0c000000000000eeeeeeeeeeeeeeee, Mode=12 PN=13) + commit; photon-verified 2026-08-16 that it is
# NOT an actuation gate (a bare SET + 0e00 commit actuates an awake unit; the app's dg/h.java E()
# writes DIRECT and never sends it). calictl sends it only to READ the configuration: serve pulls
# it before a wake-up edit whose config is unknown (R5, ``LIGHT_REQUEST_CONFIG``), never as a
# write preamble.


def decode_control(func, frame: bytes) -> dict:
    """Decode a control frame using the function's CONTROL field offsets
    (protocol.decode uses STATE offsets, which differ)."""
    bits = protocol.to_bits(frame)
    return {
        f.name: protocol.get_field(bits, f.offset, f.width)
        for f in func.control_fields
        if f.placed and f.offset + f.width <= len(bits)
    }  # skip fields past a short frame
