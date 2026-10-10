"""The firmware's session and console, driven by a scripted fake transport (#154).

.. req:: Firmware session and console
   :id: R_FW_SESSION

   ``firmware/components/cali_core/session.c`` keeps the bonded link: after the runner reaches
   bonded (or a boot with a stored bond reconnects and re-encrypts) it discovers, subscribes every
   notifying state char, lets the heartbeat run ``CODEC_HEARTBEAT_WARMUP_MS`` (calictl's
   ``HEARTBEAT_WARMUP_S``), reads every ``CODEC_CHARS`` function in order, and prints one
   ``SNAP`` of the ``codec_decode`` of each stored frame. Read completions and notifications both
   replace that function's whole frame, so the LAST frame to arrive wins (the app's order: it
   subscribes, then reads; one decoder for both, decompile 2026-10-07 — as ``calictl.device.read_all``,
   ``R_READ_LAST_FRAME_WINS``). Once the first read-all completed a notification prints a new
   ``SNAP``, and water (``1302``) is re-read every ``CALI_SESSION_WATER_REREAD_MS`` while the link
   is up, so a parked latch served at connect is corrected without a reconnect. A 1003 heartbeat runs every
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
    # cooler's read came after the push: the last frame (the read) wins
    assert s[0]["fn"]["cooler"] == protocol.decode(funcs["cooler"], b"\x01\x02")
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


STALE_WATER = "03011d010016"  # FreshWaterLevel 1: a parked latch pushed on the subscribe
LIVE_WATER = "03111d010016"  # FreshWaterLevel 17: what the read that follows returns
REREAD = int(
    re.search(
        r"#define CALI_SESSION_WATER_REREAD_MS (\d+)u", (CORE / "include" / "cali_session.h").read_text()
    ).group(1)
)


def _water(snap):
    funcs = protocol.load()
    overrides.apply(funcs)
    return snap["fn"]["water"], funcs["water"]


def test_read_after_the_subscribe_push_wins(fake):
    """The unit pushes a stale water frame on the subscribe; the read-all still reads water and
    that read — the last frame — is what the SNAP (and so ``/api/state``) shows. The app subscribes
    1302 then reads it, and one decoder takes both (decompile 2026-10-07).

    .. test:: A read after the subscribe push wins (last frame wins, app order)
       :id: T_FW_SESSION_PUSH_BEATS_LATCH
       :links: R_FW_SESSION
    """
    reads = ["READ %x 0%s" % (c, " " + LIVE_WATER if c == 0x1302 else "") for c in CHARS]
    out = run(fake, *PAIRED, "DISCOVERED 0", "NOTIFY 1302 " + STALE_WATER, "tick %d" % WARM, *reads)
    rest = after(out, "CALL subscribe %d" % CHARS[-1])
    assert [line for line in rest if line.startswith("CALL read")] == ["CALL read %d" % c for c in CHARS]
    (snap,) = snaps(out)
    got, f = _water(snap)
    assert got == protocol.decode(f, bytes.fromhex(LIVE_WATER))


def test_push_while_its_read_is_outstanding_then_the_read_wins(fake):
    """The cooler read is on the air when the unit pushes cooler; the read completion arrives
    last, so it wins (no frame is privileged over another — only arrival order counts)."""
    funcs = protocol.load()
    overrides.apply(funcs)
    reads = ["READ %x 0" % c for c in CHARS]
    i = CHARS.index(0x1102)
    out = run(fake, *PAIRED, *read_all(reads=reads[:i] + ["NOTIFY 1102 aabbccdd"] + reads[i:]))
    (snap,) = snaps(out)
    assert snap["fn"]["cooler"] == protocol.decode(funcs["cooler"], b"\x01\x02")


def test_notify_after_the_read_wins(fake):
    """A water notification after the read-all replaces the read (and SNAPs)."""
    out = run(fake, *PAIRED, *READ_ALL, "NOTIFY 1302 " + LIVE_WATER)
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(LIVE_WATER))


def test_water_is_re_read_periodically_while_the_link_is_up(fake):
    """Water is re-read every ``CALI_SESSION_WATER_REREAD_MS`` after the read-all (calictl reads
    1302 on every 30 s poll), so a stale latch served at connect is corrected the moment a real
    (higher) reading comes in; the re-read's frame SNAPs. A later LATCH notify (a fresh drop with the
    grey tank frozen) no longer overrides the plausible reading — the stale-latch guard holds it
    (``test_water_latch_is_held_vs_a_plausible_baseline``).

    .. test:: The firmware re-reads water periodically while the link is up
       :id: T_FW_SESSION_WATER_REREAD
       :links: R_FW_SESSION
    """
    stale = ["READ %x 0%s" % (c, " " + STALE_WATER if c == 0x1302 else "") for c in CHARS]
    early = run(fake, *PAIRED, *read_all(reads=stale), "tick %d" % (T + REREAD - 100))
    assert calls(after(early, "CALL read %d" % 0x1302), "read") == []  # not before the period
    out = run(
        fake,
        *PAIRED,
        *read_all(reads=stale),
        "tick %d" % (T + REREAD - 100),
        "tick %d" % (T + REREAD),
        "READ 1302 0 " + LIVE_WATER,
    )
    rest = after(out, "CALL read %d" % 0x1302)  # the read-all's own water read
    assert calls(rest, "read") == ["CALL read %d" % 0x1302]  # the periodic re-read
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(LIVE_WATER))  # the live read wins (a rise)
    out = run(
        fake,
        *PAIRED,
        *read_all(reads=stale),
        "tick %d" % (T + REREAD),
        "READ 1302 0 " + LIVE_WATER,
        "NOTIFY 1302 " + STALE_WATER,
        "tick %d" % (T + 2 * REREAD),
    )
    got, f = _water(snaps(out)[-1])
    # The latch notify (fresh 17->1, grey frozen) is held: the plausible LIVE reading stays shown.
    assert got == protocol.decode(f, bytes.fromhex(LIVE_WATER))
    assert len(calls(out, "read %d" % 0x1302)) == 3  # read-all + two periodic re-reads


# Water stale-latch guard (calictl.freshness.implausible_water_drop, ported to the ESP session).
# Fresh drop + grey frozen = the parked latch -> hold; any grey movement (or a rise) = live -> adopt.
PLAUSIBLE_WATER = LIVE_WATER  # fresh 17, waste 0
LATCH_WATER = STALE_WATER  # fresh 1,  waste 0 (grey frozen) -> held
GREY_MOVED_WATER = "03011d010516"  # fresh 1,  waste 5 (grey moved) -> live, adopted
REFILL_WATER = "03141d010016"  # fresh 20, waste 0 (a rise)      -> adopted


def _served_water(fake, *extra):
    """SNAP the water frame after a read-all whose 1302 read returns PLAUSIBLE_WATER, then ``extra``."""
    reads = ["READ %x 0%s" % (c, " " + PLAUSIBLE_WATER if c == 0x1302 else "") for c in CHARS]
    out = run(fake, *PAIRED, *read_all(reads=reads), *extra)
    got, f = _water(snaps(out)[-1])
    return got, f, out


def test_water_latch_is_held_vs_a_plausible_baseline(fake):
    """A fresh-water drop while the grey tank is frozen is the parked latch: hold the last plausible
    reading rather than serve the low (serve.py's stale guard, now on the ESP).

    .. test:: The session holds the last plausible water reading against the parked latch
       :id: T_FW_SESSION_WATER_LATCH
       :links: R_FW_SESSION
    """
    got, f, _ = _served_water(fake, "NOTIFY 1302 " + LATCH_WATER, "tick %d" % (T + 100))
    assert got == protocol.decode(f, bytes.fromhex(PLAUSIBLE_WATER))  # held, not the latch


def test_water_drop_with_grey_movement_is_adopted(fake):
    """A fresh drop WITH grey movement proves the unit is live-measuring — adopt it, don't hold."""
    got, f, _ = _served_water(fake, "NOTIFY 1302 " + GREY_MOVED_WATER, "tick %d" % (T + 100))
    assert got == protocol.decode(f, bytes.fromhex(GREY_MOVED_WATER))


def test_water_refill_is_adopted(fake):
    """A rising fresh level is never a latch (it is a refill / re-measure) — adopt it."""
    got, f, _ = _served_water(fake, "NOTIFY 1302 " + REFILL_WATER, "tick %d" % (T + 100))
    assert got == protocol.decode(f, bytes.fromhex(REFILL_WATER))


def test_water_plausible_reading_is_persisted(fake):
    """A plausible water read is written to NVS (WATER_GOOD_KEY) so a reboot can restore it.

    .. test:: The session persists the plausible water baseline
       :id: T_FW_SESSION_WATER_PERSIST
       :links: R_FW_SESSION
    """
    reads = ["READ %x 0%s" % (c, " " + PLAUSIBLE_WATER if c == 0x1302 else "") for c in CHARS]
    out = run(fake, *PAIRED, *read_all(reads=reads), "kvhex water_good")
    assert ("KV water_good " + PLAUSIBLE_WATER) in out  # persisted, hex-for-hex


def test_water_baseline_restored_from_nvs_rejects_a_latch_on_boot(fake):
    """A fresh boot (new process) pre-seeded with a persisted baseline still rejects the parked
    latch on the very first read — the reboot-while-parked case serve.py's ``_water_good`` covers."""
    latch_reads = ["READ %x 0%s" % (c, " " + LATCH_WATER if c == 0x1302 else "") for c in CHARS]
    out = run(
        fake,
        "kvsethex water_good " + PLAUSIBLE_WATER,  # NVS as if written by a previous boot
        "reinit",  # boot the session with that NVS present
        *PAIRED,
        *read_all(reads=latch_reads),
    )
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(PLAUSIBLE_WATER))  # restored baseline held


