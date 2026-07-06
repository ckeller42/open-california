# calictl

Read and control the VW California T7 **Camper Unit** over BLE, and bridge it to
Home Assistant. Own-vehicle interoperability. Dictionary-driven: every field
layout comes from [`protocol/dictionary.yaml`](../protocol/dictionary.yaml) (the
auto-extracted, live-verified protocol map).

## Layers

| Module | Responsibility |
|---|---|
| `protocol.py` | stdlib parser for the dictionary + MSB-first frame decode/encode |
| `semantics.py` | live-verified transforms (water `Level`=current/`Volume`=capacity, energy ×0.1 V + stale-age + signed currents, per-function `Installed` gating, camping independent outputs) |
| `device.py` | robust BLE (single `async with` connect, adapter-reset + rescan recovery, `ConnectionUnavailable` when the phone app holds the one slot) |
| `overrides.py` | the 4 cooler/heater timer offsets resolved from `sf/a.java` `f()` (extractor left `MERGED_AMBIGUOUS`) |
| `cli.py` | `status` / `get` / `raw` / `set` / `serve` / `influx` |
| `serve.py` | unified daemon: one BLE owner → InfluxDB + MQTT (HA discovery) + commands |
| `mqtt.py` | Home Assistant MQTT-discovery entity configs |

## CLI

```
python -m calictl status                 # interpreted status of all 13 functions
python -m calictl get water              # one function: decoded + interpreted (JSON)
python -m calictl raw cooler             # raw hex of the state characteristic
python -m calictl set cooler power off   # control (writes + verifies readback)
python -m calictl set cooler level 3
python -m calictl serve --dry-run        # print HA discovery + one poll, no broker
python -m calictl serve                  # unified daemon (InfluxDB + MQTT); config via env
```

## Home Assistant

`serve` (MQTT broker + InfluxDB from env: `MQTT_HOST/USER/PASSWORD`, `INFLUXDB_TOKEN`,
…) publishes MQTT-discovery configs so the van appears as a
device with entities (fresh/waste water %, fridge on/level, leisure battery
V/level, DC-DC charging, USB charger, air heater, roof position) plus a fridge
power **switch**. State is published to `calivan/<function>` as JSON; the switch
subscribes to `calivan/cooler/set/power`.

## Deploy (on buspi)

```
# needs: bleak (present in ~/cali-venv); paho-mqtt for the daemon
~/cali-venv/bin/pip install paho-mqtt
# copy calictl/ and protocol/dictionary.yaml under one dir, then:
cd ~/open-california && ~/cali-venv/bin/python -m calictl status
```

## Safety posture

**Reads are fully live-verified.** Control **writes** build the correct
full-packet frame (verified byte-for-byte against the app's frame builder and
the sentinel/no-op timer semantics), but a live control write has not yet been
confirmed on the vehicle — so `set` always reads the state back and reports
whether the change actually took effect. Control is limited to the fridge until
a live write is verified. The unit is **bonded-but-unauthenticated**: any bonded
central has full replayable control, so treat Pi access as privileged.
