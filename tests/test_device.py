"""device.py tests with a stubbed `bleak` — locks in the actuate arming protocol
(handshake reads -> subscribe-all -> 1003 heartbeat -> control write -> readback) and
the _session retry -> ConnectionUnavailable path, with no real BLE."""
import asyncio
import sys
import types

import pytest

from calictl import device, overrides, protocol


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
    """Actuation runs the shared arm prologue (handshake + heartbeat) before the control write.

    .. test:: Actuation runs the shared arm prologue (handshake + heartbeat) before writing
       :id: T_ACTUATE_ARM
       :links: R_ACTUATE_ARM
    """
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
    from tools.mock_unit import MockBleakClient, MockCamperUnit
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


def test_actuate_roof_stops_when_event_set(fake_bleak):
    import asyncio as _a

    from calictl import control, overrides, protocol
    funcs = protocol.load(); overrides.apply(funcs)
    f = funcs["roof"]
    move = control.roof_frame(funcs, "open"); stop = control.roof_frame(funcs, "stop")
    ev = _a.Event(); ev.set()                       # already-set -> loop must not stream, just STOP
    async def _run():
        dev = device.CamperDevice("AA:BB:CC:DD:EE:FF")
        await dev.actuate_roof(f, move, stop, verify=False, validate_s=None, stop_event=ev)
        return fake_bleak.instances[-1]
    cli = asyncio.run(_run())
    # byte 0 is the direction (bytes 1-4 are the live counter); with the event pre-set the move
    # loop never streams an open frame (dir 0x01) — only the STOP frame (dir 0x00) is written.
    dirs = [d[0] for op, u, d in cli.calls if op == "write" and u == f.control_char]
    assert 0x01 not in dirs and 0x00 in dirs


def test_actuate_roof_stops_at_limit_position(fake_bleak, monkeypatch):
    """A move ceases (-> STOP) as soon as the roof Position reaches the direction's limit, rather
    than running to the travel cap.

    .. test:: Roof travel auto-stops at the limit position
       :id: T_ROOF_LIMIT_STOP
       :links: R_ROOF_ACTUATE
    """
    from calictl import control, overrides, protocol
    funcs = protocol.load(); overrides.apply(funcs)
    f = funcs["roof"]
    move = control.roof_frame(funcs, "open"); stop = control.roof_frame(funcs, "stop")
    # Position is the 4-bit field at offset 0 (MSB-first) -> high nibble. 0x10 -> Position 1 = "open",
    # the terminal for an "open" move. State-char reads report already-at-limit; other reads = zeros.
    open_payload = bytes([0x10]) + bytes(15)
    async def _read(self, uuid):
        self.calls.append(("read", str(uuid), None))
        return open_payload if str(uuid) == f.state_char else bytes(6)
    monkeypatch.setattr(fake_bleak, "read_gatt_char", _read)
    monkeypatch.setattr(device, "ROOF_LIMIT_POLL_S", 0.0)   # poll after every frame
    async def _run():
        dev = device.CamperDevice("AA:BB:CC:DD:EE:FF")
        await dev.actuate_roof(f, move, stop, verify=False, validate_s=None,
                               limit_positions=control.roof_limit_positions("open"))
        return fake_bleak.instances[-1]
    cli = asyncio.run(_run())
    dirs = [d[0] for op, u, d in cli.calls if op == "write" and u == f.control_char]
    # reached the open limit right after the first frame -> exactly one move (0x01), then STOP (0x00)
    assert dirs.count(0x01) == 1 and dirs[-1] == 0x00


def test_resolve_addr_prefers_env(monkeypatch):
    """CALICTL_ADDR env var takes precedence over the pairing cache."""
    monkeypatch.setenv("CALICTL_ADDR", "11:22:33:44:55:66")
    monkeypatch.delenv("CALICTL_PAIRING_CACHE", raising=False)
    assert device.resolve_addr() == "11:22:33:44:55:66"


def test_resolve_addr_uses_cache_when_no_env(monkeypatch, tmp_path):
    """Falls back to the pairing cache when CALICTL_ADDR is not set."""
    monkeypatch.delenv("CALICTL_ADDR", raising=False)
    cache_file = tmp_path / "pairing.json"
    cache_file.write_text('{"address": "aa:bb:cc:dd:ee:ff"}')
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(cache_file))
    assert device.resolve_addr() == "aa:bb:cc:dd:ee:ff"


def test_resolve_addr_placeholder_when_file_missing(monkeypatch):
    """Falls back to the placeholder when the cache file does not exist."""
    monkeypatch.delenv("CALICTL_ADDR", raising=False)
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", "/nonexistent/path/pairing.json")
    assert device.resolve_addr() == "AA:BB:CC:DD:EE:FF"


def test_resolve_addr_placeholder_on_malformed_json(monkeypatch, tmp_path):
    """Falls back to the placeholder when the cache JSON is malformed."""
    monkeypatch.delenv("CALICTL_ADDR", raising=False)
    cache_file = tmp_path / "pairing.json"
    cache_file.write_text("not valid json {")
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(cache_file))
    assert device.resolve_addr() == "AA:BB:CC:DD:EE:FF"


def test_resolve_addr_placeholder_on_missing_address_field(monkeypatch, tmp_path):
    """Falls back to the placeholder when the cache JSON has no 'address' field."""
    monkeypatch.delenv("CALICTL_ADDR", raising=False)
    cache_file = tmp_path / "pairing.json"
    cache_file.write_text('{"wrong_field": "11:22:33:44:55:66"}')
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(cache_file))
    assert device.resolve_addr() == "AA:BB:CC:DD:EE:FF"


def test_resolve_addr_placeholder_on_empty_address(monkeypatch, tmp_path):
    """Falls back to the placeholder when the cached address string is empty."""
    monkeypatch.delenv("CALICTL_ADDR", raising=False)
    cache_file = tmp_path / "pairing.json"
    cache_file.write_text('{"address": ""}')
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(cache_file))
    assert device.resolve_addr() == "AA:BB:CC:DD:EE:FF"


def test_resolve_addr_placeholder_on_oserror(monkeypatch, tmp_path):
    """Falls back to the placeholder when the cache path is unreadable (OSError/IsADirectoryError).

    Simulates a permission-denied or directory-read scenario by pointing to a directory
    instead of a file, which raises IsADirectoryError on read_text().
    """
    monkeypatch.delenv("CALICTL_ADDR", raising=False)
    # Point the cache path to a directory, not a file -> IsADirectoryError on read_text()
    monkeypatch.setenv("CALICTL_PAIRING_CACHE", str(tmp_path))
    assert device.resolve_addr() == "AA:BB:CC:DD:EE:FF"