SEED_WATER = "03161d010016"  # fresh 22, waste 0: buspi's banked last-plausible reading


def test_water_seed_replaces_a_cold_start_latch_baseline(fake):
    """After a reflash the session cold-starts on the parked latch (it becomes the baseline);
    ``water seed <hex>`` hands it the last plausible reading — persisted, shown, and the next latch
    is held against it.

    .. test:: The console seeds the persisted fresh-water baseline
       :id: T_FW_SESSION_WATER_SEED
       :links: R_FW_SESSION
    """
    latch_reads = ["READ %x 0%s" % (c, " " + LATCH_WATER if c == 0x1302 else "") for c in CHARS]
    out = run(
        fake,
        *PAIRED,
        *read_all(reads=latch_reads),  # cold start: the latch is the baseline
        "> water seed " + SEED_WATER,
        "NOTIFY 1302 " + LATCH_WATER,  # the next parked read
        "tick %d" % (T + 100),
        "kvhex water_good",
    )
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(SEED_WATER))  # seeded value held vs the latch
    assert ("KV water_good " + SEED_WATER) in out  # persisted


APP_CAPTURE_WATER = "03141d010016"  # fresh 20, waste 0: the real unit's 1302 read, 2026-10-10 app capture


def test_water_real_drop_with_grey_zero_is_served_and_becomes_the_baseline(fake):
    """Regression 2026-10-10: grey reads 0 on every frame on this van, so the old "any fresh drop
    with grey frozen" rule held the real 22 -> 20 L drop forever. Only a drop to <=
    ``CALI_SESSION_WATER_LATCH_MAX_L`` (the observed 1 L latch) is held; 20 L is live — served,
    persisted as the new baseline — and a following 1 L latch is held against it.

    .. test:: The session serves a real fresh-water drop and holds only the 1 L latch
       :id: T_FW_SESSION_WATER_LATCH_FLOOR
       :links: R_FW_SESSION
    """
    reads = ["READ %x 0%s" % (c, " " + APP_CAPTURE_WATER if c == 0x1302 else "") for c in CHARS]
    out = run(
        fake,
        "kvsethex water_good " + SEED_WATER,  # 22 L baseline from an earlier boot
        "reinit",
        *PAIRED,
        *read_all(reads=reads),
        "kvhex water_good",
    )
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(APP_CAPTURE_WATER))  # 20 L served live
    assert ("KV water_good " + APP_CAPTURE_WATER) in out  # and is the new baseline
    out = run(
        fake,
        "kvsethex water_good " + SEED_WATER,
        "reinit",
        *PAIRED,
        *read_all(reads=reads),
        "NOTIFY 1302 " + LATCH_WATER,  # the parked 1 L latch
        "tick %d" % (T + 100),
    )
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(APP_CAPTURE_WATER))  # latch held vs 20 L


