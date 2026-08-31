import asyncio

from calictl import control, observer, overrides, protocol, serve


def _funcs():
    f = protocol.load(); overrides.apply(f); return f


def test_observer_logs_transitions_and_bursts_on_engine_start(capsys):
    """CampingObserver: first poll is a silent baseline; a later change logs one 'camping-watch'
    line; and an engine-start edge (ignition OR dcdc_charging rising) starts the fast-poll burst.
    It NEVER actuates — a unit test of the extracted observer, no Server scaffolding needed.

    .. test:: The observer logs transitions and bursts on an engine-start edge
       :id: T_CAMPING_OBSERVER
       :links: R_CAMPING_OBSERVER
    """
    obs = observer.CampingObserver(_funcs())
    base = {"vehicle": {"ignition_on": False},
            "energy": {"dcdc_charging": False, "soc2_pct": 80, "dcdc_current": 0, "batt2_current": 0.0},
            "campingmode": {"master_on": True, "usb_charger": True, "lights_on": False, "enable": False}}
    obs.observe(base)                        # baseline poll -> nothing logged, no burst
    assert obs.bursting is False
    assert capsys.readouterr().out == ""
    # engine start: ignition + dcdc rise, camping sheds -> one log line + a burst armed
    started = {"vehicle": {"ignition_on": True},
               "energy": {"dcdc_charging": True, "soc2_pct": 80, "dcdc_current": 30, "batt2_current": 12.0},
               "campingmode": {"master_on": False, "usb_charger": False, "lights_on": False, "enable": True}}
    obs.observe(started)
    out = capsys.readouterr().out
    assert "camping-watch:" in out and "master_on 1->0" in out and "ignition 0->1" in out
    assert obs.bursting is True              # fast-poll burst armed by the engine-start edge


def test_push_observer_logs_camping_change(capsys, monkeypatch):
    """CampingObserver.on_push: a pushed 1202 frame that changes camping master logs one
    'camping-push' line; the first push is a silent baseline; a non-watched char is ignored.
    Change-detection is what we test here (decode/interpret are covered elsewhere)."""
    funcs = _funcs()
    obs = observer.CampingObserver(funcs)
    ch = str(funcs["campingmode"].state_char).lower()
    seq = [{"master_on": True, "usb_charger": True, "lights_on": False, "enable": False},
           {"master_on": False, "usb_charger": True, "lights_on": False, "enable": False}]
    i = {"n": 0}

    def fake_interp(fn, decoded):
        r = seq[min(i["n"], len(seq) - 1)]
        i["n"] += 1
        return r
    monkeypatch.setattr(observer.semantics, "interpret", fake_interp)
    monkeypatch.setattr(observer.protocol, "decode", lambda f, d: d)
    obs.on_push(ch, b"\x00")                    # baseline push -> silent
    assert capsys.readouterr().out == ""
    obs.on_push(ch, b"\x00")                    # master 1->0 -> one line
    assert "camping-push[campingmode]: master_on 1->0" in capsys.readouterr().out
    obs.on_push("0000dead-0000-1000-8000-00805f9b34fb", b"\x00")   # unwatched char -> ignored
    assert capsys.readouterr().out == ""


def test_push_observer_logs_cooler_quiet_time_change(capsys, monkeypatch):
    """A pushed 1102 cooler frame that changes the quiet-time schedule logs one line — the live
    probe for whether the unit BROADCASTS night-timer changes (issue #99). First push = baseline."""
    funcs = _funcs()
    obs = observer.CampingObserver(funcs)
    ch = str(funcs["cooler"].state_char).lower()
    seq = [{"on": True, "level": 3, "quiet_scheduled": False, "quiet_from": 0, "quiet_to": 0,
            "timer_active": False},
           {"on": True, "level": 3, "quiet_scheduled": True, "quiet_from": 22, "quiet_to": 6,
            "timer_active": False}]
    i = {"n": 0}

    def fake_interp(fn, decoded):
        r = seq[min(i["n"], len(seq) - 1)]
        i["n"] += 1
        return r
    monkeypatch.setattr(observer.semantics, "interpret", fake_interp)
    monkeypatch.setattr(observer.protocol, "decode", lambda f, d: d)
    obs.on_push(ch, b"\x00")                    # baseline push -> silent
    assert capsys.readouterr().out == ""
    obs.on_push(ch, b"\x00")                    # quiet time enabled 22->6 -> one line
    out = capsys.readouterr().out
    assert "camping-push[cooler]:" in out
    assert "quiet_scheduled 0->1" in out and "quiet_from 0->22" in out and "quiet_to 0->6" in out


