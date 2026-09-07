# Getting Camper Unit data into Grafana (buspi stack)

buspi already runs **InfluxDB 2** (`:8086`, org `home`, bucket `buspi`) and
**Grafana** (`:3000`), fed by systemd readers. calictl plugs in the same way.

## 1. The daemon writes to InfluxDB

There is no separate reader service: the one BLE-owning daemon, `calictl serve`
(`calictl.service`), writes to InfluxDB on every poll **when `INFLUXDB_TOKEN` is set** in its
environment file — alongside MQTT and the web UI, same process, one connection.

```bash
# in the daemon's env file (/etc/opencalifornia/calictl.env for an install.sh host,
# /etc/buspi/secrets.env on the legacy buspi host) — root, 0600:
INFLUXDB_TOKEN=<write token>
INFLUX_URL=http://localhost:8086      # default
INFLUX_ORG=home                       # default
INFLUX_BUCKET=buspi                   # default

sudo systemctl restart calictl.service
journalctl -u calictl -f              # each poll should show the BLE read + Influx write
```

The client lib must be importable by the daemon's Python (`install.sh --with-sinks`, or
`pip install influxdb-client` into its venv).

It writes to bucket **`buspi`** as measurement **`camper`**, tagged `function`
(water/energy/cooler/…) and `vehicle=vwcamper`. Every numeric/boolean field of
the interpreted status becomes a float field (bools → 1/0).

Key fields: `water fresh_percent/waste_percent`, `energy batt2_v/soc2_level/
dcdc_charging/dcdc_power`, `cooler on/level`, `campingmode usb_charger`,
`airheater running/level`, `roof position`, plus each function's `installed`.

## 2. Grafana

The InfluxDB datasource is already configured. Import `camper-dashboard.json`
(Dashboards → Import), or add panels with Flux like:

```flux
// Fresh water level %
from(bucket: "buspi")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "camper" and r.function == "water"
                       and r._field == "fresh_percent")
  |> aggregateWindow(every: v.windowPeriod, fn: last)

// Leisure battery voltage
from(bucket: "buspi")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "camper" and r.function == "energy"
                       and r._field == "batt2_v")
```

## Notes
- `energy batt1_*` are only valid while the engine runs; the reader already
  suppresses the engine-off sentinel (writes no `batt1_v` then), so a gap in that
  series is expected, not a fault.
- If ignition is on and the **phone app** is connected, the unit's single BLE
  slot is taken and polls log "poll skipped" — a gap, not a crash.
- Adjust cadence with `--interval <seconds>` on the unit's `ExecStart` (`calictl serve
  --interval 30` is the default); there is no `POLL_INTERVAL` env knob.
