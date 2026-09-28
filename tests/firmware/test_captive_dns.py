"""cali_captive: the captive-portal DNS responder + OS probe-path table.

``firmware/components/cali_core/captive_dns.c`` answers every A-record (QTYPE 1) query over UDP
port 53 with the hotspot's own address (TTL 0, RCODE 0); any other QTYPE gets an empty but still
successful answer (RCODE 0, ANCOUNT 0) — never NXDOMAIN. ``cali_captive_is_probe()`` is the fixed
table of paths Android/iOS/macOS/Windows/Firefox request right after joining a network to check for
a captive portal. Driven here through ``test/captive_cli.c``: a ``cali_net_t`` whose UDP ops replay
scripted datagrams and report each reply's bytes (or that it was dropped).

.. test:: Captive DNS answers every A query with the hotspot address
   :id: T_FW_CAPTIVE_DNS
   :links: R_FW_WIFI_PROVISION
"""
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
AP_IP = CONSTS["NET_AP_ADDR_U32"]


@pytest.fixture(scope="module")
def captive_cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("captive") / "captive_cli"
    subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-I", str(CORE / "include"),
                    "-I", str(ROOT / "csrc"), str(CORE / "captive_dns.c"),
                    str(CORE / "test" / "captive_cli.c"), "-o", str(out)], check=True)
    return out


def build_query(qname, qtype, qid=0x1234, flags=0x0100, qclass=1, qdcount=1):
    """A one-question DNS query message as bytes (header + QNAME + QTYPE + QCLASS)."""
    labels = b"".join(bytes([len(p)]) + p.encode() for p in qname.split(".")) + b"\x00"
    header = struct.pack(">HHHHHH", qid, flags, qdcount, 0, 0, 0)
    return header + labels + struct.pack(">HH", qtype, qclass)


def drive(cli, lines, args=()):
    proc = subprocess.run([str(cli), *args], input=("\n".join(lines) + "\n").encode(),
                           capture_output=True, check=True, timeout=10)
    assert proc.stderr.decode() == ("init=-1\n" if "nobind" in args else "init=0\n")
    return proc.stdout.decode()


def ask(cli, query, args=()):
    """Queue one datagram, poll once, return the core's reply bytes, or None if it was dropped."""
    out = drive(cli, ["dgram " + query.hex(), "poll"], args)
    lines = out.strip("\n").split("\n") if out.strip("\n") else []
    assert len(lines) == 1, lines
    if lines[0] == "DROP":
        return None
    assert lines[0].startswith("REPLY ")
    return bytes.fromhex(lines[0][len("REPLY "):])


def parse_header(reply):
    return struct.unpack(">HHHHHH", reply[:12])


def test_a_query_answered_with_ap_ip(captive_cli):
    q = build_query("example.com", 1, qid=0xBEEF, flags=0x0100)
    reply = ask(captive_cli, q)
    assert reply is not None
    qid, flags, qd, an, ns, ar = parse_header(reply)
    assert qid == 0xBEEF
    assert flags & 0x8000            # QR = 1 (response)
    assert (flags >> 11) & 0xF == 0  # OPCODE kept (0 = standard query)
    assert flags & 0x0400            # AA = 1
    assert flags & 0x0100            # RD kept from the query
    assert not flags & 0x0080        # RA = 0
    assert flags & 0x000F == 0       # RCODE 0
    assert (qd, an, ns, ar) == (1, 1, 0, 0)

    qsection = q[12:]
    assert reply[12:12 + len(qsection)] == qsection   # question echoed verbatim
    rr = reply[12 + len(qsection):]
    assert len(reply) == 12 + len(qsection) + 16
    assert rr[0:2] == b"\xC0\x0C"                     # compression pointer to the name @ offset 12
    assert struct.unpack(">H", rr[2:4])[0] == 1        # TYPE A
    assert struct.unpack(">H", rr[4:6])[0] == 1        # CLASS IN
    assert struct.unpack(">I", rr[6:10])[0] == 0       # TTL 0
    assert struct.unpack(">H", rr[10:12])[0] == 4      # RDLENGTH 4
    assert rr[12:16] == struct.pack(">I", AP_IP)       # RDATA = the hotspot's own address


