"""The firmware's session and console, driven by a scripted fake transport (#154).

.. req:: Firmware session and console
   :id: R_FW_SESSION

   ``firmware/components/cali_core/session.c`` keeps the bonded link: after the runner reaches
   bonded (or a boot with a stored bond reconnects and re-encrypts) it discovers, subscribes every
   notifying state char, lets the heartbeat run ``CODEC_HEARTBEAT_WARMUP_MS`` (calictl's
   ``HEARTBEAT_WARMUP_S``), reads every ``CODEC_CHARS`` function in order — except one the unit
   pushed since the subscribe, and a read never overwrites a frame pushed while it was outstanding
   (as ``calictl.device.read_all``: the notification beats the stale latch) — and prints one
   ``SNAP`` of the ``codec_decode`` of each stored frame; a notification replaces that function's
   whole frame (a new ``SNAP`` once the first read-all completed). A 1003 heartbeat runs every
   ``CODEC_HEARTBEAT_PERIOD_MS`` from ``CODEC_HEARTBEAT_START`` while the link is up; a lost link —
   drop, failed connect/encryption/discovery, a heartbeat that cannot be written or fails —
   reconnects by bond after 1 s, doubling to 60 s. ``console.c`` is the line protocol (``pair`` /
   ``passkey N`` / ``forget`` / ``status`` / ``quit`` in; ``STATE`` / ``SNAP`` / ``LOG`` out); a
   passkey outside ``waiting_passkey`` is ignored. (C comments are not autodoc'd: this docstring is
   the sphinx-needs shim, like ``test_runner_fake``.)

.. test:: Session and console call/output sequences against a fake transport
   :id: T_FW_SESSION_FAKE
   :links: R_FW_SESSION, R_FW_PAIRING_RUNNER, R_FW_WRITE_ALLOWLIST, R_FW_CONTROL_API

``session_fake.c`` compiles console + session + runner + SM + ``csrc/codec.c`` with the host ``cc``
(macOS too, no NimBLE) and scripts transport events; the end-to-end proof over NimBLE against the
Bumble fake unit is ``test_host_e2e.py`` (``T_FW_HOST_E2E``).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from calictl import overrides, protocol
from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
IDENTITY = "C0:FF:EE:CA:11:F0"
CHARS = [
    0x1702,
    0x1202,
    0x1102,
    0x1602,
    0x1001,
    0xF001,
    0x1502,
    0x2102,
    0x1402,
    0x2002,
    0x1902,
    0x1802,
    0x1004,
    0x1302,
]  # csrc/codec_chars.h CODEC_CHARS order
FNS = [
    "airheater",
    "campingmode",
    "cooler",
    "energy",
    "general",
    "generalpurposesignals",
    "lighting",
    "livingroomheater",
    "roof",
    "roofaircondition",
    "satelliteantenna",
    "stairs",
    "vehicle",
    "water",
]
HB = 0x00100000
# calictl.device.HEARTBEAT_WARMUP_S as generated into the header the firmware compiles against
WARM = int(
    re.search(
        r"#define CODEC_HEARTBEAT_WARMUP_MS (\d+)", (ROOT / "csrc" / "codec_chars.h").read_text()
    ).group(1)
)
PERIOD = int(
    re.search(
        r"#define CODEC_HEARTBEAT_PERIOD_MS (\d+)", (ROOT / "csrc" / "codec_chars.h").read_text()
    ).group(1)
)


@pytest.fixture(scope="module")
def fake(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("session") / "session_fake"
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            "-I",
            str(ROOT / "csrc"),
            "-I",
            str(ROOT / "firmware/components/platform/include"),
            str(CORE / "console.c"),
            str(CORE / "snapshot.c"),
            str(CORE / "json.c"),
            str(CORE / "session.c"),
            str(CORE / "control.c"),
            str(CORE / "control_run.c"),
            str(CORE / "runner.c"),
            str(CORE / "pairing_sm.c"),
            str(ROOT / "csrc" / "codec.c"),
            str(CORE / "wifi_run.c"),
            str(CORE / "wifi_sm.c"),
            str(CORE / "captive_dns.c"),
            str(CORE / "test" / "session_fake.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def run(fake, *script):
    r = subprocess.run(
        [str(fake)], input="\n".join(script) + "\n", capture_output=True, text=True, timeout=30, check=True
    )
    return r.stdout.splitlines()


PAIRED = [
    "> pair",
    "FOUND",
    "CONNECTED",
    "PASSKEY_REQ",
    "> passkey 123456",
    "bond 1",
    "ENC_OK",
    "READ 1004 0",
]


def read_all(at=0, reads=None):
    """DISCOVERED at session time ``at`` (the last tick), the tick that ends the heartbeat warm-up,
    then the READ completions (default: every function, status 0, data 0102)."""
    return ["DISCOVERED 0", "tick %d" % (at + WARM)] + (
        reads if reads is not None else ["READ %x 0" % c for c in CHARS]
    )


READ_ALL = read_all()
T = WARM  # session time once READ_ALL has run


def after(out, marker):
    return out[out.index(marker) + 1 :]


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
    assert len(snaps(out)) == 1 and out[-1].startswith("SNAP ")  # one SNAP, after the last read


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
    script = read_all(
        reads=["READ %x %d%s" % (c, 14 if c == 0x1602 else 0, " ff" if c == 0x1102 else "") for c in CHARS]
    )
    snap = snaps(run(fake, *PAIRED, *script))[0]
    assert "energy" not in snap["fn"]  # read failed: absent
    assert snap["fn"]["cooler"] == protocol.decode(funcs["cooler"], b"\xff")


def test_notify_replaces_the_whole_frame_and_snaps_only_after_read_all(fake):
    funcs = protocol.load()
    overrides.apply(funcs)
    mid = READ_ALL[:3] + ["NOTIFY 1102 aabbccdd"] + READ_ALL[3:]  # cooler pushed mid read-all
    out = run(fake, *PAIRED, *mid, "NOTIFY 1102 11")
    s = snaps(out)
    assert len(s) == 2  # the mid notify printed none
    assert s[0]["fn"]["cooler"] == protocol.decode(funcs["cooler"], bytes.fromhex("aabbccdd"))
    assert s[1]["fn"]["cooler"] == protocol.decode(funcs["cooler"], b"\x11")
    assert {k: v for k, v in s[1]["fn"].items() if k != "cooler"} == {
        k: v for k, v in s[0]["fn"].items() if k != "cooler"
    }


def test_read_all_waits_the_heartbeat_warm_up(fake):
    """Like ``calictl.device.read_all``: after the subscribes the heartbeat runs for
    ``CODEC_HEARTBEAT_WARMUP_MS`` before the first read — no read on any earlier tick.

    .. test:: The firmware read-all waits the heartbeat warm-up
       :id: T_FW_SESSION_WARMUP
       :links: R_FW_SESSION
    """
    warming = ["tick %d" % t for t in range(100, WARM, 100)]
    out = run(fake, *PAIRED, "DISCOVERED 0", *warming)
    assert not calls(after(out, "CALL discover"), "read"), out  # still warming up
    beats = calls(out, "write_heartbeat")
    assert len(beats) >= WARM // PERIOD  # the liveness is running
    out = run(fake, *PAIRED, "DISCOVERED 0", *warming, "tick %d" % WARM)
    rest = after(out, "CALL subscribe %d" % CHARS[-1])
    first_read = rest.index("CALL read %d" % CHARS[0])
    assert calls(rest[:first_read], "write_heartbeat") == beats  # beats, then the reads


def test_push_before_its_read_skips_the_read(fake):
    """A function the unit pushed after the subscribe is not read: the notification beats the
    stale read latch (``calictl.device.read_all``), so its frame is the pushed one.

    .. test:: A pushed frame is not replaced by the read-all's read
       :id: T_FW_SESSION_PUSH_BEATS_LATCH
       :links: R_FW_SESSION
    """
    funcs = protocol.load()
    overrides.apply(funcs)
    reads = ["READ %x 0" % c for c in CHARS if c != 0x1102]
    out = run(
        fake,
        *PAIRED,
        "DISCOVERED 0",
        "NOTIFY 1102 aabbccdd",  # pushed during the warm-up
        "tick %d" % WARM,
        *reads,
    )
    rest = after(out, "CALL subscribe %d" % CHARS[-1])
    assert "CALL read %d" % 0x1102 not in rest
    assert [line for line in rest if line.startswith("CALL read")] == [
        "CALL read %d" % c for c in CHARS if c != 0x1102
    ]
    (snap,) = snaps(out)
    assert snap["fn"]["cooler"] == protocol.decode(funcs["cooler"], bytes.fromhex("aabbccdd"))


def test_push_while_its_read_is_outstanding_keeps_the_push(fake):
    """The cooler read is already on the air when the unit pushes cooler: the read's completion
    (the older latch) does not overwrite the pushed frame."""
    funcs = protocol.load()
    overrides.apply(funcs)
    reads = ["READ %x 0" % c for c in CHARS]
    i = CHARS.index(0x1102)
    out = run(fake, *PAIRED, *read_all(reads=reads[:i] + ["NOTIFY 1102 aabbccdd"] + reads[i:]))
    assert "CALL read %d" % 0x1102 in out  # it was requested
    (snap,) = snaps(out)
    assert snap["fn"]["cooler"] == protocol.decode(funcs["cooler"], bytes.fromhex("aabbccdd"))
    assert snap["fn"]["energy"] == protocol.decode(funcs["energy"], b"\x01\x02")  # the rest read


def test_heartbeat_every_period_from_the_start_counter(fake):
    out = run(fake, *PAIRED, *READ_ALL, *["tick %d" % t for t in range(T + 100, T + 1300, 100)])
    assert calls(out, "write_heartbeat") == ["CALL write_heartbeat %d" % n for n in (HB, HB + 1, HB + 2)]


def test_no_heartbeat_before_bonded(fake):
    out = run(fake, "> pair", "FOUND", "CONNECTED", *["tick %d" % t for t in range(0, 3000, 100)])
    assert not calls(out, "write_heartbeat")


def _reconnects(out):
    return [i for i, line in enumerate(out) if line == "CALL connect_bonded"]


def test_unwritable_heartbeat_drops_and_reconnects_after_1s(fake):
    t = T + PERIOD  # the beat after READ_ALL's first one
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        "fail write_heartbeat",
        "tick %d" % t,
        *["tick %d" % u for u in range(t + 100, t + 1200, 100)],
    )
    i = out.index("CALL write_heartbeat %d" % (HB + 1))
    assert out[i + 1 : i + 3] == ["LOG session: heartbeat not written -1", "CALL disconnect"]
    ticks_before = [line for line in out if line == "CALL connect_bonded"]
    assert len(ticks_before) == 1
    out2 = run(fake, *PAIRED, *READ_ALL, "fail write_heartbeat", "tick %d" % t, "tick %d" % (t + 900))
    assert not calls(out2, "connect_bonded")  # not before 1 s


def test_failed_heartbeat_completion_drops_and_reconnects(fake):
    out = run(fake, *PAIRED, *READ_ALL, "HEARTBEAT 14", "tick %d" % (T + 1000))
    rest = after(out, "LOG session: heartbeat failed 14")
    assert rest[:1] == ["CALL disconnect"] and "CALL connect_bonded" in rest


def test_backoff_doubles_to_60s_and_resets_once_encrypted(fake):
    script = list(PAIRED) + READ_ALL + ["DISCONNECTED"]
    t, at = T, []
    for delay in (1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000):
        t += delay
        script += ["tick %d" % (t - 1), "tick %d" % t, "CONNECT_FAIL"]
        at.append(t)
    out = run(fake, *script)
    assert len(calls(out, "connect_bonded")) == len(at)
    logs = [line for line in out if line.startswith("LOG session: reconnect in")]
    assert logs == [
        "LOG session: reconnect in %d ms" % d
        for d in (1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000)
    ]
    # success resets the delay
    t += 60000
    out = run(fake, *script, "tick %d" % t, "CONNECTED", "ENC_OK", "DISCONNECTED")
    assert out[-1] == "LOG session: reconnect in 1000 ms"
    assert "CALL discover" in after(out, "CALL connect_bonded")


def test_boot_with_bond_reconnects_and_reports_idle_with_address(fake):
    out = run(fake, "bond 1", "boot", "CONNECTED", "ENC_OK", *READ_ALL)
    assert out[:2] == [
        "CALL connect_bonded",  # calictl: idle + bond address
        'STATE {"state":"idle","attempts":0,"error":null,"address":"%s"}' % IDENTITY,
    ]
    assert out[2] == "CALL discover" and len(snaps(out)) == 1


def test_boot_without_bond_is_idle_and_never_scans(fake):
    out = run(fake, "boot", *["tick %d" % t for t in range(0, 5000, 100)])
    assert out == ['STATE {"state":"idle","attempts":0,"error":null,"address":null}']


def test_unencrypted_reconnect_times_out(fake):
    out = run(fake, "bond 1", "boot", "tick 0", "CONNECTED", "tick 14900", "tick 15000")
    assert after(out, "CALL connect_bonded")[1:] == [
        "LOG session: link not encrypted after 15000 ms",
        "CALL disconnect",
        "LOG session: reconnect in 1000 ms",
    ]


def test_forget_stops_the_session(fake):
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        "> forget",
        "DISCONNECTED",
        *["tick %d" % t for t in range(T, T + 5000, 100)],
    )
    rest = out[out.index("CALL remove_bond") :]
    assert rest[-1] == 'STATE {"state":"idle","attempts":0,"error":null,"address":null}'
    assert not calls(rest, "connect_bonded") and not calls(rest, "write_heartbeat")


def test_console_lines(fake):
    out = run(
        fake, "> passkey 123456", ">   status  ", "> passkey 1234567", "> passkey 12a", "> bogus", "> quit"
    )
    assert out == [
        'STATE {"state":"idle","attempts":0,"error":null,"address":null}',
        "LOG console: bad passkey",
        "LOG console: bad passkey",
        "LOG unknown command: bogus",
        "QUIT",
    ]


def test_link_drop_mid_read_all_reconnects_and_reads_afresh(fake):
    """The link drops between two READs of a read-all: nothing more goes to the dead link (no read,
    no heartbeat — also not for a late READ completion), no SNAP of the half-read set, a reconnect
    by bond after the 1 s backoff, then a fresh read-all from the first function and one SNAP."""
    half = READ_ALL[:7]  # DISCOVERED + warm-up + 5 of the 14 reads
    drop_at = T + 600
    out = run(
        fake,
        *PAIRED,
        *half,
        "tick %d" % drop_at,
        "DISCONNECTED",
        "READ %x 0" % CHARS[5],  # a completion racing the drop: stale
        *["tick %d" % t for t in range(drop_at + 100, drop_at + 1100, 100)],
        "CONNECTED",
        "ENC_OK",
        *read_all(at=drop_at + 1000),
    )
    drop = out.index("LOG session: reconnect in 1000 ms")
    reconnect = out.index("CALL connect_bonded")
    assert drop < reconnect
    dead = out[drop:reconnect]
    assert not calls(dead, "read") and not calls(dead, "write_heartbeat"), dead
    assert not snaps(out[:reconnect])  # the half-finished read-all printed none
    # the reconnect waits the backoff: not on the ticks before drop + 1000
    ticks = run(
        fake,
        *PAIRED,
        *half,
        "tick %d" % drop_at,
        "DISCONNECTED",
        *["tick %d" % t for t in range(drop_at + 100, drop_at + 1000, 100)],
    )
    assert not calls(ticks, "connect_bonded")
    fresh = out[reconnect:]
    assert fresh[1] == "CALL discover"
    assert [line for line in fresh if line.startswith("CALL read")] == [
        "CALL read %d" % c for c in CHARS
    ]  # the whole read-all again, from the start
    assert len(snaps(fresh)) == 1 and fresh[-1].startswith("SNAP ")


def test_last_update_stamps_every_stored_frame(fake):
    """``cali_session_last_update_ms`` is 0 until a frame is stored, then the latest tick's now_ms
    of the latest READ/NOTIFY store (the web page's ``link.last_snap_age_ms`` source)."""
    out = run(
        fake,
        "lastupd",
        *PAIRED,
        "lastupd",
        *READ_ALL,
        "lastupd",
        "tick %d" % (T + 700),
        "lastupd",
        "NOTIFY 1102 11",
        "lastupd",
    )
    assert [line for line in out if line.startswith("LASTUPD")] == [
        "LASTUPD 0",
        "LASTUPD 0",
        "LASTUPD %d" % T,
        "LASTUPD %d" % T,
        "LASTUPD %d" % (T + 700),
    ]


def test_snap_line_bytes_are_pinned(fake):
    """The console ``SNAP`` line, byte for byte, for a fixed frame set: only cooler read (one byte,
    so only the fields inside it), every other read failed. Pins the emitter (``snapshot.c``) and
    the line framing together — key order, no whitespace, the ``t`` of the read-all's last tick."""
    reads = ["READ %x %s" % (c, "0 01" if c == 0x1102 else "14") for c in CHARS]
    out = run(fake, *PAIRED, *read_all(reads=reads))
    assert [line for line in out if line.startswith("SNAP ")] == [
        'SNAP {"t":%d,"fn":{"cooler":{"Error":0,"NightTimerSet":0,"Installed":0,"TimerElapsed":0,'
        '"TimerState":0,"State":1}}}' % T
    ]


# ---- the WiFi runner (wifi_run.c) on the same tick as the session ----------------------------

AP = "NET ap_start %s %s" % (CONSTS["NET_AP_SSID"], CONSTS["NET_AP_PSK"])
AP_UP = "LOG wifi: setup hotspot up (%s)" % CONSTS["NET_AP_SSID"]
PSK = "test-psk-1234"


def test_wifi_off_until_booted(fake):
    """Without ``wifi_boot`` (host_main without ``--http``) the WiFi runtime is off: ``wifi`` commands
    only say so, no cali_net call happens, and ``status`` prints the unchanged STATE line."""
    out = run(
        fake,
        "> wifi status",
        "> wifi scan",
        "> wifi forget",
        "> wifi set minsel " + PSK,
        "> status",
        *["tick %d" % t for t in range(0, 1000, 100)],
    )
    assert out == ["LOG wifi: not enabled"] * 4 + [
        'STATE {"state":"idle","attempts":0,"error":null,"address":null}'
    ]


def test_wifi_boot_without_creds_opens_setup_and_scans(fake):
    out = run(fake, "wifi_boot", "NET_AP_STARTED", "NET_SCAN_DONE minsel other", "> wifi status", "> status")
    assert out == [
        AP,
        "NET udp_bind 53",
        "NET scan",
        AP_UP,
        "LOG wifi: setup ssid=- ip=- rssi=- scan=2",
        'STATE {"state":"idle","attempts":0,"error":null,"address":null,'
        '"wifi":{"mode":"setup","ssid":null,"ip":null}}',
    ]


def _online(*extra):
    return [
        "wifi_boot",
        "NET_AP_STARTED",
        "NET_SCAN_DONE minsel",
        "> wifi set minsel " + PSK,
        "NET_GOT_IP 192.168.1.42",
        *extra,
    ]


def test_wifi_set_stores_joins_and_goes_online(fake):
    out = run(
        fake,
        *_online(
            "rssi -61", "tick 100", "tick 200", "> wifi status", "> status", "kv wifi_ssid", "kv wifi_psk"
        ),
    )
    rest = after(out, AP_UP)
    assert rest == [
        "LOG wifi: joining minsel",
        "NET sta_start minsel " + PSK,
        "NET mdns %s %d" % (CONSTS["NET_HOSTNAME"], CONSTS["NET_HTTP_PORT"]),
        "LOG wifi: online 192.168.1.42",
        "LOG wifi: station ssid=minsel ip=192.168.1.42 rssi=-61 scan=1",
        'STATE {"state":"idle","attempts":0,"error":null,"address":null,'
        '"wifi":{"mode":"station","ssid":"minsel","ip":"192.168.1.42"}}',
        "KV wifi_ssid minsel",
        "KV wifi_psk " + PSK,
    ]


def test_wifi_set_rejects_bad_input_without_storing(fake):
    out = run(
        fake,
        "wifi_boot",
        "> wifi set",
        "> wifi set minsel short",
        "> wifi set " + "s" * 33 + " " + PSK,
        "> wifi bogus",
        "kv wifi_ssid",
    )
    assert after(out, "NET scan") == [
        "LOG wifi: usage: wifi set <ssid> <psk>",
        "LOG wifi: bad psk",
        "LOG wifi: bad ssid",
        "LOG unknown command: wifi bogus",
        "KV wifi_ssid missing",
    ]


def test_mistyped_wifi_line_never_echoes_the_passphrase(fake):
    """An unknown ``wifi`` subcommand (or a mistyped ``wifi`` word) logs only the command words, never
    the rest of the line: a typo'd ``wifi set`` still carries the passphrase."""
    out = run(
        fake,
        "wifi_boot",
        "> wifi sett minsel " + PSK,
        "> wifi SET minsel " + PSK,
        "> wif set minsel " + PSK,
        "> wifi\tsett minsel " + PSK,
        "kv wifi_psk",
    )
    assert not any(PSK in line for line in out)
    assert after(out, "NET scan") == [
        "LOG unknown command: wifi sett",
        "LOG unknown command: wifi SET",
        "LOG unknown command: wif",
        "LOG unknown command: wifi sett",
        "KV wifi_psk missing",
    ]


def test_wifi_forget_in_setup_keeps_the_running_hotspot(fake):
    """A forget while the setup hotspot already runs (per the runner's bookkeeping) never restarts
    it: no second ap_start, no captive-DNS re-bind — so no second ``setup hotspot up`` either."""
    out = run(fake, "wifi_boot", "NET_AP_STARTED", "NET_SCAN_DONE minsel", "> wifi forget", "> wifi status")
    assert out.count(AP) == 1 and out.count("NET udp_bind 53") == 1
    assert out.count(AP_UP) == 1
    assert after(out, AP_UP) == [
        "NET sta_stop",
        "LOG wifi: credentials cleared",
        "NET scan",
        "LOG wifi: setup ssid=- ip=- rssi=- scan=1",
    ]


def test_wifi_ap_stopped_event_clears_the_hotspot_bookkeeping(fake):
    """An ``AP_STOPPED`` the runner did not ask for (the platform's hotspot went down) clears its
    ``ap_running``: the next ``AP_START`` (here a forget) really restarts the hotspot instead of
    trusting a hotspot that is gone (the captive DNS is re-bound: its old socket closed first)."""
    out = run(fake, "wifi_boot", "NET_AP_STARTED", "NET_SCAN_DONE minsel", "NET_AP_STOPPED", "> wifi forget")
    assert out.count(AP) == 2
    assert after(out, AP_UP) == [
        "NET sta_stop",
        "LOG wifi: credentials cleared",
        AP,
        "NET close 5",
        "NET udp_bind 53",
        "NET scan",
    ]


def test_wifi_clear_creds_reports_a_failed_erase(fake):
    """``credentials cleared`` only when both erases succeeded (a missing key is success); a failed
    erase says so instead of claiming the creds are gone."""
    out = run(
        fake, "wifi_boot", "NET_AP_STARTED", "> wifi set minsel wrong-psk-99", "kverasefail 1", "NET_FAILED 2"
    )
    assert after(out, "NET sta_start minsel wrong-psk-99") == [
        "LOG wifi: failed auth",
        "LOG wifi: credential erase failed",
    ]


def test_wifi_retry_after_loss_uses_the_copied_credentials(fake):
    """R14: the runner keeps its own copy of the credentials (the console and web.c zero theirs right
    after the call): the retry after a loss joins with the same SSID + PSK."""
    out = run(fake, *_online("tick 100", "NET_LOST", "tick 200", "tick 1100", "tick 1200"))
    rest = after(out, "LOG wifi: online 192.168.1.42")
    assert rest == [
        "LOG wifi: lost",
        "NET sta_stop",
        "LOG wifi: joining minsel",
        "NET sta_start minsel " + PSK,
    ]


def test_wifi_typo_fails_back_to_setup_and_clears_creds(fake):
    out = run(
        fake,
        "wifi_boot",
        "NET_AP_STARTED",
        "> wifi set minsel wrong-psk-99",
        "NET_FAILED 2",
        "kv wifi_ssid",
        "kv wifi_psk",
        "> wifi status",
    )
    assert after(out, "NET sta_start minsel wrong-psk-99") == [
        "LOG wifi: failed auth",
        "LOG wifi: credentials cleared",
        "KV wifi_ssid missing",
        "KV wifi_psk missing",
        "LOG wifi: setup ssid=- ip=- rssi=- scan=0",
    ]


@pytest.mark.parametrize("code,name", [(1, "not_found"), (2, "auth"), (3, "other")])
def test_wifi_last_fail_is_the_logged_reason_until_new_creds(fake, code, name):
    """I1/R23: the runner keeps the reason of the last ``WACT_LOG_REASON`` for the setup page
    (``cali_wifi_run_last_fail``); new credentials (``WEV_CREDS_SET``) clear it, so an old typo's
    reason never shows on the next attempt."""
    out = run(
        fake,
        "wifi_boot",
        "NET_AP_STARTED",
        "lastfail",
        "> wifi set minsel wrong-psk-99",
        "NET_FAILED %d" % code,
        "lastfail",
        "> wifi set minsel " + PSK,
        "lastfail",
    )
    assert [line for line in out if line.startswith("LASTFAIL")] == [
        "LASTFAIL -",
        "LASTFAIL " + name,
        "LASTFAIL -",
    ]


def test_wifi_last_fail_cleared_when_online(fake):
    """A saved network that failed once (not_found, retrying) and then joins: GOT_IP clears it."""
    out = run(
        fake,
        "kvset wifi_ssid minsel",
        "kvset wifi_psk " + PSK,
        "wifi_boot",
        "NET_FAILED 1",
        "lastfail",
        "tick 100",
        "tick 1100",
        "NET_GOT_IP 192.168.1.42",
        "lastfail",
    )
    assert [line for line in out if line.startswith("LASTFAIL")] == ["LASTFAIL not_found", "LASTFAIL -"]


def test_wifi_boot_with_saved_creds_joins_and_keeps_them_on_failure(fake):
    """R6: saved credentials join at boot without a hotspot; a failure retries, never wipes them."""
    out = run(
        fake,
        "kvset wifi_ssid minsel",
        "kvset wifi_psk " + PSK,
        "wifi_boot",
        "NET_FAILED 1",
        "kv wifi_ssid",
        "tick 100",
        "tick 1100",
    )
    assert out == [
        "LOG wifi: joining minsel",
        "NET sta_start minsel " + PSK,
        "LOG wifi: failed not_found",
        "KV wifi_ssid minsel",
        "LOG wifi: joining minsel",
        "NET sta_start minsel " + PSK,
    ]


def test_wifi_forget_clears_creds_and_opens_setup(fake):
    """Online past the AP-close window (hotspot closed), a forget reopens the hotspot."""
    out = run(
        fake,
        *_online("tick 100", "tick 30100", "> wifi forget", "kv wifi_ssid", "kv wifi_psk", "NET_AP_STARTED"),
    )
    assert after(out, "LOG wifi: setup hotspot closed") == [
        "NET sta_stop",
        "LOG wifi: credentials cleared",
        AP,
        "NET udp_bind 53",
        "NET scan",
        "KV wifi_ssid missing",
        "KV wifi_psk missing",
        AP_UP,
    ]


def test_wifi_forget_inside_the_ap_close_window_keeps_the_hotspot(fake):
    """Online but the hotspot still up (inside NET_AP_CLOSE_MS): a forget keeps it running."""
    out = run(fake, *_online("> wifi forget", "kv wifi_ssid"))
    assert after(out, "LOG wifi: online 192.168.1.42") == [
        "NET sta_stop",
        "LOG wifi: credentials cleared",
        "NET scan",
        "KV wifi_ssid missing",
    ]


def test_wifi_set_while_online_replaces_and_reconnects(fake):
    """``wifi set`` while online is never a silent no-op: the new credentials replace the old ones
    (kv kept, not cleared) and the runner rejoins through the setup flow."""
    out = run(fake, *_online("> wifi set other new-psk-5678", "kv wifi_ssid", "kv wifi_psk"))
    rest = after(out, "LOG wifi: online 192.168.1.42")
    assert rest[0] == "LOG wifi: credentials replaced, reconnecting"
    assert "NET sta_stop" in rest
    assert AP not in rest  # the hotspot is still up (inside the AP-close window)
    assert "LOG wifi: credentials cleared" not in rest
    assert rest[-4:] == [
        "LOG wifi: joining other",
        "NET sta_start other new-psk-5678",
        "KV wifi_ssid other",
        "KV wifi_psk new-psk-5678",
    ]


def test_wifi_same_creds_again_while_joining_is_a_no_op(fake):
    """Review I1: the setup page retries a Connect whose answer got lost — the first POST may well
    have arrived (the join's channel switch dropped the phone). The same SSID + PSK again while that
    join runs must not restart it: no ``sta_stop``, no second ``sta_start``, no extra scan."""
    out = run(
        fake,
        "wifi_boot",
        "NET_AP_STARTED",
        "NET_SCAN_DONE minsel",
        "> wifi set minsel " + PSK,
        "tick 100",
        "> wifi set minsel " + PSK,
        "tick 200",
    )
    rest = after(out, AP_UP)
    assert rest.count("NET sta_start minsel " + PSK) == 1, rest
    assert "NET sta_stop" not in rest and "NET scan" not in rest, rest
    assert "LOG wifi: credentials replaced, reconnecting" not in rest, rest


def test_wifi_same_creds_again_while_online_keeps_the_link(fake):
    """... and once that join is online, the late retry keeps the station link up."""
    out = run(fake, *_online("> wifi set minsel " + PSK, "tick 100"))
    rest = after(out, "LOG wifi: online 192.168.1.42")
    assert not [line for line in rest if line.startswith("NET ") or "replaced" in line], rest


def test_wifi_scan_waits_while_a_scan_is_in_flight(fake):
    """A scan request while one is in flight (until its SCAN_DONE) is dropped, not queued."""
    out = run(
        fake,
        "wifi_boot",
        "> wifi scan",
        "tick 100",
        "NET_SCAN_DONE a",
        "> wifi scan",
        "> wifi scan",
        "tick 200",
    )
    assert [line for line in out if line == "NET scan"] == ["NET scan"] * 2  # boot's, then one more


def test_wifi_page_scans_at_most_once_per_interval(fake):
    """Bench walk #154: the setup page GETs ``/api/wifi`` on load and again a moment later, and every
    scan takes the shared radio off the hotspot's channel (the phone on the page drops, its Connect
    POST fails). A page-asked scan (``cali_wifi_run_scan_auto``) starts only if none started in the
    last ``NET_SCAN_MIN_INTERVAL_MS`` — the setup start's own scan counts."""
    gap = CONSTS["NET_SCAN_MIN_INTERVAL_MS"]
    out = run(
        fake,
        "wifi_boot",  # AP_START's scan, at 0
        "NET_AP_STARTED",
        "NET_SCAN_DONE a",
        "tick 100",
        "webscan",
        "tick 2100",
        "webscan",
        "tick %d" % (gap - 1),
        "webscan",
        "tick %d" % gap,
        "webscan",  # the interval is over: one more
        "NET_SCAN_DONE a",
        "tick %d" % (gap + 100),
        "webscan",
    )
    assert out.count("NET scan") == 2, out


def test_wifi_first_page_scan_runs_at_once(fake):
    """No scan yet (a station booting from saved creds never opened the hotspot): the first
    page-asked scan starts right away, the next one inside the interval does not."""
    out = run(
        fake,
        "kvset wifi_ssid minsel",
        "kvset wifi_psk " + PSK,
        "wifi_boot",
        "tick 5000",
        "webscan",
        "NET_SCAN_DONE a",
        "tick 5100",
        "webscan",
    )
    assert out.count("NET scan") == 1, out


def test_wifi_failed_scan_keeps_the_last_list(fake):
    """M2: a scan the radio refused or aborted (a join in flight: SCAN_DONE with nscan -1) keeps
    the last good list instead of blanking it ("No networks found"), and ends the in-flight scan so
    the next request starts one; an empty but successful scan still empties the list."""
    out = run(
        fake,
        "wifi_boot",
        "NET_SCAN_DONE minsel other",
        "> wifi scan",
        "NET_SCAN_FAILED",
        "> wifi status",
        "> wifi scan",
        "NET_SCAN_DONE",
        "> wifi status",
    )
    assert [line for line in out if line.startswith("LOG wifi: unprovisioned") or "scan=" in line] == [
        "LOG wifi: setup ssid=- ip=- rssi=- scan=2",
        "LOG wifi: setup ssid=- ip=- rssi=- scan=0",
    ]
    assert out.count("NET scan") == 3  # boot's, then one per request: the failed one is not in flight


def test_wifi_scan_deferred_while_ble_pairing_is_active(fake):
    """R15, the one BLE-coex gate: no WiFi scan while a pairing flow is active (runner state not idle,
    bonded or error); the request runs on the first tick after the flow ends."""
    out = run(
        fake,
        "wifi_boot",
        "NET_SCAN_DONE a",
        "> pair",
        "> wifi scan",
        "tick 100",
        "FOUND",
        "CONNECTED",
        "tick 200",
        "PASSKEY_REQ",
        "> passkey 123456",
        "bond 1",
        "ENC_OK",
        "READ 1004 0",
        "tick 300",
    )
    start = next(i for i, line in enumerate(out) if line.startswith("CALL start_scan"))
    bonded = out.index('STATE {"state":"bonded","attempts":0,"error":null,"address":"%s"}' % IDENTITY)
    assert "NET scan" not in out[start:bonded], out
    assert "NET scan" in out[bonded:]


def test_wifi_scan_not_blocked_by_pairing_error(fake):
    """PAIR_ERROR has no timeout: it must not block scans forever."""
    out = run(
        fake, "wifi_boot", "NET_SCAN_DONE a", "> pair", "tick 0", "tick 30000", "> status", "> wifi scan"
    )
    assert json.loads(out[-2][6:])["state"] == "error", out
    assert out[-1] == "NET scan"


def test_wifi_loss_does_not_touch_session(fake):
    """The WiFi dropping and the runner's retries never reach the BLE session: no transport call but
    the heartbeat, no ``LOG session:`` line, the beats keep their period and a push still SNAPs.

    .. test:: WiFi loss leaves the BLE session alone
       :id: T_FW_WIFI_LOSS_SESSION
       :links: R_FW_WIFI_BLE_COEX, R_FW_SESSION
    """
    ticks = ["tick %d" % t for t in range(T + 100, T + 3100, 100)]
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        *_online(),
        "NET_LOST",
        *ticks,
        "NET_GOT_IP 192.168.1.42",
        "NET_LOST",
        "NOTIFY 1102 11",
    )
    rest = after(out, "LOG wifi: online 192.168.1.42")
    assert "LOG wifi: lost" in rest
    assert not [line for line in rest if line.startswith("LOG session:")], rest
    assert [line for line in rest if line.startswith("CALL")] == [
        "CALL write_heartbeat %d" % n for n in range(HB + 1, HB + 1 + 3000 // PERIOD)
    ]
    assert rest[-1].startswith("SNAP ")


# ---- the control path (#154 B) -------------------------------------------------------------------

from tools import gen_control_vectors  # noqa: E402
from tools.mock_unit import _pack_state  # noqa: E402

_CH = (ROOT / "csrc" / "codec_chars.h").read_text()
ARM = int(re.search(r"#define CODEC_ARM_DELAY_MS (\d+)", _CH).group(1))
FOLLOW = int(re.search(r"#define CODEC_FOLLOW_DELAY_MS (\d+)", _CH).group(1))
TICK = 100  # the session tick (host_main.c / app_main.c TICK_MS)
DEADLINE = int(
    re.search(r"#define CALI_CTL_DEADLINE_MS (\d+)", (CORE / "include" / "cali_control.h").read_text()).group(
        1
    )
)
FUNCS = protocol.load()
overrides.apply(FUNCS)


def pack(fn, **fields):
    return _pack_state(FUNCS[fn], fields).hex()


def armed(frames=None, reads=None):
    """Bonded, read-all served ``frames`` ({state char: hex}; others the default 0102), and the tick
    that ends the arm delay (the link came up at session time 0)."""
    frames = frames or {}
    reads = reads or ["READ %x 0%s" % (c, " " + frames[c] if c in frames else "") for c in CHARS]
    return [*PAIRED, *read_all(reads=reads), "tick %d" % ARM]


def states_of(frames=None):
    frames = frames or {}
    return {fn: protocol.decode(FUNCS[fn], bytes.fromhex(frames.get(c, "0102"))) for c, fn in zip(CHARS, FNS)}


def writes(out):
    return [line for line in out if line.startswith("CALL write ")]


def want_writes(fn, what, value, frames=None):
    e = gen_control_vectors.expect(FUNCS, fn, what, value, states_of(frames))
    assert e["kind"] == "frames", e
    return ["CALL write %s %s" % (f["char"], f["hex"]) for f in e["frames"]]


def test_control_waits_for_an_armed_link(fake):
    """A command needs an armed link and the function's state; never a defaults frame.

    .. test:: A command needs an armed link and the function's state; never a defaults frame
       :id: T_FW_CONTROL_READY
       :links: R_FW_CONTROL_API
    """
    out = run(fake, *PAIRED, *READ_ALL, "> set cooler power on", "tick %d" % (WARM + 100))
    assert "LOG control: cooler/power not ready (no armed link or no state yet)" in out and not writes(out)


def test_control_waits_for_the_first_read_all(fake):
    reads = ["READ %x 0" % c for c in CHARS[:5]]  # the arm delay passes with the read-all unfinished
    out = run(
        fake,
        *PAIRED,
        *read_all(reads=reads),
        "tick %d" % ARM,
        "> set cooler power on",
        "tick %d" % (ARM + 100),
    )
    assert "LOG control: cooler/power not ready (no armed link or no state yet)" in out and not writes(out)


def test_control_needs_the_target_state(fake):
    reads = ["READ %x %d" % (c, 14 if c == 0x1102 else 0) for c in CHARS]  # the cooler read failed
    out = run(fake, *armed(reads=reads), "> set cooler power on", "tick %d" % (ARM + 100))
    assert "LOG control: cooler/power not ready (no armed link or no state yet)" in out and not writes(out)


def test_control_sends_calictls_frame_once_armed(fake):
    frames = {0x1102: pack("cooler", Installed=1, State=0, Mode=4, Level=3)}
    out = run(fake, *armed(frames), "> set cooler power on", "tick %d" % (ARM + 100), "WRITTEN 1101 0")
    assert writes(out) == want_writes("cooler", "power", "on", frames)
    assert "LOG control: cooler/power sending" in out and out[-1] == "LOG control: cooler/power sent"


def test_control_lighting_commit_follows_after_the_follow_delay(fake):
    t = ARM + 100
    early = run(
        fake,
        *armed(),
        "> set lighting kitchen 5",
        "tick %d" % t,
        "WRITTEN 1501 0",
        "tick %d" % (t + FOLLOW),  # an ACK just before the next tick: FOLLOW after the tick is too early
    )
    assert len(writes(early)) == 1
    out = run(
        fake,
        *armed(),
        "> set lighting kitchen 5",
        "tick %d" % t,
        "WRITTEN 1501 0",
        "tick %d" % (t + FOLLOW + TICK),
        "WRITTEN 1501 0",
    )
    assert writes(out) == want_writes("lighting", "kitchen", "5")
    assert out[-1] == "LOG control: lighting/kitchen sent"


def test_control_save_profile_colour_is_four_writes_in_actuate_order(fake):
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "> set lighting save_profile 3 amber",
        "tick %d" % t,
        "WRITTEN 1501 0",
        "tick %d" % (t + FOLLOW + TICK),
        "WRITTEN 1501 0",
        "tick %d" % (t + FOLLOW + 2 * TICK),  # the save frame: no delay, the next tick
        "WRITTEN 1501 0",
        "tick %d" % (t + 2 * FOLLOW + 3 * TICK),
        "WRITTEN 1501 0",
    )
    assert writes(out) == want_writes("lighting", "save_profile", "3 amber")
    assert len(writes(out)) == 4 and out[-1] == "LOG control: lighting/save_profile sent"


