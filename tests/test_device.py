"""device.py tests with a stubbed `bleak` — locks in the actuate arming protocol
(handshake reads -> subscribe-all -> 1003 heartbeat -> control write -> readback) and
the _session retry -> ConnectionUnavailable path, with no real BLE."""
import asyncio
import sys
import types

import pytest

from calictl import device, protocol, overrides


class _Char:
    def __init__(self, uuid, props):
        self.uuid, self.properties = uuid, props


class _Svc:
    def __init__(self, chars):
        self.characteristics = chars


class _FakeClient:
    """Records every GATT call in order; connect() can be told to fail N times first."""
    instances = []

    def __init__(self, addr, timeout=None):
        self.addr = addr
        self.is_connected = False
        self.calls = []                 # (op, uuid, data)
        self.notified = []
        _FakeClient.instances.append(self)

    async def connect(self):
        if _FakeClient.fail_connect > 0:
            _FakeClient.fail_connect -= 1
            raise RuntimeError("le-connection-abort-by-local")
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    @property
    def services(self):
        # two notifiable status chars + one write-only control char
        return [_Svc([_Char(device._aux_uuid("1102"), ["read", "notify"]),
                      _Char(device._aux_uuid("1101"), ["write"]),
                      _Char(device._aux_uuid("1202"), ["read", "notify"])])]

    async def read_gatt_char(self, uuid):
        self.calls.append(("read", str(uuid), None))
        return bytes(6)                 # any decodable payload

    async def write_gatt_char(self, uuid, data, response=None):
        self.calls.append(("write", str(uuid), bytes(data)))

    async def start_notify(self, uuid, cb):
        self.notified.append(str(uuid))


@pytest.fixture
def fake_bleak(monkeypatch):
    _FakeClient.instances = []
    _FakeClient.fail_connect = 0
    mod = types.ModuleType("bleak")
    mod.BleakClient = _FakeClient
    monkeypatch.setitem(sys.modules, "bleak", mod)
    real_sleep = asyncio.sleep
    async def _fast(*_a, **_k):        # keep sleeps instant but still yield to the loop
        await real_sleep(0)
    monkeypatch.setattr(device.asyncio, "sleep", _fast)
    return _FakeClient


def _cooler():
    f = protocol.load(); overrides.apply(f); return f["cooler"]


def test_actuate_arms_then_writes(fake_bleak):
    f = _cooler()
    frame = bytes.fromhex("3d4300000000")
    post = asyncio.run(device.CamperDevice("AA:BB:CC:DD:EE:FF").actuate(f, frame, verify=True))
    c = fake_bleak.instances[-1]
    reads = [u for op, u, _ in c.calls if op == "read"]
    writes = [(u, d) for op, u, d in c.calls if op == "write"]
    # handshake: version + auth chars were read
    assert device.VERSION_CHAR in reads and device.AUTH_CHAR in reads
    # subscribed to the two notifiable chars only
    assert c.notified == [device._aux_uuid("1102"), device._aux_uuid("1202")]
    # heartbeat: >=1 write to 1003, first value is the BE start counter
    hb = [d for u, d in writes if u == device.HEARTBEAT_CHAR]
    assert hb and hb[0] == device.HEARTBEAT_START.to_bytes(4, "big")
    # the control frame was written to the cooler control char
    assert (f.control_char, frame) in writes
    # verify -> read the state char back and returned a decoded dict
    assert f.state_char in reads and isinstance(post, dict)


def test_actuate_no_verify_returns_none(fake_bleak):
    f = _cooler()
    post = asyncio.run(device.CamperDevice().actuate(f, bytes(6), verify=False))
    assert post is None


def test_session_retries_then_raises(fake_bleak):
    fake_bleak.fail_connect = 99          # every connect attempt fails
    with pytest.raises(device.ConnectionUnavailable):
        asyncio.run(device.CamperDevice().read(_cooler()))


def test_read_all_skips_and_returns_bytes(fake_bleak):
    funcs = protocol.load(); overrides.apply(funcs)
    out = asyncio.run(device.CamperDevice().read_all(funcs))
    # every function with a state_char is present as bytes
    assert out and all(isinstance(v, bytes) for v in out.values())
    assert "cooler" in out


def test_serve_poll_caches_and_interprets(fake_bleak):
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    async def _run():
        s._ble = asyncio.Lock()             # normally created inside run()'s loop
        return await s.poll()
    states = asyncio.run(_run())
    assert "cooler" in states and states["cooler"]     # interpreted
    assert "cooler" in s._last                          # DECODED state cached for on_command


def test_on_command_reads_state_when_cache_cold(fake_bleak):
    # H1: a cold cache must NOT build a frame from defaults (would force cooler ON);
    # on_command reads the live state first.
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._read_only = False                            # writes enabled for this actuation test
    async def _run():
        s._ble = asyncio.Lock()
        await s.on_command("cooler", "power", "off")
    assert "cooler" not in s._last
    asyncio.run(_run())
    assert "cooler" in s._last               # it did a fresh state read before actuating


def test_actuate_on_arm_false_skips_handshake_and_arm_delay(monkeypatch):
    """The persistent fast path: given a live heartbeat, a write must NOT replay the
    handshake or wait ARM_DELAY_S -- that is the entire latency win."""
    from calictl import control
    from tools.mock_unit import MockCamperUnit, MockBleakClient
    funcs = protocol.load(); overrides.apply(funcs)
    unit = MockCamperUnit(); unit.armed = True            # heartbeat already ticking
    client = MockBleakClient.bind(unit)("MO:CK", timeout=1)

    slept = []
    real_sleep = asyncio.sleep
    async def fake_sleep(s, *a, **k):
        slept.append(s)
        await real_sleep(0)
    monkeypatch.setattr(device.asyncio, "sleep", fake_sleep)

    async def _run():
        await client.connect()
        dev = device.CamperDevice("MO:CK")
        frame = control.build(funcs, "cooler", "power", "on", {"State": 0, "Level": 3, "Mode": 4})
        return await dev._actuate_on(client, funcs["cooler"], frame, verify=True, arm=False)

    post = asyncio.run(_run())
    assert device.ARM_DELAY_S not in slept          # no 3s arm wait
    assert post is not None and post.get("State") == 1   # write applied
    assert unit.decoded("cooler")["State"] == 1
