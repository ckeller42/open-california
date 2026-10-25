"""The firmware's session and console, driven by a scripted fake transport (#154).

.. req:: Firmware session and console
   :id: R_FW_SESSION

   ``firmware/components/cali_core/session.c`` keeps the bonded link: after the runner reaches
   bonded (or a boot with a stored bond reconnects and re-encrypts) it discovers, subscribes the
   notifying state chars of the unit's variant (``R_FW_VARIANT_GATE``), lets the heartbeat run
   ``CODEC_HEARTBEAT_WARMUP_MS`` (calictl's ``HEARTBEAT_WARMUP_S``), reads each of the variant's
   ``CODEC_CHARS`` functions (``READS`` order), and prints one
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

.. req:: Firmware per-variant subscribe and read gate
   :id: R_FW_VARIANT_GATE

   The session subscribes and reads only the functions the unit's vehicle variant has, as the app
   does (5.4.0 ``isSupportedForVariant``; the real app on the real unit, CAPTURE 2026-10-10). The
   variant is the 1004 ``CarVariant``: 0 California T7, 1 Grand California, 2 California Next, any
   other value T7. Roof: T7 only; satellite antenna, stairs, living-room heater, roof air-condition:
   Grand California only; energy, water, air heater, cooler: T7 and Grand California; every other
   function: all variants. With no 1004 frame stored on this boot, the session reads 1004 first
   (the app's order), then subscribes, then reads the variant's other functions; a reconnect in the
   same boot uses the stored frame. The subscribes leave out 1001 and f001 (read only, as the app).
   When the 1004 read fails the session subscribes and reads every function (no data lost to the
   gate). It logs the skipped functions once per link.

.. test:: Session and console call/output sequences against a fake transport
   :id: T_FW_SESSION_FAKE
   :links: R_FW_SESSION, R_FW_PAIRING_RUNNER, R_FW_WRITE_ALLOWLIST, R_FW_CONTROL_API

``session_fake.c`` compiles console + session + runner + SM + ``csrc/codec.c`` + ``csrc/ports.c`` with the host ``cc``
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
# R_FW_VARIANT_GATE: what a California T7 (the default 0102 frames' CarVariant 0, as this van) skips
T7_SKIPS = {0x2102, 0x2002, 0x1902, 0x1802}  # livingroomheater, roofaircondition, satelliteantenna, stairs
# the app's 8 CCCD writes on a T7 (CAPTURE 2026-10-10), in CODEC_CHARS order: no 1001/f001 (read only)
SUBS = [c for c in CHARS if c not in T7_SKIPS | {0x1001, 0xF001}]
# the read-all order (session.c READ_FIRST, R_FW_SESSION): the home screen's functions first, then the
# rest in CODEC_CHARS order — a T7's functions only. A reconnect's pass reads 1004 at its place here; a
# boot's first link reads 1004 BEFORE the subscribes (the variant) and its pass skips it (FIRST_READS).
READS = [
    0x1602,
    0x1302,
    0x1102,
    0x1202,
    0x1004,
    0x1502,
    0x1702,
    0x1001,
    0xF001,
    0x1402,
]
FIRST_READS = [0x1004] + [c for c in READS if c != 0x1004]
# the pass over EVERY function (variant unknown, or a Grand California): the same order, all 14
READS_ALL = [*READS[:7], *READS[7:9], 0x2102, 0x1402, 0x2002, 0x1902, 0x1802]
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
            str(CORE / "diag.c"),
            str(CORE / "control.c"),
            str(CORE / "control_run.c"),
            str(CORE / "runner.c"),
            str(CORE / "sensors.c"),  # runner.c hands sensor adverts to it
            str(CORE / "pairing_sm.c"),
            str(CORE / "ble_exp.c"),
            str(CORE / "ble_exp_run.c"),
            str(CORE / "time.c"),
            str(ROOT / "csrc" / "codec.c"),
            str(ROOT / "csrc" / "ports.c"),
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


def read_all(at=0, reads=None, first=True):
    """DISCOVERED at session time ``at`` (the last tick), the tick that ends the heartbeat warm-up,
    then the READ completions (default: every T7 function, status 0, data 0102). ``first`` (no 1004
    stored on this boot yet): the 1004 completion moves before the warm-up tick — the variant read
    that precedes the subscribes (R_FW_VARIANT_GATE)."""
    reads = list(reads if reads is not None else ["READ %x 0" % c for c in READS])
    pre = []
    if first:
        pre = [r for r in reads if r.startswith("READ 1004 ")][:1] or ["READ 1004 0"]
        reads = [r for r in reads if not r.startswith("READ 1004 ")]
    return ["DISCOVERED 0", *pre, "tick %d" % (at + WARM)] + reads


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
    assert rest[1] == "CALL read %d" % 0x1004  # the variant first (R_FW_VARIANT_GATE)
    assert rest[2:10] == ["CALL subscribe %d" % c for c in SUBS]
    assert [line for line in rest if line.startswith("CALL read")] == [
        "CALL read %d" % c for c in FIRST_READS
    ]
    assert len(snaps(out)) == 1 and out[-1].startswith("SNAP ")  # one SNAP, after the last read


def test_io_hook_sees_every_unit_frame_and_link_change(fake):
    """The SD log's tap (R_FW_SD_LOG, cali_session_on_io): link up, every READ and NOTIFY as the unit
    sent it (before the water guard), link down — and nothing for a failed read.

    .. test:: The session hands every unit frame and link change to the SD log
       :id: T_FW_SD_LOG_TAP
       :links: R_FW_SD_LOG
    """
    reads = ["READ %x %d" % (c, 14 if c == 0x1602 else 0) for c in READS]  # energy's read fails
    out = run(fake, "iohook", *PAIRED, *read_all(reads=reads), "NOTIFY 1302 01", "DISCONNECTED")
    io = [line for line in out if line.startswith("IO ")]
    assert io[0] == "IO 2 0 "  # up
    assert io[1:-2] == ["IO 1 %x 0102" % c for c in FIRST_READS if c != 0x1602]  # reads, raw
    assert io[-2:] == ["IO 0 1302 01", "IO 3 0 "]  # the 1 L latch as sent (held, not served), down


def test_snap_is_codec_decode_of_every_stored_frame(fake):
    funcs = protocol.load()
    overrides.apply(funcs)
    snap = snaps(run(fake, *PAIRED, *READ_ALL))[0]
    # a lone 0102 water frame (fresh 2 L) hasn't settled; a T7 never reads the Grand-California-only four
    stored = [fn for fn, c in zip(FNS, CHARS) if fn != "water" and c not in T7_SKIPS]
    assert list(snap["fn"]) == stored
    for fn in stored:
        assert snap["fn"][fn] == protocol.decode(funcs[fn], b"\x01\x02"), fn


def test_short_frame_and_failed_read(fake):
    funcs = protocol.load()
    overrides.apply(funcs)
    script = read_all(
        reads=["READ %x %d%s" % (c, 14 if c == 0x1602 else 0, " ff" if c == 0x1102 else "") for c in READS]
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
    """With a warm-up set (``warm 2000``, calictl's ``read_all`` default) the heartbeat runs that long
    after the subscribes before the first read — no read on any earlier tick. The firmware default is
    0 (``T_FW_BLE_WARM``).

    .. test:: The firmware read-all waits the heartbeat warm-up
       :id: T_FW_SESSION_WARMUP
       :links: R_FW_SESSION
    """
    warming = ["tick %d" % t for t in range(100, WARM, 100)]
    out = run(fake, *PAIRED, "warm %d" % WARM, "DISCOVERED 0", "READ 1004 0", *warming)
    assert not calls(after(out, "CALL subscribe %d" % CHARS[-1]), "read"), out  # still warming up
    beats = calls(out, "write_heartbeat")
    assert len(beats) >= WARM // PERIOD  # the liveness is running
    out = run(fake, *PAIRED, "warm %d" % WARM, "DISCOVERED 0", "READ 1004 0", *warming, "tick %d" % WARM)
    rest = after(out, "CALL subscribe %d" % CHARS[-1])
    first_read = rest.index("CALL read %d" % FIRST_READS[1])
    assert calls(rest[:first_read], "write_heartbeat") == beats  # beats, then the reads


def test_warm_ms_knob_sets_the_read_pass_warm_up(fake):
    """The default warm-up is 0 (measured unneeded 2026-10-22): the read pass starts on the first tick
    after the subscribes; ``cali_session_set_warm_ms`` (``POST /api/ble {"warm_ms":N}``) sets another.

    .. test:: The warm-up knob shortens the read pass's warm-up
       :id: T_FW_BLE_WARM
       :links: R_FW_BLE_WARM
    """
    out = run(fake, *PAIRED, "DISCOVERED 0", "READ 1004 0", "tick 100")  # the default
    assert calls(after(out, "CALL subscribe %d" % CHARS[-1]), "read")[0] == "CALL read %d" % FIRST_READS[1]
    out = run(fake, *PAIRED, "warm 500", "DISCOVERED 0", "READ 1004 0", "tick 400")
    assert not calls(after(out, "CALL subscribe %d" % CHARS[-1]), "read")  # still warming up


STALE_WATER = "03011d010016"  # FreshWaterLevel 1: a parked latch pushed on the subscribe
LIVE_WATER = "03111d010016"  # FreshWaterLevel 17: what the read that follows returns
REREAD = int(
    re.search(
        r"#define CALI_SESSION_WATER_REREAD_MS (\d+)u", (CORE / "include" / "cali_session.h").read_text()
    ).group(1)
)
SETTLE = int(re.search(r"#define FRESH_SETTLE_MS (\d+)u", (ROOT / "csrc" / "ports.h").read_text()).group(1))


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
    reads = ["READ %x 0%s" % (c, " " + LIVE_WATER if c == 0x1302 else "") for c in READS]
    out = run(
        fake,
        *PAIRED,
        "DISCOVERED 0",
        "READ 1004 0",  # the variant read (R_FW_VARIANT_GATE), then the subscribes
        "NOTIFY 1302 " + STALE_WATER,
        "tick %d" % WARM,
        *[r for r in reads if not r.startswith("READ 1004 ")],
        "tick %d" % (WARM + SETTLE),
        "NOTIFY 1302 " + LIVE_WATER,
    )
    rest = after(out, "CALL subscribe %d" % CHARS[-1])
    assert [line for line in rest if line.startswith("CALL read")] == [
        "CALL read %d" % c for c in FIRST_READS[1:]
    ]
    first, last = snaps(out)
    assert "water" not in first["fn"]  # cold start: the read's 17 L is a candidate, not yet shown
    got, f = _water(last)
    # 17 L adopted SETTLE after the READ: the read (not the earlier push) set the candidate
    assert got == protocol.decode(f, bytes.fromhex(LIVE_WATER))


def test_push_while_its_read_is_outstanding_then_the_read_wins(fake):
    """The cooler read is on the air when the unit pushes cooler; the read completion arrives
    last, so it wins (no frame is privileged over another — only arrival order counts)."""
    funcs = protocol.load()
    overrides.apply(funcs)
    reads = ["READ %x 0" % c for c in READS]
    i = CHARS.index(0x1102)
    out = run(fake, *PAIRED, *read_all(reads=reads[:i] + ["NOTIFY 1102 aabbccdd"] + reads[i:]))
    (snap,) = snaps(out)
    assert snap["fn"]["cooler"] == protocol.decode(funcs["cooler"], b"\x01\x02")


def test_notify_after_the_read_wins(fake):
    """A water notification after the read-all replaces the read (and SNAPs): it is the candidate
    the ramp debounce adopts SETTLE later (the read's 0102 = 2 L candidate is replaced)."""
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        "NOTIFY 1302 " + LIVE_WATER,
        "tick %d" % (T + SETTLE),
        "NOTIFY 1302 " + LIVE_WATER,
    )
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(LIVE_WATER))