def test_water_seed_rejects_a_wrong_length_frame(fake):
    """A frame that is not exactly the water frame length is refused; the baseline is untouched."""
    out = run(fake, "> water seed 0316", "> water seed " + SEED_WATER + "00", "kvhex water_good")
    assert sum(line.startswith("LOG water: seed rejected") for line in out) == 2
    assert "KV water_good missing" in out


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


KICKED = int(
    re.search(
        r"#define CALI_SESSION_KICKED_RECONNECT_MS (\d+)u",
        (CORE / "include" / "cali_session.h").read_text(),
    ).group(1)
)


def test_unit_kick_after_read_all_paces_the_reconnect(fake):
    """The parked unit terminates an idle held link (HCI 0x13, remote user terminated) ~15-20 s
    after each connect while tolerating calictl's 30 s connect-read-release poll (field morning
    2026-10-09, #264): a remote-terminated drop AFTER this link's read-all reconnects on that poll
    cadence instead of hammering the 1 s backoff — the kick loop becomes a unit-approved duty
    cycle. The paced delay never feeds the exponential backoff state.

    .. test:: A unit-initiated kick after read-all paces the reconnect to the poll cadence
       :id: T_FW_SESSION_KICK_PACED
       :links: R_FW_SESSION
    """
    out = run(fake, *PAIRED, *READ_ALL, "DISCONNECTED 19", "tick %d" % (T + KICKED - 100))
    assert "LOG session: reconnect in %d ms" % KICKED in out
    assert not calls(after(out, "LOG session: link lost (event 6, status 19)"), "connect_bonded")
    out = run(fake, *PAIRED, *READ_ALL, "DISCONNECTED 19", "tick %d" % (T + KICKED))
    assert calls(after(out, "LOG session: reconnect in %d ms" % KICKED), "connect_bonded")
    # the pace is not backoff state: the next non-kick loss starts at the 1 s minimum again
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        "DISCONNECTED 19",
        "tick %d" % (T + KICKED),
        "CONNECT_FAIL",
    )
    assert out[-1] == "LOG session: reconnect in 1000 ms"


