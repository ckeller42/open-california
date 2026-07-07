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
LIGHT_ON_BRIGHTNESS = 14         # zone value for "on" (0..15; the app/dict default is 14)


def _lighting(funcs, what, value, last):
    """Build a lighting SET_BRIGHTNESS frame (char 1501).

    KEY FIX (decompile audit 2026-07-08, re-gap-inventory §A1): the frame must carry
    the **currently-active ProfileNumber**, which is an identity round-trip of the
    ProfileNumber the unit reports in its 1502 state (chain: `dg/l.java` f5472x==ordinal,
    `w10/d.java` identity, `dg/a.java` readback). Sending `ProfileNumber=0` addresses an
    inactive profile and the unit silently ignores it — the bug behind the earlier
    ACK-but-no-op. So we **echo `last["ProfileNumber"]`** into the SET frame. Statically
    airtight; pending one live confirm on buspi.

    Zone handling: carry current per-zone brightness (per-zone→physical-lamp map still
    UNVERIFIED — an `ef.i`→BrightnessL[1..13] channel map exists but not the lamp binding):
      brightness <0-15> -> scale only currently-lit zones (0 = all off);
      power on/off       -> every zone to full / dark.
    """
    f = funcs["lighting"]
    zone_fields = [cf.name for cf in f.control_fields if cf.name.startswith("BrightnessL")]
    cur = {z: int(last.get(z) or 0) for z in zone_fields}
    if what == "brightness":
        b = int(value)
        if not 0 <= b <= 15:
            raise ValueError("lighting brightness must be 0-15, got %r" % value)
        zones = {z: (b if cur[z] else 0) for z in zone_fields}   # scale lit zones only
    elif what == "power":
        full = LIGHT_ON_BRIGHTNESS if _truthy(value) else 0
        zones = {z: full for z in zone_fields}
    else:
        return None
    profile = int(last.get("ProfileNumber") or 0)   # echo the active profile (the fix)
    vals = {"ProfileNumber": profile, "Mode": LIGHT_MODE_SET_BRIGHTNESS,
            "Timestamp": 0, "LightValue": 0, **zones}
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

    ``power`` on/off drives ``NormalOperationRequest`` (1=on, 0=off — verified from
    the app's ``rf/b.java`` ``C2()`` at :187); ``level`` sets ``HeatingLevel`` 0-15.
    Full-packet, MSB-first, like cooler. NOTE: not yet live-verified on-device.

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


BUILDERS = {"campingmode": _camping, "cooler": _cooler, "lighting": _lighting,
            "airheater": _airheater}


def build(funcs, function, what, value, last_decoded):
    b = BUILDERS.get(function)
    return b(funcs, what, value, last_decoded) if b else None


def decode_control(func, frame: bytes) -> dict:
    """Decode a control frame using the function's CONTROL field offsets
    (protocol.decode uses STATE offsets, which differ)."""
    bits = protocol.to_bits(frame)
    return {f.name: protocol.get_field(bits, f.offset, f.width)
            for f in func.control_fields if f.placed}
