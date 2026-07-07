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


BUILDERS = {"campingmode": _camping, "cooler": _cooler}


def build(funcs, function, what, value, last_decoded):
    b = BUILDERS.get(function)
    return b(funcs, what, value, last_decoded) if b else None


def decode_control(func, frame: bytes) -> dict:
    """Decode a control frame using the function's CONTROL field offsets
    (protocol.decode uses STATE offsets, which differ)."""
    bits = protocol.to_bits(frame)
    return {f.name: protocol.get_field(bits, f.offset, f.width)
            for f in func.control_fields if f.placed}
