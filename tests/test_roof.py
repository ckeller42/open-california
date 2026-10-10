"""Roof safety tests (device.actuate_roof), with a stubbed `bleak`.

SAFETY-SENSITIVE + NOT-LIVE-VERIFIED (roof not installed on this van; asserted against the
mock only). Model settled by the app-engine decompile (`w8/a`, 2026-07-13): the app streams
the OPEN/CLOSE move frame IMMEDIATELY until STOP -- there is NO STOP-hold / confirmation phase
(the STOP frames seen in a capture were the press-and-hold dead-man, not protocol). The
SafetyCounter (frame bytes 1-4, 32-bit BE) is APP-GENERATED and purely time-derived: a random
seed + 1 per ~500 ms of wall-clock; it does NOT echo the unit's counter. The unit self-gates
motor actuation until the counter validates (SafetyCounterValid, char 1402 bit 7), within a
~3 s dead-man.

These tests lock in:
  * the time-derived counter formula (`_roof_safety_counter`): +1 per 500 ms, wraps at 2^32;
  * the move stream: move frames (byte0==0x01) then a final STOP (byte0==0x00), counter
    non-decreasing and seeded (random-in-range by default, or an explicit counter_seed);
  * a STOP is *attempted* on every exit path -- normal end AND a mid-move link drop (the write
    raises; the STOP write is still attempted, then swallowed -- the real stop there is the
    hardware dead-man halting when frames cease, UNVERIFIED, so the STOP attempt is all we assert);
  * the dead-man validation: if SafetyCounterValid is still false after validate_s the move is
    aborted (-> STOP), and it continues when the bit is set.

Move frames carry byte0 == 0x01 (Up); the STOP frame carries byte0 == 0x00 -- that byte
distinguishes a STOP write from a move write (bytes 1-4 are the live SafetyCounter).
"""

import asyncio
import sys
import types

import pytest

from calictl import control, device, overrides, protocol

# roof state payloads for the readback / validation read (char 1402):
_STATE_INVALID = bytes(16)  # SafetyCounterValid bit clear
_STATE_VALID = bytes([0x01, 0x00])  # SafetyCounterValid bit set (offset 7, MSB-first)


class _Char:
    def __init__(self, uuid, props):
        self.uuid, self.properties = uuid, props


class _Svc:
    def __init__(self, chars):
        self.characteristics = chars


class _RoofClient:
    """Records every write (even failed ones). `drop_after` control writes -> the link drops:
    the write raises and is_connected flips False (mirrors the van's mid-move disconnect).
    `read_payload` is what every read_gatt_char returns (set the roof-state validity bit)."""

    instances = []
    drop_after = None  # None = never drop (normal end-of-travel)
    ctrl = None  # roof control-char uuid (set by the fixture)
    read_payload = _STATE_INVALID

    def __init__(self, addr, timeout=None, adapter=None):
        self.addr = addr
        self.is_connected = False
        self.writes = []  # (uuid, data) for EVERY attempt, including the one that raised
        self._ctrl_writes = 0
        _RoofClient.instances.append(self)

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    @property
    def services(self):
        return [
            _Svc(
                [
                    _Char(device._aux_uuid("1402"), ["read", "notify"]),
                    _Char(device._aux_uuid("1401"), ["write"]),
                ]
            )
        ]

    async def read_gatt_char(self, uuid):
        return _RoofClient.read_payload

    async def write_gatt_char(self, uuid, data, response=None):
        self.writes.append((str(uuid), bytes(data)))
        if str(uuid) == _RoofClient.ctrl:
            self._ctrl_writes += 1
            if _RoofClient.drop_after is not None and self._ctrl_writes >= _RoofClient.drop_after:
                self.is_connected = False
                raise RuntimeError("le-connection-abort-by-local")

    async def start_notify(self, uuid, cb):
        pass


@pytest.fixture
def roof(monkeypatch):
    funcs = protocol.load()
    overrides.apply(funcs)
    _RoofClient.instances = []
    _RoofClient.drop_after = None
    _RoofClient.read_payload = _STATE_INVALID
    _RoofClient.ctrl = funcs["roof"].control_char
    mod = types.ModuleType("bleak")
    mod.BleakClient = _RoofClient
    monkeypatch.setitem(sys.modules, "bleak", mod)
    real_sleep = asyncio.sleep

    async def _fast(*_a, **_k):
        await real_sleep(0)

    monkeypatch.setattr(device.asyncio, "sleep", _fast)
    return funcs


