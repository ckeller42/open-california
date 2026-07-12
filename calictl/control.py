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
LIGHT_ON_BRIGHTNESS = 13         # "on" brightness (0..13; 14=unchanged sentinel, so max real=13)

# Friendly zone key -> BrightnessL control field, from the app's Lighting screen + the HCI
# capture (which nibble tracked each slider). Reading trio (Links/Rechts/Beifahrer) map to
# L1/L2/L4 — group confirmed, the left/right/passenger split within it is best-effort.
LIGHT_ZONES = {
    "reading-1": "BrightnessLTwo", "reading-2": "BrightnessLOne", "reading-3": "BrightnessLFour",
    "kitchen": "BrightnessLSeven", "roof-ambient": "BrightnessLEight", "outside-rear": "BrightnessLThree",
}


def _lighting(funcs, what, value, last):
    """Build a lighting control frame (char 1501). CRACKED via HCI capture 2026-07-08.

    The app's SET_BRIGHTNESS changes ONE zone at a time: the target zone carries the new
    brightness, every OTHER zone carries the leave-unchanged sentinel ``14`` (NOT 0 — 0 sets
    that zone to 0). ``ProfileNumber`` echoes the active profile. Verified byte-for-byte
    against the app (e.g. Kitchen=5 under profile 9 -> ``0904000000000000eeeeeee5eeeeeeee``).

    Grammar (``what``):
      * a zone key (``LIGHT_ZONES``) or a raw ``BrightnessL*`` field  -> SET_BRIGHTNESS that zone
        to ``value`` (0-13); all other zones = 14 (unchanged).
      * ``"all"`` -> set every zone to ``value``; ``"power"`` on->13 / off->0 for every zone.
      * ``"profile"`` -> SET_PROFILE (Mode 16); ``value`` = target ProfileNumber.
      * ``"color"`` -> SET_COLOR (Mode 6); ``value`` = a ``LIGHT_COLORS`` name; recolours the
        active profile (LightValue = palette index 1-10). On-device apply UNVERIFIED (re-gap A1).
    """
    f = funcs["lighting"]
    zone_fields = [cf.name for cf in f.control_fields if cf.name.startswith("BrightnessL")]
    profile = int(last.get("ProfileNumber") or 0)
    base = {"ProfileNumber": profile, "Mode": LIGHT_MODE_SET_BRIGHTNESS, "Timestamp": 0, "LightValue": 0}

    def _b(v):
        # real brightness is 0..13; 14 is the leave-unchanged sentinel and 15 is unused,
        # so neither is a valid value to *set* a zone to (would silently mean "no change").
        b = int(v)
        if not 0 <= b <= LIGHT_ON_BRIGHTNESS:
            raise ValueError("lighting brightness must be 0-%d, got %r" % (LIGHT_ON_BRIGHTNESS, v))
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
        b = LIGHT_ON_BRIGHTNESS if _truthy(value) else 0
        vals = {**base, **{z: b for z in zone_fields}}
    elif what == "all":
        b = _b(value)
        vals = {**base, **{z: b for z in zone_fields}}
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


def decode_control(func, frame: bytes) -> dict:
    """Decode a control frame using the function's CONTROL field offsets
    (protocol.decode uses STATE offsets, which differ)."""
    bits = protocol.to_bits(frame)
    return {f.name: protocol.get_field(bits, f.offset, f.width)
            for f in func.control_fields
            if f.placed and f.offset + f.width <= len(bits)}   # skip fields past a short frame
