import asyncio
from calictl import serve, protocol, overrides, control


def test_meta_session_state_reflects_supervisor(monkeypatch):
    """_meta.session mirrors the supervisor's state machine; online == (session up)."""
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._session_state = "up"
    st = serve.ServeBackend(s, loop=None).state()
    assert st["_meta"]["session"] == "up" and st["_meta"]["online"] is True
    s._session_state = "asleep"
    st = serve.ServeBackend(s, loop=None).state()
    assert st["_meta"]["session"] == "asleep" and st["_meta"]["online"] is False


def test_supervise_session_backoff_to_asleep(monkeypatch):
    """Repeated connect failures (parked van) escalate the state to 'asleep' without spinning."""
    import asyncio
    from calictl import serve, device
    from tools.mock_unit import MockCamperUnit, MockBleakClient
    import sys, types
    unit = MockCamperUnit(); unit.drop()                 # asleep from the start
    fake = types.ModuleType("bleak"); fake.BleakClient = MockBleakClient.bind(unit)
    sys.modules["bleak"] = fake

    # device._session retries the connect 3x with a real asyncio.sleep(4) between attempts;
    # collapse those to keep this a fast unit test. We assert on state, never on sleep timing.
    real = asyncio.sleep
    async def fast_sleep(x, *a, **k):
        await real(0)
    monkeypatch.setattr(serve.asyncio, "sleep", fast_sleep)

    s = serve.Server("MO:CK", influx_enabled=False)
    s._persistent = True

    async def _run():
        s._ble = asyncio.Lock()
        # run a bounded number of supervisor iterations
        for _ in range(5):
            await s._session_connect_once()
        return s._session_state
    state = asyncio.run(_run())
    assert state == "asleep"
    assert s._backoff_fails >= serve.Server.ASLEEP_AFTER


def test_supervise_session_loop_backs_off_without_spinning(monkeypatch):
    """_supervise_session's own loop: connect fails (asleep), it escalates to 'asleep' and
    backs off (up to the 60s cap) rather than spinning."""
    import asyncio, sys, types
    from calictl import serve
    from tools.mock_unit import MockCamperUnit, MockBleakClient
    unit = MockCamperUnit(); unit.drop()                     # asleep from the start
    fake = types.ModuleType("bleak"); fake.BleakClient = MockBleakClient.bind(unit)
    sys.modules["bleak"] = fake

    class _Stop(Exception):
        pass
    sleeps = []
    real = asyncio.sleep
    s = serve.Server("MO:CK", influx_enabled=False)
    s._persistent = True

    # Patching serve.asyncio.sleep patches the global asyncio module, so device._session's own
    # retry sleeps (value 4) land in this list too, alongside the supervisor's backoff sleeps
    # (5/10/30/60). Stop the otherwise-infinite loop once the machine has ESCALATED to 'asleep'
    # AND actually backed off at the 60 s cap -- that is the behaviour under test. The len cap is
    # a pure safety backstop so a regression can't hang the suite.
    async def fake_sleep(x, *a, **k):
        sleeps.append(x)
        await real(0)
    monkeypatch.setattr(serve.asyncio, "sleep", fake_sleep)

    # Stop the otherwise-infinite supervisor loop from the connect call -- it is awaited DIRECTLY in
    # the loop, so a raise propagates out (the backoff now runs in a child task via asyncio.wait, so
    # a raise there would be trapped). 8 iterations is enough to escalate to 'asleep' and reach the
    # 60 s backoff cap.
    real_connect = s._session_connect_once
    _n = {"i": 0}
    async def counting_connect():
        _n["i"] += 1
        ok = await real_connect()
        if _n["i"] >= 8:
            raise _Stop
        return ok
    monkeypatch.setattr(s, "_session_connect_once", counting_connect)

    async def _run():
        s._ble = asyncio.Lock()
        s._wake_session = asyncio.Event()          # keep-warm nudge target (never set in this test)
        s._note_ui_activity()                       # UI active -> supervisor attempts connects (vs idle-release)
        try:
            await s._supervise_session()
        except _Stop:
            pass
        return s._session_state, sleeps
    state, sleeps = asyncio.run(_run())
    assert state == "asleep"                                 # escalated after ASLEEP_AFTER fails
    assert 60 in sleeps and all(x > 0 for x in sleeps)       # capped backoff, never a 0-sleep spin


