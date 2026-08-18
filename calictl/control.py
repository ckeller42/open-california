"""Per-function control-frame builders (full-packet, dictionary-driven).

Shared by the CLI (`calictl set`) and the daemon (`serve.on_command`) so both
build identical frames. Frames are written under a 1003 liveness heartbeat
(`device.actuate`), which is what arms actuation on-device (issue #2)."""
from __future__ import annotations
from . import protocol, overrides

LIGHT_ON, LIGHT_OFF = 0, 1   # camping lights inverted (app K0 writes (!on)?1:0). VERIFY live.
SENTINEL = 3                 # 2-bit "leave unchanged" (sg.a default)


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("on", "true", "1")


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


def camping_values(**changes) -> dict:
    """Only the changed field is set; every other camping field stays at the
    leave-unchanged sentinel 3 (the lights are inverted, so carrying a state
    value would be wrong; 3 = no-op per the app's control model)."""
    vals = {"State": SENTINEL, "UsbCharger": SENTINEL,
            "InteriorLight": SENTINEL, "OutsideLight": SENTINEL}
    vals.update(changes)
    return vals


def _camping(funcs, what, value, last):
    # Matches the app's camping screen: ONE combined "lights" toggle (tf/a K0 writes
    # 0 to BOTH light fields for ON, inverted), USB (B2) + master (z2) normal.
    # lights/usb are only effective while master (camping mode) is on.
    on = _truthy(value)   # strips whitespace; "on " must not read as OFF
    if what == "lights":                       # combined interior+outside, inverted
        v = LIGHT_ON if on else LIGHT_OFF
        ch = {"InteriorLight": v, "OutsideLight": v}
    elif what == "usb":                        # rear USB ports, normal
        ch = {"UsbCharger": 1 if on else 0}
    elif what == "master":                     # camping mode on/off, normal
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
ENERGY_MODE_UNCHANGED = 3   # 2-bit leave-unchanged sentinel (pg/a v())


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
    return protocol.encode(funcs["energy"], vals,
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["energy"])


def _cooler_values(state: dict, **changes) -> dict:
    """Full-packet cooler control values: carry current State/Mode/Level and the
    schedule (writing the current schedule back = no change), timer ACTION fields
    at no-op, then apply `changes`. (Moved from cli.cmd_set so the daemon shares it.)"""
    vals = dict(
        State=state.get("State", 1), Mode=state.get("Mode", 4),
        Level=state.get("Level", 3),
        # no-op actions. NB: the cross-check (2026-08-17) suggested NightTimerSet=3 / hours=31 from
        # the app's v() class-defaults, but the REAL captured app power-on frame (tests/scenarios/
        # cooler/power-on) sends NightTimerSet=0 and the hour bytes 0 — the wire capture is ground
        # truth, so these stay 0. (A good example of capture > decompiled-default inference.)
        TimerStart=3, TimerCancel=3, NightTimerSet=0,
        NightTimerHourOff=0, NightTimerHourOn=0,
        TimerHour=state.get("TimerHourSet", 0), TimerMin=state.get("TimerMinSet", 0),
    )
    vals.update(changes)
    return vals


# Cooler Mode enum (vf/c.java: readback J0()=Mode==2, P1()=Mode==4): 0=normal (quiet off),
# 2=manual quiet, 4=timer-based quiet. Staged by vf/c.f(this, <n>) via T1(0)/x0(2)/k0(4).
COOLER_MODES = {"normal": 0, "quiet": 2, "timer_quiet": 4}


