"""Per-function control-frame builders (full-packet, dictionary-driven)."""
from __future__ import annotations
from . import protocol

LIGHT_ON, LIGHT_OFF = 0, 1   # camping lights inverted (app K0 writes (!on)?1:0). VERIFY live.
SENTINEL = 3                 # 2-bit "leave unchanged" (sg.a default)


def camping_values(**changes) -> dict:
    """Only the changed field is set; every other camping field stays at the
    leave-unchanged sentinel 3 (the lights are inverted, so carrying a state
    value would be wrong; 3 = no-op per the app's control model)."""
    vals = {"State": SENTINEL, "UsbCharger": SENTINEL,
            "InteriorLight": SENTINEL, "OutsideLight": SENTINEL}
    vals.update(changes)
    return vals


def _camping(funcs, what, value, last):
    on = str(value).lower() in ("on", "true", "1")
    if what == "interior_light":
        ch = {"InteriorLight": LIGHT_ON if on else LIGHT_OFF}
    elif what == "outside_light":
        ch = {"OutsideLight": LIGHT_ON if on else LIGHT_OFF}
    elif what == "usb_charger":
        ch = {"UsbCharger": 1 if on else 0}
    else:
        return None
    return protocol.encode(funcs["campingmode"], camping_values(**ch), frame_bytes=1)


BUILDERS = {"campingmode": _camping}


def build(funcs, function, what, value, last_decoded):
    b = BUILDERS.get(function)
    return b(funcs, what, value, last_decoded) if b else None


def decode_control(func, frame: bytes) -> dict:
    """Decode a control frame using the function's CONTROL field offsets
    (protocol.decode uses STATE offsets, which differ)."""
    bits = protocol.to_bits(frame)
    return {f.name: protocol.get_field(bits, f.offset, f.width)
            for f in func.control_fields if f.placed}