def test_water_is_re_read_periodically_while_the_link_is_up(fake):
    """Water is re-read every ``CALI_SESSION_WATER_REREAD_MS`` after the read-all (calictl reads
    1302 on every 30 s poll), so a stale latch served at connect is corrected once a real reading
    has settled — two re-reads of the same level ``FRESH_SETTLE_MS`` or more apart; each
    re-read's frame SNAPs. A later LATCH notify (a fresh drop with the grey tank frozen) no longer
    overrides the plausible reading — the stale-latch guard holds it
    (``test_water_latch_is_held_vs_a_plausible_baseline``).

    .. test:: The firmware re-reads water periodically while the link is up
       :id: T_FW_SESSION_WATER_REREAD
       :links: R_FW_SESSION
    """
    stale = ["READ %x 0%s" % (c, " " + STALE_WATER if c == 0x1302 else "") for c in READS]
    early = run(fake, *PAIRED, *read_all(reads=stale), "tick %d" % (T + REREAD - 100))
    assert calls(after(early, "CALL read %d" % READS[-1]), "read") == []  # not before the period
    out = run(
        fake,
        *PAIRED,
        *read_all(reads=stale),
        "tick %d" % (T + REREAD - 100),
        "tick %d" % (T + REREAD),
        "READ 1302 0 " + LIVE_WATER,
    )
    rest = after(out, "CALL read %d" % READS[-1])  # after the read-all (its own water read is 2nd)
    assert calls(rest, "read") == ["CALL read %d" % 0x1302]  # the periodic re-read
    assert "water" not in snaps(out)[-1]["fn"]  # one read of a new level: not adopted yet
    confirmed = [
        *PAIRED,
        *read_all(reads=stale),
        "tick %d" % (T + REREAD),
        "READ 1302 0 " + LIVE_WATER,
        "tick %d" % (T + 2 * REREAD),
        "READ 1302 0 " + LIVE_WATER,
    ]
    got, f = _water(snaps(run(fake, *confirmed))[-1])
    assert got == protocol.decode(f, bytes.fromhex(LIVE_WATER))  # the same level 30 s later: adopted
    out = run(fake, *confirmed, "NOTIFY 1302 " + STALE_WATER, "tick %d" % (T + 3 * REREAD))
    got, f = _water(snaps(out)[-1])
    # The latch notify (fresh 17->1, grey frozen) is held: the plausible LIVE reading stays shown.
    assert got == protocol.decode(f, bytes.fromhex(LIVE_WATER))
    assert len(calls(out, "read %d" % 0x1302)) == 4  # read-all + three periodic re-reads


# Water stale-latch guard (calictl.freshness.implausible_water_drop, ported to the ESP session).
# Fresh drop + grey frozen = the parked latch -> hold; any grey movement (or a rise) = live -> adopt.
PLAUSIBLE_WATER = LIVE_WATER  # fresh 17, waste 0
LATCH_WATER = STALE_WATER  # fresh 1,  waste 0 (grey frozen) -> held
GREY_MOVED_WATER = "03011d010516"  # fresh 1,  waste 5 (grey moved) -> live, adopted
REFILL_WATER = "03141d010016"  # fresh 20, waste 0 (a rise)      -> adopted


def _served_water(fake, *extra):
    """SNAP the water frame after a read-all whose 1302 read returns PLAUSIBLE_WATER (the persisted
    baseline, so adopted at once), then ``extra``."""
    reads = ["READ %x 0%s" % (c, " " + PLAUSIBLE_WATER if c == 0x1302 else "") for c in READS]
    out = run(
        fake, "kvsethex water_good " + PLAUSIBLE_WATER, "reinit", *PAIRED, *read_all(reads=reads), *extra
    )
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
    """A fresh drop WITH grey movement proves the unit is live-measuring — adopt it once settled."""
    got, f, out = _served_water(
        fake,
        "NOTIFY 1302 " + GREY_MOVED_WATER,
        "tick %d" % (T + SETTLE),
        "NOTIFY 1302 " + GREY_MOVED_WATER,
    )
    assert got == protocol.decode(f, bytes.fromhex(GREY_MOVED_WATER))
    assert _water(snaps(out)[-2])[0] == protocol.decode(f, bytes.fromhex(PLAUSIBLE_WATER))  # not at once


def test_water_refill_is_adopted(fake):
    """A rising fresh level is never a latch (it is a refill / re-measure) — adopt it once settled."""
    got, f, out = _served_water(
        fake, "NOTIFY 1302 " + REFILL_WATER, "tick %d" % (T + SETTLE), "NOTIFY 1302 " + REFILL_WATER
    )
    assert got == protocol.decode(f, bytes.fromhex(REFILL_WATER))
    assert _water(snaps(out)[-2])[0] == protocol.decode(f, bytes.fromhex(PLAUSIBLE_WATER))  # not at once


def test_water_plausible_reading_is_persisted(fake):
    """A plausible water read is written to NVS (WATER_GOOD_KEY) so a reboot can restore it.

    .. test:: The session persists the plausible water baseline
       :id: T_FW_SESSION_WATER_PERSIST
       :links: R_FW_SESSION
    """
    reads = ["READ %x 0%s" % (c, " " + PLAUSIBLE_WATER if c == 0x1302 else "") for c in READS]
    settled = ["tick %d" % (T + SETTLE), "NOTIFY 1302 " + PLAUSIBLE_WATER]
    out = run(fake, *PAIRED, *read_all(reads=reads), *settled, "kvhex water_good")
    assert ("KV water_good " + PLAUSIBLE_WATER) in out  # persisted, hex-for-hex


def test_water_baseline_restored_from_nvs_rejects_a_latch_on_boot(fake):
    """A fresh boot (new process) pre-seeded with a persisted baseline still rejects the parked
    latch on the very first read — the reboot-while-parked case serve.py's ``_water_good`` covers."""
    latch_reads = ["READ %x 0%s" % (c, " " + LATCH_WATER if c == 0x1302 else "") for c in READS]
    out = run(
        fake,
        "kvsethex water_good " + PLAUSIBLE_WATER,  # NVS as if written by a previous boot
        "reinit",  # boot the session with that NVS present
        *PAIRED,
        *read_all(reads=latch_reads),
    )
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(PLAUSIBLE_WATER))  # restored baseline held


def test_water_baseline_time_is_this_boot_only(fake):
    """``cali_session_water_good_ms`` (``/api/state`` ``device.water_held_age_ms``) is the session
    time a real read adopted/confirmed the baseline; an NVS-restored baseline has none (0)."""
    latch_reads = ["READ %x 0%s" % (c, " " + LATCH_WATER if c == 0x1302 else "") for c in READS]
    boot = ["kvsethex water_good " + PLAUSIBLE_WATER, "reinit", *PAIRED]
    assert "WATERGOOD 1 0" in run(fake, *boot, *read_all(reads=latch_reads), "watergood")  # held, no time
    _, _, out = _served_water(fake, "NOTIFY 1302 " + LATCH_WATER, "tick %d" % (T + 100), "watergood")
    assert ("WATERGOOD 1 %d" % T) in out  # confirmed by the read at T, then held against the latch


SEED_WATER = "03161d010016"  # fresh 22, waste 0: a persisted last-plausible reading


APP_CAPTURE_WATER = "03141d010016"  # fresh 20, waste 0: the real unit's 1302 read, 2026-10-10 app capture


def test_water_real_drop_with_grey_zero_is_served_and_becomes_the_baseline(fake):
    """Regression 2026-10-10: grey reads 0 on every frame on this van, so the old "any fresh drop
    with grey frozen" rule held the real 22 -> 20 L drop forever. Only a drop to <=
    ``FRESH_LATCH_MAX_L`` (the observed 1 L latch) is held; 20 L is live — served,
    persisted as the new baseline once it has settled — and a following 1 L latch is held against it.

    .. test:: The session serves a real fresh-water drop and holds only the 1 L latch
       :id: T_FW_SESSION_WATER_LATCH_FLOOR
       :links: R_FW_SESSION
    """
    reads = ["READ %x 0%s" % (c, " " + APP_CAPTURE_WATER if c == 0x1302 else "") for c in READS]
    out = run(
        fake,
        "kvsethex water_good " + SEED_WATER,  # 22 L baseline from an earlier boot
        "reinit",
        *PAIRED,
        *read_all(reads=reads),
        "tick %d" % (T + SETTLE),
        "NOTIFY 1302 " + APP_CAPTURE_WATER,
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
        "tick %d" % (T + SETTLE),
        "NOTIFY 1302 " + APP_CAPTURE_WATER,
        "NOTIFY 1302 " + LATCH_WATER,  # the parked 1 L latch
        "tick %d" % (T + SETTLE + 100),
    )
    got, f = _water(snaps(out)[-1])
    assert got == protocol.decode(f, bytes.fromhex(APP_CAPTURE_WATER))  # latch held vs 20 L


def _water_frame(w):
    """A 1302 frame for vector levels: 03 <fresh> 1d 01 <waste> 16 (waste None -> cut before it;
    "empty" -> 53: FreshWaterInfoPopUp 5, the unit's measured-empty alarm)."""
    return (
        ("53" if w.get("empty") else "03")
        + "%02x1d01" % w["fresh"]
        + ("" if w["waste"] is None else "%02x16" % w["waste"])
    )


def test_water_ramp_debounce_matches_the_parity_vectors(fake):
    """The session replays ``tests/vectors/freshness.json`` ``sequences`` (the ramp debounce,
    ``calictl.freshness.settle_water``) step for step: each step a 1302 NOTIFY at session time
    ``t``, the SNAP shows the new reading when the vector says adopt, else the baseline (or no water
    on a cold start).

    .. test:: The session's water ramp debounce matches calictl's
       :id: T_FW_SESSION_WATER_RAMP
       :links: R_FW_SESSION, R_WATER_RAMP_DEBOUNCE
    """
    funcs = protocol.load()
    overrides.apply(funcs)
    vectors = json.loads((ROOT / "tests" / "vectors" / "freshness.json").read_text())["sequences"]
    reads = ["READ %x %d" % (c, 1 if c == 0x1302 else 0) for c in READS]  # water read fails: no frame
    for seq in vectors:
        good = seq["good"]
        boot = ["kvsethex water_good " + _water_frame(good), "reinit"] if good else []
        steps = []
        for t, fresh, waste, _, *e in seq["steps"]:
            w = {"fresh": fresh, "waste": waste, "empty": bool(e and e[0])}
            steps += ["tick %d" % (T + t), "NOTIFY 1302 " + _water_frame(w)]
        shown = [
            sn["fn"].get("water") for sn in snaps(run(fake, *boot, *PAIRED, *read_all(reads=reads), *steps))
        ]
        want = []
        for _t, fresh, waste, adopt, *e in seq["steps"]:
            if adopt:
                good = {"fresh": fresh, "waste": waste, "empty": bool(e and e[0])}
            want.append(protocol.decode(funcs["water"], bytes.fromhex(_water_frame(good))) if good else None)
        assert shown[1:] == want, seq["id"]  # shown[0] = the read-all's SNAP


def test_heartbeat_every_period_from_the_start_counter(fake):
    out = run(fake, *PAIRED, *READ_ALL, *["tick %d" % t for t in range(T + 100, T + 1300, 100)])
    assert calls(out, "write_heartbeat") == ["CALL write_heartbeat %d" % n for n in (HB, HB + 1, HB + 2)]


def test_no_heartbeat_before_bonded(fake):
    out = run(fake, "> pair", "FOUND", "CONNECTED", *["tick %d" % t for t in range(0, 3000, 100)])
    assert not calls(out, "write_heartbeat")


def _reconnects(out):
    return [i for i, line in enumerate(out) if line == "CALL connect_bonded"]