def _cooler(funcs, what, value, last):
    # `power` on/off flips State (encode validates State in {0,1}); `level` sets the cooling
    # intensity 1-5; `mode` sets the quiet Mode enum; the timer/night branches arm the cooler's
    # scheduling (all decompile-verified from vf/c.java, NOT yet live-verified). Everything else
    # carries current state, so only the targeted field changes.
    if what == "power":
        ch = {"State": 1 if _truthy(value) else 0}
    elif what == "level":
        lvl = int(value)
        if not 1 <= lvl <= 5:
            raise ValueError("cooler level must be 1-5, got %r" % value)
        ch = {"Level": lvl}
    elif what == "mode":                       # quiet mode (vf/c.java T1/x0/k0 -> Mode 0/2/4)
        key = str(value).strip().lower()
        if key not in COOLER_MODES:
            return None
        ch = {"Mode": COOLER_MODES[key]}
    elif what == "timer_set":                  # start-at TimerHour:TimerMin (vf/c.java:635 y0())
        hh, mm = _hhmm(value)
        ch = {"TimerHour": hh, "TimerMin": mm}
    elif what == "timer_start":                # arm cooling-start timer (vf/c.java:193 D())
        ch = {"TimerStart": 1}
    elif what == "timer_cancel":               # cancel it (vf/c.java:251 X0())
        ch = {"TimerCancel": 1}
    elif what in ("night_on", "night_off"):    # quiet-schedule hours (vf/c.java c0()/Y2()), 0-23
        hr = int(value)
        if not 0 <= hr <= 23:
            raise ValueError("night timer hour must be 0-23, got %r" % value)
        ch = {"NightTimerHourOn" if what == "night_on" else "NightTimerHourOff": hr}
    else:
        return None
    return protocol.encode(funcs["cooler"], _cooler_values(last, **ch),
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["cooler"])


# dg/n.java Mode enum (full, verified 2026-08-17): 0=NO_MODE, 4=SET_BRIGHTNESS, 6=SET_COLOR,
# 8=SET_DOUBLE, 12=REQUEST_CONFIG, 16=SET_PROFILE, 20=WAKEUP_TIME, 24=SYSTEM_TIME, 28=PREVIEW.
LIGHT_MODE_SET_BRIGHTNESS = 4
LIGHT_MODE_SET_COLOR = 6         # recolour the active profile: LightValue = palette index (1-10)
LIGHT_MODE_SET_PROFILE = 16      # switch active profile (payload carries the ProfileNumber)
# Profile-number enum (dg/l.java mirrors ef/k.java): 0=LIGHTS_OFF, 1-7=FAVORITE1-7, 8=DOOR_CONTACT,
# 9=LIVE_VIEW (the per-zone-edit profile SET_BRIGHTNESS hardcodes), 10=WAKEUP_LIGHT,
# 11=INTERIOR_LIGHT, 12=LIGHTS_ON, 13=DEFAULT, 14=INIT sentinel.
LIGHT_PROFILE_ALL_ON = 12        # LIGHTS_ON  — the app's "Alle Lichter" master ON  (dg/h.java:323 Q())
LIGHT_PROFILE_ALL_OFF = 0        # LIGHTS_OFF — the app's "Alle Lichter" master OFF
# Colour palette (dg/j.java, decompile 2026-07-12): SET_COLOR carries ONE index in LightValue for
# the whole target profile — not RGB. On-device apply is UNVERIFIED (same lighting-apply gap as
# SET_BRIGHTNESS, re-gap A1); the frame layout is byte-decoded from the app.
LIGHT_COLORS = {
    "warm-white": 1, "blood-orange": 2, "amber": 3, "pistachio": 4, "peppermint": 5,
    "mint": 6, "azure": 7, "dark-blue": 8, "red": 9, "salmon": 10,
}
# Per-zone "leave unchanged" sentinel — CRACKED via HCI capture 2026-07-08: the app fills every
# zone it is NOT changing with 14 (0xe), not 0. 0 means "set this zone to 0"; 14 = "no change".
LIGHT_UNCHANGED = 14
# Brightness is the dg/i.java enum, NOT a raw 0-13 scale: 0=OFF, 1-10 = 10%..100% in 10% steps,
# 11=DEFAULT, 12 unused, 13=NOT_EQUIPPED (read-only marker for absent zones — writing it is
# garbage; we did exactly that for "power on" until the 2026-08-16 capture).
LIGHT_ON_BRIGHTNESS = 10         # "on" = 100% (enum PERCENTAGE_100)
LIGHT_MAX_SET = 11               # highest settable value (11 = DEFAULT brightness)
# Every SET_BRIGHTNESS frame the app sends hardcodes ProfileNumber=9 (LIVE_VIEW, the "live editing"
# profile) — `w(9, PENDING)` in dg/h.java:170-264 — NOT the currently-active profile. Echoing the
# live ProfileNumber instead (0 when the lights are off) is why our SET_BRIGHTNESS was ignored until
# a profile was activated; the real gate was always "PN=9 in the frame", not "a profile is active".
LIGHT_BRIGHTNESS_PROFILE = 9

