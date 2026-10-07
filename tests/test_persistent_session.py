import asyncio

from calictl import control, device, overrides, protocol
from tools.mock_unit import MockBleakClient, MockCamperUnit, _pack_state


def _funcs():
    f = protocol.load()
    overrides.apply(f)
    return f


def test_persistent_session_starts_armed_and_actuates_without_arm_delay(monkeypatch):
    """
    .. test:: Persistent session actuates arm-free
       :id: T_PERSISTENT_SESSION
       :links: R_PERSISTENT_SESSION

       A started PersistentSession is armed by its continuous heartbeat, so a write applies
       without the per-command ARM_DELAY (the < 1 s fast path).
    """
    funcs = _funcs()
    unit = MockCamperUnit()
    monkeypatch.setattr(device, "HEARTBEAT_WARMUP_S", 0.0, raising=False)

    slept = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s, *a, **k):
        slept.append(s)
        await real_sleep(0)

    monkeypatch.setattr(device.asyncio, "sleep", fake_sleep)

    async def _run():
        dev = device.CamperDevice("MO:CK")
        # inject the mock bleak module so _session() builds a MockBleakClient
        import sys
        import types

        fake = types.ModuleType("bleak")
        fake.BleakClient = MockBleakClient.bind(unit)
        sys.modules["bleak"] = fake
        sess = device.PersistentSession(dev)
        await sess.start()
        assert sess.is_up is True
        assert unit.armed is True  # heartbeat armed the unit at start
        frame = control.build(funcs, "cooler", "power", "on", {"State": 0, "Level": 3, "Mode": 4})
        post = await sess.actuate(funcs["cooler"], frame, verify=True)
        raw = await sess.read_all(funcs)
        await sess.aclose()
        return post, raw

    post, raw = asyncio.run(_run())
    assert device.ARM_DELAY_S not in slept  # arm-free write
    assert post.get("State") == 1
    assert "cooler" in raw and protocol.decode(funcs["cooler"], raw["cooler"])["State"] == 1
    assert unit.armed  # still armed after (heartbeat ran through the session)


def test_persistent_read_all_retries_transient_failures_and_logs(monkeypatch):
    """
    .. test:: PersistentSession.read_all retries a failed live read like the per-op path
       :id: T_PERSISTENT_READ_ALL_RETRY
       :links: R_PERSISTENT_SESSION

       A char not satisfied by a push must be retried up to 3x (0.8 s backoff, matching
       ``CamperDevice._read_all_on``) before being skipped.
    """
    funcs = _funcs()
    cooler_char = funcs["cooler"].state_char

    class FlakyClient:
        is_connected = True

        def __init__(self):
            self.calls = 0

        async def read_gatt_char(self, uuid):
            self.calls += 1
            if uuid == cooler_char and self.calls < 3:
                raise RuntimeError("transient")
            return bytes(8)

    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s, *a, **k):
        sleeps.append(s)
        await real_sleep(0)

    monkeypatch.setattr(device.asyncio, "sleep", fake_sleep)

    sess = device.PersistentSession(device.CamperDevice("MO:CK"))
    client = FlakyClient()
    sess._client = client
    sess._notif = {}

    async def _run():
        return await sess.read_all({"cooler": funcs["cooler"]})

    out = asyncio.run(_run())
    assert "cooler" in out  # eventually succeeded via retry
    assert client.calls == 3
    assert sleeps.count(0.8) == 2  # two retries, same backoff as _read_all_on


def test_persistent_read_all_logs_after_exhausted_retries(monkeypatch, capsys):
    """
    .. test:: PersistentSession.read_all logs (doesn't raise) after 3 failed attempts
       :id: T_PERSISTENT_READ_ALL_LOG
       :links: R_PERSISTENT_SESSION
    """
    funcs = _funcs()

    class DeadClient:
        is_connected = True

        async def read_gatt_char(self, uuid):
            raise RuntimeError("boom")

    async def fast_sleep(s, *a, **k):
        pass

    monkeypatch.setattr(device.asyncio, "sleep", fast_sleep)

    sess = device.PersistentSession(device.CamperDevice("MO:CK"))
    sess._client = DeadClient()
    sess._notif = {}

    async def _run():
        return await sess.read_all({"cooler": funcs["cooler"]})

    out = asyncio.run(_run())
    assert "cooler" not in out
    assert "read_all: cooler failed after retries" in capsys.readouterr().out


