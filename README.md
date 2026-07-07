# open-california

[![CI](https://github.com/ckeller42/open-california/actions/workflows/ci.yml/badge.svg)](https://github.com/ckeller42/open-california/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![Runtime deps](https://img.shields.io/badge/runtime%20deps-stdlib%20only-brightgreen)](#design)
[![License](https://img.shields.io/badge/license-TBD-lightgrey)](#license)

Reverse-engineered **read + control** for the **VW California T7 camper control unit** over
Bluetooth LE — own-vehicle interoperability. Reads all vehicle telemetry and (with the
firmware liveness gate solved) actuates loads, feeding **Home Assistant** (MQTT) and
**Grafana** (InfluxDB) from a Raspberry Pi.

> ⚠️ Independent RE project, **not affiliated with Volkswagen**. No VW binaries, decompiled
> source, manuals, or artwork are distributed. See **[DISCLAIMER.md](DISCLAIMER.md)** before use.

## What it does

- **Telemetry** — decodes 14 BLE functions (cooler, air-heater, camping mode, lighting,
  energy, water, roof, vehicle state, …) into meaningful signals, cross-validated by a signal
  catalog + coverage guardrail so no signal is silently dropped or mislabeled.
- **Control** — `calictl set cooler power on|off` (and campingmode, lighting, …) actuate for
  real via a **1003-characteristic liveness heartbeat** that arms the firmware's write gate.
- **Integration** — one BLE-owning daemon fans out to InfluxDB/Grafana and Home Assistant.

## Quickstart

```sh
python3 -m pytest tests/ -q            # test suite (stdlib-only; no BLE/MQTT needed)
python3 -m calictl status              # live read of every function  (needs BLE + a free slot)
python3 -m calictl get vehicle         # ignition / car-variant / RTC / roll-pitch leveling
python3 -m calictl set cooler power on # actuate (heartbeat-armed); reads back + reports
python3 -m calictl serve [--dry-run]   # the unified daemon -> InfluxDB + MQTT
```

Runtime needs only the standard library at import; `bleak`/`paho-mqtt`/`influxdb_client` are
imported lazily and only when actually talking to BLE/MQTT/InfluxDB.

## Layout

| Path | What |
|---|---|
| `calictl/` | runtime: `protocol` (decode/encode), `semantics` (interpret), `device` (BLE), `control`/`overrides` (frames), `serve` (daemon), `mqtt`/`influx` (sinks), `cli` |
| `protocol/dictionary.yaml` | extracted field map — source of truth for bit layout |
| `protocol/signals.yaml` | signal catalog — surface/omit decision + provenance per field |
| `tools/` | `extract_protocol` (regenerates the dictionary), `audit_signals` (coverage/semantic-review), `triage`, `catalog` |
| `docs/business-logic/` | RE notes (control recipes, the write gate, signal scales) |
| `ui/` | machine-usable GUI specs (`screens/*.yaml`) + `prototype.html` (icons not committed — VW copyright) |
| `calictl/deploy/` | systemd unit, Mosquitto + HA compose, Grafana dashboard |

## Design

- **Runtime is stdlib-only at import** (tests run without BLE/MQTT installed).
- **One BLE owner** — a single daemon holds the vehicle's single connection slot, serialized by
  an `asyncio.Lock`; control writes never race a poll.
- **Dictionary-driven** — every field has a catalog decision (a dropped/unaccounted field fails
  CI); manual bit offsets live only in `overrides.py`.

See [`CLAUDE.md`](CLAUDE.md) for the full contributor guide and hard rules, and
[`docs/business-logic/`](docs/business-logic/) for the reverse-engineering notes.

## License

**Not yet chosen** — currently all-rights-reserved by default. See [DISCLAIMER.md](DISCLAIMER.md).
If you plan to use or contribute, open an issue; a license decision is pending (and warrants
legal review given the VW-interop context).