# Lighting COMMIT/APPLY frame. HCI-verified 2026-07-13: the app follows every SET_BRIGHTNESS /
# SET_PROFILE with this exact frame, and the unit only APPLIES the change once it lands (Mode 0,
# ProfileNumber + all zones = the 14 "unchanged" sentinel). This is the fix for the old
# lighting-apply gap — our SET frames were already byte-identical to the app's; only this commit
# was missing. Sent as the `follow` frame of device.actuate for every lighting write.
LIGHT_COMMIT = bytes.fromhex("0e00000000000000eeeeeeeeeeeeeeee")

# Friendly zone key -> BrightnessL control field. Two provenance tiers:
#   CONFIRMED by the 2026-07-08 HCI capture (which nibble tracked each dragged slider):
#     reading trio L2/L1/L4 (Links/Rechts/Beifahrer — group confirmed, the split is best-effort),
#     "kitchen" = L7 (the Küche *Kochen*/cooking light), "roof-ambient" = L8, "outside-rear" = L3.
#   INFERRED by elimination (2026-07-15 screenshots): the app shows this van has 8 lamps, and
#     semantics._REAL_LIGHT_ZONES says exactly 8 zones are real (L1-L8). Six are pinned above; the
#     app's two remaining rows are Küche *Ambientelicht* and Aufstelldach *Leselicht*, so they must
#     be the two unpinned zones L5/L6 — but WHICH is which is unverified. The GUI marks these
#     unverified; a single slider drag on-device (watch which lamp lights) will confirm/swap them.
LIGHT_ZONES = {
    "reading-1": "BrightnessLTwo", "reading-2": "BrightnessLOne", "reading-3": "BrightnessLFour",
    "kitchen": "BrightnessLSeven", "roof-ambient": "BrightnessLEight", "outside-rear": "BrightnessLThree",
    "kitchen-ambient": "BrightnessLFive",   # CONFIRMED 2026-08-16 capture: Küche Ambientelicht slider moved L5
    "roof-reading": "BrightnessLSix",        # L6 — Aufstelldach Leselicht by elimination (solid now L5 is pinned)
}


