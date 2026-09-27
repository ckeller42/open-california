# calictl

Read and control the VW California T7 **Camper Unit** over BLE, and bridge it to
Home Assistant. Own-vehicle interoperability. Dictionary-driven: every field
layout comes from [`protocol/dictionary.yaml`](../protocol/dictionary.yaml) (the
auto-extracted, live-verified protocol map).

## Layers

See [`ARCHITECTURE.md`](../ARCHITECTURE.md) for how these fit together (BLE → decode → semantics →
sinks).

| Module | Responsibility |
|---|---|
| `protocol.py` | stdlib parser for the dictionary + MSB-first frame decode/encode |
| `overrides.py` | the 4 cooler/heater timer offsets resolved from `sf/a.java` `f()` (extractor left `MERGED_AMBIGUOUS`) |
| `semantics.py` | live-verified transforms (water `Level`=current/`Volume`=capacity, energy ×0.1 V + stale-age + signed currents, per-function `Installed` gating, camping independent outputs) |
| `freshness.py` | physical-plausibility guards for latched/stale reads (the fresh-water stale latch) |
| `device.py` | robust BLE (single `async with` connect, retries with opt-in adapter reset, `1003` heartbeat, roof streaming, `ConnectionUnavailable` when the phone app holds the one slot) |
| `session.py` | persistent-BLE-session supervisor (holds the slot while the web UI is active, releases it when idle, hands it over for roof moves) |
| `control.py` | per-function full-packet control-frame builders (`BUILDERS`), shared by `set` and the daemon |
| `postcheck.py` | post-write applied-check: did a control write land in the resulting state? |
| `serve.py` | unified daemon: one BLE owner → InfluxDB + MQTT (HA discovery) + web UI + commands |
| `web.py` | stdlib HTTP server for the web UI + JSON API (never opens BLE) |
| `mqtt.py` | Home Assistant MQTT-discovery entity configs |
| `influx.py` | InfluxDB writes for the Grafana stack |
| `history.py` | bounded on-disk leisure-battery history for the web UI (Influx-free) |
| `observer.py` | passive camping/ignition observer (logs transitions, fast-poll burst; never actuates) |
| `automation.py` | auto camper mode — restore camping after you park |
| `firmware.py` / `anchors.py` | firmware-drift raw-frame capture / plausibility anchors for a silently-wrong decode |
| `pairing.py` / `pairing_bluez.py` | platform-free guided-pairing state machine / its async runner + BlueZ transport |
| `trace.py` | opt-in BLE trace recorder (`CALICTL_BLE_TRACE`) |
| `log.py` | daemon logging (`CALICTL_LOG_LEVEL`) |
| `cli.py` | `status` / `get` / `raw` / `set` / `serve` / `influx` |

## CLI

```
python -m calictl status                 # interpreted status of all 14 functions
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