def test_control_att_error_fails_without_commit(fake):
    """A write the unit refuses fails the command and gets no commit.

    .. test:: A write the unit refuses fails the command and gets no commit
       :id: T_FW_CONTROL_ATT_ERROR
       :links: R_FW_CONTROL_API
    """
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "> set lighting kitchen 5",
        "tick %d" % t,
        "WRITTEN 1501 3",
        *["tick %d" % (t + d) for d in range(100, 2000, 100)],
    )
    assert len(writes(out)) == 1
    assert "LOG control: lighting/kitchen failed: the unit refused the write" in out


def test_control_write_failing_inside_the_call_fails_the_command(fake):
    """The NimBLE transport reports a write it cannot start (e.g. its op queue fails the ATT
    request at once) as a WRITTEN error *inside* ``write()``: that completion must count, not be
    taken for a stray ACK and leave the sequencer waiting (then busy) for one that never comes."""
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "syncwritten 3",
        "> set lighting kitchen 5",
        "tick %d" % t,
        *["tick %d" % (t + d) for d in range(100, 1000, 100)],
        "> set cooler level 2",
    )
    assert len([w for w in writes(out) if w.startswith("CALL write 1501 ")]) == 1  # no commit
    assert "LOG control: lighting/kitchen failed: the unit refused the write" in out
    assert "LOG control: cooler/level sending" in out  # not stuck busy