def _all_real_zones(zone_fields, b):
    """Value ``b`` for the REAL lamp zones (L1-L8), the unchanged sentinel elsewhere.

    ``power``/``all`` used to write every one of the 16 control zones — including the
    never-equipped L9-L16, which only coincidentally looked right while "on" was the 13
    NOT_EQUIPPED marker. Only L1-L8 exist on this van (semantics._REAL_LIGHT_ZONES); the
    app's "Alle Lichter" frame is uncaptured, so stay conservative: never touch phantom zones.
    """
    from .semantics import _LZONES, _REAL_LIGHT_ZONES  # stdlib-only sibling; lazy to match style
    real = {"BrightnessL" + suf for suf, num in _LZONES.items() if num in _REAL_LIGHT_ZONES}
    return {z: (b if z in real else LIGHT_UNCHANGED) for z in zone_fields}


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
      * ``"all"`` -> set every REAL zone (L1-L8) to ``value`` (a calictl convenience, not an app
        action); never-equipped zones (L9-L16) always get the unchanged sentinel.
      * ``"profile"`` -> SET_PROFILE (Mode 16); ``value`` = target ProfileNumber.
      * ``"color"`` -> SET_COLOR (Mode 6); ``value`` = a ``LIGHT_COLORS`` name; recolours the
        active profile (LightValue = palette index 1-10). On-device apply UNVERIFIED (re-gap A1).
    """
    f = funcs["lighting"]
    zone_fields = [cf.name for cf in f.control_fields if cf.name.startswith("BrightnessL")]
    # SET_BRIGHTNESS/power/all hardcode ProfileNumber=9 like the app (writes land even with the
    # lights off / PN=0). SET_PROFILE overrides it with the target; SET_COLOR recolours that same 9.
    base = {"ProfileNumber": LIGHT_BRIGHTNESS_PROFILE, "Mode": LIGHT_MODE_SET_BRIGHTNESS,
            "Timestamp": 0, "LightValue": 0}

    def _b(v):
        # settable brightness is the dg/i enum 0-11 (0=off, 1-10=10%..100%, 11=default);
        # 12 is unused, 13=NOT_EQUIPPED and 14=unchanged are markers, never valid to set.
        b = int(v)
        if not 0 <= b <= LIGHT_MAX_SET:
            raise ValueError("lighting brightness must be 0-%d (0=off, 1-10=10%%..100%%, "
                             "11=default), got %r" % (LIGHT_MAX_SET, v))
        return b

    if what == "profile":
        vals = {**base, "Mode": LIGHT_MODE_SET_PROFILE, "ProfileNumber": int(value),
                **{z: LIGHT_UNCHANGED for z in zone_fields}}
    elif what == "save_profile":
        # Save the CURRENT lighting into a favorite (dg/h.java:564 l3 applyProfileBrightness):
        # SET_BRIGHTNESS with ProfileNumber = the favorite N (NOT the live-view 9), every equipped
        # zone carrying its current brightness (read from `last`), NOT_EQUIPPED zones left at 14.
        # This is how the app DEFINES a favorite. Decompile-derived; NOT yet wire-verified.
        n = int(value)
        if not 1 <= n <= 7:
            raise ValueError("save_profile target must be a favorite 1-7, got %r" % value)
        from .semantics import _LZONES, _REAL_LIGHT_ZONES  # stdlib-only sibling; lazy to match style
        real = {"BrightnessL" + suf for suf, num in _LZONES.items() if num in _REAL_LIGHT_ZONES}
        st = last or {}
        zones = {}
        for z in zone_fields:
            cur = st.get(z)
            zones[z] = cur if (z in real and isinstance(cur, int) and 0 <= cur <= LIGHT_MAX_SET) \
                else LIGHT_UNCHANGED
        vals = {**base, "Mode": LIGHT_MODE_SET_BRIGHTNESS, "ProfileNumber": n, **zones}
    elif what == "color":                   # recolour the active profile (LightValue = palette idx)
        idx = LIGHT_COLORS.get(str(value).lower().replace("_", "-"))
        if idx is None:
            raise ValueError("unknown light colour %r; one of: %s"
                             % (value, ", ".join(sorted(LIGHT_COLORS))))
        vals = {**base, "Mode": LIGHT_MODE_SET_COLOR, "LightValue": idx,
                **{z: LIGHT_UNCHANGED for z in zone_fields}}
    elif what == "power":
        # app-faithful master toggle (dg/h.java:323-337 Q()): a SET_PROFILE selecting LIGHTS_ON
        # (12) / LIGHTS_OFF (0), NOT per-zone brightness. Matches how the app's "Alle Lichter"
        # switch works, so it restores the user's saved on-state rather than forcing every lamp
        # to 100%. Zones carry the unchanged sentinel like every other profile-select frame.
        pn = LIGHT_PROFILE_ALL_ON if _truthy(value) else LIGHT_PROFILE_ALL_OFF
        vals = {**base, "Mode": LIGHT_MODE_SET_PROFILE, "ProfileNumber": pn,
                **{z: LIGHT_UNCHANGED for z in zone_fields}}
    elif what == "all":
        # not an app action (the app is per-zone) — our convenience: every REAL lamp to one level
        vals = {**base, **_all_real_zones(zone_fields, _b(value))}
    else:                                   # a single zone (friendly key or BrightnessL field)
        field = LIGHT_ZONES.get(what, what)
        if field not in zone_fields:
            return None
        vals = {**base, **{z: LIGHT_UNCHANGED for z in zone_fields}, field: _b(value)}
    return protocol.encode(f, vals, frame_bytes=overrides.CONTROL_FRAME_BYTES["lighting"])


def _airheater_values(state: dict, **changes) -> dict:
    """Full-packet airheater control values (char 1701, ``sf/a.java``).

    The three 2-bit request/confirmation fields default to the leave-unchanged
    sentinel ``3``; the physical fields (mode/level/air-distribution/running-time)
    carry current state so a targeted change doesn't disturb them; then ``changes``
    is applied. Timer fields fall back to their dictionary defaults when the state
    decode doesn't expose them.
    """
    vals = dict(
        NormalOperationRequest=SENTINEL, PermanentOperationRequest=SENTINEL,
        PermanentOperationConfirmation=SENTINEL,
        AirDistribution=state.get("AirDistribution", 0),
        OperationModeAirHeater=state.get("OperationModeAirHeater", 7),
        HeatingLevel=state.get("HeatingLevel", 11),
        OperationModeCombined=0,
        RunningTime=state.get("RunningTime", 127),
        TimerHour=state.get("TimerHour", 31), TimerMin=state.get("TimerMin", 63),
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
    :param what: ``"power"`` or ``"level"``.
    :param value: on/off token for power, or 1-10 (10=HI) for level.
    :param last: current decoded airheater state (carried into the frame).
    :returns: the 6-byte control frame, or ``None`` for an unknown target.

    .. req:: Build airheater control frame
       :id: R_AIRHEATER_SET
       :status: implemented
       :tags: ble, control, airheater

       ``calictl`` shall build a full-packet airheater (char 1701) control frame
       for ``power`` (via ``NormalOperationRequest`` = 1/0) and ``level`` (via
       ``HeatingLevel`` 1-10), carrying current state for untargeted fields.
    """
    if what == "power":
        ch = {"NormalOperationRequest": 1 if _truthy(value) else 0}
    elif what == "level":
        lvl = int(value)
        # App-settable range is 1-10 (rf/b.java:783 q4 stages HeatingLevel only if 0<i<=10; 11 is
        # the leave-unchanged/commit sentinel). 0-15 is the raw field WIDTH, not the exposed range.
        if not 1 <= lvl <= 10:
            raise ValueError("airheater HeatingLevel must be 1-10 (10=HI), got %r" % value)
        ch = {"HeatingLevel": lvl}
    elif what == "runtime":                    # RunningTime, minutes (rf/b.java:199 D4()); no app bound
        rt = int(value)
        if not 0 <= rt <= 255:
            raise ValueError("airheater runtime must be 0-255, got %r" % value)
        ch = {"RunningTime": rt}
    elif what == "timer":                       # start-at TimerHour:TimerMin (rf/b.java:165 B0())
        hh, mm = _hhmm(value)
        ch = {"TimerHour": hh, "TimerMin": mm}
    # NB: "permanent heating" is intentionally NOT wired — the app's E3() only ever writes
    # PermanentOperationRequest=0 (OFF/cancel); no ON write site exists in rf/b.java, so the
    # ON value is unknown. Don't guess a write that arms a fuel-burning heater.
    else:
        return None
    return protocol.encode(funcs["airheater"], _airheater_values(last, **ch),
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["airheater"])


