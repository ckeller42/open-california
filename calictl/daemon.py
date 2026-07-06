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
    """Back-compat shim: the daemon is now the unified `serve` process. MQTT
    connection params still come from the CLI args (mapped onto the env vars
    `serve` reads)."""
    import os
    from . import serve
    if broker:
        os.environ["MQTT_HOST"] = broker
    os.environ["MQTT_PORT"] = str(port)
    serve.Server(addr, interval=interval).run()
