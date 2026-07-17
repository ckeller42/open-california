import asyncio
from calictl import device, protocol, overrides, control
from tools.mock_unit import MockCamperUnit, MockBleakClient


def _funcs():
    f = protocol.load(); overrides.apply(f); return f


def test_persistent_session_starts_armed_and_actuates_without_arm_delay(monkeypatch):
    """.. test:: Persistent session actuates arm-free
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
        slept.append(s); await real_sleep(0)
    monkeypatch.setattr(device.asyncio, "sleep", fake_sleep)

    async def _run():
        dev = device.CamperDevice("MO:CK")
        # inject the mock bleak module so _session() builds a MockBleakClient
        import sys, types
        fake = types.ModuleType("bleak")
        fake.BleakClient = MockBleakClient.bind(unit)
        sys.modules["bleak"] = fake
        sess = device.PersistentSession(dev)
        await sess.start()
        assert sess.is_up is True
        assert unit.armed is True                       # heartbeat armed the unit at start
        frame = control.build(funcs, "cooler", "power", "on", {"State": 0, "Level": 3, "Mode": 4})
        post = await sess.actuate(funcs["cooler"], frame, verify=True)
        raw = await sess.read_all(funcs)
        await sess.aclose()
        return post, raw

    post, raw = asyncio.run(_run())
    assert device.ARM_DELAY_S not in slept              # arm-free write
    assert post.get("State") == 1
    assert "cooler" in raw and protocol.decode(funcs["cooler"], raw["cooler"])["State"] == 1
    assert unit.armed  # still armed after (heartbeat ran through the session)


def test_persistent_read_all_retries_transient_failures_and_logs(monkeypatch):
    """.. test:: PersistentSession.read_all retries a failed live read like the per-op path
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
        sleeps.append(s); await real_sleep(0)
    monkeypatch.setattr(device.asyncio, "sleep", fake_sleep)

    sess = device.PersistentSession(device.CamperDevice("MO:CK"))
    client = FlakyClient()
    sess._client = client
    sess._notif = {}

    async def _run():
        return await sess.read_all({"cooler": funcs["cooler"]})

    out = asyncio.run(_run())
    assert "cooler" in out                    # eventually succeeded via retry
    assert client.calls == 3
    assert sleeps.count(0.8) == 2              # two retries, same backoff as _read_all_on


def test_persistent_read_all_logs_after_exhausted_retries(monkeypatch, capsys):
    """.. test:: PersistentSession.read_all logs (doesn't raise) after 3 failed attempts
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
    """.. test:: PersistentSession.read_all aborts the cycle on a genuine link drop
       :id: T_PERSISTENT_READ_ALL_BREAK
       :links: R_PERSISTENT_SESSION

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
    assert out == {}   # first read dropped the link -> loop broke, second func never attempted


def test_read_all_only_trusts_sticky_push_for_push_only_funcs(monkeypatch):
    """.. test:: read_all scopes the sticky-notification cache to PUSH_ONLY_FUNCS
       :id: T_PERSISTENT_READ_ALL_SCOPED_PUSH
       :links: R_PERSISTENT_SESSION

       Only push-only-for-freshness functions (water/1302) may be served from a cached
       notification indefinitely. Every other notifiable char must be LIVE-read each poll, so a
       stale subscribe-time value in ``_notif`` never pins it.
    """
    funcs = _funcs()
    unit = MockCamperUnit()
    cooler_char = str(funcs["cooler"].state_char).lower()
    water_char = str(funcs["water"].state_char).lower()
    live_cooler_raw = unit.read(funcs["cooler"].state_char)     # the current (fresh) truth
    stale_cooler_raw = bytes([0xFF] * len(live_cooler_raw))     # obviously-different stale value
    water_push_raw = unit.read(funcs["water"].state_char)

    class Client:
        is_connected = True

        async def read_gatt_char(self, uuid):
            return unit.read(str(uuid))

    sess = device.PersistentSession(device.CamperDevice("MO:CK"))
    sess._client = Client()
    sess._notif = {cooler_char: stale_cooler_raw, water_char: water_push_raw}

    async def _run():
        return await sess.read_all({"cooler": funcs["cooler"], "water": funcs["water"]})

    out = asyncio.run(_run())
    assert out["cooler"] == live_cooler_raw          # LIVE read wins for a non-push-only func
    assert out["cooler"] != stale_cooler_raw
    assert out["water"] == water_push_raw            # water still uses the sticky push
