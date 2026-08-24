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

from . import mqtt, overrides, protocol, semantics
from .device import CamperDevice

# Alert enums are strings (dropped by Influx), but we still want them charted in Grafana.
# Emit a numeric `<key>_code` for these curated fields so a state-timeline can map code->label.
# Key = flattened field name; 0 = ok/none (also for None/unknown) so the series stays continuous.
# Codes match the semantics enums: cooler `fault` (semantics.cooler) + roof `alert` (semantics.roof).
_ENUM_CODES: dict[str, dict[str, int]] = {
    "fault": {"error": 1, "emergency": 2, "door_open": 3},
    "alert": {"child_lock": 1, "error": 2, "sensor_error": 3,
              "emergency_locked": 4, "not_possible": 5, "low_battery": 6},
}


def numeric_fields(interp: dict) -> dict[str, float]:
    """Flatten interpretation to InfluxDB fields: everything numeric/boolean is
    written as a float (bool -> 1.0/0.0); strings/None/lists are dropped, except
    list-valued keys become a `<key>_count` and curated alert enums (see
    :data:`_ENUM_CODES`) become a numeric `<key>_code` (0 = ok/none)."""
    out: dict[str, float] = {}
    for k, v in mqtt.flatten(interp).items():
        if k in _ENUM_CODES:                       # alert enum -> code (None/unknown -> 0 = ok)
            out[k + "_code"] = float(_ENUM_CODES[k].get(v, 0))
        elif isinstance(v, bool):
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


def field_series(field: str, function: str, *, days: float = 7.0, every: str = "1h", fn: str = "mean"):
    """Read a recent time series of one ``camper`` field from InfluxDB.

    A general Influx read helper (not used by the web UI, which is Influx-free). Lazy
    `influxdb_client` (only in solix-env); returns ``[]`` when Influx is unavailable or the
    token is unset, so callers degrade gracefully rather than raise.

    :param field: the flattened field key (e.g. ``"fresh_liters"``).
    :param function: the ``function`` tag (e.g. ``"water"``).
    :param days: look-back window in days.
    :param every: `aggregateWindow` bucket, to bound the row count.
    :param fn: aggregate function — ``"mean"`` for charts, ``"last"`` for reliability analysis
        (``last`` keeps the ACTUAL sampled value per window, so a frozen/latched reading isn't
        smoothed away; ``mean`` would hide freezes).
    :returns: ``[(epoch_seconds, value)]`` ascending, or ``[]``.
    """
    token = os.environ.get("INFLUXDB_TOKEN")
    if not token:
        return []
    if fn not in ("mean", "last", "max", "min", "first"):
        raise ValueError("unsupported aggregate fn %r" % fn)
    url = os.environ.get("INFLUX_URL", "http://localhost:8086")
    org = os.environ.get("INFLUX_ORG", "home")
    bucket = os.environ.get("INFLUX_BUCKET", "buspi")
    flux = (
        'from(bucket:"%s") |> range(start:-%gd) '
        '|> filter(fn:(r)=>r._measurement=="camper" and r.function=="%s" and r._field=="%s") '
        '|> aggregateWindow(every:%s, fn:%s, createEmpty:false) |> keep(columns:["_time","_value"])'
        % (bucket, days, function, field, every, fn)
    )
    try:
        from influxdb_client import InfluxDBClient
        with InfluxDBClient(url=url, token=token, org=org) as client:
            tables = client.query_api().query(flux, org=org)
    except Exception as e:   # network down, deps absent, bad query -> no series, never crash
        print("influx field_series(%s) failed: %r" % (field, e), flush=True)
        return []
    out = []
    for table in tables:
        for rec in table.records:
            v = rec.get_value()
            if v is not None:
                out.append((int(rec.get_time().timestamp()), float(v)))
    out.sort()
    return out


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
