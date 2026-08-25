<p align="center">
  <img src="docs/assets/logo.png" width="140" alt="Open California logo">
</p>
<h1 align="center">Open California</h1>

<p align="center">
  <b>Run your VW California T7 camper from Linux — every sensor read, every load actuated, over Bluetooth LE.</b>
</p>

<p align="center">
  <a href="https://github.com/ckeller42/open-california/actions/workflows/ci.yml"><img src="https://github.com/ckeller42/open-california/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://ckeller42.github.io/open-california/"><img src="https://img.shields.io/badge/docs-github%20pages-blue" alt="Docs"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/runtime%20deps-stdlib%20only-brightgreen" alt="Runtime deps">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
</p>

<p align="center">
  📖 <b><a href="https://ckeller42.github.io/open-california/">Detailed documentation</a></b> —
  API reference, requirement traceability, and the <a href="https://ckeller42.github.io/open-california/protocol-sequences.html">protocol sequence diagrams</a>.
</p>

The camper control unit only talks to the vendor's iOS/Android app — so from Linux, Home
Assistant, or a script there's simply no way to see the water level or turn on the fridge.
**Open California** reverse-engineers its Bluetooth-LE protocol into a clean, **stdlib-only**
toolkit: full telemetry, *real* control writes, and a self-hosted **web UI + Home Assistant +
Grafana**, all from a Raspberry Pi.

<p align="center">
  <img src="docs/screenshots/light_00_dashboard.png" width="230" alt="Dashboard — status overview + feature tiles">
  <img src="docs/screenshots/light_06_Energy.png" width="230" alt="Energy — batteries, voltages, currents, sources">
  <img src="docs/screenshots/light_05_Water.png" width="230" alt="Water — fresh and grey tanks">
</p>

> 🛑 **Use at your own risk.** It sends control writes to a real vehicle (heaters, roof,
> electrical loads) and can damage it or void your warranty — provided **AS IS, no warranty, no
> liability**. Independent project, **not affiliated with Volkswagen**; no VW binaries, sources,
> manuals, or artwork are distributed. See **[DISCLAIMER.md](DISCLAIMER.md)**.

## What it does

- **Reads everything** — decodes 14 BLE functions (cooler, air-heater, camping mode, lighting,
  energy, water, roof, vehicle state, …) into meaningful signals, cross-validated by a signal
  catalog + coverage guardrail so nothing is silently dropped or mislabeled. Values stay **fresh**
  by holding the unit's `1003` liveness heartbeat during reads (a bare read goes stale — [why, and
  the water push-caveat](https://ckeller42.github.io/open-california/protocol-sequences.html#fresh-state-read-under-heartbeat)).
- **Actually controls** — `calictl set cooler power on` (camping mode, lighting, roof, …) actuate
  for real, armed by the same `1003` heartbeat that unlocks the firmware's [write gate](https://ckeller42.github.io/open-california/protocol-sequences.html#heartbeat-armed-control-write)
  (lighting, roof, and range-rejection have their own [sequence diagrams](https://ckeller42.github.io/open-california/protocol-sequences.html)).
- **Fits your stack** — one BLE-owning daemon fans out to **InfluxDB/Grafana** and **Home
  Assistant** (MQTT), and serves the web UI above — same process, one connection.

## Quickstart

```sh
python3 -m pytest tests/ -q            # test suite — stdlib-only, no BLE/MQTT needed
python3 -m calictl status              # live read of every function (needs BLE + a free slot)
python3 -m calictl set cooler power on # actuate (heartbeat-armed); reads back + reports
python3 -m calictl serve --web 8080    # the daemon → InfluxDB + MQTT + web UI at :8080 (read-only)
python3 -m calictl serve --web 8080 --enable-writes   # ...allow control writes to the vehicle
```

The **daemon is read-only by default** — it will not write to the vehicle until you pass
`--enable-writes` (or set `CALICTL_ENABLE_WRITES=1`), so a stray deploy never actuates anything by
accident. The web UI disables its controls and shows a banner when read-only.

Only the standard library is imported at load; `bleak` / `paho-mqtt` / `influxdb_client` load
lazily, only when actually talking to BLE / MQTT / InfluxDB.

## Raspberry Pi

Fresh Pi to a running monitor in one guided step — deps, virtualenv, **BLE pairing**, config, and
the systemd service:

```sh
curl -fsSL https://raw.githubusercontent.com/ckeller42/open-california/main/install.sh | sh
```

Pairing is interactive (type the passkey the camper shows). `sh install.sh --dry-run` previews
every step without changing anything. Full guide: **[docs/raspberry-pi-setup.md](docs/raspberry-pi-setup.md)**.

## Tested on

- **Vehicle:** VW California **T7** camper control unit, over its vendor BLE GATT service.
- **Equipment installed** on the reference van (varies by van; not-installed functions are
  gated off automatically): cooler, camping mode, water, energy, lighting, air heater, and the
  pop-top roof. **Not installed** here — stairs, living-room heater, roof-A/C, satellite,
  solar; those functions are decompile/static-verified only, not live-tested.
- **Control writes live-actuation-verified** on that van: cooler, camping mode, lighting. Roof
  and air heater are installed but not yet live-verified (roof's frame is protocol-derived;
  air heater's setters are decompile-verified but buspi was offline during its capture window)
  — the five not-installed functions above are untested on real hardware entirely.
- **Host:** a Raspberry Pi ("buspi", Debian 13, aarch64), Python 3.13, BlueZ via `bleak`.

Full GATT service/characteristic map, software-version fields, the equipment/verification
tables: **[Hardware reference](https://ckeller42.github.io/open-california/hardware.html)**.

## How it works

- **Stdlib-only at import** — the runtime pulls no third-party packages until it actually needs a
  BLE/MQTT/InfluxDB connection, so tests run anywhere.
- **One BLE owner** — a single daemon holds the vehicle's single connection slot, serialized by an
  `asyncio.Lock`; control writes never race a poll.
- **Dictionary-driven** — every field is extracted into `protocol/dictionary.yaml` and has a
  catalog decision in `protocol/signals.yaml`; a dropped or unaccounted field **fails CI**. Manual
  bit offsets live only in `overrides.py`.

Contributor guide and hard rules: **[CLAUDE.md](CLAUDE.md)**. Reverse-engineering notes (control
recipes, the write gate, value-freshness, signal scales): **[docs/business-logic/](docs/business-logic/)**.

## License

**[MIT](LICENSE)** — covers the original work here (`calictl` code, tooling, docs); no VW material
is included. No warranty, no liability — see **[DISCLAIMER.md](DISCLAIMER.md)**.