def test_store_generalpurpose_env_flag(monkeypatch):
    """The F000/F001 diagnostic-register RE probe is opt-in via CALICTL_STORE_GENERALPURPOSE."""
    monkeypatch.setenv("CALICTL_STORE_GENERALPURPOSE", "1")
    assert serve.Server(influx_enabled=False)._store_gp is True
    monkeypatch.setenv("CALICTL_STORE_GENERALPURPOSE", "no")
    assert serve.Server(influx_enabled=False)._store_gp is False
    monkeypatch.delenv("CALICTL_STORE_GENERALPURPOSE", raising=False)
    assert serve.Server(influx_enabled=False)._store_gp is False


def test_persistent_flag_is_single_source_of_truth(monkeypatch):
    """Server._persistent and the SessionSupervisor's flag must never diverge: Server._persistent
    is a property over the supervisor's, so a post-construction toggle propagates. Guards against
    the split-brain where run()/state() (server flag) and live_session()/nudge() (supervisor flag)
    would disagree. Uses CALICTL_PERSISTENT_SESSION=0 so a diverged copy would be observable rather
    than masked by the env-unset default of True."""
    monkeypatch.setenv("CALICTL_PERSISTENT_SESSION", "0")
    s = serve.Server(influx_enabled=False)
    assert s._persistent is False and s._sessions._persistent is False   # both read the env
    s._persistent = True                                                 # runtime/test toggle
    assert s._sessions._persistent is True                               # propagated, not shadowed
    assert s._persistent is s._sessions._persistent                      # one source of truth


def test_meta_session_state_reflects_supervisor(monkeypatch):
    """_meta.session mirrors the supervisor's state machine; online == (session up)."""
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._sessions.session_state = "up"
    st = serve.ServeBackend(s, loop=None).state()
    assert st["_meta"]["session"] == "up" and st["_meta"]["online"] is True
    s._sessions.session_state = "asleep"
    st = serve.ServeBackend(s, loop=None).state()
    assert st["_meta"]["session"] == "asleep" and st["_meta"]["online"] is False


def test_supervise_session_backoff_to_asleep(monkeypatch):
    """Repeated connect failures (parked van) escalate the state to 'asleep' without spinning."""
    import asyncio
    import sys
    import types

    from calictl import serve
    from tools.mock_unit import MockBleakClient, MockCamperUnit
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
            await s._sessions._connect_once()
        return s._sessions.session_state
    state = asyncio.run(_run())
    assert state == "asleep"
    assert s._sessions._backoff_fails >= s._sessions.ASLEEP_AFTER


def test_supervise_session_loop_backs_off_without_spinning(monkeypatch):
    """_supervise_session's own loop: connect fails (asleep), it escalates to 'asleep' and
    backs off (up to the 60s cap) rather than spinning."""
    import asyncio
    import sys
    import types

    from calictl import serve
    from tools.mock_unit import MockBleakClient, MockCamperUnit
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
    real_connect = s._sessions._connect_once
    _n = {"i": 0}
    async def counting_connect():
        _n["i"] += 1
        ok = await real_connect()
        if _n["i"] >= 8:
            raise _Stop
        return ok
    monkeypatch.setattr(s._sessions, "_connect_once", counting_connect)

    async def _run():
        s._ble = asyncio.Lock()
        s._sessions.attach(s._ble)          # keep-warm nudge target (never set in this test)
        s._note_ui_activity()                       # UI active -> supervisor attempts connects (vs idle-release)
        try:
            await s._sessions.supervise()
        except _Stop:
            pass
        return s._sessions.session_state, sleeps
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
    # influx must never be consulted by the stale path
    from calictl import freshness, influx
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

    async def fake_actuate_roof(f, move_frame, stop_frame, verify=True, stop_event=None,
                                limit_positions=None):
        calls["actuate_roof"] = (f.name, move_frame, stop_frame, limit_positions)
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
    _, move_frame, stop_frame, limit_positions = calls["actuate_roof"]
    funcs = protocol.load(); overrides.apply(funcs)
    assert move_frame == control.roof_frame(funcs, "open")
    assert stop_frame == control.roof_frame(funcs, "stop")
    # the open-move carries its terminal limit so travel auto-stops at the open position
    assert limit_positions == control.roof_limit_positions("open")


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