def test_unwritable_heartbeat_drops_and_reconnects_after_the_cooldown(fake):
    t = T + PERIOD  # the beat after READ_ALL's first one
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        "fail write_heartbeat",
        "tick %d" % t,
        *["tick %d" % u for u in range(t + 100, t + 7100, 100)],
    )
    i = out.index("CALL write_heartbeat %d" % (HB + 1))
    assert out[i + 1 : i + 3] == ["LOG session: heartbeat not written -1", "CALL disconnect"]
    ticks_before = [line for line in out if line == "CALL connect_bonded"]
    assert len(ticks_before) == 1
    out2 = run(fake, *PAIRED, *READ_ALL, "fail write_heartbeat", "tick %d" % t, "tick %d" % (t + 6999))
    assert not calls(out2, "connect_bonded")  # not before the 7 s cooldown


def test_failed_heartbeat_completion_drops_and_reconnects(fake):
    out = run(fake, *PAIRED, *READ_ALL, "HEARTBEAT 14", "tick %d" % (T + 7000))
    rest = after(out, "LOG session: heartbeat failed 14")
    assert rest[:1] == ["CALL disconnect"] and "CALL connect_bonded" in rest


def test_backoff_doubles_to_60s_and_resets_after_a_stable_link(fake):
    script = list(PAIRED) + READ_ALL + ["DISCONNECTED"]
    t, at = T, []
    # the first reconnect waits the unit's 7 s cooldown, the later ones their backoff step
    for delay in (7000, 2000, 4000, 8000, 16000, 32000, 60000, 60000):
        t += delay
        script += ["tick %d" % (t - 1), "tick %d" % t, "CONNECT_FAIL"]
        at.append(t)
    out = run(fake, *script)
    assert len(calls(out, "connect_bonded")) == len(at)
    logs = [line for line in out if line.startswith("LOG session: reconnect in")]
    assert logs == [
        "LOG session: reconnect in %d ms" % d
        for d in (7000, 2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000)
    ]
    # a link the unit ends at once keeps the delay; one that held CALI_SESSION_STABLE_MS resets it
    t += 60000
    up = [*script, "tick %d" % t, "CONNECTED", "ENC_OK"]
    out = run(fake, *up, "DISCONNECTED")
    assert out[-1] == "LOG session: reconnect in 60000 ms"
    assert "CALL discover" in after(out, "CALL connect_bonded")
    out = run(fake, *up, "tick %d" % (t + 59999), "DISCONNECTED")
    assert out[-1] == "LOG session: reconnect in 60000 ms"
    out = run(fake, *up, "tick %d" % (t + 60000), "DISCONNECTED")
    assert out[-1] == "LOG session: reconnect in 7000 ms"  # delay reset to 1 s, the cooldown rules


def test_unit_ending_each_link_soon_backs_off(fake):
    """Camping master off (2026-10-22): the unit ends every link ~1-31 s after it comes up. Each such
    loss doubles the delay instead of resetting it at link-up, so the reconnects back off to 60 s
    rather than waking the unit every 1 s (never sooner than the 7 s cooldown).

    .. test:: Links the unit ends soon back the reconnects off
       :id: T_FW_SESSION_SHORT_LINK_BACKOFF
       :links: R_FW_SESSION
    """
    script = list(PAIRED) + READ_ALL + ["DISCONNECTED 531"]
    t = T
    for delay in (1000, 2000, 4000, 8000, 16000, 32000, 60000):
        t += max(delay, 7000)  # the unit's cooldown floors the early steps
        script += ["tick %d" % t, "CONNECTED", "ENC_OK", "tick %d" % (t + 1300), "DISCONNECTED 531"]
        t += 1300
    out = run(fake, *script)
    logs = [line for line in out if line.startswith("LOG session: reconnect in")]
    assert logs == [
        "LOG session: reconnect in %d ms" % d for d in (7000, 7000, 7000, 8000, 16000, 32000, 60000, 60000)
    ]


def test_unit_hangup_after_read_all_takes_the_backoff(fake):
    """A unit-side hang-up (HCI 0x13, raw or NimBLE host-encoded 531) after read-all reconnects on
    the ordinary backoff (floored by the unit's 7 s cooldown), like calictl's supervisor: the #266 30 s "kicked" pacing was removed
    once #279 fixed the root cause (the unit's ATT MTU request is answered; parked links hold).

    .. test:: A unit hang-up after read-all takes the ordinary backoff
       :id: T_FW_SESSION_HANGUP_BACKOFF
       :links: R_FW_SESSION
    """
    for status in (19, 531, 8):
        out = run(fake, *PAIRED, *READ_ALL, "DISCONNECTED %d" % status)
        assert out[-1] == "LOG session: reconnect in 7000 ms", status


# R_FW_BLE_REST frames: 1202 campingmode (Installed 1, State 0 = off / 1 = on) and a 22-byte 1602
# energy frame whose StateLandAfs (bits 36-39) is the shore state: 0 inactive, 1 active, 2 standby
CAMPING_OFF, CAMPING_ON = "2000", "2100"
REST_PRE = ["ign 0"]  # the ignition known off (the stubbed cali_ac_ignition; unknown = no rest)


def energy(shore):
    return bytes([0, 0, 0, 0, shore] + [0] * 17).hex()


def parked(camping=CAMPING_OFF, shore=0, at=0, first=True):
    """A read pass whose 1202/1602 frames carry these camping / shore states (the rest are 0102)."""
    data = {0x1202: camping, 0x1602: energy(shore)}
    return read_all(at, ["READ %x 0 %s" % (c, data.get(c, "0102")) for c in READS], first)


def ticks(t0, t1, step=1000):
    return ["tick %d" % t for t in range(t0, t1 + 1, step)]


def test_unit_hangup_with_camping_off_and_no_shore_rests(fake):
    """The app's rule (5.4.0 gg/k): the unit ends the link (HCI 0x13, raw 19 or NimBLE 531) while
    camping mode is off and no shore power feeds it -> no reconnect ("Disconnected to save energy").
    The satellite also needs the ignition known off and no camping restore owed.

    .. test:: A unit hang-up with camping off and no shore power rests without reconnecting
       :id: T_FW_BLE_REST_ENTER
       :links: R_FW_BLE_REST
    """
    for status in (19, 531):
        out = run(
            fake,
            "diaghook",
            *REST_PRE,
            *PAIRED,
            *parked(),
            "DISCONNECTED %d" % status,
            *ticks(T, T + 120000),
            "rest",
        )
        tail = after(out, "LOG session: link lost (event 6, status %d)" % status)
        assert not calls(tail, "connect_bonded") and "LOG session: reconnect in" not in " ".join(tail)
        assert out[-1] == "REST 1 %d" % (30 * 60000 - 120000)  # the default 30 min rest poll
        assert {"ev": "evt", "kind": "rest", "on": True, "why": "camping_off"} in _diag(out)


def test_other_losses_reconnect_as_before(fake):
    """Camping on, shore power active (1) or standby (2), a supervision timeout (8), an unknown
    energy frame, the ignition on or unknown, a camping restore owed (the auto camper's armed
    state), or a command in flight: the ordinary backoff, never rest.

    .. test:: Camping on, shore power, a timeout, an unknown frame, the ignition, a restore owed or a command in flight reconnect as before
       :id: T_FW_BLE_REST_NOT
       :links: R_FW_BLE_REST
    """
    for camping, shore, status in [
        (CAMPING_ON, 0, 19),
        (CAMPING_OFF, 1, 19),
        (CAMPING_OFF, 2, 531),
        (CAMPING_OFF, 0, 8),
        (CAMPING_OFF, 0, 0x208),
    ]:
        out = run(fake, *REST_PRE, *PAIRED, *parked(camping, shore), "DISCONNECTED %d" % status, "rest")
        assert out[-2:] == ["LOG session: reconnect in 7000 ms", "REST 0 -1"], (camping, shore, status)
    for pre in (["ign 1"], ["ign -1"], ["ign 0", "acowe 1"], []):
        out = run(fake, *pre, *PAIRED, *parked(), "DISCONNECTED 19", "rest")
        assert out[-2:] == ["LOG session: reconnect in 7000 ms", "REST 0 -1"], pre
    out = run(fake, *REST_PRE, *PAIRED, *READ_ALL, "DISCONNECTED 19", "rest")  # 0102: no StateLandAfs
    assert out[-2:] == ["LOG session: reconnect in 7000 ms", "REST 0 -1"]
    # a write out, its ACK not yet in: the unit's hang-up may follow the command (camping on)
    frames = {0x1202: CAMPING_OFF, 0x1602: energy(0)}
    out = run(
        fake,
        *REST_PRE,
        *armed(frames),
        "> set cooler level 2",
        "tick %d" % (ARM + 100),
        "DISCONNECTED 19",
        "rest",
    )
    assert len(writes(out)) == 1
    assert out[-2:] == ["LOG session: reconnect in 7000 ms", "REST 0 -1"]


def test_command_and_connect_leave_rest_but_a_mode_change_does_not(fake):
    """Rest ends on a control command or the Connect button (POST /api/ble connect), each after the
    unit's 7 s cooldown, and on a forget or a new pairing; a mode change alone (the idle policy's
    viewer -> hold, /api/ble mode) never connects out of it.

    .. test:: A command or Connect leaves rest; a mode change stays in it
       :id: T_FW_BLE_REST_LEAVE
       :links: R_FW_BLE_REST
    """
    rest = [*REST_PRE, *PAIRED, *parked(), "DISCONNECTED 19"]
    out = run(fake, *rest, "submit - cooler level 2", "tick %d" % (T + 6999))
    assert "SUBMIT 7 -" in out and not calls(after(out, "SUBMIT 7 -"), "connect_bonded")
    out = run(fake, *rest, "submit - cooler level 2", "tick %d" % (T + 7000), "rest")
    assert calls(after(out, "SUBMIT 7 -"), "connect_bonded") and out[-1] == "REST 0 -1"
    out = run(fake, *rest, "tick %d" % (T + 3000), "connect", "tick %d" % (T + 6999))
    assert "CONNECT 0" in out and not calls(after(out, "CONNECT 0"), "connect_bonded")
    out = run(fake, *rest, "tick %d" % (T + 3000), "connect", "tick %d" % (T + 7000))
    assert calls(after(out, "CONNECT 0"), "connect_bonded")
    out = run(fake, *rest, "> ble mode off", "> ble mode hold", *ticks(T, T + 60000), "rest")
    assert not calls(after(out, "LOG session: link lost (event 6, status 19)"), "connect_bonded")
    assert out[-1].startswith("REST 1 ")
    out = run(fake, *rest, "bond 0", "connect")
    assert out[-1] == "CONNECT -1"
    out = run(fake, *rest, "> forget", "rest")
    assert out[-1] == "REST 0 -1"
    out = run(fake, *rest, "> forget", *PAIRED, "rest")  # a new pairing starts out of rest too
    assert out[-1] == "REST 0 -1"


