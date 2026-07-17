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
