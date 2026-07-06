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
import time

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


def build_points(states: dict[str, dict]):
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


async def _poll(funcs, dev) -> dict[str, dict]:
    raw = await dev.read_all(funcs)
    return {name: semantics.interpret(name, protocol.decode(funcs[name], data))
            for name, data in raw.items()}


def run(interval: float | None = None, addr: str | None = None):
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS

    url = os.environ.get("INFLUX_URL", "http://localhost:8086")
    org = os.environ.get("INFLUX_ORG", "home")
    bucket = os.environ.get("INFLUX_BUCKET", "buspi")
    token = os.environ["INFLUXDB_TOKEN"]
    interval = interval if interval is not None else float(os.environ.get("POLL_INTERVAL", "60"))

    funcs = protocol.load(); overrides.apply(funcs)
    dev = CamperDevice(addr) if addr else CamperDevice()
    client = InfluxDBClient(url=url, token=token, org=org)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    loop = asyncio.new_event_loop()

    print("calictl influx -> %s bucket=%s org=%s interval=%.0fs" % (url, bucket, org, interval))
    while True:
        try:
            states = loop.run_until_complete(_poll(funcs, dev))
            pts = build_points(states)
            write_api.write(bucket=bucket, org=org, record=pts)
            print("wrote %d points (%d functions)" % (len(pts), len(states)), flush=True)
        except ConnectionUnavailable as e:
            print("poll skipped: %s" % e, flush=True)
        except Exception as e:
            print("write error: %s" % e, flush=True)
        time.sleep(interval)


def write_once(addr: str | None = None) -> int:
    """One poll+write cycle (for testing). Returns points written."""
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS
    url = os.environ.get("INFLUX_URL", "http://localhost:8086")
    org = os.environ.get("INFLUX_ORG", "home")
    bucket = os.environ.get("INFLUX_BUCKET", "buspi")
    token = os.environ["INFLUXDB_TOKEN"]
    funcs = protocol.load(); overrides.apply(funcs)
    dev = CamperDevice(addr) if addr else CamperDevice()
    states = asyncio.run(_poll(funcs, dev))
    pts = build_points(states)
    client = InfluxDBClient(url=url, token=token, org=org)
    client.write_api(write_options=SYNCHRONOUS).write(bucket=bucket, org=org, record=pts)
    print("wrote %d points to %s/%s" % (len(pts), bucket, org))
    return len(pts)
