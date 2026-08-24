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
_STATE_INVALID = bytes(16)            # SafetyCounterValid bit clear
_STATE_VALID = bytes([0x01, 0x00])   # SafetyCounterValid bit set (offset 7, MSB-first)


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
    drop_after = None            # None = never drop (normal end-of-travel)
    ctrl = None                  # roof control-char uuid (set by the fixture)
    read_payload = _STATE_INVALID

    def __init__(self, addr, timeout=None):
        self.addr = addr
        self.is_connected = False
        self.writes = []         # (uuid, data) for EVERY attempt, including the one that raised
        self._ctrl_writes = 0
        _RoofClient.instances.append(self)

    async def connect(self):
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    @property
    def services(self):
        return [_Svc([_Char(device._aux_uuid("1402"), ["read", "notify"]),
                      _Char(device._aux_uuid("1401"), ["write"])])]

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
    funcs = protocol.load(); overrides.apply(funcs)
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
    assert device._roof_safety_counter(seed, 499) == seed          # still within tick 0
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
    move = control.roof_frame(roof, "open")     # byte0 = 0x01
    stop = control.roof_frame(roof, "stop")     # byte0 = 0x00
    asyncio.run(device.CamperDevice("AA:BB:CC:DD:EE:FF").actuate_roof(
        roof["roof"], move, stop, max_duration_s=0.02, period_s=0.001,
        validate_s=None, counter_seed=1000, verify=False))
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
    asyncio.run(device.CamperDevice().actuate_roof(
        roof["roof"], move, stop, max_duration_s=0.02, period_s=0.001,
        validate_s=None, verify=False))
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
    asyncio.run(device.CamperDevice().actuate_roof(
        roof["roof"], move, stop, max_duration_s=0.02, period_s=0.001,
        validate_s=None, counter_seed=0x4242, verify=False))
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    assert _counters(ctrl)[0] == 0x4242, "explicit counter_seed must seed the first frame"


def test_actuate_roof_attempts_stop_after_linkdrop_mid_move(roof):
    """SAFETY: a link drop during the move stream still attempts a final STOP; no raise.

    .. test:: STOP is attempted after a mid-move link drop
       :id: T_ROOF_STOP_LINKDROP
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    _RoofClient.drop_after = 2                   # link drops on the 2nd move write
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    # must NOT raise despite the link dropping mid-move
    asyncio.run(device.CamperDevice().actuate_roof(
        roof["roof"], move, stop, max_duration_s=5.0, period_s=0.001,
        validate_s=None, verify=False))
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
    _RoofClient.read_payload = _STATE_INVALID    # SafetyCounterValid stays 0
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    asyncio.run(device.CamperDevice().actuate_roof(
        roof["roof"], move, stop, max_duration_s=5.0, period_s=0.001,
        validate_s=0.0, verify=False))           # check immediately -> invalid -> abort
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
    _RoofClient.read_payload = _STATE_VALID      # SafetyCounterValid == 1
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    asyncio.run(device.CamperDevice().actuate_roof(
        roof["roof"], move, stop, max_duration_s=0.02, period_s=0.001,
        validate_s=0.0, verify=False))           # check immediately -> valid -> keep streaming
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    b0 = [d[0] for d in ctrl]
    # ran to the max-duration cap (many frames), not aborted after the first check
    assert len(ctrl) > 3, "a valid SafetyCounter must not abort the move"
    assert b0[-1] == 0x00, "the move still ends in a STOP"