def _ctrl_writes(client, funcs):
    return [d for u, d in client.writes if u == funcs["roof"].control_char]


def _counters(ctrl):
    """The 32-bit BE SafetyCounter (frame bytes 1-4) from each control write."""
    return [int.from_bytes(d[1:5], "big") for d in ctrl]


def test_roof_safety_counter_is_time_derived_plus_one_per_tick():
    """The app's SafetyCounter is ``seed + floor(elapsed_ms/500)`` (mod 2^32).

    .. test:: Roof SafetyCounter increments +1 per 500 ms of wall-clock and wraps at 2^32
       :id: T_ROOF_COUNTER_FORMULA
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    seed = 1000
    assert device._roof_safety_counter(seed, 0) == seed
    assert device._roof_safety_counter(seed, 499) == seed  # still within tick 0
    assert device._roof_safety_counter(seed, 500) == seed + 1
    assert device._roof_safety_counter(seed, 1200) == seed + 2
    # wraps as a 32-bit unsigned int
    assert device._roof_safety_counter(0xFFFFFFFF, 500) == 0


def test_actuate_roof_streams_move_then_stop(roof):
    """The move streams OPEN frames (byte0==0x01) and ends with a STOP frame (byte0==0x00).

    .. test:: Roof drive streams move frames then a final STOP
       :id: T_ROOF_STOP_NORMAL
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    move = control.roof_frame(roof, "open")  # byte0 = 0x01
    stop = control.roof_frame(roof, "stop")  # byte0 = 0x00
    asyncio.run(
        device.CamperDevice("11:22:33:44:55:66").actuate_roof(
            roof["roof"],
            move,
            stop,
            max_duration_s=0.02,
            period_s=0.001,
            validate_s=None,
            counter_seed=1000,
            verify=False,
        )
    )
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    b0 = [d[0] for d in ctrl]
    assert ctrl, "no control writes at all"
    assert b0[0] == 0x01, "the move stream must start immediately with an OPEN frame (no STOP-hold)"
    assert b0[-1] == 0x00, "last control write must be the STOP frame (byte0==0)"
    assert all(v == 0x01 for v in b0[:-1]), "every pre-STOP frame must be a move (no STOP-hold phase)"
    ctr = _counters(ctrl)
    assert ctr[0] == 1000, "first frame must carry the seed"
    assert all(b >= a for a, b in zip(ctr, ctr[1:])), "counter must be monotonic non-decreasing"


def test_actuate_roof_default_seed_is_random_in_range(roof):
    """With no counter_seed the SafetyCounter seeds from a random int in [1, ROOF_SAFETY_SEED_MAX].

    .. test:: Roof SafetyCounter defaults to a random app-style seed in range
       :id: T_ROOF_COUNTER_SEED
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    asyncio.run(
        device.CamperDevice("11:22:33:44:55:66").actuate_roof(
            roof["roof"], move, stop, max_duration_s=0.02, period_s=0.001, validate_s=None, verify=False
        )
    )
    ctr = _counters(_ctrl_writes(_RoofClient.instances[-1], roof))
    assert 1 <= ctr[0] <= device.ROOF_SAFETY_SEED_MAX, "seed must be a random int in [1, MAX]"


def test_actuate_roof_counter_seed_override(roof):
    """A caller-supplied counter_seed overrides the random seed (deterministic for tests).

    .. test:: Roof SafetyCounter honours an explicit counter_seed override
       :id: T_ROOF_COUNTER_OVERRIDE
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    asyncio.run(
        device.CamperDevice("11:22:33:44:55:66").actuate_roof(
            roof["roof"],
            move,
            stop,
            max_duration_s=0.02,
            period_s=0.001,
            validate_s=None,
            counter_seed=0x4242,
            verify=False,
        )
    )
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    assert _counters(ctrl)[0] == 0x4242, "explicit counter_seed must seed the first frame"