def test_on_command_refuses_precondition_without_actuating(monkeypatch):
    """Characterization: on_command's OWN precondition gate (the MQTT/HA path — the web
    backend gates earlier and separately). Roof-reading with the roof closed must return
    None and never build/actuate."""
    s = serve.Server(influx_enabled=False)
    s._read_only = False
    s._last = {"lighting": {"BrightnessLNine": 0}, "roof": {"Position": 0, "Installed": 1}}
    called = {"actuate": False}

    async def fake_actuate(*a, **k):
        called["actuate"] = True
        return None

    monkeypatch.setattr(s.dev, "actuate", fake_actuate)

    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("lighting", "roof-reading", 5)

    assert asyncio.run(_run()) is None
    assert called["actuate"] is False


def test_reconnect_closes_the_old_session_no_leak(monkeypatch):
    """FIX (final review): _session_connect_once must aclose() the outgoing session before
    replacing it. Without this, every drop->reconnect cycle leaks the old session's heartbeat
    task (runs forever, `_stop` never set) and its BleakClient (never disconnected)."""
    import asyncio
    import sys
    import types

    from calictl import device, serve
    from tools.mock_unit import MockBleakClient, MockCamperUnit

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
        assert await s._sessions._connect_once() is True     # first connect -> up
        old = s._sessions._session
        assert old.is_up is True
        assert old._stop.is_set() is False
        unit.drop(); unit.wake()                            # link cycles; van reachable again
        ok = await s._sessions._connect_once()                # reconnect
        assert ok is True
        assert s._sessions._session is not old                        # a NEW session object
        assert s._sessions._session.is_up is True
        assert old._stop.is_set() is True                   # old session was closed, not leaked
        return True

    assert asyncio.run(_run())


def test_on_command_uses_persistent_session_when_up(monkeypatch):
    """When a session is up, on_command writes through it (arm-free), not dev.actuate."""
    import asyncio

    from calictl import overrides, protocol, serve
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
    s._sessions._session = FakeSession()

    async def _run():
        s._ble = asyncio.Lock()
        return await s.on_command("cooler", "power", "on")
    result = asyncio.run(_run())
    assert calls["session"] == 1 and calls["dev"] == 0
    assert result is True     # readback State==1 matches "on"


def test_session_toggle_disconnect_and_connect():
    """The connection toggle: Disconnect forces the session inactive (frees the slot for the app)
    even while the browser keeps polling; Connect clears the release and marks the UI active."""
    import asyncio

    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._persistent = True

    async def _disc():
        s._ble = asyncio.Lock(); s._sessions.attach(s._ble)
        s._note_ui_activity()                       # UI is being polled (active)
        assert s._sessions._ui_active() is True
        r = await s.set_session_mode("disconnect")
        return r
    r = asyncio.run(_disc())
    assert r["mode"] == "release"
    assert s._sessions._session_mode == "release"
    assert s._sessions._ui_active() is False                  # release overrides activity -> slot freed

    async def _conn():
        s._sessions.attach(s._ble)
        return await s.set_session_mode("connect")
    r = asyncio.run(_conn())
    assert r["mode"] == "auto" and s._sessions._session_mode is None
    assert s._sessions._ui_active() is True                   # connect re-marked activity