def test_aaaa_query_gets_no_answer(captive_cli):
    q = build_query("example.com", 28)  # AAAA
    reply = ask(captive_cli, q)
    assert reply is not None
    _, flags, qd, an, ns, ar = parse_header(reply)
    assert flags & 0x8000
    assert flags & 0x000F == 0          # RCODE 0: never NXDOMAIN
    assert (qd, an, ns, ar) == (1, 0, 0, 0)
    assert reply[12:] == q[12:]         # just the echoed question, no answer record


@pytest.mark.parametrize("path,expected", [
    ("/generate_204", 1), ("/gen_204", 1), ("/hotspot-detect.html", 1),
    ("/library/test/success.html", 1), ("/connecttest.txt", 1), ("/ncsi.txt", 1),
    ("/canonical.html", 1), ("/success.txt", 1),
    ("/api/state", 0), ("/", 0),
])
def test_captive_probe_paths_redirect(captive_cli, path, expected):
    assert drive(captive_cli, ["probe " + path]) == "PROBE %d\n" % expected


def test_truncated_query_dropped(captive_cli):
    assert ask(captive_cli, b"\x00" * 11) is None  # shorter than the 12-byte header


def test_response_to_query_with_qr_set_dropped(captive_cli):
    q = build_query("example.com", 1, flags=0x8000)  # QR = 1: a response, not a query
    assert ask(captive_cli, q) is None


def test_qdcount_not_one_dropped(captive_cli):
    assert ask(captive_cli, build_query("example.com", 1, qdcount=2)) is None
    assert ask(captive_cli, build_query("example.com", 1, qdcount=0)) is None


def test_label_running_past_packet_dropped(captive_cli):
    header = struct.pack(">HHHHHH", 1, 0x0100, 1, 0, 0, 0)
    q = header + bytes([10]) + b"ab"  # a label claims 10 bytes; only 2 follow before the end
    assert ask(captive_cli, q) is None


def test_oversized_name_dropped(captive_cli):
    header = struct.pack(">HHHHHH", 1, 0x0100, 1, 0, 0, 0)
    labels = b"".join(bytes([6]) + b"abcdef" for _ in range(40))  # 40*7 = 280 wire bytes: over 253
    q = header + labels + b"\x00" + struct.pack(">HH", 1, 1)
    assert ask(captive_cli, q) is None


def test_several_datagrams_in_one_poll(captive_cli):
    q1 = build_query("one.example", 1, qid=1)
    q2 = build_query("two.example", 1, qid=2)
    q3 = build_query("bad", 1, qdcount=2)  # malformed: dropped
    out = drive(captive_cli, ["dgram " + q1.hex(), "dgram " + q2.hex(), "dgram " + q3.hex(), "poll"])
    lines = out.strip("\n").split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("REPLY ") and lines[1].startswith("REPLY ") and lines[2] == "DROP"
    r1 = bytes.fromhex(lines[0][len("REPLY "):])
    r2 = bytes.fromhex(lines[1][len("REPLY "):])
    assert parse_header(r1)[0] == 1 and parse_header(r2)[0] == 2  # matched to the right query by ID


def test_bind_failure_makes_poll_noop(captive_cli):
    q = build_query("example.com", 1)
    out = drive(captive_cli, ["dgram " + q.hex(), "poll"], args=["nobind"])
    assert out == ""  # udp_bind failed: poll never even calls recvfrom


def test_one_poll_answers_at_most_16_datagrams(captive_cli):
    """A flood cannot starve the tick: one poll answers at most 16 datagrams, the rest wait for the
    next poll."""
    qs = [build_query("q%d.example" % i, 1, qid=i) for i in range(20)]
    out = drive(captive_cli, ["dgram " + q.hex() for q in qs] + ["poll"])
    assert len(out.strip("\n").split("\n")) == 16
    out = drive(captive_cli, ["dgram " + q.hex() for q in qs] + ["poll", "poll"])
    lines = out.strip("\n").split("\n")
    assert len(lines) == 20 and all(line.startswith("REPLY ") for line in lines)
    assert [parse_header(bytes.fromhex(line[6:]))[0] for line in lines] == list(range(20))
