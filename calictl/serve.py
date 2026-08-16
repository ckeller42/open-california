"""Unified calictl daemon: one BLE owner -> InfluxDB + MQTT + commands.

A single process owns the van's single BLE connection slot. Each poll fans out
to both InfluxDB (Grafana stack) and MQTT (Home Assistant discovery/state).
MQTT command messages are serviced by BLE writes under the same lock, so a
control write can never race a poll for the one connection slot.

`paho.mqtt.client` is imported lazily in `run()` and `control` lazily in
`on_command` (both may be absent on a dev box / not-yet-built), so
`import calictl.serve` stays cheap and offline-safe.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from . import protocol, semantics, overrides, mqtt, influx, freshness, history
from .device import CamperDevice, ConnectionUnavailable

_WEBUI_DIR = str(Path(__file__).resolve().parent / "webui")



def installed_from(states: dict) -> set:
    """Functions whose interpreted state reports `installed` truthy."""
    return {fn for fn, i in states.items() if i.get("installed")}


class ServeBackend:
    """Adapts a running `Server` to `calictl.web`'s backend interface.

    Opens no BLE of its own: state reads the poll cache (`server._last`) and
    commands bridge onto the daemon's event loop via `on_command`, under the
    daemon's existing `_ble` lock. Safe to call from the web server's thread.

    :param server: the running `Server` (its `_last` cache + `on_command`).
    :param loop: the daemon's asyncio event loop (`Server._loop`); commands are
        scheduled onto it with `asyncio.run_coroutine_threadsafe`.
    :param read_only: when true, `calictl.web` rejects command POSTs before
        `command()` is ever called (enforced by `web.py`, mirrored here for
        callers that check the attribute directly).
    """

    def __init__(self, server, loop, read_only=False):
        self._s = server
        self._loop = loop
        self.read_only = read_only

    def state(self):
        """Interpreted state for every function in the poll cache (no BLE read), plus a `_meta`
        block with the last-successful-poll time and an online flag. The camper unit deep-sleeps
        when the van is parked and then can't be reached at all, so the UI must show the last
        known values *with an "as of" age* and an offline indicator — never a blank or a stale
        number dressed as live. `_meta` is not a function name, so it never renders as a tile."""
        out = {}
        for fn, decoded in dict(self._s._last or {}).items():
            out[fn] = semantics.interpret(fn, decoded)
        # Stale fresh-water: serve the last PLAUSIBLE reading (not the parked latch), flagged stale.
        water = out.get("water")
        if isinstance(water, dict) and isinstance(water.get("fresh"), dict):
            stale_since = self._s._water_stale_since
            good = self._s._water_good
            if stale_since and isinstance(good, dict) and isinstance(good.get("fresh"), dict):
                # the hold substitutes the WHOLE dict, so flag BOTH tanks — waste is just as held
                # as fresh (it was served frozen-but-unflagged for a month before 2026-08-16)
                out["water"] = {**good, "fresh": {**good["fresh"], "stale": True},
                                "stale_since": stale_since}
                if isinstance(good.get("waste"), dict):
                    out["water"]["waste"] = {**good["waste"], "stale": True}
        ts = self._s._last_ok_ts
        age = (time.time() - ts) if ts else None
        persistent = getattr(self._s, "_persistent", False)
        session_state = getattr(self._s, "_session_state", "off")
        # Session state drives online only once the persistent machinery is active (past "off");
        # before the supervisor has run, or when persistence is off, use the poll-recency rule so
        # the pre-persistent behaviour (and its tests) still hold.
        if persistent and session_state != "off":
            online = session_state == "up"
        else:
            online = bool(age is not None and age < self._s.interval * 3 + 30)
        out["_meta"] = {
            "last_seen": ts,
            "age_s": round(age) if age is not None else None,
            "online": online,
            "read_only": bool(self.read_only),
            "session": session_state if persistent else "off",
        }
        return out

    def history(self, hours=24):
        """Leisure-battery samples for the last ``hours``, oldest first — read from the daemon's
        own append-only file, NOT from InfluxDB (the GUI stays Influx-free by design).

        ``gap_s`` is the caller's cue for where to BREAK the plotted line: the unit deep-sleeps for
        days when parked, so an absent stretch means "no data", never "held flat". It reuses the
        same threshold as ``_meta.online`` so "we lost the van" means one thing everywhere.

        :param hours: window size, clamped to 1..48 (48 h is the retention ceiling).
        :returns: ``{"samples": [[ts, volts, amps], ...], "gap_s": float, "now": float,
            "hours": int}``.
        """
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            hours = 24
        hours = max(1, min(48, hours))
        now = time.time()
        return {
            "samples": history.load(self._s._history_cache, since=now - hours * 3600),
            "gap_s": self._s.interval * 3 + 30,
            "now": now,
            "hours": hours,
        }

    def screens_bytes(self):
        """Raw bytes of the authored `webui/screens.json` GUI spec."""
        with open(os.path.join(_WEBUI_DIR, "screens.json"), "rb") as f:
            return f.read()

    def command(self, function, what, value, confirm=False):
        """Bridge a web-UI command onto the daemon loop and report the result.

        Runs on the web server's thread; blocks it (not the event loop) for up
        to 90s waiting on `Server.on_command`, which performs the BLE write
        under the daemon's `_ble` lock.

        :param function: target function name (e.g. ``"cooler"``).
        :param what: the targeted control (e.g. ``"power"``).
        :param value: the requested value.
        :param confirm: unused here; `web.py` already gated safety-sensitive
            targets (e.g. ``roof``) before calling this.
        :returns: ``{"ok": True, "applied": <True/False/None>, "state": <interp
            or None>, "error": None}``. ``applied`` mirrors `on_command`'s
            return: whether the readback showed the change took effect.
        """
        fut = asyncio.run_coroutine_threadsafe(
            self._s.on_command(function, what, value), self._loop)
        applied = fut.result(timeout=90)   # on_command returns applied-ness
        interp = self.state().get(function)
        return {"ok": True, "applied": applied, "state": interp, "error": None, "function": function}


async def dry_run(addr=None):
    """Print the MQTT discovery configs + one live poll — no broker, no InfluxDB.
    Backs `calictl serve --dry-run`."""
    funcs = protocol.load(); overrides.apply(funcs)
    print("# --- HA discovery configs ---")
    for topic, cfg in mqtt.render_discovery().items():
        print(topic, "=>", json.dumps(cfg))
    print("\n# --- one poll ---")
    dev = CamperDevice(addr) if addr else CamperDevice()
    try:
        states = await influx.poll_states(funcs, dev)
    except ConnectionUnavailable as e:
        print("(device unreachable: %s)" % e); return
    for fn, interp in states.items():
        topic, payload = mqtt.render_state(fn, interp)
        print(topic, "=>", payload)


class Server:
    SESSION_BACKOFF = (5, 10, 30, 60)   # reconnect backoff seconds, capped
    ASLEEP_AFTER = 4                     # consecutive fails at cap -> label 'asleep'

    def __init__(self, addr=None, *, interval=30.0, influx_enabled=True):
        self.funcs = protocol.load(); overrides.apply(self.funcs)
        self.dev = CamperDevice(addr) if addr else CamperDevice()
        self.interval = interval
        self.influx_enabled = influx_enabled
        self._ble = None                    # asyncio.Lock(); created inside run()'s loop
        self._published = set()             # functions whose discovery is sent
        self._last = {}                     # function -> last DECODED state (for commands)
        self._last_ok_ts = None             # epoch of the last SUCCESSFUL poll (for offline/age)
        # Persist the last-known state so a restart while the van is asleep still shows the last
        # real values (the unit can be unreachable for days when parked). Env-overridable path.
        self._state_cache = os.environ.get(
            "CALICTL_STATE_CACHE", os.path.expanduser("~/.cache/calictl/last_state.json"))
        self._water_stale_since = None      # ts the fresh-water read went physically-impossible (stale latch)
        self._water_good = None             # last PLAUSIBLE interpreted water (baseline for the stale guard)
        # Leisure-battery history for the web UI's 24 h chart. Its own append-only file (NOT the
        # state blob: that gets rewritten every poll, and 24 h of samples would mean ~330 MB/day
        # of SD-card writes). The GUI reads this instead of Influx -- see calictl/history.py.
        self._history_cache = os.environ.get(
            "CALICTL_HISTORY_CACHE", os.path.expanduser("~/.cache/calictl/history.jsonl"))
        self._appends = 0                   # appends since the last trim rewrite
        self._load_last()                   # AFTER the defaults: it restores _water_good/_water_stale_since
        self._mqtt = None
        self._iw = None                     # influx write_api
        self._loop = None
        self._web_port = None                # set by the CLI to enable the web UI
        self._read_only = True               # SAFE DEFAULT: reject writes until explicitly enabled
        self._httpd = None
        # Persistent armed session (fast actuation). Default on; CALICTL_PERSISTENT_SESSION=0
        # falls back to the connect-per-op model. See docs/.../persistent-ble-session-design.md.
        self._persistent = os.environ.get("CALICTL_PERSISTENT_SESSION", "1") != "0"
        self._session = None
        self._session_state = "off"        # off|connecting|up|degraded|asleep
        self._backoff_fails = 0

    def _load_last(self):
        """Load the persisted last-known decoded state (best-effort; missing/corrupt -> empty)."""
        try:
            with open(self._state_cache) as f:
                blob = json.load(f)
            self._last = {fn: v for fn, v in (blob.get("last") or {}).items() if fn in self.funcs}
            self._last_ok_ts = blob.get("ts")
            self._water_good = blob.get("water_good")      # last plausible water survives restarts
            self._water_stale_since = blob.get("water_stale_since")
        except (OSError, ValueError):
            pass

    def _save_last(self):
        """Atomically persist the last-known decoded state + timestamp (never crashes the poll)."""
        try:
            os.makedirs(os.path.dirname(self._state_cache), exist_ok=True)
            tmp = self._state_cache + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"ts": self._last_ok_ts, "last": self._last,
                           "water_good": self._water_good,
                           "water_stale_since": self._water_stale_since}, f)
            os.replace(tmp, self._state_cache)
        except OSError:
            pass

    def _record_history(self, energy):
        """Record one leisure-battery sample for the web UI's 24 h chart. Never raises.

        Only the leisure (second) battery is recorded: it is always measured, and its scales are
        the verified ones (``semantics.energy``), so the chart can carry real V/A units. The
        starter battery is measured only with terminal-15 on and reads sentinels otherwise, which
        would be a permanently empty series.

        :param energy: the interpreted ``energy`` state of this poll, or None if it wasn't read.
        """
        if not energy:
            return
        if history.append(self._history_cache, self._last_ok_ts,
                          energy.get("batt2_v"), energy.get("batt2_current")):
            self._appends += 1
            if self._appends >= history.TRIM_EVERY:      # amortised: ~1 rewrite per 4 h of polling
                self._appends = 0
                history.trim(self._history_cache)

    def _live_session(self):
        """The persistent session if it's currently up, else None (use the per-op path)."""
        s = self._session
        return s if (self._persistent and s is not None and s.is_up) else None

    async def poll(self):
        # One BLE read of every function under the lock; cache the DECODED state
        # (on_command builds full-packet control frames from it) and derive the
        # INTERPRETED state for MQTT + InfluxDB.
        async with self._ble:
            sess = self._live_session()
            raw = await (sess.read_all(self.funcs) if sess is not None
                         else self.dev.read_all(self.funcs))
        states = {}
        new_last = dict(self._last)         # build a fresh copy, then publish atomically
        for fn, data in raw.items():
            decoded = protocol.decode(self.funcs[fn], data)
            new_last[fn] = decoded
            states[fn] = semantics.interpret(fn, decoded)
        # Stale-latch guard: when the van is parked/locked the unit stops measuring fresh water and
        # returns a bogus low (true 17 L read back as 1 L). A fresh drop from the last PLAUSIBLE
        # reading with no matching grey rise is physically impossible -> serve/publish that last
        # plausible reading, flagged stale. The baseline is the persisted `_water_good` (survives
        # restarts, NO Influx dependency). KNOWN LIMIT (cold start): a brand-new install with no
        # cached baseline that first reads while parked will accept the latched low as the baseline
        # and show it unflagged. This is inherent — with no history and no "water-system-on" signal,
        # a latched 1 L is indistinguishable from a genuinely near-empty tank, and any flag would
        # clear on the next (identical) parked read; a real level self-establishes once the van is
        # next active. Persistence covers the common restart-while-parked case.
        new_water = states.get("water")
        if new_water is not None and (new_water.get("fresh") or {}).get("liters") is not None:
            base = self._water_good
            if base is not None and freshness.implausible_water_drop(new_water, base):
                states["water"] = base                # MQTT/Influx get the plausible level, not the latch
                self._water_stale_since = self._water_stale_since or self._last_ok_ts or time.time()
            else:
                self._water_good = new_water           # a plausible read -> new baseline
                self._water_stale_since = None
        if states:                          # a real read happened -> mark fresh + persist
            # Rebind (never mutate in place): the web thread reads `_last` unlocked in
            # ServeBackend.state(), so it must only ever see a complete dict, not one
            # growing mid-iteration (RuntimeError). Reference assignment is atomic (GIL).
            self._last = new_last
            self._last_ok_ts = time.time()
            self._save_last()
            self._record_history(states.get("energy"))
        # MQTT discovery for newly-seen installed functions
        inst = installed_from(states)
        new = inst - self._published
        if new and self._mqtt:
            for topic, cfg in mqtt.render_discovery(installed=new).items():
                self._mqtt.publish(topic, json.dumps(cfg), retain=True)
            self._published |= inst
        for fn, interp in states.items():
            if fn in inst and self._mqtt:
                t, payload = mqtt.render_state(fn, interp)
                self._mqtt.publish(t, payload)
        if self.influx_enabled and self._iw:
            # Store only INSTALLED functions (same set MQTT publishes). The uninstalled ones
            # (satellite / living-room heater / roof-A/C / stairs / generalpurposesignals) emit
            # only raw pass-through fields (WordZeroFour, System, ...) — pure noise on this van.
            self._iw.write(bucket=os.environ.get("INFLUX_BUCKET", "buspi"),
                           org=os.environ.get("INFLUX_ORG", "home"),
                           record=influx.points_for({fn: states[fn] for fn in inst}))
        return states

    async def _session_connect_once(self) -> bool:
        """One connect attempt for the supervisor. Updates _session_state + _backoff_fails.
        Returns True if the session came up."""
        from . import device  # lazy
        self._session_state = "connecting"
        if self._session is not None:
            # Close the outgoing session before replacing it: otherwise its heartbeat task
            # (self._stop never set) loops forever and its BleakClient is never disconnected —
            # an unbounded leak on every drop->reconnect cycle.
            try:
                await self._session.aclose()
            except Exception:
                pass
            self._session = None
        sess = device.PersistentSession(self.dev)
        try:
            await sess.start()
        except Exception as e:
            self._backoff_fails += 1
            self._session = None
            self._session_state = "asleep" if self._backoff_fails >= self.ASLEEP_AFTER else "degraded"
            print("session connect failed (%d): %s" % (self._backoff_fails, e), flush=True)
            return False
        self._session = sess
        self._session_state = "up"
        self._backoff_fails = 0
        print("persistent session up", flush=True)
        return True

    async def _supervise_session(self) -> None:
        """Keep the persistent session up; back off while the van is unreachable."""
        while True:
            if self._session is not None and self._session.is_up:
                await asyncio.sleep(self.interval)
                continue
            async with self._ble:
                ok = await self._session_connect_once()
            if not ok:
                idx = min(self._backoff_fails, len(self.SESSION_BACKOFF)) - 1
                await asyncio.sleep(self.SESSION_BACKOFF[max(0, idx)])

    async def on_command(self, function, what, value):
        """Build + write a control frame, then read back and report applied-ness.

        :param function: target function name (e.g. ``"cooler"``).
        :param what: the targeted control (e.g. ``"power"``, ``"level"``).
        :param value: the requested value (on/off token, or numeric).
        :returns: ``True`` if the post-write readback shows the targeted field
            now matches the request, ``False`` if it does not, or ``None`` when
            there is nothing to check against (unknown target, a frame that
            can't be built, or a cold-cache read failure). The MQTT command
            path ignores this; `ServeBackend.command` surfaces it to the web UI.
        """
        from . import control  # lazy
        from .cli import _set_check  # lazy; reuse the CLI's post-write field check
        if self._read_only:
            # SAFE DEFAULT: no vehicle writes unless writes were explicitly enabled. Central gate,
            # so it also blocks the MQTT/HA command path (web.py rejects earlier with a 405).
            print("read-only: refusing command %s/%s" % (function, what), flush=True)
            return None
        # actuate holds a 1003 liveness heartbeat across the write (arms actuation,
        # issue #2); the same self._ble lock keeps it the single BLE owner.
        async with self._ble:
            if function == "roof":
                # SAFETY-SENSITIVE: a single roof frame won't complete travel and has
                # no guaranteed STOP, so roof must stream the move frame with a live
                # SafetyCounter, bounded then always STOP (device.actuate_roof), never the
                # one-shot device.actuate. Mirrors cli._set_roof. `what` = direction (open/close/stop).
                try:
                    move_frame = control.roof_frame(self.funcs, what)
                    stop_frame = control.roof_frame(self.funcs, "stop")
                except ValueError:
                    return None
                f = self.funcs["roof"]
                if what == "stop":
                    await self.dev.actuate(f, stop_frame, verify=True)
                else:
                    await self.dev.actuate_roof(f, move_frame, stop_frame, verify=True)
                # roof has no _set_check row -- keep the honest "not applied" (unknown).
                return None
            last = self._last.get(function)
            if not last:
                # cold cache -> a full-packet frame built from field DEFAULTS would carry
                # e.g. cooler State=1 (fridge ON) or lighting ProfileNumber=0 (silent no-op).
                # Read the live state first so untargeted fields carry the real values.
                try:
                    sess = self._live_session()
                    raw = await (sess.read_one(self.funcs[function]) if sess is not None
                                 else self.dev.read(self.funcs[function]))
                    last = protocol.decode(self.funcs[function], raw)
                    self._last = {**self._last, function: last}   # atomic rebind (web thread reads unlocked)
                except ConnectionUnavailable as e:
                    print("command %s skipped (no state read): %s" % (function, e), flush=True)
                    return None
            frame = control.build(self.funcs, function, what, value, last)
            if frame is None:
                return None
            sess = self._live_session()
            if sess is not None:
                post = await sess.actuate(self.funcs[function], frame, verify=True,
                                          follow=control.commit_for(function),
                                          pre=control.preamble_for(function))
            else:
                post = await self.dev.actuate(self.funcs[function], frame, verify=True,
                                              follow=control.commit_for(function),
                                              pre=control.preamble_for(function))
            if post is None:
                return None
            self._last = {**self._last, function: post}   # atomic rebind (web thread reads unlocked)
            interp = semantics.interpret(function, post)
            _, got, want = _set_check(function, what, value, interp, post)
            if got is None and want is None:   # no table entry for this target
                return None
            if function == "lighting":
                # the lighting state char is a write-through ECHO — a matching readback proves
                # only that the frame arrived, never that the lamp lit (CLAUDE.md, 2026-08-16).
                # An echo MISMATCH is still a real failure (link/refusal), so report False; a
                # match is honestly "unknown". Upgrading to real verification needs the 1502
                # Mode-4 ramp-notification layout decoded first (it differs from the control
                # frame layout) — an open RE task.
                return None if got == want else False
            return got == want

    def _maybe_start_mqtt(self, loop):
        """Connect the MQTT client for Home Assistant, or skip gracefully. Returns the client
        or None. MQTT is OPTIONAL: paho-mqtt not installed, or the broker being unreachable,
        must never stop BLE polling + the web UI (web-only / dev / e2e runs have no broker)."""
        try:
            import paho.mqtt.client as mqtt_client  # lazy
        except ImportError:
            print("mqtt: paho-mqtt not installed — skipping MQTT (polling + web only)", flush=True)
            return None
        host = os.environ.get("MQTT_HOST", "localhost")
        port = int(os.environ.get("MQTT_PORT", "1883"))
        user = os.environ.get("MQTT_USER")
        password = os.environ.get("MQTT_PASSWORD")
        cmd_map = getattr(mqtt, "command_topics", lambda: {})()
        # paho-mqtt >= 2.0 requires an explicit callback API version; request V1 so the
        # 4-arg on_connect / 3-arg on_message below stay valid on both 1.x and 2.x.
        try:
            cli = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1)
        except AttributeError:
            cli = mqtt_client.Client()   # paho-mqtt < 2.0
        if user:
            cli.username_pw_set(user, password)
        cli.will_set(mqtt.availability_topic(), "offline", retain=True)

        def on_connect(c, u, flags, rc):
            c.publish(mqtt.availability_topic(), "online", retain=True)
            for topic in cmd_map:
                c.subscribe(topic)
            print("mqtt connected to %s:%d (%d command topics)" % (host, port, len(cmd_map)), flush=True)

        def on_message(c, u, msg):
            fn, what = cmd_map.get(msg.topic, (None, None))
            if not fn:
                return
            value = msg.payload.decode().strip()
            print("command: %s %s %s" % (fn, what, value), flush=True)
            fut = asyncio.run_coroutine_threadsafe(self.on_command(fn, what, value), loop)

            def _log_command_failure(f, fn=fn, what=what, value=value):
                exc = f.exception()
                if exc is not None:
                    print("command failed: %s %s %s: %r" % (fn, what, value, exc), flush=True)

            fut.add_done_callback(_log_command_failure)

        cli.on_connect = on_connect
        cli.on_message = on_message
        try:
            cli.connect(host, port, keepalive=60)
        except OSError as e:
            print("mqtt: connect to %s:%d failed (%s) — skipping MQTT (polling + web only)"
                  % (host, port, e), flush=True)
            return None
        cli.loop_start()
        return cli

    def _maybe_start_web(self, loop):
        """Start the embedded web UI if ``_web_port`` is set, returning the server or None.

        The web UI is OPTIONAL: a bind failure (e.g. the port is already taken) must never
        take down BLE polling + MQTT. On any startup error we log and continue headless.
        """
        port = getattr(self, "_web_port", None)
        if port is None:
            return None
        from . import web  # lazy: stdlib http.server only, keep serve.py's import light
        backend = ServeBackend(self, loop, read_only=getattr(self, "_read_only", False))
        try:
            httpd = web.serve_http(backend, _WEBUI_DIR, "0.0.0.0", port)
        except OSError as e:
            print("web UI failed to start on port %d (%s) — continuing without it"
                  % (port, e), flush=True)
            return None
        print("web UI on http://0.0.0.0:%d%s"
              % (port, "  (read-only)" if getattr(self, "_read_only", False) else ""), flush=True)
        return httpd

    def run(self):
        """Blocking daemon: connect MQTT (+ optional InfluxDB), then poll→fan-out
        on a timer while servicing HA commands from the MQTT network thread."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ble = asyncio.Lock()          # the van allows ONE connection; created on this loop

        # --- MQTT (Home Assistant) — optional; a missing broker must not stop polling+web ---
        self._mqtt = self._maybe_start_mqtt(loop)

        # --- InfluxDB (Grafana/buspi) ---
        if self.influx_enabled:
            token = os.environ.get("INFLUXDB_TOKEN")
            if token:
                from influxdb_client import InfluxDBClient
                from influxdb_client.client.write_api import SYNCHRONOUS
                url = os.environ.get("INFLUX_URL", "http://localhost:8086")
                org = os.environ.get("INFLUX_ORG", "home")
                client = InfluxDBClient(url=url, token=token, org=org)
                self._iw = client.write_api(write_options=SYNCHRONOUS)
                print("influx -> %s org=%s" % (url, org), flush=True)
            else:
                print("influx disabled: no INFLUXDB_TOKEN", flush=True)

        # --- web UI (replica app, embedded) — optional; must never crash the daemon ---
        self._httpd = self._maybe_start_web(loop)

        async def _forever():
            if self._persistent:
                asyncio.ensure_future(self._supervise_session())
            while True:
                try:
                    if self._persistent and self._live_session() is None:
                        await asyncio.sleep(2)        # supervisor is (re)connecting
                        continue
                    states = await self.poll()
                    print("polled %d functions" % len(states), flush=True)
                except ConnectionUnavailable as e:
                    print("poll skipped: %s" % e, flush=True)
                except Exception:
                    import traceback
                    print("poll error:", flush=True)
                    traceback.print_exc()
                await asyncio.sleep(self.interval)

        try:
            loop.run_until_complete(_forever())
        finally:
            if self._mqtt is not None:
                info = self._mqtt.publish(mqtt.availability_topic(), "offline", retain=True)
                try:
                    info.wait_for_publish(timeout=2)   # flush the retained "offline" before stopping
                except Exception:
                    pass
                self._mqtt.loop_stop()


def run(addr=None, *, interval=30.0, influx_enabled=True):
    """Module-level convenience: construct a Server and run it."""
    Server(addr, interval=interval, influx_enabled=influx_enabled).run()