def test_rest_poll_reads_once_and_drops_the_link(fake):
    """Rest polling (rest_poll_min, stored): every n min one probe connects, runs one read pass and
    drops the satellite's own link right after it, back to rest; a read that shows camping on keeps
    the link (rest ends), and so does a command during the probe. rest_poll_min 0, or mode off, =
    no probe.

    .. test:: The rest poll reads once, drops the link, rests again; 0 or mode off = no poll
       :id: T_FW_BLE_REST_POLL
       :links: R_FW_BLE_REST
    """
    poll = 15 * 60000
    rest = ["restpoll 15", *REST_PRE, *PAIRED, *parked(), "DISCONNECTED 19"]
    out = run(fake, *rest, *ticks(T, T + poll - 1000, 10000))
    assert not calls(after(out, "LOG session: link lost (event 6, status 19)"), "connect_bonded")
    up = [*rest, "tick %d" % (T + poll), "CONNECTED", "ENC_OK", *parked(at=T + poll, first=False)]
    out = run(fake, *up, "rest")
    tail = after(out, "LOG session: rest poll")
    assert calls(tail, "connect_bonded") and calls(tail, "disconnect")
    assert tail.index("CALL disconnect") > max(i for i, l in enumerate(tail) if l.startswith("SNAP "))
    assert out[-1] == "REST 1 %d" % poll
    out = run(fake, *up, *ticks(T + poll + WARM, T + 2 * poll - 1000, 10000))  # nothing until the next poll
    assert not calls(after(out, "LOG session: probe ok (read_all)"), "connect_bonded")
    on = [*rest, "tick %d" % (T + poll), "CONNECTED", "ENC_OK", *parked(CAMPING_ON, at=T + poll, first=False)]
    out = run(fake, *on, "rest")
    assert not calls(after(out, "LOG session: rest poll"), "disconnect") and out[-1] == "REST 0 -1"
    # a command while the probe connects: rest ends, the probe's link stays (the command needs it)
    cmd = [*rest, "tick %d" % (T + poll), "submit - cooler level 2", "CONNECTED", "ENC_OK"]
    out = run(fake, *cmd, *parked(at=T + poll, first=False), "rest")
    assert not calls(after(out, "LOG session: rest poll"), "disconnect") and out[-1] == "REST 0 -1"
    out = run(fake, *rest, "> ble mode off", *ticks(T, T + 3 * poll, 60000), "rest")
    assert not calls(after(out, "LOG session: link lost (event 6, status 19)"), "connect_bonded")
    out = run(
        fake,
        "restpoll 0",
        *REST_PRE,
        *PAIRED,
        *parked(),
        "DISCONNECTED 19",
        *ticks(T, T + 4 * 3600000, 60000),
        "rest",
    )
    assert not calls(after(out, "LOG session: link lost (event 6, status 19)"), "connect_bonded")
    assert out[-1] == "REST 1 -1"


def test_rest_probe_dropped_mid_pass_leaves_rest_only_on_a_positive_frame(fake):
    """A rest probe whose link drops before its read pass ends still leaves the rest when the
    frames it already got show camping on or shore power; with camping off it stays in rest.

    .. test:: A probe cut off after a camping-on or shore frame leaves rest and reconnects; camping off stays
       :id: T_FW_BLE_REST_DROP
       :links: R_FW_BLE_REST
    """
    poll = 15 * 60000
    rest = ["restpoll 15", *REST_PRE, *PAIRED, *parked(), "DISCONNECTED 19"]
    head = [*rest, "tick %d" % (T + poll), "CONNECTED", "ENC_OK", "DISCOVERED 0", "READ 1004 0"]
    cut = "tick %d" % (T + poll + WARM)
    shore0 = ["READ 1602 0 " + energy(0), "READ 1302 0", "READ 1102 0"]
    drops = [
        [*shore0, "READ 1202 0 " + CAMPING_ON],
        ["READ 1602 0 " + energy(1)],
        ["READ 1602 0 " + energy(2)],
    ]
    for frames in drops:
        out = run(fake, *head, cut, *frames, "DISCONNECTED 8", "rest")
        assert out[-1] == "REST 0 -1" and "LOG session: reconnect in" in "\n".join(out[-4:])
    out = run(fake, *head, cut, *shore0, "READ 1202 0 " + CAMPING_OFF, "DISCONNECTED 8", "rest")
    assert out[-1].startswith("REST 1 ")


def watched(t0, t1, step=2000):
    """A visible page between session times t0 and t1: a /api/state poll (``viewer``) + a tick every 2 s."""
    out = []
    for t in range(t0, t1 + 1, step):
        out += ["viewer %d" % t, "tick %d" % t]
    return out


def test_a_viewer_keeps_redialling_after_the_power_save_hang_up(fake):
    """R_FW_BLE_VIEWER_KEEP: with a page polling (a viewer), the unit's power-save hang-up does not
    start the rest. The satellite redials after the 7 s cooldown and the backoff does not grow across
    these planned hang-ups (without a viewer the 4th short link would wait 8 s, then 16 s). The redial
    flag holds from the hang-up until the new link takes commands; any other loss clears it.

    .. test:: A viewer turns the power-save hang-up into a redial at the cooldown, backoff not growing
       :id: T_FW_BLE_VIEWER_REDIAL
       :links: R_FW_BLE_VIEWER_KEEP
    """
    script = [*REST_PRE, *PAIRED, *parked(), "input %d" % T, "DISCONNECTED 19", "redial", "rest"]
    out = run(fake, *script)
    assert out[-3:] == ["LOG session: reconnect in 7000 ms", "REDIAL 1", "REST 0 -1"]
    t = T
    for _ in range(5):  # up 30 s, the unit hangs up, the next link at the cooldown
        script += [*watched(t + 2000, t + 6000), "viewer %d" % (t + 7000), "tick %d" % (t + 7000)]
        script += [
            "CONNECTED",
            "ENC_OK",
            *parked(at=t + 7000, first=False),
            "tick %d" % (t + 7000 + WARM + ARM),
        ]
        script += ["redial", *watched(t + 9000, t + 37000), "DISCONNECTED 531"]
        t += 37000
    out = run(fake, *script, "redial", "rest")
    logs = [line for line in out if line.startswith("LOG session: reconnect in")]
    assert logs == ["LOG session: reconnect in 7000 ms"] * 6
    assert len(calls(out, "connect_bonded")) == 5
    assert [line for line in out if line.startswith("REDIAL")] == ["REDIAL 1"] + ["REDIAL 0"] * 5 + [
        "REDIAL 1"
    ]
    assert out[-1] == "REST 0 -1"
    out = run(fake, *script, "tick %d" % (t + 7000), "CONNECT_FAIL", "redial")  # trouble, not the plan
    assert out[-1] == "REDIAL 0"


def test_no_viewer_rests_as_before(fake):
    """R_FW_BLE_VIEWER_KEEP: a page hidden or closed stops polling; 10 s after its last poll the
    hang-up starts the rest exactly as without the viewer keep.

    .. test:: Without a viewer in the last 10 s the hang-up rests
       :id: T_FW_BLE_VIEWER_GONE
       :links: R_FW_BLE_VIEWER_KEEP
    """
    base = [*REST_PRE, *PAIRED, *parked(), "input %d" % T]
    out = run(fake, *base, *ticks(T, T + 10000), "DISCONNECTED 19", "redial", "rest")
    assert out[-2:] == ["REDIAL 1", "REST 0 -1"]  # the last poll 10 s ago: still a viewer
    out = run(fake, *base, *ticks(T, T + 10001, 10001), "DISCONNECTED 19", "redial", "rest")
    assert out[-2] == "REDIAL 0" and out[-1].startswith("REST 1 ")


def test_a_viewer_appearing_in_rest_leaves_it_at_once(fake):
    """R_FW_BLE_VIEWER_KEEP: a page opened or returned to sends a deliberate poll (``?seen=1`` =
    ``input``); in rest that call ends the rest (the same reply says rest false, no card flash) and
    reconnects as soon as the unit's cooldown allows. A plain poll with no input in 10 min stays in
    rest; mode off keeps the rest.

    .. test:: A returning viewer leaves rest in the same call and reconnects after the cooldown
       :id: T_FW_BLE_VIEWER_LEAVES_REST
       :links: R_FW_BLE_VIEWER_KEEP
    """
    rest = [*REST_PRE, *PAIRED, *parked(), "DISCONNECTED 19"]
    out = run(fake, *rest, "tick %d" % (T + 20000), "viewer %d" % (T + 20000), "rest")
    assert out[-1].startswith("REST 1 ")  # a poll alone is no input
    out = run(fake, *rest, "tick %d" % (T + 20000), "input %d" % (T + 20000), "rest", "redial")
    assert out[-2:] == ["REST 0 -1", "REDIAL 1"]
    out = run(fake, *rest, "tick %d" % (T + 20000), "input %d" % (T + 20000), "tick %d" % (T + 20100))
    assert calls(after(out, "LOG session: power-save rest off (viewer)"), "connect_bonded")
    out = run(fake, *rest, "tick %d" % (T + 3000), "input %d" % (T + 3000), "tick %d" % (T + 6999))
    assert not calls(after(out, "LOG session: power-save rest off (viewer)"), "connect_bonded")
    out = run(fake, *rest, "tick %d" % (T + 3000), "input %d" % (T + 3000), "tick %d" % (T + 7000))
    assert calls(after(out, "LOG session: power-save rest off (viewer)"), "connect_bonded")
    out = run(fake, *rest, "> ble mode off", "input %d" % T, "rest")
    assert out[-1].startswith("REST 1 ")


def test_ten_minutes_without_input_rest_despite_the_viewer(fake):
    """R_FW_BLE_VIEWER_KEEP idle cap: 10 min without user input (POST /api/ble active; a page appearing
    starts the clock, a poll alone is no input) -> the next hang-up rests even with the page visible.
    Input resumes the redial, also out of the rest.

    .. test:: Ten minutes without input rest despite a viewer; input resumes the redial
       :id: T_FW_BLE_VIEWER_IDLE_CAP
       :links: R_FW_BLE_VIEWER_KEEP
    """
    cap = 600000
    base = [*REST_PRE, *PAIRED, *parked(), "input %d" % T, *watched(T, T + cap, 4000)]
    out = run(fake, *base, "viewer %d" % (T + cap + 1), "tick %d" % (T + cap + 1), "DISCONNECTED 19", "rest")
    assert out[-1].startswith("REST 1 ")
    out = run(
        fake, *base, "input %d" % (T + cap - 5000), "tick %d" % (T + cap + 1), "DISCONNECTED 19", "rest"
    )
    assert out[-1] == "REST 0 -1"
    out = run(
        fake,
        *base,
        "viewer %d" % (T + cap + 1),
        "tick %d" % (T + cap + 1),
        "DISCONNECTED 19",
        "viewer %d" % (T + cap + 2000),  # a poll in rest: still idle
        "rest",
        "input %d" % (T + cap + 3000),
        "rest",
        "redial",
    )
    told = [line for line in out if line.startswith(("REST", "REDIAL"))]
    assert told[0].startswith("REST 1 ") and told[1:] == ["REST 0 -1", "REDIAL 1"]
    # a 10.5 s poll stall (a slow network) is no new input: the cap still counts from T
    stall = [*REST_PRE, *PAIRED, *parked(), "input %d" % T, *watched(T, T + 300000, 4000)]
    stall += ["tick %d" % (T + 310500), *watched(T + 310500, T + cap, 4000)]
    out = run(fake, *stall, "viewer %d" % (T + cap + 1), "tick %d" % (T + cap + 1), "DISCONNECTED 19", "rest")
    assert out[-1].startswith("REST 1 ")


def test_a_reconnect_out_of_rest_that_never_reaches_the_unit_rests_again(fake):
    """R_FW_BLE_VIEWER_KEEP: a viewer took the session out of rest, but the unit deep-sleeps, so the
    connects fail or time out. Once the viewer is gone the next failure re-enters the rest instead of
    an endless backoff; while the viewer stays the backoff goes on, and a link that came up ends it.

    .. test:: Failed reconnects out of rest go back to rest once nobody looks
       :id: T_FW_BLE_VIEWER_UNREACHABLE
       :links: R_FW_BLE_VIEWER_KEEP
    """
    left = [*REST_PRE, *PAIRED, *parked(), "DISCONNECTED 19", "tick %d" % (T + 7000), "input %d" % (T + 7000)]
    left += ["tick %d" % (T + 7100)]  # connect_bonded
    out = run(fake, *left, "tick %d" % (T + 17101), "CONNECT_FAIL", "rest")
    assert out[-1].startswith("REST 1 ")
    assert "LOG session: power-save rest on (unreachable)" in out
    out = run(fake, *left, "viewer %d" % (T + 9000), "tick %d" % (T + 9000), "CONNECT_FAIL", "rest")
    assert out[-1] == "REST 0 -1"  # the viewer still looks: keep trying
    out = run(fake, *left, "tick %d" % (T + 7100 + 20000), "rest")  # no connect verdict at all: timeout
    assert out[-1].startswith("REST 1 ")
    up = [*left, "CONNECTED", "ENC_OK", *parked(at=T + 7100, first=False), "tick %d" % (T + 30000)]
    out = run(fake, *up, "DISCONNECTED 8", "rest")  # the link came up: an ordinary loss, backoff
    assert out[-1] == "REST 0 -1"


