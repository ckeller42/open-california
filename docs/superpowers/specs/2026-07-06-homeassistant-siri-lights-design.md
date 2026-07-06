# Home Assistant + Apple Home (Siri) full control of the VW California via calictl

**Date:** 2026-07-06
**Status:** Design approved, pre-implementation
**Goal:** Bring **everything the CaliforniaOnTour app offers** — controls and
status — into **Home Assistant**, grouped the way the app groups it, and expose
the controllable features to **Apple Home / Siri**. Driven by `calictl` over BLE.
Keep **open-california standalone** so any California + Raspberry Pi owner can
reuse it.

## Purpose & success criteria

- **Parity:** every feature the app exposes for the owner's vehicle appears in HA
  — control entities for the app's *Remote Control* features, sensor entities for
  its *Overview* status — and only the features the van actually has (mirroring
  the app's per-feature `Installed`-bit visibility).
- **Siri:** the controllable features (lights, fridge, heaters, roof-AC, camping
  toggles) are HomeKit accessories; "Hey Siri, turn on the van light" (and the
  rest) works when the van is reachable.
- **Reusability:** a second owner clones open-california, follows
  `HOMEASSISTANT.md`, and stands up the same stack with no buspi-specific knowledge.

## The app's grouping (source of truth: `ui/screens/home.yaml`)

The Vehicle tab's dashboard organizes features into two buckets:

- **Vehicle Overview** (status): second battery, fresh water, waste water, issues.
- **Remote Control** (8 controllable features): air-heater, living-room-heater,
  coolbox (fridge), heating-&-cooling (roof-AC), hot-water, lighting,
  camping-mode, step (stairs). Roof pop-top + satellite antenna are reachable
  separately (vehicle-info).

Each feature's visibility is **data-driven by its 1-bit `Installed` flag** in its
BLE state frame (`docs/business-logic/feature-availability.md`). We mirror this:
only publish/enable features whose `Installed` bit is set. On this van that is
fridge, water, energy, air-heater, roof, camping-mode, lighting; stairs,
living-room-heater, roof-AC, satellite stay hidden — exactly like the app.

## HA mapping

- **One HA *device* per function** (the HA-native equivalent of the app's
  per-feature screens): `VW California — Fridge`, `— Water`, `— Energy`,
  `— Air Heater`, `— Lighting`, `— Camping Mode`, `— Roof`, … Each device
  carries that function's control + sensor entities.
- Delivered via **MQTT discovery**: `mqtt.py` expands from today's curated subset
  to the **complete per-function entity set**, each entity's discovery config
  naming its function `device` block so HA groups them.
- Only devices for `Installed` features are published (dynamic parity).

### Function → HomeKit accessory

| Feature | HomeKit accessory | Controls exposed |
|---|---|---|
| Camping interior/outside light | 2× Light | on/off |
| Interior LED lighting (0x1500) | Light (dimmable) | on/off + brightness |
| Fridge (cooler) | Switch (+ level number) | on/off, cooling level 1–5 |
| Air heater | Switch (+ level) | on/off, heating level |
| Living-room heater | Climate/Thermostat | air/water temp, mode |
| Roof A/C (0x2000) | Climate/Fan | on/off, mode, fan, temp |
| Camping USB charger | Switch | on/off |
| Water / battery / tanks | Sensor (read-only) | — |
| **Roof pop-top (0x1400)** | **Sensor only (position)** | **excluded from Siri** |

**Roof is intentionally read-only in HomeKit.** It is a dead-man's-switch (1 Hz
movement heartbeat + 3 s SafetyCounter ack); a voice-triggered raise with nobody
watching is unsafe. Position is a sensor; raise/lower stays in-app/manual.

Heater/roof-AC HomeKit typing (Switch vs Thermostat) is finalized in the plan;
where a clean Climate mapping is awkward, fall back to Switch + number/select.

## Architecture

```
Siri → Apple Home → HomeKit bridge (HA `homekit:`) → HA entity
                                                        │ MQTT command  calivan/<fn>/set/<what>
                                                        ▼
   Mosquitto ◀── discovery + state ── calictl daemon ──BLE write──▶ VW Camper Unit
      :1883                             │  SOLE BLE owner of hci0
                                        └── also writes InfluxDB (existing EDR→cloud path)
```

- **Two new containers** in `~/monitoring/docker-compose.yml`: **Mosquitto**
  (`eclipse-mosquitto`, `:1883`, password auth, persistent volumes) and **Home
  Assistant** (`home-assistant:stable`, **`network_mode: host`** for HomeKit
  mDNS, config volume).
- **HA never touches BLE** — `hci0` is shared with the Anker/Victron/Govee
  readers, so calictl stays the only BLE consumer; HA talks only MQTT.
- buspi has headroom (arm64, ~2.9 GB free RAM, 104 GB disk).