def test_actuate_roof_attempts_stop_after_linkdrop_mid_move(roof):
    """SAFETY: a link drop during the move stream still attempts a final STOP; no raise.

    .. test:: STOP is attempted after a mid-move link drop
       :id: T_ROOF_STOP_LINKDROP
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    _RoofClient.drop_after = 2  # link drops on the 2nd move write
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    # must NOT raise despite the link dropping mid-move
    asyncio.run(
        device.CamperDevice("11:22:33:44:55:66").actuate_roof(
            roof["roof"], move, stop, max_duration_s=5.0, period_s=0.001, validate_s=None, verify=False
        )
    )
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    # the loop broke early on the drop (nowhere near a 5 s deadline of ~1 ms ticks)
    assert len(ctrl) <= 3
    assert ctrl[-1][0] == 0x00, "STOP must be attempted after a link drop"


def test_actuate_roof_aborts_when_safetycounter_invalid(roof):
    """SAFETY: if SafetyCounterValid is still false after validate_s the move aborts (-> STOP).

    .. test:: Roof move aborts to STOP when the SafetyCounter never validates
       :id: T_ROOF_COUNTER_INVALID
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    _RoofClient.read_payload = _STATE_INVALID  # SafetyCounterValid stays 0
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    asyncio.run(
        device.CamperDevice("11:22:33:44:55:66").actuate_roof(
            roof["roof"], move, stop, max_duration_s=5.0, period_s=0.001, validate_s=0.0, verify=False
        )
    )  # check immediately -> invalid -> abort
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    b0 = [d[0] for d in ctrl]
    # aborted right after the first move + validity check, nowhere near the 5 s cap
    assert len(ctrl) <= 3
    assert any(v == 0x01 for v in b0), "at least one move frame is sent before the validity check"
    assert b0[-1] == 0x00, "an invalid SafetyCounter must still end in a STOP"