def test_serve_state_meta_offline_online_and_persistence(tmp_path, monkeypatch):
    """The daemon must keep serving the last-known values behind an offline flag when the van's
    unreachable, and the cache must survive a restart (the unit deep-sleeps for days when parked)."""
    import time
    monkeypatch.setenv("CALICTL_STATE_CACHE", str(tmp_path / "last.json"))
    s = serve.Server(influx_enabled=False)
    s._last = {"cooler": {"Installed": 1, "State": 1, "Level": 3, "Mode": 4}}
    be = serve.ServeBackend(s, loop=None)
    # no successful poll yet -> offline, but last-known values still shown
    st = be.state()
    assert st["_meta"]["online"] is False and st["_meta"]["last_seen"] is None
    assert st["cooler"]["on"] is True
    # a fresh poll -> online + persisted
    s._last_ok_ts = time.time(); s._save_last()
    assert be.state()["_meta"]["online"] is True
    # a brand-new Server (restart) loads the persisted cache
    s2 = serve.Server(influx_enabled=False)
    assert s2._last.get("cooler", {}).get("State") == 1 and s2._last_ok_ts is not None


def test_state_renders_water_without_influx():
    """state() renders the fresh-water tank straight from the BLE cache — no Influx, no forecast."""
    s = serve.Server(influx_enabled=False)
    s._last = {"water": {"FreshWaterUnit": 1, "FreshWaterLevel": 11, "FreshWaterVolume": 29,
                         "WasteWaterUnit": 1, "WasteWaterLevel": 0, "WasteWaterVolume": 22}}
    fresh = serve.ServeBackend(s, loop=None).state()["water"]["fresh"]
    assert fresh["liters"] == 11 and "days_left" not in fresh and "drain_lpd" not in fresh


def test_state_flags_stale_water():
    """When stale, state() serves the last PLAUSIBLE reading (_water_good) flagged stale, NOT the
    latched low in _last."""
    s = serve.Server(influx_enabled=False)
    s._last = {"water": {"FreshWaterUnit": 1, "FreshWaterLevel": 1, "FreshWaterVolume": 29}}  # latched low
    s._water_good = {"installed": True, "fresh": {"liters": 17, "capacity_l": 29, "percent": 59},
                     "waste": {"liters": 1, "capacity_l": 22, "percent": 5}}
    s._water_stale_since = 1_700_000_000.0
    held = serve.ServeBackend(s, loop=None).state()["water"]
    fresh = held["fresh"]
    assert fresh["stale"] is True
    assert fresh["liters"] == 17               # the last plausible level, not the latched 1 L
    assert "days_left" not in fresh
    # the substitution holds the WHOLE dict, so waste is a held value too — it must carry the
    # same stale flag (regression: waste was served frozen-but-unflagged for a month, 2026-08-16)
    assert held["waste"]["stale"] is True


def test_water_baseline_persists_across_restart(monkeypatch, tmp_path):
    """_water_good + _water_stale_since must survive a restart (load must not be clobbered by the
    __init__ defaults — the ordering bug that silently disabled persistence)."""
    monkeypatch.setenv("CALICTL_STATE_CACHE", str(tmp_path / "state.json"))
    s = serve.Server(influx_enabled=False)
    s._last = {"water": {"FreshWaterUnit": 1, "FreshWaterLevel": 1, "FreshWaterVolume": 29}}
    s._water_good = {"installed": True, "fresh": {"liters": 18, "capacity_l": 29, "percent": 62},
                     "waste": {"liters": 1, "capacity_l": 22, "percent": 5}}
    s._water_stale_since = 1_700_000_000.0
    s._save_last()
    s2 = serve.Server(influx_enabled=False)          # "restart" — same cache path
    assert s2._water_good == s._water_good and s2._water_stale_since == 1_700_000_000.0


def test_water_good_baseline_needs_no_influx(monkeypatch):
    """The stale guard's baseline is the in-memory/persisted _water_good — no Influx. A live
    plausible read establishes it; a later impossible drop is flagged without any Influx call."""
    from calictl import freshness
    # influx must never be consulted by the stale path
    from calictl import influx
    def _boom(*a, **k):
        raise AssertionError("stale detection must not call influx.field_series")
    monkeypatch.setattr(influx, "field_series", _boom)
    good = {"installed": True, "fresh": {"liters": 17, "capacity_l": 29, "percent": 59},
            "waste": {"liters": 1, "capacity_l": 22, "percent": 5}}
    latch = {"installed": True, "fresh": {"liters": 1, "capacity_l": 29, "percent": 3},
             "waste": {"liters": 1, "capacity_l": 22, "percent": 5}}
    assert freshness.implausible_water_drop(latch, good) is True     # flagged purely from the baseline


