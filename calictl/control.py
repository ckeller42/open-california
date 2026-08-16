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


def _cooler_values(state: dict, **changes) -> dict:
    """Full-packet cooler control values: carry current State/Mode/Level and the
    schedule (writing the current schedule back = no change), timer ACTION fields
    at no-op, then apply `changes`. (Moved from cli.cmd_set so the daemon shares it.)"""
    vals = dict(
        State=state.get("State", 1), Mode=state.get("Mode", 4),
        Level=state.get("Level", 3),
        TimerStart=3, TimerCancel=3, NightTimerSet=0,   # no-op actions
        NightTimerHourOff=0, NightTimerHourOn=0,
        TimerHour=state.get("TimerHourSet", 0), TimerMin=state.get("TimerMinSet", 0),
    )
    vals.update(changes)
    return vals


def _cooler(funcs, what, value, last):
    # `power` on/off flips State (encode validates State in {0,1}); `level` sets the
    # cooling intensity 1-5. Everything else carries current state, so only the
    # targeted field changes.
    if what == "power":
        ch = {"State": 1 if _truthy(value) else 0}
    elif what == "level":
        lvl = int(value)
        if not 1 <= lvl <= 5:
            raise ValueError("cooler level must be 1-5, got %r" % value)
        ch = {"Level": lvl}
    else:
        return None
    return protocol.encode(funcs["cooler"], _cooler_values(last, **ch),
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["cooler"])


LIGHT_MODE_SET_BRIGHTNESS = 4    # dg/n.java Mode enum (0=no-op, 4=SET_BRIGHTNESS, 16=SET_PROFILE)
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
    ``HeatingLevel`` 0-15. Full-packet, MSB-first, like cooler. VERIFIED against the real
    app by HCI capture (2026-07-08): calictl's on/off frames match byte-for-byte —
    on ``3d7b007f1f3f``, off ``3c7b007f1f3f`` (``NormalOperationRequest`` 1/0, other fields
    at their leave-unchanged sentinels).

    :param funcs: loaded + overridden Function map.
    :param what: ``"power"`` or ``"level"``.
    :param value: on/off token for power, or 0-15 for level.
    :param last: current decoded airheater state (carried into the frame).
    :returns: the 6-byte control frame, or ``None`` for an unknown target.

    .. req:: Build airheater control frame
       :id: R_AIRHEATER_SET
       :status: implemented
       :tags: ble, control, airheater

       ``calictl`` shall build a full-packet airheater (char 1701) control frame
       for ``power`` (via ``NormalOperationRequest`` = 1/0) and ``level`` (via
       ``HeatingLevel`` 0-15), carrying current state for untargeted fields.
    """
    if what == "power":
        ch = {"NormalOperationRequest": 1 if _truthy(value) else 0}
    elif what == "level":
        lvl = int(value)
        if not 0 <= lvl <= 15:
            raise ValueError("airheater level must be 0-15, got %r" % value)
        ch = {"HeatingLevel": lvl}
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
# The move needs a ~1 Hz heartbeat (ig/c.java re-sends Up/Down at 1 Hz until STOP);
# a single frame will NOT complete travel. The heartbeat is driven by
# device.actuate_roof, NOT the one-shot device.actuate.
#   OPEN = Up1/Down0    CLOSE = Up0/Down1    STOP = Up0/Down0
# SafetyCounter (32-bit) is an app<->unit echo/handshake; its increment rule is
# UNVERIFIED, so we send 0 and resend the identical move frame each tick.
ROOF_MOVES = {"open": (1, 0), "close": (0, 1), "stop": (0, 0)}   # -> (Up, Down)


def roof_frame(funcs, direction: str, counter: int = 0) -> bytes:
    """Build one roof (char 1401) move frame. SAFETY-SENSITIVE + NOT-LIVE-VERIFIED.

    ``direction`` is ``open``/``close``/``stop`` (Up/Down per ``ROOF_MOVES``);
    ``counter`` fills the 32-bit ``SafetyCounter`` (echo/handshake, increment rule
    UNVERIFIED). Raises ValueError on an unknown direction."""
    if direction not in ROOF_MOVES:
        raise ValueError("roof direction must be open/close/stop, got %r" % direction)
    up, down = ROOF_MOVES[direction]
    vals = {"Up": up, "Down": down, "SafetyCounter": counter}
    return protocol.encode(funcs["roof"], vals,
                           frame_bytes=overrides.CONTROL_FRAME_BYTES["roof"])


def _roof(funcs, what, value, last):
    """Generic builder entry for roof: ``what`` is the direction (open/close/stop);
    ``value`` is unused. Returns ONE move frame — the CLI drives the 1 Hz heartbeat
    via ``device.actuate_roof``. SAFETY-SENSITIVE + NOT-LIVE-VERIFIED."""
    if what not in ROOF_MOVES:
        return None
    return roof_frame(funcs, what)


BUILDERS = {"campingmode": _camping, "cooler": _cooler, "lighting": _lighting,
            "airheater": _airheater, "roofaircondition": _roofaircondition,
            "stairs": _stairs, "livingroomheater": _livingroomheater, "roof": _roof}


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