def test_actuate_roof_continues_when_safetycounter_valid(roof):
    """When SafetyCounterValid is set the move streams past the validity check to the cap.

    .. test:: Roof move continues past validation when the SafetyCounter is valid
       :id: T_ROOF_COUNTER_VALID
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    _RoofClient.read_payload = _STATE_VALID  # SafetyCounterValid == 1
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    asyncio.run(
        device.CamperDevice("11:22:33:44:55:66").actuate_roof(
            roof["roof"], move, stop, max_duration_s=0.02, period_s=0.001, validate_s=0.0, verify=False
        )
    )  # check immediately -> valid -> keep streaming
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    b0 = [d[0] for d in ctrl]
    # ran to the max-duration cap (many frames), not aborted after the first check
    assert len(ctrl) > 3, "a valid SafetyCounter must not abort the move"
    assert b0[-1] == 0x00, "the move still ends in a STOP"


def test_actuate_roof_ticks_the_1003_heartbeat_with_no_prearm_gap(roof, monkeypatch):
    """The roof move runs with the 1003 liveness heartbeat ticking, and the SafetyCounter still
    streams immediately — no ``ARM_DELAY_S`` pre-arm, no warm-up sleep before the first frame.

    calictl follows the app (owner decision 2026-10-05): the app's 1003 heartbeat is session-global
    and keeps ticking during a roof move (decompile ``zf/d:183 -> d2/s:795-802 -> mj/d:247 ->
    c/i:349-367``, #235). The pre-#235 "no heartbeat during a roof move" contract is gone; the
    "no pre-arm gap" half of #150 stands (a gap would restart the unit's ~3 s counter withhold).

    .. test:: A roof move ticks the 1003 heartbeat and streams the counter with no pre-arm gap
       :id: T_ROOF_HEARTBEAT_NO_GAP
       :links: R_ROOF_ACTUATE
    """
    slept = []
    real_sleep = asyncio.sleep

    async def _rec(s=0, *_a, **_k):
        slept.append(s)
        await real_sleep(0)

    monkeypatch.setattr(device.asyncio, "sleep", _rec)
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    asyncio.run(
        device.CamperDevice("11:22:33:44:55:66").actuate_roof(
            roof["roof"],
            move,
            stop,
            max_duration_s=device.HEARTBEAT_PERIOD_S * 2.5,  # long enough for >= 2 beats
            period_s=0.001,
            validate_s=None,
            counter_seed=1000,
            verify=False,
        )
    )
    writes = _RoofClient.instances[-1].writes
    hb = str(device.HEARTBEAT_CHAR)
    beats = [i for i, (u, _d) in enumerate(writes) if u == hb]
    ctrl = [i for i, (u, _d) in enumerate(writes) if u == roof["roof"].control_char]
    assert len(beats) >= 2, "the 1003 heartbeat must tick during the roof move"
    assert ctrl[0] < beats[-1] < ctrl[-1], "beats must interleave with the move frames"
    beat_ctrs = [int.from_bytes(writes[i][1], "big") for i in beats]
    assert beat_ctrs == list(range(beat_ctrs[0], beat_ctrs[0] + len(beat_ctrs))), "heartbeat is +1"
    # no pre-arm gap: the only sleeps are the frame period (no ARM_DELAY_S, no warm-up)
    assert device.ARM_DELAY_S not in slept and device.HEARTBEAT_WARMUP_S not in slept
    assert set(slept) <= {0.001}
    assert _counters([writes[i][1] for i in ctrl])[0] == 1000, "first frame carries the seed (t=0)"


def test_actuate_roof_sends_stop_when_cancelled_mid_move(roof):
    """A move cancelled mid-stream (daemon shutdown) still attempts a best-effort STOP instead of
    relying on the unit's unverified dead-man (review minor 3 on #238).

    .. test:: A cancelled roof move still attempts a STOP
       :id: T_ROOF_STOP_ON_CANCEL
       :links: R_ROOF_ACTUATE
    """
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")

    async def _run():
        task = asyncio.ensure_future(
            device.CamperDevice("11:22:33:44:55:66").actuate_roof(
                roof["roof"], move, stop, max_duration_s=30.0, period_s=0.001, validate_s=None, verify=False
            )
        )
        while not _RoofClient.instances or len(_ctrl_writes(_RoofClient.instances[-1], roof)) < 3:
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    assert ctrl[-1][0] == 0x00, "a cancelled move must still attempt STOP"


def test_roof_stream_ticks_plus_one_and_a_direction_change_repeats_the_counter(roof):
    """The app's roof-screen stream (CAPTURE 2026-10-10): one frame per tick, counter +1 per tick; a
    direction change goes out AT ONCE with the CURRENT counter — the first move frame repeats the
    last STOP frame's counter and the first STOP after it the last move frame's (26 of 26 changes on
    the wire). ``set`` with an unchanged direction writes nothing; ``close`` stops the ticker.

    .. test:: Roof stream: +1 per tick, a direction change repeats the current counter
       :id: T_ROOF_STREAM_COUNTER
       :links: R_ROOF_VIEW_STREAM
    """
    client = _RoofClient("11:22:33:44:55:66")
    client.is_connected = True
    stop = control.roof_frame(roof, "stop")
    move = control.roof_frame(roof, "open")

    async def _wait(s):
        try:
            await asyncio.wait_for(asyncio.Event().wait(), s)
        except TimeoutError:
            pass

    async def _run():
        st = device.RoofStream(client, roof["roof"], stop, seed=500, period_s=0.02)
        await st.set(stop)
        await _wait(0.07)
        await st.set(move)
        await st.set(move)  # unchanged: no extra frame
        await _wait(0.05)
        await st.set(stop)
        await _wait(0.05)
        await st.close()
        n = len(client.writes)
        await _wait(0.05)
        return n

    n_closed = asyncio.run(_run())
    ctrl = _ctrl_writes(client, roof)
    assert len(client.writes) == n_closed, "no frame after close"
    dirs, ctrs = [d[0] for d in ctrl], _counters(ctrl)
    assert ctrs[0] == 500 and dirs[0] == 0x00
    for i in range(1, len(ctrl)):
        if dirs[i] != dirs[i - 1]:
            assert ctrs[i] == ctrs[i - 1], "a direction change repeats the current counter"
        else:
            assert ctrs[i] == ctrs[i - 1] + 1, "a tick is +1"
    assert dirs.count(0x01) >= 2 and dirs[-1] == 0x00
    assert [k for k in range(1, len(dirs)) if dirs[k] != dirs[k - 1]] == [
        dirs.index(0x01),
        len(dirs) - 1 - dirs[::-1].index(0x01) + 1,
    ], "exactly one switch to the move byte and one back"