def test_meta_exposes_session_mode():
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._sessions._session_mode = "release"
    assert serve.ServeBackend(s, loop=None).state()["_meta"]["session_mode"] == "release"
    s._sessions._session_mode = None
    assert serve.ServeBackend(s, loop=None).state()["_meta"]["session_mode"] == "auto"


def test_ui_active_window():
    """The session-scoping window: fresh activity -> active; stale or never-used -> idle."""
    import time

    from calictl import serve
    s = serve.Server(influx_enabled=False)
    assert s._sessions._ui_active() is False                      # never used
    s._note_ui_activity()
    assert s._sessions._ui_active() is True                       # just now
    s._sessions._last_ui_activity = time.time() - (serve._UI_IDLE_S + 5)
    assert s._sessions._ui_active() is False                      # gone idle -> release the slot


def test_supervise_releases_session_when_ui_idle(monkeypatch):
    """App-friendliness: when the web UI is idle, the supervisor RELEASES the held session so the
    phone app can use the single BLE slot.

    .. test:: SessionSupervisor releases the BLE slot when the web UI goes idle
       :id: T_SESSION_SUPERVISOR
       :links: R_SESSION_SUPERVISOR

       Exercises :meth:`calictl.session.SessionSupervisor.supervise` — an idle UI drives one
       ``aclose()`` of the held session and drops the state to ``off``.
    """
    import asyncio

    from calictl import serve
    s = serve.Server("MO:CK", influx_enabled=False)
    s._persistent = True
    s._sessions._last_ui_activity = None                          # idle (never used)
    closed = {"n": 0}
    class FakeSess:
        is_up = True
        async def aclose(self):
            closed["n"] += 1
    s._sessions._session = FakeSess()

    class _Stop(Exception):
        pass
    async def fake_wait(aws, timeout=None):             # stop after the first idle-release
        raise _Stop
    monkeypatch.setattr(serve.asyncio, "wait", fake_wait)

    async def _run():
        s._ble = asyncio.Lock()
        s._sessions.attach(s._ble)
        try:
            await s._sessions.supervise()
        except _Stop:
            pass
        return closed["n"], s._sessions._session, s._sessions.session_state
    n, sess, state = asyncio.run(_run())
    assert n == 1 and sess is None and state == "off"   # released, slot freed


def test_on_command_nudges_session_when_down(monkeypatch):
    """Keep-warm: a command with no live session resets the grown backoff and wakes the
    supervisor to reconnect now, so the fast-path session comes up promptly."""
    import asyncio

    from calictl import overrides, protocol, serve
    funcs = protocol.load(); overrides.apply(funcs)
    s = serve.Server(influx_enabled=False)
    s._persistent = True
    s._read_only = False
    s._sessions._session = None                       # no live session (van was asleep)
    s._sessions._backoff_fails = 3                     # backoff grew during the sleep
    s._last = {"lighting": {"ProfileNumber": 9}}
    monkeypatch.setattr(serve, "_SESSION_WAIT_S", 0.1)   # don't idle the full wait-for-session window
    async def dev_actuate(*a, **k):
        return None
    monkeypatch.setattr(s.dev, "actuate", dev_actuate)

    async def _run():
        s._ble = asyncio.Lock()
        s._sessions.attach(s._ble)
        await s.on_command("lighting", "kitchen", 8)
        return s._sessions._wake.is_set(), s._sessions._backoff_fails
    was_set, fails = asyncio.run(_run())
    assert was_set is True and fails == 0    # supervisor prodded, backoff reset


def test_on_command_lighting_is_fast_path_confirmed_by_notification(monkeypatch):
    """Lighting takes the fast path: NO preamble, NO blocking readback (verify=False); the write
    returns immediately and applied-ness comes from the unit's real 1502 Mode-4 notification."""
    import asyncio

    from calictl import overrides, protocol, serve
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
    s._sessions._session = FakeSession()

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


