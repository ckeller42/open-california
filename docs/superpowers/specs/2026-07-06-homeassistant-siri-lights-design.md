# Home Assistant + Apple Home (Siri) control of the VW California via calictl

**Date:** 2026-07-06
**Status:** Design approved, pre-implementation
**Goal:** Turn the van's lights on/off with **Siri** ("Hey Siri, turn on the van
light"), by exposing them through Home Assistant → Apple Home (HomeKit), driven
by `calictl` over BLE. Architecture must also carry the read-only telemetry
(water, battery, fridge, …) into HA, and keep **open-california standalone** so
any California + Raspberry Pi owner can reuse it.

## Purpose & success criteria

- **Primary:** Siri turns the van lights on/off. Target lights (per owner: "all"):
  camping-mode **interior** light, camping-mode **outside/awning** light, and the
  **main interior LED lighting** (service `0x1500`) as a dimmable light.
- **Secondary:** the van's sensors already flowing to InfluxDB also appear in HA
  (via MQTT), and a curated subset (lights + optionally fridge) is exposed to
  Apple Home.
- **Success:** speaking to Siri toggles the physical light within a few seconds
  when the van is reachable; state in Apple Home reflects the vehicle.
- **Reusability:** a second user clones open-california, follows
  `HOMEASSISTANT.md`, and stands up the same stack without any buspi-specific
  knowledge.

## Architecture

```
Siri → Apple Home → HomeKit bridge (HA `homekit:`) → HA light entity
                                                         │ MQTT command
                                                         ▼   calivan/<fn>/set/<what>
   Mosquitto ◀── discovery + state ── calictl daemon ──BLE write──▶ VW Camper Unit
      :1883                              │  SOLE BLE owner of hci0
                                         └── also writes InfluxDB (existing EDR→cloud path)
```

- **Two new containers** in `~/monitoring/docker-compose.yml`:
  - **Mosquitto** (`eclipse-mosquitto`), `:1883`, password auth, persistent config/data volumes.
  - **Home Assistant** (`ghcr.io/home-assistant/home-assistant:stable`), **`network_mode: host`**
    (required so HomeKit's Bonjour/mDNS is reachable by Apple devices on the LAN), config volume.
- **HA never touches BLE.** `hci0` is shared with the Anker/Victron/Govee readers,
  so calictl remains the only BLE consumer. HA talks only MQTT.
- Resources are fine on buspi (arm64, ~2.9 GB free RAM, 104 GB disk).

## Component: unified `calictl` daemon (Decision ①)

Replace the standalone `calictl-influx` service with a single **`calictl.service`**
(`python -m calictl daemon` running under `solix-env`) that owns all van BLE:

- One asyncio loop; on each poll cycle it **connects once**, reads all state, and
  **fans out to both sinks**: InfluxDB points (existing `influx.py`) *and* MQTT
  state/discovery (existing `mqtt.py`).
- Subscribes MQTT command topics; a command triggers a **BLE control write**.
- An **`asyncio.Lock` around all BLE access** so a command write and a poll never
  overlap (the van allows one connection; a single process with a lock makes this
  trivial, and avoids the self-contention two separate services would cause).
- Connect-on-demand with graceful failure: if the van is unreachable (e.g. the
  **phone app holds the single BLE slot**), polls log "skipped" and commands
  report failure; HA shows the entity unavailable / last-known.
- Adapter power-cycle recovery stays **opt-in/off** (already changed) to protect
  the shared adapter.

Config via env (`EnvironmentFile`): existing Influx vars + new
`MQTT_HOST/PORT/USER/PASSWORD`. Poll interval configurable (default 30 s for
responsiveness; sensors don't need faster).

## Component: calictl control extension (lights)

Build and **unit-test** control-frame builders + command mapping, mirroring the
verified cooler path:

- **Camping-mode lights** (service `0x1200`, control model `lg/a`): write
  `InteriorLight` and `OutsideLight` **independently** (the wire has separate
  fields; the app's `K0` setter ties them together and uses **inverted polarity**
  — our frames set each field directly, polarity confirmed by the live test).
  Full-packet rule: carry current `State`/`UsbCharger`, no-op the rest.
- **Main interior LED** (service `0x1500`, control model `eg/a`): expose as a
  dimmable light — on/off + brightness via `LightValue` / `Brightness*` /
  `Mode` (`SET_BRIGHTNESS`). Scope v1 to on/off + a single brightness; per-zone
  scenes are out of scope.
- **MQTT entities** (`mqtt.py`): add `light` components (brightness for the LED,
  on/off for camping lights) with command topics; `daemon._apply_command`
  extended beyond `cooler` to these functions.

## Component: Home Assistant + HomeKit

- HA auto-discovers all calictl entities via MQTT discovery (no manual entity YAML).
- **`homekit:`** integration (bridge mode) in `configuration.yaml`, **filtered**
  to the van light entities (fridge + key sensors optional). Emits a pairing PIN.
- Add the bridge in the Apple Home app via the PIN → Siri controls the lights.
- HomeKit PIN + MQTT credentials are instance secrets (buspi-config), not in open-california.

## Repo split (config-placement question)

**open-california (standalone/reusable):**
- `calictl/` — bridge + control code (light frames, unified daemon, tests).
- `calictl/deploy/homeassistant/`:
  - `docker-compose.snippet.yml` — generic mosquitto + homeassistant services.
  - `configuration.yaml` — example HA config (MQTT is auto; `homekit:` filter).
  - `mosquitto.conf` — example broker config (auth on).
  - `HOMEASSISTANT.md` — end-to-end setup for any user.
- `calictl/deploy/calictl.service` — unified daemon unit (generic, `EnvironmentFile`).

**buspi-config (instance-specific):**
- Merge mosquitto + homeassistant into buspi's real `~/monitoring/docker-compose.yml`.
- HA config volume + secrets (MQTT user/pass, HomeKit PIN) in gitignored
  `/etc/buspi/*.env`; 0600 root.
- README subsection referencing open-california (same pattern as `calictl-influx`).

## Gating risk & first step (Decision ②)

**No calictl control write has ever landed on the vehicle** (the fridge attempt
was blocked by phone-app BLE contention). The entire feature depends on it.

**Implementation step 1 (gating):** with the phone app disconnected, write a
camping interior-light on/off frame, **read the state bit back to confirm**, and
ideally eyeball the physical light. Also empirically confirm the **on/off polarity**
(camping lights are inverted in the app). If a reliable write can't be achieved,
**stop and reassess** before building the HA/MQTT stack.

## Testing

- **Unit (offline):** light frame builders (`encode`) + command→values mapping,
  extending `tests/test_calictl.py`. `daemon --dry-run` renders MQTT discovery for
  the new light entities.
- **Integration (gated, live):** step-1 write verification; MQTT round-trip
  (command topic → BLE write → state reflects); manual Apple Home pairing + a Siri
  on/off.

## Out of scope (YAGNI)

- Per-zone lighting scenes / colour / wake-up light automation (v1 = on/off + one brightness).
- HA automations, non-van integrations, HA dashboards (HA is the Siri/HomeKit path here, not a hub build-out).
- Exposing every function to HomeKit (curated subset only).
- Cloud/remote HomeKit access (LAN + an Apple Home hub is the user's path).

## Open items to resolve during implementation

- Exact `on` value/polarity for camping `InteriorLight`/`OutsideLight` (confirm live).
- Interior-LED "on" semantics: brightness value + `Mode` to send for a plain on
  (confirm against `eg/a` control model + a live write).
- Mosquitto auth model (dedicated `calictl` + `homeassistant` users vs one shared).
