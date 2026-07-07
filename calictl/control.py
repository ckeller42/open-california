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
    on = str(value).lower() in ("on", "true", "1")
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
    # NOTE: on-device apply is UNCONFIRMED and this frame is known to be WRONG.
    # Decompile audit (2026-07-07, docs/business-logic/re-gap-inventory.md §A1) shows the
    # app's SET_BRIGHTNESS frame carries the *currently-active* ProfileNumber (dg/h.java:615
    # sends w10.d.b(k).f5472x, never 0) and encodes on/off purely in the zone nibbles;
    # LightValue is NOT in the SET_BRIGHTNESS frame (it's the SET_PROFILE on/off lever).
    # ProfileNumber=0 addresses an inactive profile -> the unit silently ignores it, which
    # is exactly what the live test saw. Fixing this needs the active-profile wire value
    # (HCI capture of an in-app brightness change; profile->wire table w10/l.java). Until
    # then `set lighting` builds a frame and reports readback honestly (NOT APPLIED).
    #
    # SET_BRIGHTNESS: ProfileNumber pinned 0 for now (also avoids the 0x0E reject seen with
    # cross-field rule encode can't express, so we pin it here). We CARRY the
    # current per-zone brightness (the per-zone -> physical-lamp mapping is
    # UNVERIFIED, so we don't blindly light every zone):
    #   brightness <0-15> -> scale only the currently-lit zones (0 = all off),
    #                        preserving which lamps are on;
    #   power on          -> every zone to full (turns ALL zones on);
    #   power off         -> every zone dark.
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
    vals = {"ProfileNumber": 0, "Mode": LIGHT_MODE_SET_BRIGHTNESS,
            "Timestamp": 0, "LightValue": 0, **zones}
    return protocol.encode(f, vals, frame_bytes=overrides.CONTROL_FRAME_BYTES["lighting"])


BUILDERS = {"campingmode": _camping, "cooler": _cooler, "lighting": _lighting}


def build(funcs, function, what, value, last_decoded):
    b = BUILDERS.get(function)
    return b(funcs, what, value, last_decoded) if b else None


def decode_control(func, frame: bytes) -> dict:
    """Decode a control frame using the function's CONTROL field offsets
    (protocol.decode uses STATE offsets, which differ)."""
    bits = protocol.to_bits(frame)
    return {f.name: protocol.get_field(bits, f.offset, f.width)
            for f in func.control_fields if f.placed}
