"""Roof move-heartbeat safety tests (device.actuate_roof), with a stubbed `bleak`.

SAFETY-SENSITIVE: the pop-top roof is driven by a bounded 1 Hz move-heartbeat that MUST end
in a STOP frame. These lock in that a STOP is *attempted* on both exit paths:
  * normal end-of-travel (the max_duration_s deadline elapses), and
  * a mid-move link drop (the move write raises; the STOP write is still attempted,
    then swallowed -- the real stop in that case is the hardware halting when the
    heartbeat ceases, which is UNVERIFIED, so the software STOP attempt is all we assert).
Move frames carry byte0 == 0x01 (Up); the STOP frame carries byte0 == 0x00 -- that byte
distinguishes a STOP write from a move write (bytes 1-4 are the live SafetyCounter).
"""
import asyncio
import sys
import types

import pytest

from calictl import device, protocol, overrides, control


class _Char:
    def __init__(self, uuid, props):
        self.uuid, self.properties = uuid, props


class _Svc:
    def __init__(self, chars):
        self.characteristics = chars


class _RoofClient:
    """Records every write (even failed ones). `drop_after` control writes -> the link drops:
    the write raises and is_connected flips False (mirrors the van's mid-move disconnect)."""
    instances = []
    drop_after = None            # None = never drop (normal end-of-travel)
    ctrl = None                  # roof control-char uuid (set by the fixture)

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
        return bytes(16)         # any decodable payload for the readback

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


def test_actuate_roof_sends_stop_on_normal_end(roof):
    """After the bounded move-heartbeat, a STOP frame (byte0==0x00) is written.

    .. test:: STOP is sent at the normal end of a roof move
       :id: T_ROOF_STOP_NORMAL
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    move = control.roof_frame(roof, "open")     # byte0 = 0x01
    stop = control.roof_frame(roof, "stop")     # byte0 = 0x00
    asyncio.run(device.CamperDevice("AA:BB:CC:DD:EE:FF").actuate_roof(
        roof["roof"], move, stop, max_duration_s=0.02, period_s=0.001, verify=False))
    ctrl = _ctrl_writes(_RoofClient.instances[-1], roof)
    assert ctrl, "no control writes at all"
    assert ctrl[-1][0] == 0x00, "last control write must be the STOP frame (byte0==0)"
    assert any(w[0] == 0x01 for w in ctrl[:-1]), "expected at least one move frame before STOP"


def test_actuate_roof_attempts_stop_after_link_drop(roof):
    """SAFETY: on a mid-move link drop the STOP write is still *attempted* (then swallowed);
    actuate_roof must not propagate the disconnect.

    .. test:: STOP is attempted even after a mid-move link drop
       :id: T_ROOF_STOP_LINKDROP
       :links: R_ROOF_ACTUATE
       :status: passing
    """
    _RoofClient.drop_after = 2                   # link drops on the 2nd control (move) write
    move = control.roof_frame(roof, "open")
    stop = control.roof_frame(roof, "stop")
    # must NOT raise despite the link dropping mid-move
    asyncio.run(device.CamperDevice().actuate_roof(
        roof["roof"], move, stop, max_duration_s=5.0, period_s=0.001, verify=False))
    c = _RoofClient.instances[-1]
    ctrl = _ctrl_writes(c, roof)
    # the loop broke early on the drop (nowhere near a 5 s deadline of ~1 ms ticks)
    assert len(ctrl) <= 4
    # a STOP frame (byte0==0x00) was attempted as the final control write
    assert ctrl[-1][0] == 0x00, "STOP must be attempted after a link drop"
