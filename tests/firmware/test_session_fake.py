"""The firmware's session and console, driven by a scripted fake transport (#154).

.. req:: Firmware session and console
   :id: R_FW_SESSION

   ``firmware/components/cali_core/session.c`` keeps the bonded link: after the runner reaches
   bonded (or a boot with a stored bond reconnects and re-encrypts) it discovers, subscribes every
   notifying state char, reads every ``CODEC_CHARS`` function in order and prints one ``SNAP`` of
   the ``codec_decode`` of each stored frame; a notification replaces that function's whole frame
   (a new ``SNAP`` once the first read-all completed). A 1003 heartbeat runs every
   ``CODEC_HEARTBEAT_PERIOD_MS`` from ``CODEC_HEARTBEAT_START`` while the link is up; a lost link —
   drop, failed connect/encryption/discovery, a heartbeat that cannot be written or fails —
   reconnects by bond after 1 s, doubling to 60 s. ``console.c`` is the line protocol (``pair`` /
   ``passkey N`` / ``forget`` / ``status`` / ``quit`` in; ``STATE`` / ``SNAP`` / ``LOG`` out); a
   passkey outside ``waiting_passkey`` is ignored. (C comments are not autodoc'd: this docstring is
   the sphinx-needs shim, like ``test_runner_fake``.)

.. test:: Session and console call/output sequences against a fake transport
   :id: T_FW_SESSION_FAKE
   :links: R_FW_SESSION, R_FW_PAIRING_RUNNER

``session_fake.c`` compiles console + session + runner + SM + ``csrc/codec.c`` with the host ``cc``
(macOS too, no NimBLE) and scripts transport events; the end-to-end proof over NimBLE against the
Bumble fake unit is ``test_host_e2e.py`` (``T_FW_HOST_E2E``).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from calictl import overrides, protocol

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
IDENTITY = "C0:FF:EE:CA:11:F0"
CHARS = [0x1702, 0x1202, 0x1102, 0x1602, 0x1001, 0xF001, 0x1502, 0x2102, 0x1402, 0x2002, 0x1902,
         0x1802, 0x1004, 0x1302]                      # csrc/codec_chars.h CODEC_CHARS order
FNS = ["airheater", "campingmode", "cooler", "energy", "general", "generalpurposesignals",
       "lighting", "livingroomheater", "roof", "roofaircondition", "satelliteantenna", "stairs",
       "vehicle", "water"]
HB = 0x00100000


@pytest.fixture(scope="module")
def fake(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("session") / "session_fake"
    # -DCODEC_NO_ENCODE: the read-only firmware's codec, so a cali_core use of codec_encode fails here too
    subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-DCODEC_NO_ENCODE",
                    "-I", str(CORE / "include"),
                    "-I", str(ROOT / "csrc"), "-I", str(ROOT / "firmware/components/platform/include"),
                    str(CORE / "console.c"), str(CORE / "session.c"), str(CORE / "runner.c"),
                    str(CORE / "pairing_sm.c"), str(ROOT / "csrc" / "codec.c"),
                    str(CORE / "test" / "session_fake.c"), "-o", str(out)], check=True)
    return out


def run(fake, *script):
    r = subprocess.run([str(fake)], input="\n".join(script) + "\n", capture_output=True, text=True,
                       timeout=30, check=True)
    return r.stdout.splitlines()


PAIRED = ["> pair", "FOUND", "CONNECTED", "PASSKEY_REQ", "> passkey 123456", "bond 1", "ENC_OK",
          "READ 1004 0"]
READ_ALL = ["DISCOVERED 0"] + ["READ %x 0" % c for c in CHARS]


def after(out, marker):
    return out[out.index(marker) + 1:]


def snaps(out):
    return [json.loads(line[5:]) for line in out if line.startswith("SNAP ")]


def calls(out, name):
    return [line for line in out if line.startswith("CALL " + name)]


def test_bonded_discovers_subscribes_then_reads_every_function_in_order(fake):
    out = run(fake, *PAIRED, *READ_ALL)
    rest = after(out, 'STATE {"state":"bonded","attempts":0,"error":null,"address":"%s"}' % IDENTITY)
    assert rest[0] == "CALL discover"
    assert rest[1:15] == ["CALL subscribe %d" % c for c in CHARS]
    assert [line for line in rest if line.startswith("CALL read")] == ["CALL read %d" % c for c in CHARS]
    assert len(snaps(out)) == 1 and out[-1].startswith("SNAP ")    # one SNAP, after the last read


def test_snap_is_codec_decode_of_every_stored_frame(fake):
    funcs = protocol.load()
    overrides.apply(funcs)
    snap = snaps(run(fake, *PAIRED, *READ_ALL))[0]
    assert list(snap["fn"]) == FNS
    for fn in FNS:
        assert snap["fn"][fn] == protocol.decode(funcs[fn], b"\x01\x02"), fn


def test_short_frame_and_failed_read(fake):
    funcs = protocol.load()
    overrides.apply(funcs)
    script = ["DISCOVERED 0"] + ["READ %x %d%s" % (c, 14 if c == 0x1602 else 0,
                                                    " ff" if c == 0x1102 else "") for c in CHARS]
    snap = snaps(run(fake, *PAIRED, *script))[0]
    assert "energy" not in snap["fn"]                                  # read failed: absent
    assert snap["fn"]["cooler"] == protocol.decode(funcs["cooler"], b"\xff")


def test_notify_replaces_the_whole_frame_and_snaps_only_after_read_all(fake):
    funcs = protocol.load()
    overrides.apply(funcs)
    mid = READ_ALL[:3] + ["NOTIFY 1102 aabbccdd"] + READ_ALL[3:]      # cooler pushed mid read-all
    out = run(fake, *PAIRED, *mid, "NOTIFY 1102 11")
    s = snaps(out)
    assert len(s) == 2                                                 # the mid notify printed none
    assert s[0]["fn"]["cooler"] == protocol.decode(funcs["cooler"], b"\x01\x02")  # its read came later
    assert s[1]["fn"]["cooler"] == protocol.decode(funcs["cooler"], b"\x11")
    assert {k: v for k, v in s[1]["fn"].items() if k != "cooler"} == \
        {k: v for k, v in s[0]["fn"].items() if k != "cooler"}


def test_heartbeat_every_period_from_the_start_counter(fake):
    out = run(fake, *PAIRED, *READ_ALL, *["tick %d" % t for t in range(0, 1300, 100)])
    assert calls(out, "write_heartbeat") == ["CALL write_heartbeat %d" % n for n in (HB, HB + 1, HB + 2)]


def test_no_heartbeat_before_bonded(fake):
    out = run(fake, "> pair", "FOUND", "CONNECTED", *["tick %d" % t for t in range(0, 3000, 100)])
    assert not calls(out, "write_heartbeat")


def _reconnects(out):
    return [i for i, line in enumerate(out) if line == "CALL connect_bonded"]


def test_unwritable_heartbeat_drops_and_reconnects_after_1s(fake):
    out = run(fake, *PAIRED, *READ_ALL, "fail write_heartbeat", "tick 0",
              *["tick %d" % t for t in range(100, 1200, 100)])
    i = out.index("CALL write_heartbeat %d" % HB)
    assert out[i + 1:i + 3] == ["LOG session: heartbeat not written -1", "CALL disconnect"]
    ticks_before = [line for line in out if line == "CALL connect_bonded"]
    assert len(ticks_before) == 1
    out2 = run(fake, *PAIRED, *READ_ALL, "fail write_heartbeat", "tick 0", "tick 900")
    assert not calls(out2, "connect_bonded")                          # not before 1 s


def test_failed_heartbeat_completion_drops_and_reconnects(fake):
    out = run(fake, *PAIRED, *READ_ALL, "tick 0", "HEARTBEAT 14", "tick 1000")
    rest = after(out, "LOG session: heartbeat failed 14")
    assert rest[:1] == ["CALL disconnect"] and "CALL connect_bonded" in rest


def test_backoff_doubles_to_60s_and_resets_once_encrypted(fake):
    script = list(PAIRED) + READ_ALL + ["DISCONNECTED"]
    t, at = 0, []
    for delay in (1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000):
        t += delay
        script += ["tick %d" % (t - 1), "tick %d" % t, "CONNECT_FAIL"]
        at.append(t)
    out = run(fake, *script)
    assert len(calls(out, "connect_bonded")) == len(at)
    logs = [line for line in out if line.startswith("LOG session: reconnect in")]
    assert logs == ["LOG session: reconnect in %d ms" % d
                    for d in (1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000)]
    # success resets the delay
    t += 60000
    out = run(fake, *script, "tick %d" % t, "CONNECTED", "ENC_OK", "DISCONNECTED")
    assert out[-1] == "LOG session: reconnect in 1000 ms"
    assert "CALL discover" in after(out, "CALL connect_bonded")


def test_boot_with_bond_reconnects_and_reports_idle_with_address(fake):
    out = run(fake, "bond 1", "boot", "CONNECTED", "ENC_OK", *READ_ALL)
    assert out[:2] == ["CALL connect_bonded",                          # calictl: idle + bond address
                       'STATE {"state":"idle","attempts":0,"error":null,"address":"%s"}' % IDENTITY]
    assert out[2] == "CALL discover" and len(snaps(out)) == 1


def test_boot_without_bond_is_idle_and_never_scans(fake):
    out = run(fake, "boot", *["tick %d" % t for t in range(0, 5000, 100)])
    assert out == ['STATE {"state":"idle","attempts":0,"error":null,"address":null}']


def test_unencrypted_reconnect_times_out(fake):
    out = run(fake, "bond 1", "boot", "tick 0", "CONNECTED", "tick 14900", "tick 15000")
    assert after(out, "CALL connect_bonded")[1:] == [
        "LOG session: link not encrypted after 15000 ms", "CALL disconnect",
        "LOG session: reconnect in 1000 ms"]


def test_forget_stops_the_session(fake):
    out = run(fake, *PAIRED, *READ_ALL, "> forget", "DISCONNECTED",
              *["tick %d" % t for t in range(0, 5000, 100)])
    rest = out[out.index("CALL remove_bond"):]
    assert rest[-1] == 'STATE {"state":"idle","attempts":0,"error":null,"address":null}'
    assert not calls(rest, "connect_bonded") and not calls(rest, "write_heartbeat")


def test_console_lines(fake):
    out = run(fake, "> passkey 123456", ">   status  ", "> passkey 1234567", "> passkey 12a",
              "> bogus", "> quit")
    assert out == ['STATE {"state":"idle","attempts":0,"error":null,"address":null}',
                   "LOG console: bad passkey", "LOG console: bad passkey",
                   "LOG unknown command: bogus", "QUIT"]


def test_link_drop_mid_read_all_reconnects_and_reads_afresh(fake):
    """The link drops between two READs of a read-all: nothing more goes to the dead link (no read,
    no heartbeat — also not for a late READ completion), no SNAP of the half-read set, a reconnect
    by bond after the 1 s backoff, then a fresh read-all from the first function and one SNAP."""
    half = READ_ALL[:6]                                   # DISCOVERED + the first 5 of 14 reads
    out = run(fake, *PAIRED, "tick 0", *half, "tick 600", "DISCONNECTED",
              "READ %x 0" % CHARS[5],                     # a completion racing the drop: stale
              *["tick %d" % t for t in range(700, 1700, 100)],
              "CONNECTED", "ENC_OK", *READ_ALL)
    drop = out.index("LOG session: reconnect in 1000 ms")
    reconnect = out.index("CALL connect_bonded")
    assert drop < reconnect
    dead = out[drop:reconnect]
    assert not calls(dead, "read") and not calls(dead, "write_heartbeat"), dead
    assert not snaps(out[:reconnect])                     # the half-finished read-all printed none
    # the reconnect waits the backoff: not on the ticks before 1600 (drop at 600 + 1000)
    ticks = run(fake, *PAIRED, "tick 0", *half, "tick 600", "DISCONNECTED",
                *["tick %d" % t for t in range(700, 1600, 100)])
    assert not calls(ticks, "connect_bonded")
    fresh = out[reconnect:]
    assert fresh[1] == "CALL discover"
    assert [line for line in fresh if line.startswith("CALL read")] == \
        ["CALL read %d" % c for c in CHARS]               # the whole read-all again, from the start
    assert len(snaps(fresh)) == 1 and fresh[-1].startswith("SNAP ")