def test_nimble_encoded_kick_is_recognised(fake):
    """ble_nimble.c forwards disconnect.reason NimBLE host-encoded (0x200 + HCI): the field log's
    status 531 is the same kick."""
    out = run(fake, *PAIRED, *READ_ALL, "DISCONNECTED 531", "tick %d" % (T + 100))
    assert "LOG session: reconnect in %d ms" % KICKED in out


def test_kick_before_read_all_keeps_the_backoff(fake):
    """A remote-terminate before this link's read-all completed is not the parked kick (the unit
    drops half-set-up links for other reasons): normal backoff."""
    out = run(fake, *PAIRED, "DISCONNECTED 19")
    assert out[-1] == "LOG session: reconnect in 1000 ms"


def test_other_drop_after_read_all_keeps_the_backoff(fake):
    out = run(fake, *PAIRED, *READ_ALL, "DISCONNECTED 8")
    assert out[-1] == "LOG session: reconnect in 1000 ms"


def test_kick_with_an_active_viewer_keeps_the_backoff(fake):
    """Somebody is watching the page (/api/state served within CALI_SESSION_VIEWER_ACTIVE_MS): a
    kicked link reconnects on the fast backoff so the page stays ~live while the parked unit keeps
    kicking; once the viewer goes stale the 30 s pacing applies again.

    .. test:: A kicked link keeps the fast backoff while a viewer is active
       :id: T_FW_SESSION_KICK_VIEWER
       :links: R_FW_SESSION
    """
    out = run(fake, *PAIRED, *READ_ALL, "webseen", "DISCONNECTED 19")
    assert out[-1] == "LOG session: reconnect in 1000 ms"
    stale = T + KICKED  # the webseen at T is exactly the window old: no longer a viewer
    out = run(fake, *PAIRED, *READ_ALL, "webseen", "tick %d" % stale, "DISCONNECTED 19")
    assert out[-1] == "LOG session: reconnect in %d ms" % KICKED


def test_command_during_the_kick_pause_connects_now(fake):
    """A control command must not wait out the 30 s pause (#264): the submit still answers
    NOT_READY (there is no link), but the pending reconnect is made due at once, so the client's
    retry lands in seconds.

    .. test:: A control command during the kick pause reconnects at once
       :id: T_FW_SESSION_KICK_NUDGE
       :links: R_FW_SESSION
    """
    t = T + 200
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        "DISCONNECTED 19",
        "tick %d" % (t - 100),
        "submit - cooler level 2",
        "tick %d" % t,
    )
    assert "SUBMIT 7 -" in out  # CALI_CTL_NOT_READY
    assert calls(after(out, "SUBMIT 7 -"), "connect_bonded")


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


def test_connect_attempt_without_any_verdict_times_out(fake):
    """A connect whose terminal event never arrives (e.g. silently cancelled under a scan, or a
    transport that lost its verdict) must not wedge the session in CONNECTING — field night
    2026-10-08 (#264): the satellite sat link-down for 45 minutes with the unit awake."""
    out = run(fake, "bond 1", "boot", "tick 0", "tick 19900", "tick 20000")
    assert after(out, "CALL connect_bonded")[1:] == [
        "LOG session: no connect verdict after 20000 ms",
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


def want_writes(fn, what, value, frames=None, latch=None, local_now=None):
    states = states_of(frames)
    if latch:
        states["lighting"] = {**states["lighting"], **latch}
    e = gen_control_vectors.expect(FUNCS, fn, what, value, states, local_now)
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
    """A link drop mid-command ends it cleanly: FAILED while nothing went out, UNCONFIRMED once a
    frame left — the unit may have actuated it with the confirming ACK lost to the drop (field
    2026-10-09, #264: a parked-unit kick between the write and its ACK, the write had landed).

    .. test:: A link drop mid-command fails it cleanly, or reports it unconfirmed once a frame left
       :id: T_FW_CONTROL_LINK_DROP
       :links: R_FW_CONTROL_API
    """
    t = ARM + 100
    out = run(fake, *armed(), "> set cooler level 2", "DISCONNECTED", "tick %d" % t)
    assert not writes(out)
    assert "LOG control: cooler/level failed: link lost" in out
    out = run(fake, *armed(), "> set cooler level 2", "tick %d" % t, "DISCONNECTED", "tick %d" % (t + 100))
    assert len(writes(out)) == 1
    assert "LOG control: cooler/level unconfirmed: link lost after the write" in out


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
    assert "LOG control: lighting/kitchen unconfirmed: link lost after the write" in out


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
    ["> set roof open", "> set roof stop", "> set stairs move extend"],
)
def test_control_roof_and_others_are_refused_without_a_write(fake, line):
    fn_what = "/".join(line.split()[2:4])
    out = run(fake, *armed(), line, "tick %d" % (ARM + 100))
    assert "LOG control: %s refused: Only via buspi or the app" % fn_what in out and not writes(out)


