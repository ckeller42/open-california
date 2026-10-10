"""List the BLE link lifecycle in a ``btsnoop_hci.log``: connects and disconnects with HCI reason.

Usage (from the repo root)::

    python3 -m tools.applab.phone.snoop_links btsnoop_hci.log

Reasons worth knowing: ``0x13`` remote user terminated, ``0x16`` local host terminated, ``0x08``
supervision timeout, ``0x3e`` failed to establish. Times: see ``snoop_att`` (raw, verify the offset).
"""

import struct
import sys

from tools.applab.phone.snoop_att import clock, records

LE_CONN_COMPLETE = (0x01, 0x0A, 0x29)  # LE meta subevents: legacy, enhanced, enhanced v2


def link_events(path: str) -> list[str]:
    """One line per LE connection complete / disconnection complete event + the matching commands."""
    out = []
    for _flags, ts, p in records(path):
        t = clock(ts)
        if len(p) < 3:
            continue
        if p[0] == 0x04 and p[1] == 0x05 and len(p) >= 7:  # Disconnection Complete
            _st, hdl, rsn = struct.unpack("<BHB", p[3:7])
            out.append(f"{t} DISCONNECT handle={hdl:#x} reason={rsn:#04x}")
        elif p[0] == 0x04 and p[1] == 0x3E and p[3] in LE_CONN_COMPLETE and len(p) >= 8:
            st, hdl, role = struct.unpack("<BHB", p[4:8])
            out.append(
                f"{t} LE_CONNECT status={st:#04x} handle={hdl:#x} role={'central' if role == 0 else 'peripheral'}"
            )
        elif p[0] == 0x01:  # HCI command
            op = struct.unpack("<H", p[1:3])[0]
            if op in (0x200D, 0x2043):
                out.append(f"{t} cmd LE_CREATE_CONNECTION")
            elif op == 0x0406 and len(p) >= 7:
                hdl, rsn = struct.unpack("<HB", p[4:7])
                out.append(f"{t} cmd DISCONNECT handle={hdl:#x} reason={rsn:#04x}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    print("\n".join(link_events(argv[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