def test_persistent_read_all_breaks_on_disconnect_mid_loop(monkeypatch):
    """
    .. test:: PersistentSession.read_all aborts the cycle on a genuine link drop
       :id: T_PERSISTENT_READ_ALL_BREAK
       :links: R_PERSISTENT_SESSION, R_READ_RETRY_SHARED

       Mirrors ``_read_all_on``: don't burn retries/remaining funcs once the client itself
       reports disconnected.
    """
    funcs = _funcs()

    class DropClient:
        def __init__(self):
            self.is_connected = True

        async def read_gatt_char(self, uuid):
            self.is_connected = False
            raise RuntimeError("dropped")

    async def fast_sleep(s, *a, **k):
        pass

    monkeypatch.setattr(device.asyncio, "sleep", fast_sleep)

    sess = device.PersistentSession(device.CamperDevice("MO:CK"))
    sess._client = DropClient()
    sess._notif = {}

    async def _run():
        return await sess.read_all({"cooler": funcs["cooler"], "campingmode": funcs["campingmode"]})

    out = asyncio.run(_run())
    assert out == {}  # first read dropped the link -> loop broke, second func never attempted


def test_read_all_live_reads_every_func_last_frame_wins(monkeypatch):
    """
    .. test:: persistent read_all live-reads water too; only a LATER notify overrides a read
       :id: T_PERSISTENT_READ_ALL_SCOPED_PUSH
       :links: R_PERSISTENT_SESSION, R_READ_LAST_FRAME_WINS

       Every function, water (1302) included, is LIVE-read each poll: a stale subscribe-time value
       in ``_notif`` never pins it (the app re-reads 1302 after subscribing; it has no push-only
       rule, decompile 2026-10-07). A notification that lands AFTER its char was read in this poll
       replaces the read (the app's one decoder: last frame wins).
    """
    funcs = _funcs()
    unit = MockCamperUnit()
    unit.state["water"]["FreshWaterLevel"] = 17
    cooler_char = str(funcs["cooler"].state_char).lower()
    water_char = str(funcs["water"].state_char).lower()
    live_cooler_raw = unit.read(funcs["cooler"].state_char)
    live_water_raw = unit.read(funcs["water"].state_char)
    stale_cooler_raw = bytes([0xFF] * len(live_cooler_raw))
    stale_water_raw = _pack_state(funcs["water"], {**unit.decoded("water"), "FreshWaterLevel": 1})
    later_water_raw = _pack_state(funcs["water"], {**unit.decoded("water"), "FreshWaterLevel": 9})
    sess = device.PersistentSession(device.CamperDevice("MO:CK"))
    push_during_cooler_read = []

    class Client:
        is_connected = True

        async def read_gatt_char(self, uuid):
            if str(uuid).lower() == cooler_char and push_during_cooler_read:
                sess._notif[water_char] = push_during_cooler_read.pop()  # lands after water's read
            return unit.read(str(uuid))

    sess._client = Client()
    order = {"water": funcs["water"], "cooler": funcs["cooler"]}

    sess._notif = {cooler_char: stale_cooler_raw, water_char: stale_water_raw}
    out = asyncio.run(sess.read_all(order))
    assert out["cooler"] == live_cooler_raw  # the subscribe-time push never pins
    assert out["water"] == live_water_raw  # water is live-read too (17, not the stale 1)

    push_during_cooler_read.append(later_water_raw)
    out = asyncio.run(sess.read_all(order))
    assert out["water"] == later_water_raw  # a notify after the read wins


def test_read_char_retry_reports_disconnect_on_successful_read():
    """A read can succeed while ``is_connected`` has already flipped false — report link-down anyway.

    .. test:: _read_char_with_retry reports link-down even when the read itself succeeds
       :id: T_READ_RETRY_POST_READ_DISCONNECT
       :links: R_READ_RETRY_SHARED

       A read can return bytes while the disconnect callback has already flipped ``is_connected``
       (an asyncio race). The helper must report ``link_up=False`` so the caller aborts the cycle
       before the next func — faithfully reproducing the originals' post-read
       ``if not is_connected: break`` (which a naive extraction dropped).
    """

    class _ReadOkButDropped:
        is_connected = False  # link already down...

        async def read_gatt_char(self, char):
            return b"\x01\x02"  # ...but the read still returns bytes

    data, up = asyncio.run(device._read_char_with_retry(_ReadOkButDropped(), "cooler", "1102"))
    assert data == b"\x01\x02" and up is False  # data kept, but caller told to stop
