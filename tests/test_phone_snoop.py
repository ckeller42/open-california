"""tools/applab/phone snoop decoders on a synthetic btsnoop file (no phone, no capture committed)."""

import struct

from tools.applab.phone import snoop_att, snoop_links

UNIT = "00001003-6c77-4b7d-bbf6-a5e587701f3d"
TS = snoop_att.BTSNOOP_EPOCH_DELTA_US + (12 * 3600 + 34 * 60 + 56) * 1_000_000 + 789_000  # 12:34:56.789


def acl(conn, l2cap, pb=0b10):
    return b"\x02" + struct.pack("<HH", conn | pb << 12, len(l2cap)) + l2cap


def att(payload, cid=4):
    return struct.pack("<HH", len(payload), cid) + payload


def btsnoop(path, recs):
    with open(path, "wb") as f:
        f.write(b"btsnoop\0" + struct.pack(">II", 1, 1002))
        for flags, pkt in recs:
            f.write(struct.pack(">IIIIq", len(pkt), len(pkt), flags, 0, TS) + pkt)


def test_att_rows_cache_fragments_and_no_smp(tmp_path, capsys):
    write = att(b"\x12" + struct.pack("<H", 0x0011) + bytes.fromhex("00000001"))
    notify = att(b"\x1b" + struct.pack("<H", 0x0011) + bytes.fromhex("cafe"))
    smp = att(b"\x01" + b"\xaa" * 6, cid=6)
    snoop = tmp_path / "btsnoop_hci.log"
    btsnoop(
        snoop,
        [
            (0, acl(0x40, write[:5])),  # sent, first fragment
            (0, acl(0x40, write[5:], pb=0b01)),  # continuation
            (1, acl(0x40, notify)),  # received
            (0, acl(0x40, smp)),  # pairing keys: never printed
        ],
    )
    cache = tmp_path / "gatt.txt"
    cache.write_text(f"0x0010=2803:0011:1a:{UNIT}\n")

    assert snoop_att.main([str(snoop), str(cache)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "12:34:56.789 > c40 WRITE_REQ 1003   00000001",
        "12:34:56.789 < c40 NOTIFY    1003   cafe",
    ]
    assert snoop_att.load_cache(str(cache))[0x12] == UNIT.replace("-", "") + ".cccd"


def test_att_rows_in_capture_discovery(tmp_path):
    uuid = bytes.fromhex(UNIT.replace("-", ""))[::-1]
    rsp = att(b"\x09\x15" + struct.pack("<HBH", 0x0010, 0x1A, 0x0011) + uuid)
    snoop = tmp_path / "s.log"
    btsnoop(snoop, [(1, acl(1, rsp)), (0, acl(1, att(b"\x52\x11\x00\x01")))])
    handles = {}
    rows = snoop_att.att_rows(str(snoop), handles)
    assert snoop_att.short(handles[0x11]) == "1003"
    assert rows[-1][3:] == ("WRITE_CMD", 0x11, "01")


def test_link_events(tmp_path):
    snoop = tmp_path / "s.log"
    le_conn = b"\x04\x3e\x13\x01" + struct.pack("<BHB", 0, 0x40, 0) + b"\x00" * 15
    disc_cmd = b"\x01\x06\x04\x03" + struct.pack("<HB", 0x40, 0x13)
    disc_evt = b"\x04\x05\x04" + struct.pack("<BHB", 0, 0x40, 0x16)
    btsnoop(snoop, [(0, b"\x01\x0d\x20\x00"), (1, le_conn), (0, disc_cmd), (1, disc_evt)])
    assert [line.split(" ", 1)[1] for line in snoop_links.link_events(str(snoop))] == [
        "cmd LE_CREATE_CONNECTION",
        "LE_CONNECT status=0x00 handle=0x40 role=central",
        "cmd DISCONNECT handle=0x40 reason=0x13",
        "DISCONNECT handle=0x40 reason=0x16",
    ]
