import asyncio
from calictl import serve, protocol, overrides, control


def test_serve_backend_state_interprets_cache():
    s = serve.Server(influx_enabled=False)
    funcs = protocol.load(); overrides.apply(funcs)
    s._last = {"cooler": {"Installed": 1, "State": 1, "Level": 3, "Mode": 4}}
    be = serve.ServeBackend(s, loop=None)
    st = be.state()
    assert st["cooler"]["installed"] is True and st["cooler"]["on"] is True


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