def test_serve_backend_state_interprets_cache():
    s = serve.Server(influx_enabled=False)
    funcs = protocol.load(); overrides.apply(funcs)
    s._last = {"cooler": {"Installed": 1, "State": 1, "Level": 3, "Mode": 4}}
    be = serve.ServeBackend(s, loop=None)
    st = be.state()
    assert st["cooler"]["installed"] is True and st["cooler"]["on"] is True


def test_state_read_is_safe_while_poll_rebinds_last(monkeypatch):
    """Cross-thread safety: ServeBackend.state() reads Server._last UNLOCKED on the web thread
    while the loop thread publishes new poll results. poll() must rebind _last atomically (copy-
    on-publish), never mutate the live dict in place -- otherwise a concurrent dict() copy raises
    'dictionary changed size during iteration' and a real applied write can surface as a 500."""
    import threading
    from tools.mock_unit import MockCamperUnit
    u = MockCamperUnit()
    safe = ["cooler", "campingmode", "lighting", "water", "vehicle", "energy"]
    pool = {fn: u.decoded(fn) for fn in safe}       # realistic decoded states (interpret cleanly)
    s = serve.Server(influx_enabled=False)
    be = serve.ServeBackend(s, loop=None)
    stop = threading.Event()
    errors = []

    def reader():                                   # the web thread hammering /api/state
        while not stop.is_set():
            try:
                be.state()
            except Exception as e:                  # a race would surface here (RuntimeError)
                errors.append(e); return

    t = threading.Thread(target=reader); t.start()
    try:
        # the loop thread republishing a cache of VARYING SIZE many times, the way poll() does.
        # Size changes are what trigger 'dictionary changed size during iteration' if state()
        # ever iterates a dict being mutated in place -- copy-on-publish must prevent it.
        for i in range(4000):
            k = 1 + (i % len(safe))
            s._last = {fn: pool[fn] for fn in safe[:k]}   # atomic rebind, never in-place mutation
    finally:
        stop.set(); t.join()
    assert not errors, "state() raced with poll rebind: %r" % (errors[:1])


def test_serve_backend_command_bridges_to_on_command(monkeypatch):
    s = serve.Server(influx_enabled=False)
    calls = []
    async def fake_on_command(fn, what, value):
        calls.append((fn, what, value)); s._last.setdefault(fn, {})
    monkeypatch.setattr(s, "on_command", fake_on_command)

    async def _run():
        loop = asyncio.get_running_loop()
        s._ble = asyncio.Lock()
        be = serve.ServeBackend(s, loop)
        # command() is sync (called from the web thread); run it off-loop
        return await asyncio.to_thread(be.command, "cooler", "power", "on", False)

    result = asyncio.run(_run())
    assert calls == [("cooler", "power", "on")]
    assert result["ok"] is True


def test_on_command_roof_open_routes_to_actuate_roof(monkeypatch):
    """SAFETY-SENSITIVE: roof open/close must use the bounded 1 Hz move-heartbeat
    (device.actuate_roof), never the one-shot device.actuate -- a single frame
    won't complete travel and has no guaranteed STOP."""
    s = serve.Server(influx_enabled=False)
    s._read_only = False                                 # writes enabled for this actuation test
    calls = {"actuate_roof": None, "actuate": None}

    async def fake_actuate_roof(f, move_frame, stop_frame, verify=True):
        calls["actuate_roof"] = (f.name, move_frame, stop_frame)
        return None

    async def fake_actuate(f, frame, verify=True):
        calls["actuate"] = (f.name, frame)
        return None

    monkeypatch.setattr(s.dev, "actuate_roof", fake_actuate_roof)
    monkeypatch.setattr(s.dev, "actuate", fake_actuate)

    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("roof", "open", None)

    result = asyncio.run(_run())
    assert result is None                     # roof has no _set_check row
    assert calls["actuate_roof"] is not None
    assert calls["actuate"] is None
    _, move_frame, stop_frame = calls["actuate_roof"]
    funcs = protocol.load(); overrides.apply(funcs)
    assert move_frame == control.roof_frame(funcs, "open")
    assert stop_frame == control.roof_frame(funcs, "stop")


