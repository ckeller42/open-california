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

from . import protocol, semantics, overrides, mqtt, influx
from .device import CamperDevice, ConnectionUnavailable


def installed_from(states: dict) -> set:
    """Functions whose interpreted state reports `installed` truthy."""
    return {fn for fn, i in states.items() if i.get("installed")}


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
    def __init__(self, addr=None, *, interval=30.0, influx_enabled=True):
        self.funcs = protocol.load(); overrides.apply(self.funcs)
        self.dev = CamperDevice(addr) if addr else CamperDevice()
        self.interval = interval
        self.influx_enabled = influx_enabled
        self._ble = None                    # asyncio.Lock(); created inside run()'s loop
        self._published = set()             # functions whose discovery is sent
        self._last = {}                     # function -> last DECODED state (for commands)
        self._mqtt = None
        self._iw = None                     # influx write_api
        self._loop = None

    async def poll(self):
        # One BLE read of every function under the lock; cache the DECODED state
        # (on_command builds full-packet control frames from it) and derive the
        # INTERPRETED state for MQTT + InfluxDB.
        async with self._ble:
            raw = await self.dev.read_all(self.funcs)
        states = {}
        for fn, data in raw.items():
            decoded = protocol.decode(self.funcs[fn], data)
            self._last[fn] = decoded
            states[fn] = semantics.interpret(fn, decoded)
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
            self._iw.write(bucket=os.environ.get("INFLUX_BUCKET", "buspi"),
                           org=os.environ.get("INFLUX_ORG", "home"),
                           record=influx.points_for(states))
        return states

    async def on_command(self, function, what, value):
        from . import control  # lazy
        # actuate holds a 1003 liveness heartbeat across the write (arms actuation,
        # issue #2); the same self._ble lock keeps it the single BLE owner.
        async with self._ble:
            last = self._last.get(function)
            if not last:
                # cold cache -> a full-packet frame built from field DEFAULTS would carry
                # e.g. cooler State=1 (fridge ON) or lighting ProfileNumber=0 (silent no-op).
                # Read the live state first so untargeted fields carry the real values.
                try:
                    last = protocol.decode(self.funcs[function],
                                           await self.dev.read(self.funcs[function]))
                    self._last[function] = last
                except ConnectionUnavailable as e:
                    print("command %s skipped (no state read): %s" % (function, e), flush=True)
                    return
            frame = control.build(self.funcs, function, what, value, last)
            if frame is None:
                return
            await self.dev.actuate(self.funcs[function], frame, verify=False)

    def run(self):
        """Blocking daemon: connect MQTT (+ optional InfluxDB), then poll→fan-out
        on a timer while servicing HA commands from the MQTT network thread."""
        import paho.mqtt.client as mqtt_client  # lazy

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ble = asyncio.Lock()          # the van allows ONE connection; created on this loop

        # --- MQTT (Home Assistant) ---
        host = os.environ.get("MQTT_HOST", "localhost")
        port = int(os.environ.get("MQTT_PORT", "1883"))
        user = os.environ.get("MQTT_USER")
        password = os.environ.get("MQTT_PASSWORD")
        # command_topics() is added by a later task; tolerate its absence.
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
        cli.connect(host, port, keepalive=60)
        cli.loop_start()
        self._mqtt = cli

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

        async def _forever():
            while True:
                try:
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
            cli.publish(mqtt.availability_topic(), "offline", retain=True)
            cli.loop_stop()


def run(addr=None, *, interval=30.0, influx_enabled=True):
    """Module-level convenience: construct a Server and run it."""
    Server(addr, interval=interval, influx_enabled=influx_enabled).run()
