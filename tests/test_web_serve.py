import asyncio
from calictl import serve, protocol, overrides, control


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
    fresh = serve.ServeBackend(s, loop=None).state()["water"]["fresh"]
    assert fresh["stale"] is True
    assert fresh["liters"] == 17               # the last plausible level, not the latched 1 L
    assert "days_left" not in fresh


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
