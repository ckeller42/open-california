#!/usr/bin/env python3
"""Push a Grafana dashboard JSON to any Grafana via its HTTP API.

Works for the local Pi Grafana (basic auth) and Grafana Cloud (API token). The
dashboard file is kept in import format (a `${DS_INFLUXDB}` datasource template +
`__inputs`); this resolves that against the target's InfluxDB datasource and
POSTs it, so the same repo file deploys everywhere.

    # local Pi
    python push_dashboard.py --url http://localhost:3000 \
        --user admin --password "$GRAFANA_PW" --dashboard camper-dashboard.json

    # Grafana Cloud
    python push_dashboard.py --url https://<stack>.grafana.net \
        --token "$GRAFANA_CLOUD_TOKEN" --dashboard camper-dashboard.json

Env fallbacks: GRAFANA_URL, GRAFANA_USER, GRAFANA_PASSWORD, GRAFANA_TOKEN.
Stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request


def _req(url, method="GET", token=None, user=None, password=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    elif user is not None:
        headers["Authorization"] = "Basic " + base64.b64encode(
            ("%s:%s" % (user, password)).encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


def find_influx_datasource(base, **auth) -> str:
    ds = _req(base + "/api/datasources", **auth)
    influx = [d for d in ds if d.get("type") == "influxdb"]
    if not influx:
        raise SystemExit("no influxdb datasource found on target Grafana")
    return next((d["uid"] for d in influx if d.get("isDefault")), influx[0]["uid"])


def prepare(dash: dict, ds_uid: str) -> dict:
    """Import-format dashboard -> API dashboard object with datasource resolved."""
    raw = json.dumps(dash)
    raw = raw.replace("${DS_INFLUXDB}", ds_uid)
    d = json.loads(raw)
    d.pop("__inputs", None)
    d.pop("__requires", None)
    d["id"] = None            # let Grafana assign; uid keeps it stable/idempotent
    return d


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("GRAFANA_URL"))
    ap.add_argument("--token", default=os.environ.get("GRAFANA_TOKEN"))
    ap.add_argument("--user", default=os.environ.get("GRAFANA_USER"))
    ap.add_argument("--password", default=os.environ.get("GRAFANA_PASSWORD"))
    ap.add_argument("--datasource-uid", help="override; else auto-detect default influxdb")
    ap.add_argument("--dashboard", required=True)
    ap.add_argument("--folder", type=int, default=0)
    a = ap.parse_args(argv)
    if not a.url:
        raise SystemExit("--url (or GRAFANA_URL) required")
    auth = {"token": a.token} if a.token else {"user": a.user, "password": a.password}

    ds_uid = a.datasource_uid or find_influx_datasource(a.url, **auth)
    dash = json.load(open(a.dashboard))
    payload = {"dashboard": prepare(dash, ds_uid), "overwrite": True,
               "folderId": a.folder, "message": "calictl push"}
    res = _req(a.url + "/api/dashboards/db", method="POST", body=payload, **auth)
    print("pushed '%s' -> %s%s (datasource %s)" %
          (dash.get("title"), a.url, res.get("url", ""), ds_uid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