def test_on_command_roof_stop_routes_to_actuate(monkeypatch):
    """roof stop is a one-shot STOP frame -- device.actuate, not the move-heartbeat."""
    s = serve.Server(influx_enabled=False)
    s._read_only = False                                 # writes enabled for this actuation test
    calls = {"actuate_roof": None, "actuate": None}

    async def fake_actuate_roof(f, move_frame, stop_frame, verify=True):
        calls["actuate_roof"] = (f.name, move_frame, stop_frame)
        return None

    async def fake_actuate(f, frame, verify=True):
        calls["actuate"] = (f.name, frame)
        return None

    monkeypatch.setattr(s.dev, "actuate_roof", fake_actuate_roof)
    monkeypatch.setattr(s.dev, "actuate", fake_actuate)

    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("roof", "stop", None)

    result = asyncio.run(_run())
    assert result is None
    assert calls["actuate"] is not None
    assert calls["actuate_roof"] is None


def test_reconnect_closes_the_old_session_no_leak(monkeypatch):
    """FIX (final review): _session_connect_once must aclose() the outgoing session before
    replacing it. Without this, every drop->reconnect cycle leaks the old session's heartbeat
    task (runs forever, `_stop` never set) and its BleakClient (never disconnected)."""
    import asyncio, sys, types
    from calictl import serve, device
    from tools.mock_unit import MockCamperUnit, MockBleakClient

    unit = MockCamperUnit()
    fake = types.ModuleType("bleak"); fake.BleakClient = MockBleakClient.bind(unit)
    sys.modules["bleak"] = fake
    monkeypatch.setattr(device, "HEARTBEAT_WARMUP_S", 0.0, raising=False)
    real_sleep = asyncio.sleep
    async def fast_sleep(x, *a, **k):
        await real_sleep(0)
    monkeypatch.setattr(serve.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(device.asyncio, "sleep", fast_sleep)

    s = serve.Server("MO:CK", influx_enabled=False)
    s._persistent = True

    async def _run():
        s._ble = asyncio.Lock()
        assert await s._session_connect_once() is True     # first connect -> up
        old = s._session
        assert old.is_up is True
        assert old._stop.is_set() is False
        unit.drop(); unit.wake()                            # link cycles; van reachable again
        ok = await s._session_connect_once()                # reconnect
        assert ok is True
        assert s._session is not old                        # a NEW session object
        assert s._session.is_up is True
        assert old._stop.is_set() is True                   # old session was closed, not leaked
        return True

    assert asyncio.run(_run())


def test_on_command_uses_persistent_session_when_up(monkeypatch):
    """When a session is up, on_command writes through it (arm-free), not dev.actuate."""
    import asyncio
    from calictl import serve, protocol, overrides, control
    funcs = protocol.load(); overrides.apply(funcs)
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._read_only = False
    s._last = {"cooler": {"Installed": 1, "State": 0, "Level": 3, "Mode": 4}}

    calls = {"session": 0, "dev": 0}
    class FakeSession:
        is_up = True
        async def actuate(self, func, frame, *, follow=None, verify=True, pre=None):
            calls["session"] += 1
            return {"State": 1, "Level": 3, "Mode": 4, "Installed": 1}
    async def dev_actuate(*a, **k):
        calls["dev"] += 1
        return None
    monkeypatch.setattr(s.dev, "actuate", dev_actuate)
    s._session = FakeSession()

    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("cooler", "power", "on")
    result = asyncio.run(_run())
    assert calls["session"] == 1 and calls["dev"] == 0
    assert result is True     # readback State==1 matches "on"


def test_ui_active_window():
    """The session-scoping window: fresh activity -> active; stale or never-used -> idle."""
    import time
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    assert s._ui_active() is False                      # never used
    s._note_ui_activity()
    assert s._ui_active() is True                       # just now
    s._last_ui_activity = time.time() - (serve._UI_IDLE_S + 5)
    assert s._ui_active() is False                      # gone idle -> release the slot


def test_supervise_releases_session_when_ui_idle(monkeypatch):
    """App-friendliness: when the web UI is idle, the supervisor RELEASES the held session so the
    phone app can use the single BLE slot."""
    import asyncio
    from calictl import serve
    s = serve.Server("MO:CK", influx_enabled=False)
    s._persistent = True
    s._last_ui_activity = None                          # idle (never used)
    closed = {"n": 0}
    class FakeSess:
        is_up = True
        async def aclose(self):
            closed["n"] += 1
    s._session = FakeSess()

    class _Stop(Exception):
        pass
    async def fake_wait(aws, timeout=None):             # stop after the first idle-release
        raise _Stop
    monkeypatch.setattr(serve.asyncio, "wait", fake_wait)

    async def _run():
        s._ble = asyncio.Lock()
        s._wake_session = asyncio.Event()
        try:
            await s._supervise_session()
        except _Stop:
            pass
        return closed["n"], s._session, s._session_state
    n, sess, state = asyncio.run(_run())
    assert n == 1 and sess is None and state == "off"   # released, slot freed


def test_on_command_nudges_session_when_down(monkeypatch):
    """Keep-warm: a command with no live session resets the grown backoff and wakes the
    supervisor to reconnect now, so the fast-path session comes up promptly."""
    import asyncio
    from calictl import serve, protocol, overrides
    funcs = protocol.load(); overrides.apply(funcs)
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._read_only = False
    s._session = None                       # no live session (van was asleep)
    s._backoff_fails = 3                     # backoff grew during the sleep
    s._last = {"lighting": {"ProfileNumber": 9}}
    monkeypatch.setattr(serve, "_SESSION_WAIT_S", 0.1)   # don't idle the full wait-for-session window
    async def dev_actuate(*a, **k):
        return None
    monkeypatch.setattr(s.dev, "actuate", dev_actuate)

    async def _run():
        s._ble = asyncio.Lock()
        s._wake_session = asyncio.Event()
        await s.on_command("lighting", "kitchen", 8)
        return s._wake_session.is_set(), s._backoff_fails
    was_set, fails = asyncio.run(_run())
    assert was_set is True and fails == 0    # supervisor prodded, backoff reset


def test_on_command_lighting_is_fast_path_confirmed_by_notification(monkeypatch):
    """Lighting takes the fast path: NO preamble, NO blocking readback (verify=False); the write
    returns immediately and applied-ness comes from the unit's real 1502 Mode-4 notification."""
    import asyncio
    from calictl import serve, protocol, overrides
    funcs = protocol.load(); overrides.apply(funcs)
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._read_only = False
    s._last = {"lighting": {"ProfileNumber": 9}}      # non-empty -> skip the cold-cache read
    key = str(funcs["lighting"].state_char).lower()
    # a genuine Mode-4 state notification captured on-device: BrightnessLSeven (Kitchen/L7) = 8
    fresh = bytes.fromhex("090400000000000000000508d00ddddd")

    seen = {}
    class FakeSession:
        is_up = True
        _notif = {}
        async def actuate(self, func, frame, *, follow=None, verify=True, pre=None):
            seen["verify"], seen["pre"], seen["follow"] = verify, pre, follow
            async def push():                          # the unit's Mode-4 arrives shortly after
                await asyncio.sleep(0.02)
                self._notif[key] = fresh
            asyncio.ensure_future(push())
            return None
    s._session = FakeSession()

    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("lighting", "kitchen", 8)
    result = asyncio.run(_run())
    assert seen["pre"] is None            # no REQUEST_CONFIG preamble on the fast path
    assert seen["verify"] is False        # no blocking echo-readback
    assert result is True                 # confirmed by the real Mode-4 notification (L7 == 8)
    assert s._last["lighting"]["BrightnessLSeven"] == 8   # served state updated from the push


# --- web UI start is optional: a bind failure must not crash the daemon ---

def test_maybe_start_web_none_when_no_port():
    s = serve.Server(influx_enabled=False)
    assert s._maybe_start_web(loop=None) is None       # _web_port unset


def test_maybe_start_web_survives_bind_failure(monkeypatch, capsys):
    from calictl import web

    def _boom(*_a, **_k):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(web, "serve_http", _boom)
    s = serve.Server(influx_enabled=False)
    s._web_port = 8088
    assert s._maybe_start_web(loop=None) is None        # swallowed, did not raise
    assert "web UI failed to start" in capsys.readouterr().out


def test_maybe_start_web_returns_server_on_success(monkeypatch, capsys):
    from calictl import web
    sentinel = object()
    monkeypatch.setattr(web, "serve_http", lambda *_a, **_k: sentinel)
    s = serve.Server(influx_enabled=False)
    s._web_port = 8088
    assert s._maybe_start_web(loop=None) is sentinel
    assert "web UI on http://0.0.0.0:8088" in capsys.readouterr().out


# --- MQTT is optional: no broker / paho absent must not crash the daemon ---

def test_maybe_start_mqtt_survives_no_broker(monkeypatch):
    monkeypatch.setenv("MQTT_PORT", "1")   # nothing listens on port 1 -> connect refused
    s = serve.Server(influx_enabled=False)
    assert s._maybe_start_mqtt(loop=None) is None   # graceful None, never raises