def test_console_wakeup_is_refused_for_want_of_a_clock(fake):
    """The ESP has no clock; the console has no page: refused with the clock reason, armed or not."""
    from tools.gen_c_dict import ESP_WAKEUP_CLOCK_REASON

    for script in (PAIRED, armed()):
        out = run(fake, *script, "> set lighting wakeup 07:00 on", "tick %d" % (ARM + 100))
        assert "LOG control: lighting/wakeup refused: %s" % ESP_WAKEUP_CLOCK_REASON in out and not writes(out)


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


from calictl import semantics  # noqa: E402

WAKE_0700_OFF = pack("lighting", Mode=20, ProfileNumber=14, Timestamp=7 * 3600, LightValue=0x1100)
FAVS_3_EMPTY = pack("lighting", Mode=12, ProfileNumber=0, LightValue=0b1111011)
MODE4 = pack("lighting", Mode=4, BrightnessLSeven=3)
DOOR_ON = pack("lighting", Mode=16, ProfileNumber=8, LightValue=1)


def _daemon_latch(*hexes):
    """semantics.lighting_config chained over the 1502 frames, like serve._last['lighting']."""
    cfg = {}
    for hx in hexes:
        cfg = semantics.lighting_config(cfg, protocol.decode(FUNCS["lighting"], bytes.fromhex(hx)))
    return cfg


def test_lighting_config_latches_from_the_units_frames(fake):
    """A READ and the later NOTIFYs latch like serve: the wake-up survives a Mode-4 frame after it.

    .. test:: The satellite latches the unit's lighting config from its own 1502 frames only
       :id: T_FW_LIGHT_LATCH
       :links: R_FW_WAKEUP
    """
    unit = (WAKE_0700_OFF, DOOR_ON, MODE4)
    out = run(fake, *armed({0x1502: FAVS_3_EMPTY}), *("NOTIFY 1502 %s" % hx for hx in unit))
    lit = snaps(out)[-1]["fn"]["lighting"]
    want = _daemon_latch(FAVS_3_EMPTY, *unit)
    assert set(want) == set(semantics.LIGHT_CONFIG_KEYS)  # the case covers all four keys
    assert {k: lit[k] for k in semantics.LIGHT_CONFIG_KEYS if k in lit} == want
    assert lit["Mode"] == 4  # the frame itself is still the latest one


def test_own_write_is_never_latched(fake):
    """R4: the ESP's own save_profile 3 frame (Mode 4, PN 3 — which would add bit 3 if it were a unit
    frame) goes to 1501 and never reaches the latch; slot 3 stays empty and its gate still refuses."""
    from calictl import control

    t = ARM + 100
    out = run(
        fake,
        *armed({0x1502: FAVS_3_EMPTY}),
        "> set lighting save_profile 3",
        "tick %d" % t,
        "WRITTEN 1501 0",
        "tick %d" % (t + FOLLOW + TICK),
        "WRITTEN 1501 0",
        "NOTIFY 1502 0102",  # an unrelated unit frame: a fresh SNAP after the write
        "> set lighting profile 3",
    )
    assert "LOG control: lighting/save_profile sent" in out
    assert snaps(out)[-1]["fn"]["lighting"]["FavouritesStored"] == 0b1111011
    assert "LOG control: lighting/profile refused: %s" % control.REASON_FAVOURITE_EMPTY in out


def test_the_latch_gates_after_the_frame_moved_on(fake):
    """The favourite gate reads the latch, not only the current frame (serve's rule)."""
    from calictl import control

    out = run(fake, *armed({0x1502: FAVS_3_EMPTY}), "NOTIFY 1502 %s" % MODE4, "> set lighting profile 3")
    assert "LOG control: lighting/profile refused: %s" % control.REASON_FAVOURITE_EMPTY in out


def test_previous_links_latch_is_shown_but_never_gates(fake):
    """Review focus 3: after a reconnect the old favourite bits stay on screen (SNAP) but the gate on the
    new link does not use them — profile 3 goes out. Once the unit reports again, it gates again."""
    from calictl import control

    up = ARM + 1200
    relinked = [
        *armed({0x1502: FAVS_3_EMPTY}),
        "DISCONNECTED",
        "tick %d" % (ARM + 100),
        "tick %d" % up,
        "CONNECTED",
        "ENC_OK",
        *read_all(at=up),
        "tick %d" % (up + ARM),
    ]
    out = run(fake, *relinked, "> set lighting profile 3", "tick %d" % (up + ARM + 100))
    assert snaps(out)[-1]["fn"]["lighting"]["FavouritesStored"] == 0b1111011
    assert "LOG control: lighting/profile sending" in out
    again = run(fake, *relinked, "NOTIFY 1502 %s" % FAVS_3_EMPTY, "> set lighting profile 3")
    assert "LOG control: lighting/profile refused: %s" % control.REASON_FAVOURITE_EMPTY in again


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