def test_control_link_drop_fails_the_command(fake):
    """A link drop mid-command fails it cleanly.

    .. test:: A link drop mid-command fails it cleanly
       :id: T_FW_CONTROL_LINK_DROP
       :links: R_FW_CONTROL_API
    """
    t = ARM + 100
    out = run(fake, *armed(), "> set cooler level 2", "tick %d" % t, "DISCONNECTED", "tick %d" % (t + 100))
    assert "LOG control: cooler/level failed: link lost" in out


def test_control_link_drop_between_frames_sends_no_commit(fake):
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "> set lighting kitchen 5",
        "tick %d" % t,
        "WRITTEN 1501 0",
        "DISCONNECTED",
        *["tick %d" % (t + d) for d in range(100, 1000, 100)],
    )
    assert len(writes(out)) == 1
    assert "LOG control: lighting/kitchen failed: link lost" in out


def test_control_timeout_then_late_ack_keeps_busy_until_acked(fake):
    """A timed-out write blocks the next command until its late ACK, which completes nothing.

    .. test:: A timed-out write blocks the next command until its late ACK, which completes nothing
       :id: T_FW_CONTROL_LATE_ACK
       :links: R_FW_CONTROL_API
    """
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "> set cooler level 2",
        "tick %d" % t,
        *["tick %d" % (t + d) for d in range(500, DEADLINE + 500, 500)],
        "> set cooler level 3",
        "WRITTEN 1101 0",
        "> set cooler level 3",
        "tick %d" % (t + DEADLINE + 600),
    )
    w1, w2 = want_writes("cooler", "level", 2)[0], want_writes("cooler", "level", 3)[0]
    assert [line for line in out if line.startswith(("LOG control:", "CALL write "))] == [
        "LOG control: cooler/level sending",
        w1,
        "LOG control: cooler/level timed out",
        "LOG control: cooler/level busy",  # the unacknowledged write still blocks
        "LOG control: cooler/level sending",  # its late ACK freed the link, completing nothing
        w2,
    ]