# --- roof / roof-A/C / stairs / LR-heater ------------------------------------
# ALL FOUR ARE NOT-LIVE-VERIFIED. None is installed on this van (see CLAUDE.md
# "Known state"), so enum polarity + carried-field behaviour are UNVERIFIED —
# statically transcribed from each function's app builder f() (re-gap §A3) and
# offset-resolved in overrides.py. encode() validates bit-widths; it does NOT
# validate that these enum values mean what the app names imply.


def _roofac_values(state: dict, **changes) -> dict:
    """Full-packet roof-A/C values (char 2001, ``lg/a.java`` case0). Carry current
    State/Mode/FanSpeed/Temperature so a targeted change leaves the rest as-is; then
    apply ``changes``. NOTE: the state decode names the fan field ``Fanspeed`` while
    the control field is ``FanSpeed``."""
    vals = dict(
        State=state.get("State", 0), Mode=state.get("Mode", 0),
        FanSpeed=state.get("Fanspeed", 0), Temperature=state.get("Temperature", 0),
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
        v = int(value)
        if not 0 <= v <= 4:
            raise ValueError("roofaircondition fanspeed must be 0-4, got %r" % value)
        ch = {"FanSpeed": v}
    elif what == "mode":
        v = int(value)
        if not 0 <= v <= 3:
            raise ValueError("roofaircondition mode must be 0-3, got %r" % value)
        ch = {"Mode": v}
    elif what == "temperature":
        v = int(value)
        if not 0 <= v <= 255:
            raise ValueError("roofaircondition temperature must be 0-255, got %r" % value)
        ch = {"Temperature": v}
    else:
        return None
    return protocol.encode(funcs["roofaircondition"], _roofac_values(last, **ch),
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["roofaircondition"])


STAIRS_MOVEMENT = {"extend": 2, "retract": 1, "stop": 0}   # pg/a.java Movement enum (UNVERIFIED)


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
        m = int(value)
        if not 0 <= m <= 3:
            raise ValueError("stairs mode must be 0-3, got %r" % value)
        ch = {"OperationMode": m}
    else:
        return None
    return protocol.encode(funcs["stairs"], _stairs_values(last, **ch),
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["stairs"])


def _lrheater_values(state: dict, **changes) -> dict:
    """Full-packet living-room-heater values (char 2101, ``gg/a.java`` case0). Carry
    current StateAir/StateWater/TemperatureWater/Mode/TemperatureAir; then apply
    ``changes``."""
    vals = dict(
        StateAir=state.get("StateAir", 0), StateWater=state.get("StateWater", 0),
        TemperatureWater=state.get("TemperatureWater", 0),
        Mode=state.get("Mode", 5), TemperatureAir=state.get("TemperatureAir", 0),
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
        v = int(value)
        if not 0 <= v <= 255:
            raise ValueError("livingroomheater temperature must be 0-255, got %r" % value)
        ch = {"TemperatureAir": v}
    else:
        return None
    return protocol.encode(funcs["livingroomheater"], _lrheater_values(last, **ch),
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["livingroomheater"])


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
ROOF_MOVES = {"open": (1, 0), "close": (0, 1), "stop": (0, 0)}   # -> (Up, Down)


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
    return protocol.encode(funcs["roof"], vals,
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["roof"])


def _roof(funcs, what, value, last):
    """Generic builder entry for roof: ``what`` is the direction (open/close/stop);
    ``value`` is unused. Returns ONE move frame — device.actuate_roof re-sends it at
    ~500 ms with a +1/frame SafetyCounter while held (the app's counter-timer sub-stream).
    SAFETY-SENSITIVE + NOT-LIVE-VERIFIED."""
    if what not in ROOF_MOVES:
        return None
    return roof_frame(funcs, what)


BUILDERS = {"campingmode": _camping, "cooler": _cooler, "lighting": _lighting,
            "airheater": _airheater, "roofaircondition": _roofaircondition,
            "stairs": _stairs, "livingroomheater": _livingroomheater, "roof": _roof,
            "energy": _energy}


def build(funcs, function, what, value, last_decoded):
    b = BUILDERS.get(function)
    return b(funcs, what, value, last_decoded) if b else None


def commit_for(function):
    """The follow/commit frame a function needs after a SET, or None. Lighting is the only one:
    the unit applies a SET_BRIGHTNESS/SET_PROFILE only once :data:`LIGHT_COMMIT` lands right after
    it (HCI-verified 2026-07-13, and confirmed on-device via readback). Callers pass this as
    ``device.actuate(..., follow=commit_for(fn))``.

    .. req:: Apply lighting changes with the commit frame
       :id: R_LIGHT_COMMIT
       :status: implemented
       :tags: control, lighting

       For lighting, ``calictl`` shall follow every SET_BRIGHTNESS/SET_PROFILE with the commit
       frame :data:`LIGHT_COMMIT` (``0e00…``); the unit applies the staged change only once it
       lands. Diagram: :need:`S_SEQ_LIGHT_COMMIT`.
    """
    return LIGHT_COMMIT if function == "lighting" else None


# Session-arm preamble — THE 2026-08-16 CRACK of the lighting-apply gap. The app opens its
# Lighting screen with a REQUEST_CONFIG (Mode=12, ProfileNumber=13) + commit; only in a session
# where this preamble has landed does the unit PHYSICALLY drive the lamps on a later
# SET_BRIGHTNESS (it also then streams real Mode-4 ramp notifications on 1502). Without it a SET
# is ACKed and echoed but the load never switches — the byte-identical-frames mystery. Verified
# with photons on-device 2026-08-16 (Kochen L7: 0 -> dark, 8 -> 80%, 0 -> dark).
LIGHT_REQUEST_CONFIG = bytes.fromhex("0d0c000000000000eeeeeeeeeeeeeeee")


def preamble_for(function):
    """The app's optional screen-open config-request frames, or None. Lighting is the only one:
    :data:`LIGHT_REQUEST_CONFIG` + :data:`LIGHT_COMMIT`, mirroring what the app writes when its
    Lighting screen opens. Callers may pass this as ``device.actuate(..., pre=preamble_for(fn))``.

    NOT an actuation gate. Photon-verified on-device 2026-08-16: a bare ``SET_BRIGHTNESS`` +
    commit actuates an awake unit with no preamble (the app's ``dg/h.java`` ``E()`` writes DIRECT
    and never sends it; the earlier "preamble required" result was a wake-state confound). The
    daemon's fast lighting path omits it; the CLI still sends it (harmless — it also pulls a fresh
    config dump on the 1502 notifications).

    :param function: the function name (e.g. ``"lighting"``).
    :returns: list of frames to write (in order) before the SET frame, or ``None``.

    .. req:: The lighting REQUEST_CONFIG preamble is an optional config pull, not an actuation gate
       :id: R_LIGHT_PREAMBLE
       :status: implemented
       :tags: control, lighting

       ``preamble_for`` shall return :data:`LIGHT_REQUEST_CONFIG` + :data:`LIGHT_COMMIT` for
       lighting (None otherwise), replicating the app's screen-open config request. Actuation
       shall NOT depend on it: a bare SET + commit applies on an awake unit (photon-verified
       2026-08-16), so the daemon's fast path omits it.
    """
    return [LIGHT_REQUEST_CONFIG, LIGHT_COMMIT] if function == "lighting" else None


def decode_control(func, frame: bytes) -> dict:
    """Decode a control frame using the function's CONTROL field offsets
    (protocol.decode uses STATE offsets, which differ)."""
    bits = protocol.to_bits(frame)
    return {f.name: protocol.get_field(bits, f.offset, f.width)
            for f in func.control_fields
            if f.placed and f.offset + f.width <= len(bits)}   # skip fields past a short frame