# ---- the wake-up: the page's clock + the REQUEST_CONFIG pull inside one command (R5) ---------------

from calictl import control  # noqa: E402

PULL = int(re.search(r"#define CODEC_CONFIG_PULL_MS (\d+)", _CH).group(1))
LN = 1791354615  # 2026-10-07 06:30:15: the page's wall clock read as UTC
WAKE_0700_ON = pack("lighting", Mode=20, ProfileNumber=14, Timestamp=7 * 3600, LightValue=0x1101)
PULL_WRITES = [
    "CALL write 1501 %s" % control.LIGHT_REQUEST_CONFIG.hex(),
    "CALL write 1501 %s" % control.LIGHT_COMMIT.hex(),
]
REFUSED, FAILED, BUSY, PENDING = 1, 8, 6, 5  # CALI_CTL_* (cali_control.h)


def _pulled(t):
    """The pull's two writes ACKed: the REQUEST_CONFIG at t, the commit FOLLOW + TICK later."""
    return ["tick %d" % t, "WRITTEN 1501 0", "tick %d" % (t + FOLLOW + TICK), "WRITTEN 1501 0"]


def test_wakeup_edit_with_unknown_config_pulls_then_builds_from_the_reply(fake):
    """R5 on the ESP: the edit carries the switch the unit reports, learnt by the app's pull.

    .. test:: An unknown wake-up config is pulled with REQUEST_CONFIG before the edit is built
       :id: T_FW_WAKEUP_PULL
       :links: R_FW_WAKEUP
    """
    t = ARM + 100
    after_pull = t + FOLLOW + 2 * TICK
    out = run(
        fake,
        *armed(),
        "submit %d lighting wakeup 08:00" % LN,
        *_pulled(t),
        "NOTIFY 1502 %s" % WAKE_0700_ON,  # the unit's reply carries its wake-up (on)
        "tick %d" % after_pull,
        "WRITTEN 1501 0",
        "tick %d" % (after_pull + FOLLOW + TICK),
        "WRITTEN 1501 0",
    )
    latch = {"WakeupTimestamp": 7 * 3600, "WakeupLightValue": 0x1101}
    assert writes(out) == PULL_WRITES + want_writes("lighting", "wakeup", "08:00", latch=latch, local_now=LN)
    assert "SUBMIT %d -" % PENDING in out and out[-1] == "DONE 0 -"
    assert "LOG control: lighting/wakeup pulling the lighting config" in out