def test_control_busy_while_one_is_running(fake):
    out = run(fake, *armed(), "> set cooler level 2", "> set cooler level 3")
    assert "LOG control: cooler/level busy" in out


def test_control_survives_interleaved_heartbeat_and_notify(fake):
    """Heartbeat completions and pushes between a write and its ACK change nothing.

    .. test:: Heartbeat completions and pushes between a write and its ACK change nothing
       :id: T_FW_CONTROL_INTERLEAVE
       :links: R_FW_CONTROL_API
    """
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "> set lighting kitchen 5",
        "tick %d" % t,
        "HEARTBEAT 0",
        "NOTIFY 1502 %s" % pack("lighting", Mode=4, BrightnessLSeven=3),
        "HEARTBEAT 0",
        "WRITTEN 1501 0",
        "tick %d" % (t + FOLLOW + TICK),
        "HEARTBEAT 0",
        "WRITTEN 1501 0",
    )
    assert writes(out) == want_writes("lighting", "kitchen", "5")
    assert out[-1] == "LOG control: lighting/kitchen sent"


def test_control_heartbeat_keeps_ticking_through_a_command(fake):
    t = ARM + 100
    out = run(fake, *armed(), "> set lighting kitchen 5", *["tick %d" % (t + d) for d in range(0, 1300, 100)])
    rest = after(out, "LOG control: lighting/kitchen sending")
    assert len(writes(rest)) == 1  # no ACK: the commit waits
    assert len(calls(rest, "write_heartbeat")) >= 1200 // PERIOD


