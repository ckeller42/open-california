"""Poll the Camper Unit and bridge it to Home Assistant over MQTT.

    calictl daemon --broker 192.168.1.10 [--interval 30]
    calictl daemon --dry-run          # print discovery + one poll, no broker

Read-only by default; the fridge switch is the one control entity and only acts
on an explicit HA command message. `paho.mqtt` is imported lazily.
"""
from __future__ import annotations

import asyncio
import json
import time

from . import protocol, semantics, overrides, mqtt
from .device import CamperDevice, ConnectionUnavailable


def _load():
    funcs = protocol.load(); overrides.apply(funcs); return funcs


async def _poll_once(funcs, dev) -> dict[str, dict]:
    raw = await dev.read_all(funcs)
    return {name: semantics.interpret(name, protocol.decode(funcs[name], data))
            for name, data in raw.items()}


async def dry_run():
    """Print the discovery configs and one live (or unreachable) poll."""
    funcs = _load()
    print("# --- HA discovery configs ---")
    for topic, payload in mqtt.render_discovery().items():
        print(topic, "=>", json.dumps(payload))
    print("\n# --- one poll ---")
    dev = CamperDevice()
    try:
        states = await _poll_once(funcs, dev)
    except ConnectionUnavailable as e:
        print("(device unreachable: %s)" % e); return
    for fn, interp in states.items():
        topic, payload = mqtt.render_state(fn, interp)
        print(topic, "=>", payload)


def run(broker: str, port: int = 1883, interval: float = 30.0, addr: str | None = None):
    """Blocking daemon: publish discovery, then poll→publish on a timer and
    service HA control commands."""
    import paho.mqtt.client as mqtt_client  # lazy

    funcs = _load()
    dev = CamperDevice(addr) if addr else CamperDevice()
    cmd_map = mqtt.command_topics()
    loop = asyncio.new_event_loop()

    cli = mqtt_client.Client()
    cli.will_set("%s/status" % mqtt.BASE, "offline", retain=True)

    def on_connect(c, u, flags, rc):
        for topic, payload in mqtt.render_discovery().items():
            c.publish(topic, json.dumps(payload), retain=True)
        c.publish("%s/status" % mqtt.BASE, "online", retain=True)
        for topic in cmd_map:
            c.subscribe(topic)
        print("connected to %s:%d, %d entities published" % (broker, port, len(mqtt.render_discovery())))

    def on_message(c, u, msg):
        fn, what = cmd_map.get(msg.topic, (None, None))
        if not fn:
            return
        value = msg.payload.decode().strip()
        print("command: %s %s %s" % (fn, what, value))
        try:
            post = loop.run_until_complete(_apply_command(funcs, dev, fn, what, value))
            if post is not None:
                topic, payload = mqtt.render_state(fn, semantics.interpret(fn, post))
                c.publish(topic, payload)
        except Exception as e:
            print("  command failed: %s" % e)

    cli.on_connect = on_connect
    cli.on_message = on_message
    cli.connect(broker, port, keepalive=60)
    cli.loop_start()
    try:
        while True:
            try:
                states = loop.run_until_complete(_poll_once(funcs, dev))
                for fn, interp in states.items():
                    topic, payload = mqtt.render_state(fn, interp)
                    cli.publish(topic, payload)
            except ConnectionUnavailable as e:
                print("poll skipped: %s" % e)
            time.sleep(interval)
    finally:
        cli.publish("%s/status" % mqtt.BASE, "offline", retain=True)
        cli.loop_stop()


async def _apply_command(funcs, dev, fn, what, value):
    """Execute an HA control command; return the post-write decoded state."""
    if fn == "cooler":
        f = funcs["cooler"]
        cur = protocol.decode(f, await dev.read(f))
        from .cli import _cooler_values
        if what == "power":
            changes = {"State": 1 if value == "on" else 0}
        elif what == "level":
            changes = {"Level": max(1, min(5, int(value)))}
        else:
            return None
        frame = protocol.encode(f, _cooler_values(cur, **changes), frame_bytes=6)
        return await dev.write_control(f, frame, verify=True)
    return None
