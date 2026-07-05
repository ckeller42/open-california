from __future__ import annotations
import re
from dataclasses import dataclass

_LABEL = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<action>[a-z0-9-]+)_(?P<index>\d{3})\.(pklg|btsnoop|pcap|pcapng)$")

@dataclass(frozen=True)
class AttEvent:
    frame: int
    t: float
    op: str          # write-req | write-cmd | read-resp | notify | other
    handle: int
    uuid: str | None
    value: bytes
    direction: str   # sent | rcvd

@dataclass(frozen=True)
class Capture:
    action: str
    index: int
    date: str
    events: list[AttEvent]

def parse_label(filename: str) -> tuple[str, str, int]:
    m = _LABEL.match(filename)
    if not m:
        raise ValueError(f"bad capture filename: {filename!r}; expect YYYY-MM-DD_<action>_NNN.<ext>")
    return m["date"], m["action"], int(m["index"])