def test_control_gate_uses_the_latest_notify(fake):
    from calictl import control

    frames = {0x1102: pack("cooler", Installed=1, State=0)}
    out = run(
        fake,
        *armed(frames),
        "NOTIFY 1102 %s" % pack("cooler", Installed=1, State=1),
        "> set cooler timer_set 07:00",
    )
    assert "LOG control: cooler/timer_set refused: %s" % control.REASON_COOLER_TIMER_NEEDS_FRIDGE_OFF in out
    assert not writes(out)


@pytest.mark.parametrize(
    "line",
    ["> set roof open", "> set roof stop", "> set lighting wakeup 07:00 on", "> set stairs move extend"],
)
def test_control_roof_and_wakeup_are_refused_without_a_write(fake, line):
    fn_what = "/".join(line.split()[2:4])
    out = run(fake, *armed(), line, "tick %d" % (ARM + 100))
    assert "LOG control: %s refused: Only via buspi or the app" % fn_what in out and not writes(out)


def test_control_transport_refusal_is_a_failed_write(fake):
    out = run(fake, *armed(), "fail write", "> set cooler level 2", "tick %d" % (ARM + 100))
    assert "LOG control: cooler/level failed: write not issued" in out


@pytest.mark.parametrize("line", ["> set", "> set cooler", "> set  cooler  "])
def test_control_set_usage(fake, line):
    out = run(fake, line)
    assert "LOG control: usage: set <function> <what> [value]" in out


