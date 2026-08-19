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

from . import protocol, semantics, overrides, mqtt, influx, freshness, history, automation
from .device import CamperDevice, ConnectionUnavailable

# How long a lighting command waits for the unit's real 1502 Mode-4 notification before returning
# an optimistic "sent" (the lamp itself already reacted; this only bounds the UI confirm latency).
_FAST_CONFIRM_S = float(os.environ.get("CALICTL_FAST_CONFIRM_S", "1.2"))

# Hold the fast-path persistent BLE session only while the web UI is ACTIVE (a browser polling
# /api/state every ~2 s, or a recent command). The van allows ONE connection at a time, so a
# permanently-held session blocks the phone app; after this many seconds without UI activity the
# daemon RELEASES the session (dropping to brief cold polls) so the app can use the slot.
_UI_IDLE_S = float(os.environ.get("CALICTL_UI_IDLE_S", "25"))

# When a command arrives and no session is up yet, wait up to this long for the supervisor to bring
# one up (it's connecting due to the keep-warm nudge) and ride it — rather than racing it with a
# redundant cold connect that fights over the single BLE slot. Falls back to the cold path on timeout.
_SESSION_WAIT_S = float(os.environ.get("CALICTL_SESSION_WAIT_S", "6"))

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
        self._s._note_ui_activity()   # a browser is polling -> the UI is active; hold the session
        out = {}
        for fn, decoded in dict(self._s._last or {}).items():
            out[fn] = semantics.interpret(fn, decoded)
        semantics.apply_sw_corrections(out)   # e.g. DC-DC current +2 on AmbSwVersion 0409/0410
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
            # "auto" (activity-scoped) or "release" (user tapped Disconnect) — lets the UI show
            # "Disconnected" distinctly from "Van asleep".
            "session_mode": (getattr(self._s, "_session_mode", None) or "auto") if persistent else "off",
            # Auto camper mode: the toggle, whether it's actively re-asserting, and a one-time
            # give-up/stand-down notice the web UI shows as a toast.
            "auto_camper": {
                "enabled": bool(getattr(self._s, "_auto_camper", False)),
                "armed": bool(getattr(self._s, "_ac_owe_restore", False)),   # a restore is owed (engine shed camping)
                "notice": getattr(self._s, "_ac_notice", None),
            },
        }
        return out

    def set_session(self, action):
        """Bridge the web-UI connection toggle onto the daemon loop (like `command`)."""
        fut = asyncio.run_coroutine_threadsafe(self._s.set_session_mode(action), self._loop)
        return fut.result(timeout=15)

    def set_auto_camper(self, on):
        """Toggle the auto-camper feature (a persisted setting; actuation still respects read-only).
        Runs synchronously — it only flips a flag + persists, no BLE."""
        return {"ok": True, "auto_camper": self._s.set_auto_camper(on)}

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
        self._wake_session = None            # asyncio.Event(); nudges the supervisor to reconnect NOW
        self._last_ui_activity = None        # epoch of the last web-UI poll/command (session scoping)
        self._session_mode = None            # None = auto (activity-scoped); "release" = user disconnected
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
        # Durable per-poll OUTCOME log so telemetry gaps can be classified after the fact (van
        # deep-sleep vs our-side BLE/daemon failure vs Influx-write failure). One tiny JSONL line
        # per poll cycle; a poll gap with matching "asleep" rows = deep sleep, "ble_error" rows =
        # connection loss while awake, and NO rows at all = the daemon itself was down.
        self._outcomes_cache = os.environ.get(
            "CALICTL_OUTCOMES_CACHE", os.path.expanduser("~/.cache/calictl/poll_outcomes.jsonl"))
        self._appends = 0                   # appends since the last trim rewrite
        self._auto_camper = False           # default BEFORE _load_last so a persisted value survives
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
        # Auto camper mode — RESTORE camping after you park (the unit refuses camping-on while driving;
        # see automation.py + docs/business-logic/auto-camper-mode.md). The `_auto_camper` toggle is
        # loaded above (persists); the rest is runtime state for the restore state machine.
        self._ac_prev_ignition = None       # previous poll's ignition (edge detection)
        self._ac_prev_master = None         # previous poll's camping master (shed vs manual-off)
        self._ac_prev_usb = None            # previous poll's camping USB (captured as pre-drive config)
        self._ac_prev_lights = None         # previous poll's camping lights (captured as pre-drive config)
        self._ac_owe_restore = False        # do we owe a restore (engine shed camping that was on)?
        self._ac_pre_drive = None           # remembered {master,usb,lights} to restore after park
        self._ac_restore_until = None       # monotonic retry-window deadline while restoring, else None
        self._ac_fails = 0                  # restore attempts this cycle
        self._ac_notice = None              # {ts, msg} for a one-time log + web toast
        self._ac_min_soc = int(os.environ.get("CALICTL_AUTO_CAMPER_MIN_SOC",
                                              automation.AUTO_CAMPER_MIN_SOC))
        self._ac_max_fails = int(os.environ.get("CALICTL_AUTO_CAMPER_MAX_FAILS",
                                                automation.AUTO_CAMPER_MAX_FAILS))
        self._ac_window_s = float(os.environ.get("CALICTL_AUTO_CAMPER_WINDOW_S",
                                                automation.AUTO_CAMPER_WINDOW_S))
        # Camping/ignition transition OBSERVER (always on, NEVER actuates). Logs every change in
        # ignition + camping substates to the journal, and BURSTS the poll rate for a window after
        # an engine start so the shed is captured at fine resolution (the 30 s cadence aliases it —
        # ignition-on and camping-off land in the same sample). This is the evidence-gathering that
        # must precede enabling auto-camper: it characterises whether the unit sheds camping once or
        # flip-flops. See docs/business-logic/auto-camper-mode.md.
        self._obs_prev = None               # last observed {ignition, master_on, usb_charger, lights_on, enable}
        self._obs_ign_edge = None           # wall time of the last ignition 0->1 (for Δ-since-start)
        self._burst_until = None            # monotonic deadline while fast-polling, else None
        self._burst_interval = float(os.environ.get("CALICTL_OBSERVE_BURST_INTERVAL_S", "3"))
        self._burst_window_s = float(os.environ.get("CALICTL_OBSERVE_BURST_S", "180"))
        # Notification-driven observer: when the persistent session is up, the unit PUSHES camping
        # state on char 1202 (and ignition on 1004) — decompile-confirmed. Log those the instant they
        # arrive (full resolution, no poll wait). Reverse-map state-char UUID -> function name.
        self._push_char_to_fn = {str(f.state_char).lower(): name
                                 for name, f in self.funcs.items() if f.state_char}
        self._push_prev = {}                # last pushed value per tracked field (for change detection)

    def _load_last(self):
        """Load the persisted last-known decoded state (best-effort; missing/corrupt -> empty)."""
        try:
            with open(self._state_cache) as f:
                blob = json.load(f)
            self._last = {fn: v for fn, v in (blob.get("last") or {}).items() if fn in self.funcs}
            self._last_ok_ts = blob.get("ts")
            self._water_good = blob.get("water_good")      # last plausible water survives restarts
            self._water_stale_since = blob.get("water_stale_since")
            self._auto_camper = bool(blob.get("auto_camper", False))   # toggle survives restarts
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
                           "water_stale_since": self._water_stale_since,
                           "auto_camper": self._auto_camper}, f)
            os.replace(tmp, self._state_cache)
        except OSError:
            pass

    def _record_outcome(self, outcome, detail=""):
        """Append this poll cycle's OUTCOME to the durable log so a later gap can be classified.
        Never raises. ``outcome`` is one of ok / asleep / ble_error / error; the session state +
        consecutive-fail count are recorded alongside so `tools.analyze_battery --diagnose` can tell
        van deep-sleep (asleep) from an our-side connection loss (ble_error) from a dead daemon
        (no rows at all)."""
        rec = {"ts": round(time.time(), 1), "outcome": outcome,
               "session": self._session_state, "fails": self._backoff_fails}
        if detail:
            rec["detail"] = str(detail)[:120]
        history.append_jsonl(self._outcomes_cache, rec)

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

    def _note_ui_activity(self):
        """Mark the web UI as active NOW (a browser poll or a command). While active, the daemon
        holds the fast-path persistent session; going idle releases it (see ``_ui_active``). Called
        from both the web thread (ServeBackend) and the loop (on_command) — a plain float write is
        atomic under the GIL."""
        self._last_ui_activity = time.time()

    def _ui_active(self):
        """True while the web UI has been used within ``_UI_IDLE_S`` — the window in which the
        daemon keeps the persistent session up. Idle beyond it -> release the slot for the app.
        A manual "release" (the user tapped Disconnect) forces inactive regardless of polling, so
        the slot goes to the phone app until they Connect again or issue a command."""
        if self._session_mode == "release":
            return False
        a = self._last_ui_activity
        return a is not None and (time.time() - a) < _UI_IDLE_S

    async def set_session_mode(self, action):
        """Explicit Connect/Disconnect from the web UI (the connection toggle).

        ``connect`` clears any manual release, marks the UI active, and nudges the supervisor to
        bring the session up NOW (warm the fast path before the first control). ``disconnect`` sets
        the manual release and wakes the supervisor, which — seeing the UI 'inactive' — aclose()s
        the session and hands the single BLE slot back to the phone app. Returns the mode + the
        current session state for the UI.
        """
        if action == "connect":
            self._session_mode = None
            self._note_ui_activity()
            self._nudge_session()
        elif action == "disconnect":
            self._session_mode = "release"
            if self._wake_session is not None:
                self._wake_session.set()   # wake the supervisor to drop the session immediately
        return {"ok": True, "mode": self._session_mode or "auto", "session": self._session_state}

    def _nudge_session(self):
        """Keep-warm: a command wants to actuate. If no session is up, reset the backoff the
        supervisor grew while the van slept and wake it to reconnect NOW — the user reaching for a
        control means the van is likely awake, so the session (and the fast path) should come up
        promptly instead of after a long backoff. No-op when a session is already live."""
        if (self._persistent and self._wake_session is not None
                and self._live_session() is None):
            self._backoff_fails = 0
            self._wake_session.set()

    def set_auto_camper(self, on):
        """Toggle the auto-camper feature (persisted). Disabling clears any owed restore. Returns the
        new state. See automation.auto_camper_restore_decide + docs/business-logic/auto-camper-mode.md."""
        self._auto_camper = bool(on)
        if not self._auto_camper:
            self._ac_owe_restore, self._ac_restore_until, self._ac_fails = False, None, 0
        self._save_last()
        print("auto-camper: %s" % ("ENABLED" if self._auto_camper else "disabled"), flush=True)
        return self._auto_camper

    @staticmethod
    def _obs_fmt(v):
        """0/1 for a bool, '-' for missing, else the raw value — compact for one journal line."""
        if v is None:
            return "-"
        if isinstance(v, bool):
            return "1" if v else "0"
        return str(v)

    def _observe_transitions(self, states):
        """PASSIVE evidence-gathering (never actuates): log every change in ignition + camping
        substates to the journal, and start a fast-poll burst after an engine start so the shed is
        captured at fine resolution. Runs every poll regardless of the auto-camper toggle.

        Why: at the 30 s cadence, engine-start and the camping shed alias into one sample, so we
        cannot tell a single clean shed from a flip-flop. Bursting to ~3 s around the engine edge
        de-aliases it (and densifies the InfluxDB series there for the analyzer). Transitions only,
        so the journal stays readable. See tools/analyze_camping.py + auto-camper-mode.md.

        Two "engine on" signals are tracked, because they differ: ``ignition`` is Terminal-15 (the
        ignition switch — can be on with the engine NOT running, e.g. accessory) while
        ``dcdc_charging`` is the DC-DC converter active = alternator feeding the leisure battery =
        the engine actually RUNNING. Recording both lets the analyzer see which one the camping shed
        follows. An engine-start burst triggers on a rising edge of EITHER."""
        veh = states.get("vehicle") or {}
        camp = states.get("campingmode") or {}
        e = states.get("energy") or {}
        cur = {"ignition": veh.get("ignition_on"),            # Terminal-15 (ignition switch)
               "dcdc_charging": e.get("dcdc_charging"),       # DC-DC active = alternator = engine RUNNING
               "master_on": camp.get("master_on"),
               "usb_charger": camp.get("usb_charger"),
               "lights_on": camp.get("lights_on"),
               "enable": camp.get("enable")}                  # camp.enable = terminal-15 as the camping fn sees it
        now = time.time()
        prev, self._obs_prev = self._obs_prev, cur
        if prev is None:
            return                                # first poll: baseline only, nothing to compare
        engine_rise = ((bool(cur["ignition"]) and not bool(prev["ignition"]))
                       or (bool(cur["dcdc_charging"]) and not bool(prev["dcdc_charging"])))
        if engine_rise:                            # engine start on EITHER signal -> stamp + fast-poll burst
            self._obs_ign_edge = now
            self._burst_until = time.monotonic() + self._burst_window_s
        changed = [k for k in cur if cur[k] != prev[k]]
        if not changed:
            return
        dt = "%.0f" % (now - self._obs_ign_edge) if self._obs_ign_edge else "-"
        parts = ", ".join("%s %s->%s" % (k, self._obs_fmt(prev[k]), self._obs_fmt(cur[k])) for k in changed)
        print("camping-watch: %s (ign=%s dcdc=%s dcdc_A=%s batt2_A=%s dt_eng=%ss soc=%s)"
              % (parts, self._obs_fmt(cur["ignition"]), self._obs_fmt(cur["dcdc_charging"]),
                 self._obs_fmt(e.get("dcdc_current")), self._obs_fmt(e.get("batt2_current")),
                 dt, self._obs_fmt(e.get("soc2_pct"))), flush=True)

    # camping/ignition fields we watch on a push, per function (mirrors _observe_transitions).
    _PUSH_FIELDS = {"campingmode": ("master_on", "usb_charger", "lights_on", "enable"),
                    "vehicle": ("ignition_on",)}

    def _on_ble_push(self, uuid, data):
        """Fired IN THE BLE LOOP the instant a status char pushes a notification (persistent session
        only). Logs camping/ignition changes at full resolution — this both CONFIRMS the unit's push
        channel on THIS unit (decompile said 1202 pushes; never yet observed here) and catches toggles
        between polls. Passive, sync, best-effort: never actuates, never raises into bleak. The 30 s
        poll observer (_observe_transitions) remains the reliable baseline; this is the fast overlay,
        tagged `camping-push` so the two channels are distinguishable in the journal."""
        fn = self._push_char_to_fn.get(uuid)
        fields = self._PUSH_FIELDS.get(fn)
        if not fields:
            return                            # not a char we watch (only campingmode / vehicle)
        interp = semantics.interpret(fn, protocol.decode(self.funcs[fn], data))
        cur = {k: interp.get(k) for k in fields}
        changed = {k: (self._push_prev[k], v) for k, v in cur.items()
                   if k in self._push_prev and self._push_prev[k] != v}
        self._push_prev.update(cur)
        if changed:
            parts = ", ".join("%s %s->%s" % (k, self._obs_fmt(o), self._obs_fmt(n))
                              for k, (o, n) in changed.items())
            print("camping-push[%s]: %s" % (fn, parts), flush=True)

    def _ac_track_prev(self, ign, master, usb, lights):
        """Roll the auto-camper previous-state snapshot (edge detection for the next poll)."""
        self._ac_prev_ignition, self._ac_prev_master = ign, master
        self._ac_prev_usb, self._ac_prev_lights = usb, lights

    async def _auto_camper_step(self, states):
        """RESTORE camping mode after you park. The unit refuses camping-on while driving (firmware
        stationary gate), so this remembers the pre-drive config when the engine sheds it, then
        re-enables it on the ignition FALLING edge (parked), gated on battery health and bounded
        retries (never fights power-saving, never loops). Runs each poll AFTER the read (the BLE lock
        is free), actuates via on_command under that lock. Never raises out of the poll."""
        veh = states.get("vehicle") or {}
        camp = states.get("campingmode") or {}
        e = states.get("energy") or {}
        ign = veh.get("ignition_on")
        master = camp.get("master_on")
        usb = camp.get("usb_charger")
        lights = camp.get("lights_on")
        if not self._auto_camper:
            self._ac_track_prev(ign, master, usb, lights)   # keep edges tracked so a mid-cycle enable won't misfire
            return
        soc = e.get("soc2_pct")
        warning = bool(e.get("sleep_warning") or e.get("warning_active")
                       or (e.get("warning_level") or 0) >= 1)
        d = automation.auto_camper_restore_decide(
            ignition_on=ign, prev_ignition=self._ac_prev_ignition,
            master_on=master, prev_master=self._ac_prev_master,
            usb_on=usb, lights_on=lights, prev_usb=self._ac_prev_usb, prev_lights=self._ac_prev_lights,
            soc=soc, warning=warning, now=time.monotonic(),
            owe_restore=self._ac_owe_restore, pre_drive=self._ac_pre_drive,
            restore_until=self._ac_restore_until, fails=self._ac_fails,
            min_soc=self._ac_min_soc, max_fails=self._ac_max_fails, window_s=self._ac_window_s)
        armed_before = self._ac_owe_restore
        self._ac_owe_restore = d["owe_restore"]
        self._ac_pre_drive = d["pre_drive"]
        self._ac_restore_until = d["restore_until"]
        self._ac_fails = d["fails"]
        self._ac_track_prev(d["prev_ignition"], d["prev_master"], d["prev_usb"], d["prev_lights"])

        # Classify the event for the journal line. "idle" = nothing notable -> silent (values are in
        # InfluxDB regardless). armed = engine shed camping we owe back; restoring = a write attempt.
        if d["owe_restore"] and not armed_before:
            event = "armed_engine_shed"
        elif d["restored"]:
            event = "restored"
        elif d["actuate"]:
            event = "restoring"
        elif d["notice"]:
            event = "stood_down" if "battery" in d["notice"] else "gave_up"
        else:
            event = "idle"
        if event != "idle":
            print("auto-camper[%s]: %s (ignition=%s camping=%s soc=%s warn=%s fails=%d owe=%s)"
                  % (event, d["notice"] or event.replace("_", " "), ign, master, soc, warning,
                     self._ac_fails, d["owe_restore"]), flush=True)
        if d["notice"]:
            self._ac_notice = {"ts": round(time.time(), 1), "msg": d["notice"]}

        if d["actuate"]:
            cfg = d["restore_config"] or {}
            if self._read_only:
                print("auto-camper: parked with a restore owed but writes are read-only; not restoring",
                      flush=True)
                self._ac_owe_restore, self._ac_restore_until = False, None   # can't act -> don't spin
                return
            try:
                await self.on_command("campingmode", "master", "on")        # master first (gates USB+lights)
                if cfg.get("usb"):
                    await self.on_command("campingmode", "usb", "on")
                if cfg.get("lights"):
                    await self.on_command("campingmode", "lights", "on")
            except Exception as ex:           # BLE hiccup etc. -> counts as a failed attempt via fails
                print("auto-camper: restore actuation failed: %r" % ex, flush=True)

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
        semantics.apply_sw_corrections(states)   # e.g. DC-DC current +2 on AmbSwVersion 0409/0410
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
            try:
                self._observe_transitions(states)      # PASSIVE: log camping/ignition changes + burst
            except Exception:
                import traceback
                print("camping-watch error:", flush=True)
                traceback.print_exc()
            try:
                await self._auto_camper_step(states)   # re-enable camper+USB after an engine start
            except Exception:
                import traceback
                print("auto-camper step error:", flush=True)
                traceback.print_exc()
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
        sess = device.PersistentSession(self.dev, on_push=self._on_ble_push)
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
        """Hold the persistent session while the web UI is ACTIVE; release the BLE slot when it goes
        idle (so the phone app can connect); back off while the van is unreachable."""
        while True:
            if not self._ui_active():
                # web UI idle -> release the slot for the phone app; the daemon falls back to brief
                # cold polls, which coexist with the app far better than a permanently-held link.
                if self._session is not None:
                    async with self._ble:
                        await self._session.aclose()
                    self._session = None
                    print("persistent session released (web UI idle)", flush=True)
                self._session_state = "off"
                # wake early when a command/poll marks activity, else re-check each interval
                waiter = asyncio.ensure_future(self._wake_session.wait())
                try:
                    await asyncio.wait({waiter}, timeout=self.interval)
                finally:
                    waiter.cancel()
                    self._wake_session.clear()
                continue
            if self._session is not None and self._session.is_up:
                await asyncio.sleep(self.interval)
                continue
            async with self._ble:
                ok = await self._session_connect_once()
            if not ok:
                idx = min(self._backoff_fails, len(self.SESSION_BACKOFF)) - 1
                # sleep the backoff, but wake IMMEDIATELY if a command nudges us (keep-warm): the
                # user reaching for a control means the van is likely awake now, so don't make them
                # wait out a backoff that grew during a long deep-sleep.
                sleeper = asyncio.ensure_future(asyncio.sleep(self.SESSION_BACKOFF[max(0, idx)]))
                waker = asyncio.ensure_future(self._wake_session.wait())
                try:
                    await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    sleeper.cancel()
                    waker.cancel()
                    self._wake_session.clear()

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
        self._session_mode = None  # a command means intent to control -> override any manual Disconnect
        self._note_ui_activity()   # a command counts as active use -> hold the session
        self._nudge_session()      # keep-warm: bring the fast-path session up now if it isn't
        # Prefer the fast persistent session over a redundant cold connect: the nudge just told the
        # supervisor to connect, so wait briefly (OUTSIDE the _ble lock, so the supervisor can take
        # it) for the session to come up rather than racing it cold over the single slot.
        if self._persistent and self._live_session() is None:
            deadline = time.monotonic() + _SESSION_WAIT_S
            while time.monotonic() < deadline and self._live_session() is None:
                await asyncio.sleep(0.2)
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
            # Lighting actuates from a bare SET on an awake unit (photon-verified 2026-08-16), so
            # the fast path drops the two things that made it slow: the REQUEST_CONFIG preamble
            # (not an actuation gate — the app's E() writes DIRECT and never sends it) and the
            # blocking echo-readback. The lamp reacts in ~0.3 s; confirmation comes from the real
            # 1502 Mode-4 notification, not the write-through echo. cooler/roof keep the armed +
            # verified path (their heartbeat gate, issue #2, is untouched).
            is_light = function == "lighting"
            pre = None if is_light else control.preamble_for(function)
            target = sess if sess is not None else self.dev
            # Snapshot the 1502 notification BEFORE the write: the unit's Mode-4 ramp push arrives
            # within the ~0.3 s write window, so a snapshot taken afterwards would already contain it
            # and look stale -> the confirm would miss it.
            before = None
            if is_light and getattr(sess, "_notif", None) is not None:
                before = sess._notif.get(str(self.funcs[function].state_char).lower())
            post = await target.actuate(self.funcs[function], frame,
                                        verify=not is_light,
                                        follow=control.commit_for(function), pre=pre)
            if is_light:
                return await self._confirm_lighting(sess, function, what, value, before)
            if post is None:
                return None
            self._last = {**self._last, function: post}   # atomic rebind (web thread reads unlocked)
            interp = semantics.interpret(function, post)
            _, got, want = _set_check(function, what, value, interp, post)
            if got is None and want is None:   # no table entry for this target
                return None
            return got == want

    async def _confirm_lighting(self, sess, function, what, value, before):
        """Confirm a fast lighting write from the unit's real 1502 Mode-4 notification.

        The SET + commit already went out (bare frame, no arm, no preamble); the lamp reacts in
        ~0.3 s. Wait briefly (``CALICTL_FAST_CONFIRM_S``) for a state-char push newer than
        ``before`` (snapshotted before the write) — the Mode-4 ramp notification carries the REAL
        brightness, unlike the write-through echo readback — update the served cache from it and
        report applied-ness. Returns ``True`` on a matching notification, else ``None`` (optimistic
        "sent"); never a false negative for lighting. No live session or no push -> ``None`` (the
        cold path can't cheaply confirm; the next poll reconciles).
        """
        from .cli import _set_check   # lazy
        f = self.funcs[function]
        notif = getattr(sess, "_notif", None)
        if notif is None:
            return None
        key = str(f.state_char).lower()
        deadline = time.monotonic() + _FAST_CONFIRM_S
        while time.monotonic() < deadline:
            cur = notif.get(key)
            if cur is not None and cur is not before:          # a fresh push arrived
                decoded = protocol.decode(f, cur)
                self._last = {**self._last, function: decoded}  # atomic rebind (web thread reads unlocked)
                interp = semantics.interpret(function, decoded)
                _, got, want = _set_check(function, what, value, interp, decoded)
                return True if (got is not None and got == want) else None
            await asyncio.sleep(0.05)
        return None

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
        self._wake_session = asyncio.Event()   # a command can wake the session supervisor early

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
                    if self._persistent and self._session_state == "connecting":
                        await asyncio.sleep(2)        # supervisor is mid-connect; don't race it cold
                        continue
                    states = await self.poll()
                    print("polled %d functions" % len(states), flush=True)
                    self._record_outcome("ok")
                except ConnectionUnavailable as e:
                    print("poll skipped: %s" % e, flush=True)
                    # van unreachable: "asleep" once the supervisor's backoff hit the cap (deep
                    # sleep), else a transient connection loss while it may still be awake.
                    self._record_outcome("asleep" if self._session_state == "asleep" else "ble_error", e)
                except Exception as e:
                    import traceback
                    print("poll error:", flush=True)
                    traceback.print_exc()
                    self._record_outcome("error", e)
                # Fast-poll burst: after an engine-start edge the observer sets _burst_until; poll at
                # the short interval until it expires, so the camping shed is captured finely.
                nap = self.interval
                if self._burst_until is not None:
                    if time.monotonic() < self._burst_until:
                        nap = min(nap, self._burst_interval)
                    else:
                        self._burst_until = None       # window elapsed -> back to the normal cadence
                await asyncio.sleep(nap)

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