def test_a_refused_connect_out_of_rest_rests_again_once_nobody_looks(fake):
    """R_FW_BLE_VIEWER_KEEP: the back-to-rest rule also covers a connect the transport refuses at once
    (``connect_bonded`` returns an error, no event follows): while the viewer looks the backoff goes
    on; the first refusal after it left re-enters the rest.

    .. test:: A refused connect out of rest rests again once no viewer looks
       :id: T_FW_BLE_VIEWER_REFUSED
       :links: R_FW_BLE_VIEWER_KEEP
    """
    left = [*REST_PRE, *PAIRED, *parked(), "DISCONNECTED 19", "tick %d" % (T + 7000), "input %d" % (T + 7000)]
    left += ["fail connect_bonded", "tick %d" % (T + 7100), "rest"]
    out = run(fake, *left)
    assert "LOG session: connect_bonded failed -1" in out and out[-1] == "REST 0 -1"  # the viewer looks
    asleep = [x for t in range(T + 8000, T + 30001, 1000) for x in ("fail connect_bonded", "tick %d" % t)]
    out = run(fake, *left, *asleep, "rest")  # every connect refused at once
    assert "LOG session: power-save rest on (unreachable)" in out and out[-1].startswith("REST 1 ")


def test_mode_off_clears_the_planned_redial(fake):
    """R_FW_BLE_VIEWER_KEEP: mode off cancels the pending reconnect, so ``redial`` goes false too
    (/api/state never reports a redial that is not planned).

    .. test:: Mode off clears the redial flag with the reconnect
       :id: T_FW_BLE_VIEWER_OFF
       :links: R_FW_BLE_VIEWER_KEEP
    """
    out = run(
        fake,
        *REST_PRE,
        *PAIRED,
        *parked(),
        "input %d" % T,
        "DISCONNECTED 19",
        "redial",
        "> ble mode off",
        "redial",
    )
    assert [line for line in out if line.startswith("REDIAL")] == ["REDIAL 1", "REDIAL 0"]


def test_rest_left_on_an_up_link_takes_the_ordinary_backoff_later(fake):
    """R_FW_BLE_VIEWER_KEEP: the "back to rest when unreachable" rule only covers a reconnect that
    never brought a link up. A rest probe whose read shows camping on ends the rest with its link up;
    camping then goes off on that same link and a loss the satellite saw (a supervision timeout, 8)
    with no viewer takes the ordinary backoff, as R_FW_BLE_REST says, not the rest.

    .. test:: Rest left on an up link never re-enters rest through the unreachable rule
       :id: T_FW_BLE_VIEWER_UP_LEFT
       :links: R_FW_BLE_VIEWER_KEEP, R_FW_BLE_REST
    """
    poll = 15 * 60000
    rest = ["restpoll 15", *REST_PRE, *PAIRED, *parked(), "DISCONNECTED 19"]
    on = [*rest, "tick %d" % (T + poll), "CONNECTED", "ENC_OK", *parked(CAMPING_ON, at=T + poll, first=False)]
    out = run(
        fake, *on, "NOTIFY 1202 %s" % CAMPING_OFF, "tick %d" % (T + poll + 5000), "DISCONNECTED 8", "rest"
    )
    assert "LOG session: power-save rest off (unit_state)" in out
    assert out[-2:] == ["LOG session: reconnect in 7000 ms", "REST 0 -1"]


def test_a_command_or_reconnect_counts_as_input(fake):
    """R_FW_BLE_VIEWER_KEEP: a control command or the Reconnect button (``wake``) stamps the input
    clock itself, so the idle cap does not depend on the page's input report arriving first.

    .. test:: A command or Reconnect is input for the idle cap
       :id: T_FW_BLE_VIEWER_WAKE_INPUT
       :links: R_FW_BLE_VIEWER_KEEP
    """
    for act in ("connect", "submit - cooler level 2"):
        rest = [*REST_PRE, *PAIRED, *parked(), "DISCONNECTED 19", "tick %d" % (T + 60000), act]
        out = run(
            fake, *rest, *watched(T + 62000, T + 66000), "CONNECTED", "ENC_OK", "DISCONNECTED 19", "redial"
        )
        assert out[-1] == "REDIAL 1", act  # never an input report, yet the viewer keeps the redial


def test_command_during_the_backoff_connects_now(fake):
    """A control command must not wait out a reconnect delay (#264): the submit still answers
    NOT_READY (there is no link), but the pending reconnect is made due as soon as the unit's cooldown
    allows (drop + 7 s), so the client's retry lands in seconds.

    .. test:: A control command during a reconnect delay reconnects as soon as the cooldown allows
       :id: T_FW_SESSION_KICK_NUDGE
       :links: R_FW_SESSION
    """
    # three short links drive the backoff to 8 s, so a plain reconnect after the next drop is due
    # at drop + 8000 -- later than the unit's 7 s cooldown, which is as soon as a nudge may connect
    script = [*PAIRED, *READ_ALL, "DISCONNECTED 19"]
    t = T
    for _ in range(3):
        t += 7000
        script += ["tick %d" % t, "CONNECTED", "ENC_OK", "tick %d" % (t + 1300), "DISCONNECTED 19"]
        t += 1300
    drop = t  # backoff is 8000 now: the plain reconnect would be at drop + 8000
    script += ["tick %d" % (drop + 100), "submit - cooler level 2"]
    out = run(fake, *script, "tick %d" % (drop + 6999))
    assert "SUBMIT 7 -" in out  # CALI_CTL_NOT_READY
    assert not calls(after(out, "SUBMIT 7 -"), "connect_bonded")  # not inside the cooldown
    out = run(fake, *script, "tick %d" % (drop + 7000))
    assert calls(after(out, "SUBMIT 7 -"), "connect_bonded")  # due at drop + 7000, not drop + 8000


def test_no_reconnect_within_the_units_cooldown(fake):
    """Camping master off (2026-10-22/23, 98 links): every link that started < 6 s after the unit
    ended the previous one was cut ~1.2 s later (51/51), every later one held (0/47). Neither the
    backoff nor a command's nudge reconnects before CALI_SESSION_UNIT_COOLDOWN_MS (7 s).

    .. test:: No reconnect within the unit's cooldown after a lost link
       :id: T_FW_SESSION_UNIT_COOLDOWN
       :links: R_FW_SESSION
    """
    out = run(fake, *PAIRED, *READ_ALL, "DISCONNECTED 531")
    assert out[-1] == "LOG session: reconnect in 7000 ms"
    out = run(fake, *PAIRED, *READ_ALL, "DISCONNECTED 531", "submit - cooler level 2", "tick %d" % (T + 6999))
    assert "SUBMIT 7 -" in out and not calls(after(out, "SUBMIT 7 -"), "connect_bonded")
    out = run(fake, *PAIRED, *READ_ALL, "DISCONNECTED 531", "submit - cooler level 2", "tick %d" % (T + 7000))
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
    by bond after the 7 s cooldown, then a fresh read-all from the first function and one SNAP."""
    half = READ_ALL[:7]  # DISCOVERED + the variant read + warm-up + 4 of the T7's reads
    drop_at = T + 600
    out = run(
        fake,
        *PAIRED,
        *half,
        "tick %d" % drop_at,
        "DISCONNECTED",
        "READ %x 0" % CHARS[5],  # a completion racing the drop: stale
        *["tick %d" % t for t in range(drop_at + 100, drop_at + 7100, 100)],
        "CONNECTED",
        "ENC_OK",
        *read_all(at=drop_at + 7000, first=False),  # 1004 stored on this boot: no variant read
    )
    drop = out.index("LOG session: reconnect in 7000 ms")
    reconnect = out.index("CALL connect_bonded")
    assert drop < reconnect
    dead = out[drop:reconnect]
    assert not calls(dead, "read") and not calls(dead, "write_heartbeat"), dead
    assert not snaps(out[:reconnect])  # the half-finished read-all printed none
    # the reconnect waits the backoff: not on the ticks before drop + 7000
    ticks = run(
        fake,
        *PAIRED,
        *half,
        "tick %d" % drop_at,
        "DISCONNECTED",
        *["tick %d" % t for t in range(drop_at + 100, drop_at + 7000, 100)],
    )
    assert not calls(ticks, "connect_bonded")
    fresh = out[reconnect:]
    assert fresh[1] == "CALL discover"
    assert [line for line in fresh if line.startswith("CALL read")] == [
        "CALL read %d" % c for c in READS
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


# R_FW_VARIANT_GATE: the app's isSupportedForVariant table (variant -> functions it does NOT have)
VARIANT_SKIPS = {
    0: {"livingroomheater", "roofaircondition", "satelliteantenna", "stairs"},
    1: {"roof"},
    2: {"roof", "livingroomheater", "roofaircondition", "satelliteantenna", "stairs"}
    | {"energy", "water", "airheater", "cooler"},
}


def _subscribed(out):
    return [int(line.split()[2]) for line in calls(out, "subscribe")]


def _read_calls(out):
    return [int(line.split()[2]) for line in calls(after(out, "CALL discover"), "read")]


@pytest.mark.parametrize("variant", [0, 1, 2, 7, -1])
def test_variant_has_is_the_apps_table(fake, variant):
    out = run(fake, *["variant %s %d" % (fn, variant) for fn in FNS])
    skips = set() if variant < 0 else VARIANT_SKIPS.get(variant, VARIANT_SKIPS[0])  # 7 -> T7, -1 -> all
    assert out == ["HAS %s %d %d" % (fn, variant, fn not in skips) for fn in FNS]


def _gated(variant):
    """(subscribes, read CALLs) a first link makes for a 1004 frame of CarVariant ``variant``."""
    skips = VARIANT_SKIPS.get(variant, VARIANT_SKIPS[0])
    has = [c for c, fn in zip(CHARS, FNS) if fn not in skips]
    return [c for c in has if c not in (0x1001, 0xF001)], [0x1004] + [
        c for c in READS_ALL if c in has and c != 0x1004
    ]


@pytest.mark.parametrize("variant", [0, 1, 2, 7])
def test_first_link_reads_the_variant_then_subscribes_and_reads_only_its_functions(fake, variant):
    """A boot's first link reads 1004 before any subscribe, then subscribes (CODEC_CHARS order) and
    reads (READ_FIRST order) only the functions the CarVariant has; 1004 is not read twice. T7 = the
    app's 8 CCCD writes (1702 1202 1102 1602 1502 1402 1004 1302 — the capture's set, here in
    CODEC_CHARS order); an unknown variant (7) is a T7.

    .. test:: The firmware subscribes and reads only the unit variant's functions
       :id: T_FW_VARIANT_GATE
       :links: R_FW_VARIANT_GATE
    """
    subs, reads = _gated(variant)
    frame = "%x102" % variant  # CarVariant = the top 4 bits of byte 0 (MSB-first)
    script = ["DISCOVERED 0", "READ 1004 0 " + frame, "tick %d" % WARM, *["READ %x 0" % c for c in reads[1:]]]
    out = run(fake, *PAIRED, *script)
    rest = after(out, "CALL discover")
    assert rest[0] == "CALL read 4100" and rest[1 : 1 + len(subs)] == ["CALL subscribe %d" % c for c in subs]
    assert _subscribed(out) == subs and _read_calls(out) == reads
    skipped = sorted(VARIANT_SKIPS.get(variant, VARIANT_SKIPS[0]), key=FNS.index)
    assert "LOG session: variant %d — skipping %s" % (variant, ", ".join(skipped)) in out
    snap = snaps(out)[-1]["fn"]
    assert not set(snap) & set(skipped) and snap["vehicle"]["CarVariant"] == variant
    if variant == 0:
        assert sorted(subs) == sorted([0x1702, 0x1502, 0x1102, 0x1602, 0x1004, 0x1302, 0x1402, 0x1202])


@pytest.mark.parametrize("fail", [["READ 1004 14"], ["fail read"]], ids=["status", "refused"])
def test_a_failed_variant_read_subscribes_and_reads_everything(fake, fail):
    pre = ["fail read", "DISCOVERED 0"] if fail == ["fail read"] else ["DISCOVERED 0", *fail]
    out = run(fake, *PAIRED, *pre, "tick %d" % WARM, *["READ %x 0" % c for c in READS_ALL])
    assert _subscribed(out) == [c for c in CHARS if c not in (0x1001, 0xF001)]
    assert _read_calls(out) == [0x1004, *READS_ALL]  # the failed variant read, then every function
    assert "LOG session: variant unknown — subscribing and reading every function" in out
    assert set(snaps(out)[-1]["fn"]) == set(FNS) - {"water"}  # the lone 0102 water hasn't settled


def test_a_reconnect_in_the_same_boot_uses_the_stored_variant(fake):
    up = T + 7200
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        "DISCONNECTED",
        "tick %d" % up,
        "CONNECTED",
        "ENC_OK",
        *read_all(at=up, first=False),
    )
    relink = after(out, "CALL connect_bonded")
    assert calls(relink, "read") == ["CALL read %d" % c for c in READS]  # no 1004 before the subscribes
    assert _subscribed(relink) == SUBS


def test_snap_line_bytes_are_pinned(fake):
    """The console ``SNAP`` line, byte for byte, for a fixed frame set: only cooler read (one byte,
    so only the fields inside it), every other read failed. Pins the emitter (``snapshot.c``) and
    the line framing together — key order, no whitespace, the ``t`` of the read-all's last tick."""
    reads = ["READ %x %s" % (c, "0 01" if c == 0x1102 else "14") for c in READS_ALL]
    # the variant read failed too: every function is read (R_FW_VARIANT_GATE's fallback)
    out = run(fake, *PAIRED, "DISCOVERED 0", "READ 1004 14", *read_all(reads=reads, first=False)[1:])
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