def test_auto_camper_toggle_persists_and_surfaces():
    """The auto-camper toggle flips + persists to the state cache and shows in _meta.auto_camper.
    (conftest points CALICTL_STATE_CACHE at a per-test temp, shared by both Server instances.)"""
    from calictl import serve
    s = serve.Server()
    be = serve.ServeBackend(s, loop=None)
    assert be.state()["_meta"]["auto_camper"]["enabled"] is False   # off by default
    assert be.set_auto_camper(True)["auto_camper"] is True
    assert be.state()["_meta"]["auto_camper"]["enabled"] is True
    s2 = serve.Server()                                             # fresh load from the same cache
    assert s2._autocamper.enabled is True
    be2 = serve.ServeBackend(s2, loop=None)
    assert be2.set_auto_camper(False)["auto_camper"] is False       # and back off


def test_load_last_survives_corrupt_auto_camper_state():
    """A present-but-null / garbage auto_camper_state must not crash Server.__init__ (the loader's
    documented 'missing/corrupt -> defaults' contract). Regression for the int(None) TypeError."""
    import json
    import os

    from calictl import serve
    cache = os.environ["CALICTL_STATE_CACHE"]          # conftest points this at a per-test temp
    with open(cache, "w") as f:
        json.dump({"auto_camper": True, "auto_camper_state": {"fails": None, "owe_restore": None}}, f)
    s = serve.Server()                                  # must not raise
    ac = s._autocamper
    assert ac.fails == 0 and ac.owe_restore is False   # safe defaults applied


def test_auto_camper_restore_debt_survives_restart():
    """The restore-after-park DEBT (owe_restore + pre_drive + wall-clock deadline + edge state) is
    persisted (via AutoCamper.to_state_dict/load), so a daemon restart / buspi reboot mid-drive
    doesn't silently drop a pending restore. Also verifies _load_last runs AFTER the AutoCamper is
    constructed (else the default would clobber the loaded debt)."""
    from calictl import serve
    s = serve.Server()
    ac = s._autocamper
    ac.owe_restore = True
    ac.pre_drive = {"master": True, "usb": True, "lights": False}
    ac.restore_until = 9_999_999_999.0           # wall-clock (far future) — survives the reload
    ac.fails = 2
    ac.prev_ignition = True                       # mid-drive when saved -> park edge detectable on reload
    s._save_last()
    s2 = serve.Server()                           # "restart" — same cache path (conftest-isolated)
    ac2 = s2._autocamper
    assert ac2.owe_restore is True
    assert ac2.pre_drive == {"master": True, "usb": True, "lights": False}
    assert ac2.restore_until == 9_999_999_999.0
    assert ac2.fails == 2
    assert ac2.prev_ignition is True


def test_meta_firmware_block_and_untested_flag():
    """_meta.firmware surfaces the version identity + the untested flag from general()."""
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._last = {"general": {"AmbSwVersion": None, "CommunicationVersion": 3}}  # comm 3 -> untested
    meta = serve.ServeBackend(s, loop=None).state()["_meta"]
    assert meta["firmware"]["comm_version"] == 3
    assert meta["firmware"]["untested"] is True
    assert "0409/0410" in meta["firmware"]["tested"]
    assert isinstance(meta["anchors"], list)