def test_wakeup_replan_after_the_pull_uses_the_clock_then_not_the_submits(fake):
    """An edit for 08:00 submitted at 07:59:59 whose reply arrives ~1.7 s after the commit's ACK is
    set for tomorrow: the re-plan runs with local_now + the seconds elapsed since the submit, as
    serve builds after its pull with its clock at that moment (Task 4 review Minor-1)."""
    ln = 1791331200 + 7 * 3600 + 59 * 60 + 59  # 2026-10-07 07:59:59
    t = ARM + 100
    acked = t + FOLLOW + TICK
    late = acked + 1700
    out = run(
        fake,
        *armed(),
        "submit %d lighting wakeup 08:00" % ln,
        *_pulled(t),
        *["tick %d" % (acked + d) for d in range(100, 1700, 100)],
        "NOTIFY 1502 %s" % WAKE_0700_ON,
        "tick %d" % late,
        "WRITTEN 1501 0",
        "tick %d" % (late + FOLLOW + TICK),
        "WRITTEN 1501 0",
    )
    latch = {"WakeupTimestamp": 7 * 3600, "WakeupLightValue": 0x1101}
    got = writes(out)[len(PULL_WRITES) :]
    assert got == want_writes("lighting", "wakeup", "08:00", latch=latch, local_now=ln + (late - ARM) // 1000)
    assert got != want_writes("lighting", "wakeup", "08:00", latch=latch, local_now=ln)  # today 08:00
    assert out[-1] == "DONE 0 -"


def test_wakeup_pull_writes_only_the_allow_listed_request_config_then_commit():
    """The pull's own frames are the vectors' config_pull: 1501 at its 16-byte frame length (the
    write allow-list, W 1501 16 -> OK 1 in test_control_parity), REQUEST_CONFIG then the commit."""
    v = json.loads((ROOT / "tests" / "vectors" / "control.json").read_text())["config_pull"]
    assert v["reason"] == control.WAKEUP_UNKNOWN
    assert ["CALL write %s %s" % (f["char"], f["hex"]) for f in v["frames"]] == PULL_WRITES
    assert {(f["char"], len(f["hex"]) // 2) for f in v["frames"]} == {("1501", 16)}


def test_wakeup_pull_without_a_wakeup_frame_is_refused_and_writes_nothing_more(fake):
    """Review focus 2: the reply has no Mode-20 frame -> refused after CODEC_CONFIG_PULL_MS, never the
    default-filled (disarming) frame, and no stray commit."""
    t = ARM + 100
    acked = t + FOLLOW + TICK
    early = run(
        fake,
        *armed(),
        "submit %d lighting wakeup 08:00" % LN,
        *_pulled(t),
        "NOTIFY 1502 %s" % pack("lighting", Mode=12, ProfileNumber=12, LightValue=0),
        *["tick %d" % (acked + d) for d in range(100, PULL, 100)],
    )
    assert not [line for line in early if line.startswith("DONE")]  # still waiting inside the window
    out = run(
        fake,
        *armed(),
        "submit %d lighting wakeup 08:00" % LN,
        *_pulled(t),
        *["tick %d" % (acked + d) for d in range(100, PULL + 1000, 100)],
    )
    assert writes(out) == PULL_WRITES
    assert [line for line in out if line.startswith("DONE")] == [
        "DONE %d %s" % (REFUSED, control.WAKEUP_UNKNOWN)
    ]
    assert "LOG control: lighting/wakeup refused: %s" % control.WAKEUP_UNKNOWN in out


def test_wakeup_with_an_explicit_switch_needs_no_pull_and_our_write_is_never_latched(fake):
    """'07:00 off' builds at once (no config needed); the unit ACKs but reports nothing, so the next
    time-only edit must pull again — our own frame never became 'unit-reported'."""
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "submit %d lighting wakeup 07:00 off" % LN,
        "tick %d" % t,
        "WRITTEN 1501 0",
        "tick %d" % (t + FOLLOW + TICK),
        "WRITTEN 1501 0",
        "submit %d lighting wakeup 08:00" % LN,
        "tick %d" % (t + FOLLOW + 2 * TICK),
    )
    first = want_writes("lighting", "wakeup", "07:00 off", local_now=LN)
    assert writes(out) == first + PULL_WRITES[:1]


def test_wakeup_no_area_is_refused_without_a_pull(fake):
    t = ARM + 100
    no_area = pack("lighting", Mode=20, ProfileNumber=14, Timestamp=7 * 3600, LightValue=0x1071)
    out = run(
        fake, *armed(), "NOTIFY 1502 %s" % no_area, "submit %d lighting wakeup 07:00" % LN, "tick %d" % t
    )
    assert "SUBMIT %d %s" % (REFUSED, control.REASON_WAKEUP_NO_AREA) in out and not writes(out)


def test_wakeup_without_a_clock_is_refused_with_the_clock_reason(fake):
    from tools.gen_c_dict import ESP_WAKEUP_CLOCK_REASON

    out = run(fake, *armed(), "submit - lighting wakeup 08:00", "tick %d" % (ARM + 100))
    assert "SUBMIT 2 %s" % ESP_WAKEUP_CLOCK_REASON in out and not writes(out)  # 2 = ELSEWHERE


def test_wakeup_pull_holds_the_sequencer_busy(fake):
    """Review focus 4: a second command while the wake-up waits for the reply is busy (the web
    answers 409), never interleaved: the unit sees only the pull, then the wake-up."""
    t = ARM + 100
    after_pull = t + FOLLOW + 2 * TICK
    out = run(
        fake,
        *armed(),
        "submit %d lighting wakeup 08:00" % LN,
        *_pulled(t),
        "> set cooler level 2",
        "submit - cooler level 3",
        "NOTIFY 1502 %s" % WAKE_0700_ON,
        "tick %d" % after_pull,
        "WRITTEN 1501 0",
        "tick %d" % (after_pull + FOLLOW + TICK),
        "WRITTEN 1501 0",
    )
    assert "LOG control: cooler/level busy" in out and "SUBMIT %d -" % BUSY in out
    latch = {"WakeupTimestamp": 7 * 3600, "WakeupLightValue": 0x1101}
    assert writes(out) == PULL_WRITES + want_writes("lighting", "wakeup", "08:00", latch=latch, local_now=LN)


def test_wakeup_pull_deadline_is_deadline_plus_pull(fake):
    """Review focus 4: one deadline for pull + write, under CALI_HTTP_PENDING_MAX_MS (8 s)."""
    t = ARM + 100
    script = [*armed(), "submit %d lighting wakeup 08:00" % LN, "tick %d" % t]
    alive = run(fake, *script, "tick %d" % (t + DEADLINE + 100))
    assert "LOG control: lighting/wakeup timed out" not in alive
    dead = run(fake, *script, "tick %d" % (t + DEADLINE + PULL + 100))
    assert "LOG control: lighting/wakeup timed out" in dead
    http = (CORE / "include" / "cali_http.h").read_text()
    assert DEADLINE + PULL < int(re.search(r"#define CALI_HTTP_PENDING_MAX_MS (\d+)", http).group(1))


def test_wakeup_link_drop_during_the_pull_fails_and_writes_nothing_more(fake):
    """The link drops while the wake-up waits for the reply: FAILED (link lost) at once, no wake-up
    frame on any later link."""
    t = ARM + 100
    acked = t + FOLLOW + TICK
    out = run(
        fake,
        *armed(),
        "submit %d lighting wakeup 08:00" % LN,
        *_pulled(t),
        "DISCONNECTED",
        "tick %d" % (acked + TICK),
        *["tick %d" % (acked + d) for d in range(200, PULL + 2000, 100)],
    )
    assert writes(out) == PULL_WRITES
    assert [line for line in out if line.startswith("DONE")] == ["DONE %d -" % FAILED]
    assert "LOG control: lighting/wakeup failed: link lost" in out


def test_wakeup_after_a_reconnect_pulls_again(fake):
    """Review focus 3: the previous link's wake-up is shown, but the edit on the new link pulls."""
    up = ARM + 1200
    t = up + ARM + 100
    out = run(
        fake,
        *armed({0x1502: WAKE_0700_ON}),
        "DISCONNECTED",
        "tick %d" % (ARM + 100),
        "tick %d" % up,
        "CONNECTED",
        "ENC_OK",
        *read_all(at=up),
        "tick %d" % (up + ARM),
        "submit %d lighting wakeup 08:00" % LN,
        "tick %d" % t,
    )
    assert snaps(out)[-1]["fn"]["lighting"]["WakeupLightValue"] == 0x1101
    assert writes(out) == PULL_WRITES[:1]


def _relink_lighting_unread(at):
    """A new link at session time ``at`` whose read-all has no lighting frame (its read fails)."""
    reads = ["READ %x %d" % (c, 14 if c == 0x1502 else 0) for c in CHARS]
    return ["CONNECTED", "ENC_OK", *read_all(at=at, reads=reads)]


def test_another_units_state_is_never_shown_after_forget_or_a_new_bond(fake):
    """Task-3 carry-over M1: when the bond's identity changes (forget, or a bond to another unit) the
    stored frames and the shown config latch are dropped — the old unit's wake-up is never displayed
    for the new one. The same unit keeps them shown across links (test above)."""
    up = ARM + 1200
    old = [*armed({0x1502: WAKE_0700_ON}), "DISCONNECTED", "tick %d" % (ARM + 100), "tick %d" % up]
    same = run(fake, *old, *_relink_lighting_unread(up))
    assert snaps(same)[-1]["fn"]["lighting"]["WakeupLightValue"] == 0x1101
    other = run(fake, *old, "ident AA:BB:CC:DD:EE:FF", *_relink_lighting_unread(up))
    assert "lighting" not in snaps(other)[-1]["fn"]
    # the new unit's own lighting frame carries no config: none of the old unit's latch rides along
    other = run(fake, *old, "ident AA:BB:CC:DD:EE:FF", "CONNECTED", "ENC_OK", *read_all(at=up))
    assert not set(semantics.LIGHT_CONFIG_KEYS) & set(snaps(other)[-1]["fn"]["lighting"])
    t = ARM + 100
    forgot = run(
        fake,
        *armed({0x1502: WAKE_0700_ON}),
        "> forget",
        "DISCONNECTED",
        *["tick %d" % (t + d) for d in range(0, 3000, 100)],
        *PAIRED,
        *read_all(at=t + 3000, reads=["READ %x %d" % (c, 14 if c == 0x1502 else 0) for c in CHARS]),
    )
    assert 'STATE {"state":"idle","attempts":0,"error":null,"address":null}' in forgot
    assert "lighting" not in snaps(forgot)[-1]["fn"]


def test_a_lighting_config_frame_keeps_the_stored_lamps(fake):
    """#284: a 1502 read/notify returns the unit's LAST frame of any kind. A config/ack frame (here the
    real door-contact echo) feeds the latch but keeps the stored lamps + active profile; a live
    SET_PROFILE frame replaces them (``semantics.lighting_merge``).

    .. test:: The firmware keeps the lamps across a lighting config frame
       :id: T_FW_LIGHT_KEEPS_LAMPS
       :links: R_LIGHT_ACTIVE_PROFILE, R_FW_SESSION
    """
    funcs = protocol.load()
    overrides.apply(funcs)
    zones = "090400000000000000050000d07ddddd"  # a zone SET's last ramp frame (PN 9, Mode 4)
    reads = ["READ %x 0%s" % (c, " " + zones if c == 0x1502 else "") for c in CHARS]
    activated = "011000000000000000030000d00ddd"  # SET_PROFILE favourite 1 (15 bytes: the fake's %31s)
    out = run(
        fake,
        *PAIRED,
        *read_all(reads=reads),
        "NOTIFY 1502 081000000000000100000000d00ddddd",
        "NOTIFY 1502 " + activated,
    )
    first, door, act = (s["fn"]["lighting"] for s in snaps(out))
    assert {k: v for k, v in door.items() if k != "DoorContact"} == first
    assert door["DoorContact"] == 1
    assert {k: v for k, v in act.items() if k != "DoorContact"} == protocol.decode(
        funcs["lighting"], bytes.fromhex(activated)
    )
