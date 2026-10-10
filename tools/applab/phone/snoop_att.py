"""List the ATT traffic in an Android ``btsnoop_hci.log`` with the unit's short char ids (stdlib).

Usage (from the repo root)::

    python3 -m tools.applab.phone.snoop_att btsnoop_hci.log [gatt.txt]

Prints one line per ATT PDU: ``time dir conn op char hex``. ``>`` = phone -> unit, ``<`` = unit ->
phone. SMP (L2CAP cid 6, the pairing keys) is never decoded or printed.

Handle -> char: the capture's own discovery (Read By Type responses) when it has one. The real app
reuses its cached GATT DB, so usually it has none — then pass ``gatt.txt``, the ``2803`` lines of
the host's BlueZ cache for the unit (``grep -h 2803 /var/lib/bluetooth/*/cache/<UNIT MAC>``,
``0x0010=2803:0011:12:<uuid>``): value handle h -> uuid, CCCD assumed at h+1.

Times are the raw btsnoop timestamps rendered as UTC, NOT converted to any zone: verify the offset
against a marked action before you trust a wall-clock time (Android has been seen 2 h off).
"""

import struct
import sys
from collections.abc import Iterator
from datetime import UTC, datetime

BTSNOOP_EPOCH_DELTA_US = 0x00DCDDB30F2F8000  # µs from 0000-01-01 to 1970-01-01

OPS = {
    0x01: "ERROR",
    0x0A: "READ_REQ",
    0x0B: "READ_RSP",
    0x12: "WRITE_REQ",
    0x13: "WRITE_RSP",
    0x1B: "NOTIFY",
    0x1D: "INDICATE",
    0x52: "WRITE_CMD",
}
HANDLE_OPS = {0x0A, 0x12, 0x1B, 0x1D, 0x52}  # PDUs that carry an attribute handle


def records(path: str) -> Iterator[tuple[int, int, bytes]]:
    """Yield ``(flags, timestamp_us, packet)`` per btsnoop record (flags bit 0: 1 = received)."""
    with open(path, "rb") as f:
        if f.read(8) != b"btsnoop\0":
            raise ValueError(f"{path}: not a btsnoop file")
        f.read(8)  # version + datalink
        while len(h := f.read(24)) == 24:
            _olen, ilen, flags, _drops, ts = struct.unpack(">IIIIq", h)
            yield flags, ts, f.read(ilen)


def clock(ts: int) -> str:
    """btsnoop timestamp -> ``HH:MM:SS.mmm`` (raw, see the module docstring)."""
    return datetime.fromtimestamp((ts - BTSNOOP_EPOCH_DELTA_US) / 1e6, UTC).strftime("%H:%M:%S.%f")[:-3]


def short(uuid: str) -> str:
    """``0000100361774b7d…`` -> ``1003`` (the unit's chars share one base, the id is bytes 2-3)."""
    return uuid[4:8] + uuid[32:] if len(uuid) >= 32 else uuid


def load_cache(path: str) -> dict[int, str]:
    """BlueZ GATT cache ``2803`` lines -> {handle: uuid}; the CCCD at value handle + 1."""
    handles = {}
    with open(path) as f:
        for line in f:
            fields = line.strip().partition("=")[2].split(":")
            if len(fields) == 4 and fields[0] == "2803":
                vh, uuid = int(fields[1], 16), fields[3].replace("-", "")
                handles[vh], handles[vh + 1] = uuid, uuid + ".cccd"
    return handles


def att_rows(path: str, handles: dict[int, str]) -> list[tuple[str, str, int, str, int | None, str]]:
    """Reassemble L2CAP over ACL and return ``(time, dir, conn, op, handle|None, hex)`` per ATT PDU.

    Read By Type responses found on the way are added to ``handles`` (in-capture discovery).
    """
    frag: dict[tuple[int, bool], bytes] = {}
    rows = []
    for flags, ts, pkt in records(path):
        if not pkt or pkt[0] != 0x02:  # HCI ACL only
            continue
        hdr = struct.unpack("<H", pkt[1:3])[0]
        conn, pb = hdr & 0xFFF, (hdr >> 12) & 3
        sent = not flags & 1
        key = (conn, sent)
        if pb == 1:  # continuation fragment
            if key not in frag:
                continue
            frag[key] += pkt[5:]
        else:
            frag[key] = pkt[5:]
        buf = frag[key]
        if len(buf) < 4:
            continue
        l2len, cid = struct.unpack("<HH", buf[:4])
        if len(buf) - 4 < l2len:
            continue
        del frag[key]
        if cid != 4:  # ATT only — skips SMP (cid 6)
            continue
        att = buf[4 : 4 + l2len]
        op = att[0]
        if op == 0x09 and len(att) > 1 and att[1] in (7, 21):  # Read By Type rsp: 0x2803 declarations
            n = att[1]
            for i in range(2, len(att) - n + 1, n):
                vh = struct.unpack("<H", att[i + 3 : i + 5])[0]
                u = att[i + 5 : i + n]
                handles[vh] = u[::-1].hex() if len(u) == 16 else "%04x" % struct.unpack("<H", u)[0]
        if op in HANDLE_OPS:
            h = struct.unpack("<H", att[1:3])[0]
            rows.append((clock(ts), ">" if sent else "<", conn, OPS[op], h, att[3:].hex()))
        elif op in OPS:
            rows.append((clock(ts), ">" if sent else "<", conn, OPS[op], None, att[1:].hex()))
    return rows


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print(__doc__, file=sys.stderr)
        return 2
    handles = load_cache(argv[1]) if len(argv) == 2 else {}
    for t, d, conn, op, h, val in att_rows(argv[0], handles):
        char = "" if h is None else short(handles[h]) if h in handles else "h%04x" % h
        print(t, d, "c%x" % conn, op.ljust(9), char.ljust(6), val)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