## Unified `calictl` daemon (Decision ①, approved)

Replace `calictl-influx.service` with one **`calictl.service`**
(`python -m calictl daemon`, under `solix-env`) that owns all van BLE:

- One asyncio loop; each poll cycle **connects once**, reads all state, and fans
  out to **both** InfluxDB (`influx.py`) and MQTT (`mqtt.py`).
- Subscribes MQTT command topics; a command runs a **BLE control write**.
- An **`asyncio.Lock` around all BLE access** so a command write and a poll never
  overlap (the van allows one connection; a single locked process avoids the
  self-contention two separate services would cause).
- Graceful failure: if the van is unreachable (e.g. the **phone app holds the
  single BLE slot**), polls log "skipped" and commands report failure → HA shows
  the entity unavailable / last-known. Adapter power-cycle stays opt-in/off
  (protects the shared adapter).
- Env (`EnvironmentFile`): existing Influx vars + `MQTT_HOST/PORT/USER/PASSWORD`.
  Poll interval default 30 s.

## calictl control extension (all Remote-Control features)

Build + **unit-test** control-frame builders + command mapping per feature,
mirroring the verified cooler path and the `docs/business-logic/*.md` setters:

- **Camping-mode** (`0x1200`, `lg/a`): `InteriorLight`, `OutsideLight`, `UsbCharger`
  written **independently** (wire has separate fields; app's `K0` ties the lights
  and uses **inverted polarity** — our frames set each field directly, polarity
  confirmed live). Full-packet: carry current `State`, no-op the rest.
- **Fridge** (`0x1100`) — already frame-verified: power + level.
- **Lighting** (`0x1500`, `eg/a`): on/off + brightness via `LightValue`/`Mode`
  (`SET_BRIGHTNESS`). Per-zone scenes out of scope.
- **Air heater** (`0x1700`), **living-room heater** (`0x2100`), **roof-AC**
  (`0x2000`): on/off + level/mode/temp per their control models (only air-heater
  is `Installed` on this van; others coded but publish only when present).
- `mqtt.py` gains the matching `light`/`switch`/`climate` entities;
  `daemon._apply_command` extended beyond `cooler` to each function.

## Repo split (config-placement question)

**open-california (standalone/reusable):**
- `calictl/` — bridge + control code (light/feature frames, unified daemon, tests).
- `calictl/deploy/homeassistant/`: `docker-compose.snippet.yml` (mosquitto + HA),
  `configuration.yaml` (MQTT auto-discovery + `homekit:` filter), `mosquitto.conf`,
  `HOMEASSISTANT.md` (end-to-end setup for any user).
- `calictl/deploy/calictl.service` — unified daemon unit (generic, `EnvironmentFile`).

**buspi-config (instance-specific):**
- Merge mosquitto + homeassistant into buspi's real `~/monitoring/docker-compose.yml`.
- HA config volume + secrets (MQTT user/pass, HomeKit PIN) in gitignored
  `/etc/buspi/*.env` (0600 root).
- README subsection referencing open-california (same pattern as `calictl-influx`).

## Gating risk & phased order (Decision ②, approved)

**No calictl control write has ever landed on the vehicle** (the fridge attempt
was blocked by phone-app BLE contention). All control depends on it. Phases:

1. **Infra + unified daemon + all sensors (read-only).** Mosquitto + HA + HomeKit
   sensors for every `Installed` feature. No writes — safe, immediately useful.
2. **Gating: land + verify one live control write** (camping interior light):
   write on/off, read the state bit back, confirm — ideally eyeball the light;
   nail the on/off **polarity**. If unreliable, **stop and reassess**.
3. **Roll controls out** to the remaining Remote-Control features + HomeKit.

## Testing

- **Unit (offline):** per-feature frame builders (`encode`) + command→values
  mapping, extending `tests/test_calictl.py`. `daemon --dry-run` renders MQTT
  discovery for the full entity set.
- **Integration (gated, live):** phase-2 write verification; MQTT round-trip
  (command → BLE write → state reflects); manual Apple Home pairing + Siri on/off.

## Out of scope (YAGNI)

- Roof pop-top control via HomeKit/Siri (safety — read-only position only).
- Per-zone lighting scenes / colour / wake-up-light automation (v1 = on/off + one brightness).
- HA automations, non-van integrations, custom HA dashboards (HA here is the
  Siri/HomeKit + parity surface, not a hub build-out).
- Cloud/remote HomeKit access (LAN + the user's Apple Home hub).

## Open items to resolve during implementation

- Exact `on` value/polarity for camping `InteriorLight`/`OutsideLight` (confirm live).
- Interior-LED "on" semantics: brightness value + `Mode` to send for a plain on.
- Heater / roof-AC HomeKit typing (Climate vs Switch+number) per control model.
- Mosquitto auth model (dedicated `calictl` + `homeassistant` users vs one shared).
