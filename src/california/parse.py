from __future__ import annotations
from california.model import AttEvent

# ATT opcode -> (name, direction). Only the ones we act on; else "other".
_OPCODES = {
    0x12: ("write-req", "sent"),
    0x52: ("write-cmd", "sent"),
    0x0b: ("read-resp", "rcvd"),
    0x1b: ("notify", "rcvd"),
    0x1d: ("notify", "rcvd"),  # handle value indication
}

def _hexcolon_to_bytes(s: str) -> bytes:
    if not s:
        return b""
    return bytes.fromhex(s.replace(":", ""))

def parse_tshark(packets: list[dict]) -> list[AttEvent]:
    out: list[AttEvent] = []
    for pkt in packets:
        layers = pkt.get("_source", {}).get("layers", {})
        att = layers.get("btatt")
        if not att:
            continue
        frame = layers.get("frame", {})
        opcode = int(att.get("btatt.opcode", "0x0"), 16)
        name, direction = _OPCODES.get(opcode, ("other", "sent"))
        uuid = att.get("btatt.uuid128") or att.get("btatt.uuid16")
        out.append(AttEvent(
            frame=int(frame.get("frame.number", "0")),
            t=float(frame.get("frame.time_relative", "0") or 0.0),
            op=name,
            handle=int(att.get("btatt.handle", "0x0"), 16),
            uuid=uuid,
            value=_hexcolon_to_bytes(att.get("btatt.value", "")),
            direction=direction,
        ))
    return out