def test_poll_captures_raw_frames_on_firmware_change(tmp_path, monkeypatch):
    """A version change between polls dumps a raw-frame snapshot; an unchanged version does not."""
    import asyncio

    from calictl import overrides, protocol, serve
    funcs = protocol.load(); overrides.apply(funcs)
    s = serve.Server(influx_enabled=False)
    s._read_only = True
    s._fw_snapshot_dir = str(tmp_path)
    # first poll establishes the baseline (amb 0410), no capture; second poll sees 0411 -> capture
    raws = [
        {"general": bytes([0x30, 0x34, 0x31, 0x30, 0x30, 0x32, 0x30, 0x37, 0x02])},   # "0410"/"0207"/2
        {"general": bytes([0x30, 0x34, 0x31, 0x31, 0x30, 0x32, 0x30, 0x37, 0x02])},   # "0411"
    ]
    seq = iter(raws)

    async def fake_read_all(fns):
        return next(seq)
    monkeypatch.setattr(s.dev, "read_all", fake_read_all)

    async def _run():
        s._ble = asyncio.Lock()
        await s.poll()          # first-ever -> BASELINE snapshot (0410)
        n_after_1 = len(list(tmp_path.glob("fw-baseline-*.json")))
        await s.poll()          # 0411 -> DRIFT snapshot
        return n_after_1, len(list(tmp_path.glob("fw-drift-*.json")))
    n_base, n_drift = asyncio.run(_run())
    assert n_base == 1 and n_drift == 1                          # baseline captured, then the drift
    snap = list(tmp_path.glob("fw-drift-0411-*.json"))[0].read_text()
    assert "30343131" in snap                                   # the raw "0411" bytes captured
    assert s._fw_seen == ("0411", 2)


def test_web_server_binds_without_reverse_dns(tmp_path, monkeypatch):
    """serve_http MUST NOT block on socket.getfqdn. On a host with slow/absent reverse-DNS (macOS,
    some CI runners, a parked van with no uplink) that lookup hangs for tens of seconds or forever,
    stalling the daemon before the web UI binds (root-caused 2026-08-28 via a faulthandler dump).
    We sabotage getfqdn to prove server_bind never calls it, and that the server binds + serves at once.

    .. test:: The web server binds without a reverse-DNS lookup
       :id: T_WEB_NO_REVERSE_DNS
       :links: R_WEB_NO_REVERSE_DNS
    """
    import json
    import socket
    import urllib.request

    from calictl import web

    def _boom(*a, **k):
        raise AssertionError("server_bind called socket.getfqdn — it would hang on a slow resolver")
    monkeypatch.setattr(socket, "getfqdn", _boom)

    class _Backend:
        read_only = True

        def state(self):
            return {"_meta": {"read_only": True}}

    httpd = web.serve_http(_Backend(), str(tmp_path), host="127.0.0.1", port=0)  # port 0 = ephemeral
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen("http://127.0.0.1:%d/api/state" % port, timeout=3) as r:
            assert r.status == 200
            assert "_meta" in json.load(r)
    finally:
        httpd.shutdown()


def test_command_surfaces_precondition_refusal_without_actuating():
    """A physical-precondition refusal (roof-reading while roof closed) must reach the web client
    as a distinct `refused` reason, NOT be scheduled as a BLE write. Regression: the reason used to
    collapse into on_command's None and the UI showed a misleading 'Sent — check the lamp'.
    loop=None proves nothing was scheduled (run_coroutine_threadsafe would raise)."""
    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._read_only = False
    s._last = {"roof": {"Position": 0}}                 # roof closed
    res = serve.ServeBackend(s, loop=None, read_only=False).command("lighting", "roof-reading", 8)
    assert res["ok"] is True and res["applied"] is False
    assert "refused" in res and "roof" in res["refused"].lower()
    # a NON-gated command with loop=None would raise (proves the gate short-circuited before scheduling)


def test_roof_stop_sets_event_lockfree_to_interrupt_inflight_move():
    """A hold-to-move release ('stop') must interrupt an in-flight open/close that HOLDS the _ble
    lock — so 'stop' flips the shared event WITHOUT acquiring the lock. Simulate a move in flight
    (lock held, event clear) and assert the stop command sets the event and returns without blocking
    on the lock."""
    import asyncio

    from calictl import serve
    s = serve.Server(influx_enabled=False)
    s._read_only = False

    async def _run():
        s._ble = asyncio.Lock()
        s._roof_stop = asyncio.Event()          # a move is 'in flight': event clear
        await s._ble.acquire()                  # the move loop holds the lock
        try:
            # stop must NOT block on the held lock; it just flips the event
            await asyncio.wait_for(s.on_command("roof", "stop", None), timeout=1.0)
        finally:
            s._ble.release()
        return s._roof_stop.is_set()

    assert asyncio.run(_run()) is True          # event set -> in-flight loop will break + STOP