def test_wifi_creds_count_as_validated_only_after_they_joined(fake):
    """#273 M1 (R6's premise): ``wifi_ok`` is "0" from ``wifi set`` until GOT_IP makes it "1". Creds
    saved but never joined (a power cut before the FAILED) boot like a setup-flow join: hotspot up,
    and a failure clears them instead of retrying a typo forever."""
    out = run(
        fake, "wifi_boot", "> wifi set minsel " + PSK, "kv wifi_ok", "NET_GOT_IP 192.168.1.42", "kv wifi_ok"
    )
    assert [line for line in out if line.startswith("KV")] == ["KV wifi_ok 0", "KV wifi_ok 1"]
    out = run(
        fake,
        "kvset wifi_ssid minsel",
        "kvset wifi_psk wrong-psk-99",
        "kvset wifi_ok 0",
        "wifi_boot",
        "NET_FAILED 2",
        "kv wifi_ssid",
    )
    assert out == [
        AP,
        "NET udp_bind 53",
        "NET scan",
        "LOG wifi: joining minsel",
        "NET sta_start minsel wrong-psk-99",
        "LOG wifi: failed auth",
        "LOG wifi: credentials cleared",
        "KV wifi_ssid missing",
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


def test_wifi_failed_replacement_restores_the_previous_network(fake):
    """G2: online on minsel, a replacement with a wrong password (auth) puts minsel back — RAM and kv,
    ``wifi_ok`` "1" — and rejoins it on the retry backoff; ``reverted`` + the replacement's reason
    stay until new credentials (the page says "back on minsel"); no passphrase in any LOG line."""
    out = run(
        fake,
        *_online(
            "> wifi set typo wrong-psk-99",
            "NET_FAILED 2",
            "kv wifi_ssid",
            "kv wifi_psk",
            "kv wifi_ok",
            "lastfail",
            "reverted",
            "tick 100",
            "tick 1100",
            "NET_GOT_IP 192.168.1.42",
            "lastfail",
            "reverted",
            "> wifi status",
            "> wifi set other new-psk-5678",
            "reverted",
            "lastfail",
        ),
    )
    rest = after(out, "NET sta_start typo wrong-psk-99")
    assert rest[:13] == [
        "LOG wifi: failed auth",
        "LOG wifi: replacement failed, back to minsel",
        "KV wifi_ssid minsel",
        "KV wifi_psk " + PSK,
        "KV wifi_ok 1",
        "LASTFAIL auth",
        "REVERTED 1",
        "LOG wifi: joining minsel",
        "NET sta_start minsel " + PSK,
        "NET mdns %s %d" % (CONSTS["NET_HOSTNAME"], CONSTS["NET_HTTP_PORT"]),
        "LOG wifi: online 192.168.1.42",
        "LASTFAIL auth",
        "REVERTED 1",
    ]
    assert rest[13].startswith("LOG wifi: station ssid=minsel ip=192.168.1.42")
    assert rest[-2:] == ["REVERTED 0", "LASTFAIL -"]  # a fresh POST/wifi set clears both
    assert "LOG wifi: credentials cleared" not in out
    assert not any(PSK in line or "wrong-psk" in line for line in out if line.startswith("LOG"))


def test_wifi_successful_replacement_drops_the_previous_network(fake):
    """G2: a replacement that joins keeps the new network (no revert); the old one is no longer a
    fallback — the next failed replacement goes back to the NEW one."""
    out = run(
        fake,
        *_online(
            "> wifi set other new-psk-5678",
            "NET_GOT_IP 192.168.1.43",
            "reverted",
            "kv wifi_ssid",
            "> wifi set typo wrong-psk-99",
            "NET_FAILED 1",
            "kv wifi_ssid",
            "lastfail",
        ),
    )
    rest = after(out, "LOG wifi: online 192.168.1.43")
    assert rest[:2] == ["REVERTED 0", "KV wifi_ssid other"]
    assert rest[-4:] == [
        "LOG wifi: failed not_found",
        "LOG wifi: replacement failed, back to other",
        "KV wifi_ssid other",
        "LASTFAIL not_found",
    ]


def test_wifi_first_setup_failure_has_nothing_to_restore(fake):
    """G2 leaves first-time setup alone: a typo there still clears the creds, nothing is reverted."""
    out = run(fake, "wifi_boot", "NET_AP_STARTED", "> wifi set typo wrong-psk-99", "NET_FAILED 2", "reverted")
    assert after(out, "NET sta_start typo wrong-psk-99") == [
        "LOG wifi: failed auth",
        "LOG wifi: credentials cleared",
        "REVERTED 0",
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


def test_wifi_scan_list_has_one_entry_per_ssid(fake):
    """#273 M9: a mesh/multi-AP network answers a scan once per access point; the list keeps one
    entry per SSID, so repeats do not use up the NET_SCAN_MAX slots on the setup page."""
    out = run(fake, "wifi_boot", "NET_SCAN_DONE minsel other minsel minsel", "> wifi status")
    assert out[-1] == "LOG wifi: setup ssid=- ip=- rssi=- scan=2"


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
    reads = reads or ["READ %x 0%s" % (c, " " + frames[c] if c in frames else "") for c in READS]
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
    reads = ["READ %x 0" % c for c in READS[:5]]  # the arm delay passes with the read-all unfinished
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
    reads = ["READ %x %d" % (c, 14 if c == 0x1102 else 0) for c in READS]  # the cooler read failed
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
    up = t + 7200  # the session reconnects 7 s (the unit's cooldown) after the drop
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
        *read_all(at=up, first=False),
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
    reads = ["READ %x %d" % (c, 14 if c == 0x1102 else 0) for c in READS]  # link 2: cooler read fails
    up = ARM + 7200
    out = run(
        fake,
        *armed(frames),
        "DISCONNECTED",
        "tick %d" % (ARM + 100),
        "tick %d" % up,
        "CONNECTED",
        "ENC_OK",
        *read_all(at=up, reads=reads, first=False),
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

    up = ARM + 7200
    relinked = [
        *armed({0x1502: FAVS_3_EMPTY}),
        "DISCONNECTED",
        "tick %d" % (ARM + 100),
        "tick %d" % up,
        "CONNECTED",
        "ENC_OK",
        *read_all(at=up, first=False),
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
    up = ARM + 7200
    t = up + ARM + 100
    out = run(
        fake,
        *armed({0x1502: WAKE_0700_ON}),
        "DISCONNECTED",
        "tick %d" % (ARM + 100),
        "tick %d" % up,
        "CONNECTED",
        "ENC_OK",
        *read_all(at=up, first=False),
        "tick %d" % (up + ARM),
        "submit %d lighting wakeup 08:00" % LN,
        "tick %d" % t,
    )
    assert snaps(out)[-1]["fn"]["lighting"]["WakeupLightValue"] == 0x1101
    assert writes(out) == PULL_WRITES[:1]


def _relink_lighting_unread(at, first=False):
    """A new link at session time ``at`` whose read-all has no lighting frame (its read fails);
    ``first``: another unit (its stored frames dropped), so the variant is read again."""
    reads = ["READ %x %d" % (c, 14 if c == 0x1502 else 0) for c in READS]
    return ["CONNECTED", "ENC_OK", *read_all(at=at, reads=reads, first=first)]


def test_another_units_state_is_never_shown_after_forget_or_a_new_bond(fake):
    """Task-3 carry-over M1: when the bond's identity changes (forget, or a bond to another unit) the
    stored frames and the shown config latch are dropped — the old unit's wake-up is never displayed
    for the new one. The same unit keeps them shown across links (test above)."""
    up = ARM + 7200
    old = [*armed({0x1502: WAKE_0700_ON}), "DISCONNECTED", "tick %d" % (ARM + 100), "tick %d" % up]
    same = run(fake, *old, *_relink_lighting_unread(up))
    assert snaps(same)[-1]["fn"]["lighting"]["WakeupLightValue"] == 0x1101
    other = run(fake, *old, "ident AA:BB:CC:DD:EE:FF", *_relink_lighting_unread(up, first=True))
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
        *read_all(at=t + 3000, reads=["READ %x %d" % (c, 14 if c == 0x1502 else 0) for c in READS]),
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
    reads = ["READ %x 0%s" % (c, " " + zones if c == 0x1502 else "") for c in READS]
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


def _diag(out):
    return [json.loads(line[5:]) for line in out if line.startswith("DIAG ")]


def test_diag_events_link_cmd_wifi(fake):
    """R_FW_DIAG_LOG call sites: link_up (reconnect time + connect attempts since the last down),
    link_down (why, the transport status, how long it was up), cmd (fn + control name, result,
    latency, source — never the value), wifi_up/wifi_down and a failed join.

    .. test:: The session, control sequencer and WiFi runner emit their diagnostics events
       :id: T_FW_DIAG_EVENTS
       :links: R_FW_DIAG_LOG
    """
    frames = {0x1102: pack("cooler", Installed=1, State=0, Mode=4, Level=3)}
    t = ARM + 100
    out = run(
        fake,
        "diaghook",
        *armed(frames),
        "> set cooler power on",
        "tick %d" % t,
        "tick %d" % (t + 300),
        "WRITTEN 1101 0",
        "> set cooler level 99",
        "DISCONNECTED 531",
        "tick %d" % (t + 7300),  # the 7 s cooldown: connect_bonded
        "CONNECT_FAIL",
        "tick %d" % (t + 9300),  # 2 s backoff: again
        "CONNECTED",
        "ENC_OK",
    )
    ev = [e for e in _diag(out) if e["ev"] == "evt"]
    assert ev[0] == {
        "ev": "evt",
        "kind": "link_up",
        "reconnect_ms": None,
        "attempts": 0,
    }  # paired: no down yet
    assert ev[1] == {"ev": "evt", "kind": "cmd", "fn": "cooler", "what": "power", "result": "ack",
                     "latency_ms": 400, "src": "console"}  # submitted at tick ARM, ACKed after t + 300  # fmt: skip
    assert ev[2]["kind"] == "cmd" and ev[2]["result"] == "bad_value" and "99" not in json.dumps(ev[2])
    assert ev[3] == {
        "ev": "evt",
        "kind": "link_down",
        "reason": "disconnected",
        "status": 531,
        "dur_ms": t + 300,
    }
    assert ev[4] == {"ev": "evt", "kind": "link_up", "reconnect_ms": 9000, "attempts": 2}
    # a heartbeat that cannot be written: the reason says so
    hb = _diag(run(fake, "diaghook", *PAIRED, *READ_ALL, "fail write_heartbeat", "tick %d" % (T + PERIOD)))
    assert hb[-1] | {"dur_ms": 0} == {
        "ev": "evt",
        "kind": "link_down",
        "reason": "heartbeat",
        "status": -1,
        "dur_ms": 0,
    }
    # WiFi: up (rssi, no previous lease), lost (rssi, time online), a failed join; never the address
    w = _diag(run(fake, "diaghook", *_online("rssi -61", "tick 100", "tick 5100", "NET_LOST")))
    assert w == [
        {"ev": "evt", "kind": "wifi_up", "rssi": 0, "ip_changed": False},
        {"ev": "evt", "kind": "wifi_down", "reason": "lost", "rssi": -61, "dur_ms": 5100},
    ]
    typo = run(
        fake, "diaghook", "wifi_boot", "NET_AP_STARTED", "> wifi set minsel wrong-psk-99", "NET_FAILED 2"
    )
    assert _diag(typo) == [{"ev": "evt", "kind": "net", "err": "join", "reason": "auth"}]


# ---- BLE link modes hold / quiet / off and the one-shot probe -----------------------------------

_RECONNECT = ["CONNECTED", "ENC_OK", "DISCOVERED 0"]


def test_quiet_mode_stops_the_heartbeat_but_keeps_the_link(fake):
    """BLE modes: quiet holds the link without 1003.

    .. test:: quiet holds the link without 1003
       :id: T_FW_BLE_QUIET
       :links: R_FW_BLE_MODE
    """
    out = run(
        fake,
        "diaghook",
        *PAIRED,
        *READ_ALL,
        "> ble mode quiet",
        *["tick %d" % t for t in range(T + 100, T + 5000, 100)],
    )
    tail = after(out, "LOG session: ble mode quiet (console)")
    assert calls(tail, "write_heartbeat") == []
    assert calls(tail, "disconnect") == []
    assert {"ev": "evt", "kind": "ble_mode", "mode": "quiet", "why": "console"} in _diag(out)


def test_off_mode_drops_the_link_and_never_reconnects(fake):
    """BLE modes: off releases the link for good.

    .. test:: off releases the link for good
       :id: T_FW_BLE_OFF
       :links: R_FW_BLE_MODE
    """
    out = run(
        fake,
        "diaghook",
        *PAIRED,
        *READ_ALL,
        "> ble mode off",
        "DISCONNECTED 531",
        *["tick %d" % t for t in range(T + 100, T + 70000, 1000)],
    )
    tail = after(out, "LOG session: ble mode off (console)")
    assert calls(tail, "disconnect") == ["CALL disconnect"]
    assert calls(tail, "connect_bonded") == []
    assert any(e.get("kind") == "link_down" and e["reason"] == "ble_off" for e in _diag(out))


def test_probe_from_off_connects_reads_all_then_disconnects(fake):
    """BLE modes: probe = one connect, one read-all, one disconnect.

    .. test:: probe = one connect, one read-all, one disconnect
       :id: T_FW_BLE_PROBE
       :links: R_FW_BLE_PROBE
    """
    t0 = T + 1000
    out = run(
        fake,
        "diaghook",
        *PAIRED,
        *READ_ALL,
        "> ble mode off",
        "DISCONNECTED 531",
        "tick %d" % t0,
        "> ble probe",
        *_RECONNECT,
        *read_all(t0 + 100, first=False),
        *["tick %d" % t for t in range(t0 + 3000, t0 + 70000, 1000)],
    )
    tail = after(out, "LOG session: ble mode off (console)")
    assert len(calls(tail, "connect_bonded")) == 1
    assert len(calls(tail, "write_heartbeat")) >= 1  # the warm-up heartbeat ran
    done = after(out, "LOG session: probe ok (read_all)")
    assert calls(done, "disconnect") == ["CALL disconnect"]  # the probe's own disconnect
    assert any(e.get("kind") == "link_down" and e["reason"] == "ble_off" for e in _diag(done))
    assert {"ev": "evt", "kind": "probe", "ok": True, "mode": "off", "reason": "read_all"} in _diag(out)


def test_probe_that_cannot_connect_logs_fail_and_stays_off(fake):
    """BLE modes: a sleeping unit = probe fail, no retry.

    .. test:: a sleeping unit = probe fail, no retry
       :id: T_FW_BLE_PROBE_FAIL
       :links: R_FW_BLE_PROBE
    """
    t0 = T + 1000
    out = run(
        fake,
        "diaghook",
        *PAIRED,
        *READ_ALL,
        "> ble mode off",
        "DISCONNECTED 531",
        "tick %d" % t0,
        "> ble probe",
        *["tick %d" % t for t in range(t0 + 1000, t0 + 90000, 1000)],
    )
    assert {"ev": "evt", "kind": "probe", "ok": False, "mode": "off", "reason": "connect_timeout"} in _diag(
        out
    )
    assert len(calls(after(out, "LOG session: ble mode off (console)"), "connect_bonded")) == 1


def test_command_path_connect_now_forces_hold(fake):
    """BLE modes: a command always gets a link.

    .. test:: a command always gets a link
       :id: T_FW_BLE_CMD_HOLD
       :links: R_FW_BLE_MODE
    """
    out = run(
        fake,
        "diaghook",
        *PAIRED,
        *READ_ALL,
        "> ble mode off",
        "DISCONNECTED 531",
        "tick %d" % (T + 1000),
        "submit - cooler power 1",
        "tick %d" % (T + 1100),
    )
    assert {"ev": "evt", "kind": "ble_mode", "mode": "hold", "why": "command"} in _diag(out)
    assert "CALL connect_bonded" in after(out, "LOG session: ble mode hold (command)")


def test_quiet_probe_runs_the_heartbeat_only_through_its_warm_up(fake):
    """BLE modes: quiet probe heartbeat window.

    .. test:: quiet probe heartbeat window
       :id: T_FW_BLE_QUIET_PROBE
       :links: R_FW_BLE_PROBE
    """
    t0 = T + 1000
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        "> ble mode quiet",
        "tick %d" % t0,
        "> ble probe",
        *["tick %d" % t for t in range(t0 + 100, t0 + 2100, 100)],
        *["READ %x 0" % c for c in READS],
        *["tick %d" % t for t in range(t0 + 2200, t0 + 8000, 100)],
    )
    hb = calls(after(out, "LOG session: ble mode quiet (console)"), "write_heartbeat")
    assert 2 <= len(hb) <= 5  # ~2 s of 600 ms heartbeats, then silence
    assert calls(after(out, "LOG session: probe ok (read_all)"), "write_heartbeat") == []


def test_boot_in_off_does_not_connect_and_a_new_bond_comes_up_in_hold(fake):
    """BLE modes: pairing resets to hold.

    .. test:: pairing resets to hold
       :id: T_FW_BLE_PAIR_HOLD
       :links: R_FW_BLE_MODE
    """
    out = run(fake, "diaghook", *PAIRED, *READ_ALL, "> ble mode off", "DISCONNECTED 531", "boot")
    assert calls(after(out, "LOG session: ble mode off (console)"), "connect_bonded") == []
    out2 = run(fake, "diaghook", "> ble mode off", *PAIRED)
    assert {"ev": "evt", "kind": "ble_mode", "mode": "hold", "why": "pairing"} in _diag(out2)
    assert calls(after(out2, "LOG session: ble mode hold (pairing)"), "connect_bonded") == []


def test_probe_waits_for_an_outstanding_water_reread(fake):
    """BLE modes: no overlapping ATT reads.

    .. test:: no overlapping ATT reads
       :id: T_FW_BLE_PROBE_REREAD
       :links: R_FW_BLE_PROBE
    """
    t0 = T + 30000  # the water re-read is due
    pre = [*PAIRED, *READ_ALL, "tick %d" % t0, "> ble probe"]
    wait = ["tick %d" % t for t in range(t0 + 100, t0 + 3000, 100)]

    def reads(*script):
        out = run(fake, *pre, *script)
        return [line for line in out if line.startswith("CALL read")]

    base = len(reads())  # the first read-all plus the water re-read
    assert reads(*wait)[base:] == []  # warm-up over, the re-read unanswered: no read-all start
    after_reply = reads(*wait, "READ 1302 0", "tick %d" % (t0 + 3100))[base:]
    assert after_reply == ["CALL read %d" % READS[0]]


def test_stop_during_a_probe_ends_it(fake):
    """BLE modes: forget mid-probe clears the probe.

    .. test:: forget mid-probe clears the probe
       :id: T_FW_BLE_PROBE_STOP
       :links: R_FW_BLE_PROBE
    """
    t0 = T + 1000
    out = run(
        fake,
        "diaghook",
        *PAIRED,
        *READ_ALL,
        "> ble mode quiet",
        "tick %d" % t0,
        "> ble probe",
        "> forget",
        "> ble",
    )
    assert {"ev": "evt", "kind": "probe", "ok": False, "mode": "quiet", "reason": "stop"} in _diag(out)
    assert (
        'LOG ble: {"ble":{"mode":"quiet","probing":false,"exp":null,"idle_min":0,"rest_poll_min":30,"unit_adv_seen_ms_ago":null}}'
        in out
    )


def test_console_ble_exp_without_time_is_refused(fake):
    """``ble exp`` needs a wall clock (SNTP or the unit's): without one it is refused, the mode stays.

    .. test:: no time, no BLE experiment
       :id: T_FW_BLE_EXP_NOTIME
       :links: R_FW_BLE_EXP
    """
    out = run(fake, *PAIRED, *READ_ALL, "> ble exp 120 22-06", "tick %d" % (T + 100), "> ble")
    assert "LOG ble-exp: refused (no time)" in out
    assert (
        out[-1]
        == 'LOG ble: {"ble":{"mode":"hold","probing":false,"exp":null,"idle_min":0,"rest_poll_min":30,"unit_adv_seen_ms_ago":null}}'
    )


@pytest.mark.parametrize(
    "line", ["> ble exp 0", "> ble exp 721", "> ble exp 60 25-06", "> ble exp 60 22", "> ble exp x"]
)
def test_console_ble_exp_bad_args_print_usage(fake, line):
    """A bad phase length or window prints the usage, also without a clock.

    .. test:: ble exp argument check
       :id: T_FW_BLE_EXP_USAGE
       :links: R_FW_BLE_EXP
    """
    assert run(fake, line)[-1] == "LOG usage: ble exp <1-720 min> [HH-HH] | ble exp off"


CLOCK = "clock 1792530000000"  # SNTP synced (2026-10-20 21:00 UTC); the window 00-00 is all day anyway


def test_command_in_quiet_goes_hold_and_writes_only_after_the_heartbeat_armed(fake):
    """A command in ``quiet`` switches to ``hold`` (``why`` ``command``); the session is not ready until
    the 1003 heartbeat has run CODEC_ARM_DELAY_MS again, so the write never goes out without it.

    .. test:: a command in quiet re-arms the heartbeat before any control write
       :id: T_FW_BLE_QUIET_COMMAND
       :links: R_FW_BLE_MODE
    """
    frames = {0x1102: pack("cooler", Installed=1, State=0, Mode=4, Level=3)}
    t0 = ARM + 100
    out = run(
        fake,
        "diaghook",
        *armed(frames),
        "> ble mode quiet",
        "tick %d" % t0,
        "> set cooler power on",  # not ready: quiet -> hold, the heartbeat restarts
        *["tick %d" % t for t in range(t0 + 100, t0 + ARM, 100)],
        "> set cooler power on",  # t0 + ARM - 100: still arming
        "tick %d" % (t0 + ARM),
        "> set cooler power on",
        "tick %d" % (t0 + ARM + 100),
    )
    assert {"ev": "evt", "kind": "ble_mode", "mode": "hold", "why": "command"} in _diag(out)
    q = after(out, "LOG session: ble mode quiet (console)")
    assert out.count("LOG control: cooler/power not ready (no armed link or no state yet)") == 2
    first = q.index(writes(q)[0])
    assert writes(q) == want_writes("cooler", "power", "on", frames)
    assert calls(q[:first], "write_heartbeat")  # the heartbeat is back before the write
    assert q.index("LOG session: ble mode hold (command)") < q.index(calls(q, "write_heartbeat")[0])


def _probe_done(at):
    """The phase-end probe on an up link: warm-up, the read-all, the tick that sees it done."""
    # the tick at ``at`` also starts the 30-s water re-read; the probe's read-all waits for it
    return [
        "tick %d" % at,
        "READ 1302 0",
        "tick %d" % (at + WARM),
        *read_all(first=False)[2:],
        "tick %d" % (at + WARM + 100),
    ]


def test_ignition_in_quiet_aborts_the_phase_back_to_hold(fake):
    """The experiment's quiet phase: the van's ignition (cali_ac_ignition) on an up link counts as
    activity — the phase ends aborted and the mode returns to hold.

    .. test:: ignition during the quiet phase aborts it, and holds while it stays on
       :id: T_FW_BLE_EXP_IGNITION
       :links: R_FW_BLE_EXP
    """
    t = T + 100
    p = t + 60000  # 1-min hold phase ends -> probe
    q = p + WARM + 200  # quiet starts
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        CLOCK,
        "diaghook",
        "> ble exp 1 00-00",
        "tick %d" % t,
        *_probe_done(p),
        "tick %d" % q,
        "tick %d" % (q + 100),
        "ign 1",
        "tick %d" % (q + 200),
        "tick %d" % (q + 200 + 10 * 60000 + 100),  # still driving past the 10-min hold: no next phase
    )
    ev = [e for e in _diag(out) if e["ev"] == "evt" and e["kind"] in ("ble_phase", "ble_mode")]
    assert ev == [
        {"ev": "evt", "kind": "ble_phase", "mode": "hold", "aborted": False, "dur_ms": p + WARM + 100 - t},
        {"ev": "evt", "kind": "ble_mode", "mode": "quiet", "why": "exp"},
        {"ev": "evt", "kind": "ble_phase", "mode": "quiet", "aborted": True, "dur_ms": 200},
        {"ev": "evt", "kind": "ble_mode", "mode": "hold", "why": "exp_abort"},
    ]


GONE = 185000  # CALI_BX_GONE_MS (test_ble_exp checks it against the sensor scan period + window)


def _to_quiet():
    """Paired + read, a 5-min experiment: the hold phase (its gone is logged at the phase end: no
    sighting since the start, the link up), its probe, then the quiet phase starts at ``q``."""
    t = T + 100
    p = t + 5 * 60000
    q = p + WARM + 200
    script = [
        *PAIRED,
        *READ_ALL,
        CLOCK,
        "diaghook",
        "> ble exp 5 00-00",
        "tick %d" % t,
        *_probe_done(p),
        "tick %d" % q,
    ]
    return script, t, p, q


def _adv_evts(out):
    return [e for e in _diag(out) if e["ev"] == "evt" and e["kind"] in ("unit_adv", "ble_phase", "ble_mode")]


def test_unit_advert_reappearing_after_a_link_down_gone_aborts_quiet(fake):
    """The unit's advert (UNIT_ADV) in quiet: the unit drops the link and is never reconnected; the
    first sighting after that is logged only (the gone before it was logged in hold, link up); a gone
    logged with the link down, then a sighting = the unit woke — activity, the phase ends aborted and
    the mode returns to hold. ``ble`` shows when the advert was last seen.

    .. test:: the unit's advert reappearing in quiet after a link-down gone wakes the experiment
       :id: T_FW_BLE_EXP_ADV_WAKE
       :links: R_FW_BLE_EXP
    """
    script, t, p, q = _to_quiet()
    out = run(
        fake,
        *script,
        "DISCONNECTED",  # the unit drops the quiet link; the reconnect never completes
        "UNIT_ADV",  # seen: logged, no wake (the gone was logged in hold)
        "tick %d" % (q + 100),
        "> ble",
        "tick %d" % (q + 100 + GONE),  # gone, link down: armed
        "tick %d" % (q + 200 + GONE),
        "UNIT_ADV",  # reappears: the unit woke
        "tick %d" % (q + 300 + GONE),
    )
    assert _adv_evts(out) == [
        {"ev": "evt", "kind": "unit_adv", "seen": False},
        {"ev": "evt", "kind": "ble_phase", "mode": "hold", "aborted": False, "dur_ms": p + WARM + 100 - t},
        {"ev": "evt", "kind": "ble_mode", "mode": "quiet", "why": "exp"},
        {"ev": "evt", "kind": "unit_adv", "seen": True},
        {"ev": "evt", "kind": "unit_adv", "seen": False},
        {"ev": "evt", "kind": "unit_adv", "seen": True},
        {"ev": "evt", "kind": "ble_phase", "mode": "quiet", "aborted": True, "dur_ms": GONE + 300},
        {"ev": "evt", "kind": "ble_mode", "mode": "hold", "why": "exp_abort"},
    ]
    status = next(ln for ln in out if ln.startswith("LOG ble: "))
    assert '"unit_adv_seen_ms_ago":100' in status


def test_unit_advert_after_a_link_up_gone_is_no_wake(fake):
    """In quiet with the link held, a gone is the held link's silence, not sleep: the next sighting
    (the unit dropping the link) is logged and the phase runs on.

    .. test:: a gone logged with the link up arms no wake
       :id: T_FW_BLE_EXP_ADV_HELD
       :links: R_FW_BLE_EXP
    """
    script, _, _, q = _to_quiet()
    out = run(
        fake,
        *script,
        "UNIT_ADV",
        "tick %d" % (q + 100),
        "tick %d" % (q + 100 + GONE),  # gone, link up: not armed
        "UNIT_ADV",
        "tick %d" % (q + 200 + GONE),
        "> ble",
    )
    ev = _adv_evts(out)
    assert [e["seen"] for e in ev if e["kind"] == "unit_adv"] == [False, True, False, True]
    assert not [e for e in ev if e["kind"] == "ble_phase" and e["mode"] == "quiet"]
    assert '"phase":"quiet"' in out[-1]


def test_unit_advert_is_not_logged_without_the_experiment(fake):
    """No experiment: the unit's advert logs no evt (``ble`` still shows the last sighting).

    .. test:: the unit's advert outside the experiment: no evt
       :id: T_FW_BLE_EXP_ADV_OFF
       :links: R_FW_BLE_EXP
    """
    out = run(fake, "diaghook", "tick 1000", "UNIT_ADV", "tick 400000", "> ble")
    assert not [e for e in _diag(out) if e.get("kind") == "unit_adv"]
    assert '"unit_adv_seen_ms_ago":399000' in out[-1]


def test_ignition_off_in_quiet_keeps_the_phase(fake):
    """Ignition 0 in the quiet phase changes nothing: the phase runs on.

    .. test:: no ignition, no interrupt
       :id: T_FW_BLE_EXP_IGNITION_OFF
       :links: R_FW_BLE_EXP
    """
    t = T + 100
    p = t + 60000
    q = p + WARM + 200
    out = run(
        fake,
        *PAIRED,
        *READ_ALL,
        CLOCK,
        "diaghook",
        "> ble exp 1 00-00",
        "tick %d" % t,
        *_probe_done(p),
        "tick %d" % q,
        "ign 0",
        "tick %d" % (q + 100),
        "> ble",
    )
    assert '"phase":"quiet"' in out[-1]


def test_a_refused_phase_probe_is_logged(fake):
    """No bond: the phase-end probe is refused — LOG + an SD ``probe`` event with reason ``refused``.

    .. test:: a refused experiment probe reaches the SD log
       :id: T_FW_BLE_EXP_PROBE_REFUSED
       :links: R_FW_BLE_EXP
    """
    out = run(fake, CLOCK, "diaghook", "> ble exp 1 00-00", "tick 1000", "tick 61000")
    assert "LOG ble-exp: probe refused" in out
    assert {"ev": "evt", "kind": "probe", "ok": False, "mode": "hold", "reason": "refused"} in _diag(out)


def test_stop_or_restart_mid_phase_logs_the_phase_aborted(fake):
    """``ble exp off`` or a new ``ble exp`` while a phase runs logs that phase (aborted, its span) first.

    .. test:: a stopped or restarted experiment keeps its running phase in the log
       :id: T_FW_BLE_EXP_STOP_PHASE
       :links: R_FW_BLE_EXP
    """
    out = run(
        fake,
        CLOCK,
        "diaghook",
        "> ble exp 5 00-00",
        "tick 1000",  # hold starts
        "tick 4000",
        "> ble exp 2 00-00",  # restart mid-phase
        "tick 5000",  # hold starts again
        "tick 9000",
        "> ble exp off",
    )
    ev = [e for e in _diag(out) if e["ev"] == "evt" and e["kind"] in ("ble_phase", "ble_exp")]
    assert ev == [
        {"ev": "evt", "kind": "ble_exp", "on": True, "phase_min": 5, "from": 0, "to": 0},
        {"ev": "evt", "kind": "ble_phase", "mode": "hold", "aborted": True, "dur_ms": 3000},
        {"ev": "evt", "kind": "ble_exp", "on": True, "phase_min": 2, "from": 0, "to": 0},
        {"ev": "evt", "kind": "ble_phase", "mode": "hold", "aborted": True, "dur_ms": 4000},
        {"ev": "evt", "kind": "ble_exp", "on": False},
    ]


def test_mode_change_mid_command_keeps_the_heartbeat(fake):
    """A lighting command is a frame + a commit; ``quiet`` set between them must not stop the 1003
    heartbeat before the commit (the sequencer checks ready only at submit).

    .. test:: a control command in flight keeps its heartbeat through a mode change
       :id: T_FW_BLE_QUIET_MID_COMMAND
       :links: R_FW_BLE_MODE
    """
    t = ARM + 100
    out = run(
        fake,
        *armed(),
        "> set lighting kitchen 5",
        "tick %d" % t,
        "WRITTEN 1501 0",
        "> ble mode quiet",
        *["tick %d" % u for u in range(t + TICK, t + FOLLOW + 2 * TICK, TICK)],
        "WRITTEN 1501 0",
    )
    w = writes(out)
    assert w == want_writes("lighting", "kitchen", "5")
    between = out[out.index(w[0]) : out.index(w[1])]
    assert calls(between, "write_heartbeat")