def test_control_after_a_link_drop_the_relinked_satellite_is_not_busy(fake):
    """The transport drops its queue with the link (no WRITTEN ever comes): once a new link is
    up and armed, the next command goes out instead of answering busy forever."""
    t = ARM + 100
    up = t + 1200  # the session reconnects 1 s after the drop
    out = run(
        fake,
        *armed(),
        "> set cooler level 2",
        "tick %d" % t,
        "DISCONNECTED",
        "tick %d" % (t + 100),
        "tick %d" % up,
        "CONNECTED",
        "ENC_OK",
        *read_all(at=up),
        "tick %d" % (up + ARM),
        "> set cooler level 3",
        "tick %d" % (up + ARM + 100),
    )
    assert "CALL connect_bonded" in out
    assert "LOG control: cooler/level busy" not in out
    assert writes(out) == [want_writes("cooler", "level", 2)[0], want_writes("cooler", "level", 3)[0]]


def test_control_stray_written_between_frames_skips_nothing(fake):
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "> set lighting kitchen 5",
        "tick %d" % t,
        "WRITTEN 1501 0",
        "WRITTEN 1501 0",  # a duplicate completion while the commit waits its follow delay
        "tick %d" % (t + FOLLOW + TICK),
        "WRITTEN 1501 0",
    )
    assert writes(out) == want_writes("lighting", "kitchen", "5")
    assert out[-1] == "LOG control: lighting/kitchen sent"


