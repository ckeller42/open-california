"""Write Camper Unit telemetry to InfluxDB (for the existing buspi Grafana stack).

Matches the pattern of the other buspi readers (govee_reader.py, vedirect):
one measurement `camper`, tagged by `function`, numeric/boolean fields from the
interpreted status. Config via env (EnvironmentFile=/etc/buspi/secrets.env):

  INFLUXDB_TOKEN  write token (required)
  INFLUX_URL      default http://localhost:8086
  INFLUX_ORG      default home
  INFLUX_BUCKET   default buspi
  POLL_INTERVAL   seconds between writes (default 60)

`influxdb_client` is imported lazily (only present in solix-env, which also has
modern bleak + bleak-retry-connector — run calictl from there for this).
"""
from __future__ import annotations

import asyncio
import os

from . import protocol, semantics, overrides, mqtt
from .device import CamperDevice, ConnectionUnavailable


def numeric_fields(interp: dict) -> dict[str, float]:
    """Flatten interpretation to InfluxDB fields: everything numeric/boolean is
    written as a float (bool -> 1.0/0.0); strings/None/lists are dropped, except
    list-valued keys become a `<key>_count`."""
    out: dict[str, float] = {}
    for k, v in mqtt.flatten(interp).items():
        if isinstance(v, bool):
            out[k] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            out[k] = float(v)
        elif isinstance(v, (list, tuple)):
            out[k + "_count"] = float(len(v))
    return out


def points_for(states: dict) -> list:
    """Build InfluxDB Points from interpreted states (one per function with any
    numeric field). Shared by the standalone `run` path and `serve`."""
    from influxdb_client import Point
    pts = []
    for fn, interp in states.items():
        fields = numeric_fields(interp)
        if not fields:
            continue
        p = Point("camper").tag("function", fn).tag("vehicle", "vwcamper")
        for k, v in fields.items():
            p.field(k, v)
        pts.append(p)
    return pts


def build_points(states: dict[str, dict]):
    return points_for(states)


async def poll_states(funcs, dev) -> dict:
    """One BLE session: read every function and return {function: interpreted}."""
    raw = await dev.read_all(funcs)
    return {name: semantics.interpret(name, protocol.decode(funcs[name], data))
            for name, data in raw.items()}


async def _poll(funcs, dev) -> dict[str, dict]:
    return await poll_states(funcs, dev)


def write_once(addr: str | None = None) -> int:
    """One poll+write cycle (for testing). Returns points written."""
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS
    url = os.environ.get("INFLUX_URL", "http://localhost:8086")
    org = os.environ.get("INFLUX_ORG", "home")
    bucket = os.environ.get("INFLUX_BUCKET", "buspi")
    token = os.environ.get("INFLUXDB_TOKEN")
    if not token:   # mirror serve.run()'s handling instead of a raw KeyError
        print("influx disabled: no INFLUXDB_TOKEN")
        return 0
    funcs = protocol.load(); overrides.apply(funcs)
    dev = CamperDevice(addr) if addr else CamperDevice()
    states = asyncio.run(_poll(funcs, dev))
    pts = build_points(states)
    with InfluxDBClient(url=url, token=token, org=org) as client:
        client.write_api(write_options=SYNCHRONOUS).write(bucket=bucket, org=org, record=pts)
    print("wrote %d points to %s/%s" % (len(pts), bucket, org))
    return len(pts)