def test_control_never_gates_on_the_previous_links_frame(fake):
    """A frame read on an earlier link is not this link's state: when the new link's read of it
    fails, the command is not ready — never built on the stale frame."""
    frames = {0x1102: pack("cooler", Installed=1, State=0, Mode=4, Level=3)}
    reads = ["READ %x %d" % (c, 14 if c == 0x1102 else 0) for c in CHARS]  # link 2: cooler read fails
    up = ARM + 1200
    out = run(
        fake,
        *armed(frames),
        "DISCONNECTED",
        "tick %d" % (ARM + 100),
        "tick %d" % up,
        "CONNECTED",
        "ENC_OK",
        *read_all(at=up, reads=reads),
        "tick %d" % (up + ARM),
        "> set cooler power on",
        "tick %d" % (up + ARM + 100),
    )
    assert "LOG control: cooler/power not ready (no armed link or no state yet)" in out and not writes(out)


@pytest.mark.parametrize(
    "line, answer",
    [
        ("> set cooler bogus", "no such control"),  # control.build returns None
        ("> set energy bogus 1", "no such control"),
        ("> set lighting bogus 1", "bad value"),  # the lighting builder raises (as calictl's)
        ("> set cooler level", "bad value"),  # no value
    ],
)
def test_control_unknown_control_or_bad_value_answers_without_an_armed_link(fake, line, answer):
    fn_what = "/".join(line.split()[2:4])
    out = run(fake, *PAIRED, line)
    assert "LOG control: %s %s" % (fn_what, answer) in out and not writes(out)


def test_control_gate_on_unknown_state_waits_for_the_link(fake):
    """A gate that refuses for want of state (night_on with no cooler state, R4) is not answered
    before the link is armed: it is "not ready", the honest answer while the state is still coming."""
    out = run(fake, *PAIRED, "> set cooler night_on")
    assert "LOG control: cooler/night_on not ready (no armed link or no state yet)" in out
