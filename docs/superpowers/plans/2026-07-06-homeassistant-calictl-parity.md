# Home Assistant + Siri full-parity control of the VW California — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring every feature the CaliforniaOnTour app offers into Home Assistant (grouped like the app, one HA device per function, only `Installed` features shown), and expose the controllable ones to Apple Home / Siri — driven by `calictl` over BLE.

**Architecture:** A single unified `calictl` daemon owns all van BLE: each poll connects once, reads all state, and fans out to InfluxDB (existing) **and** MQTT discovery/state; it subscribes MQTT command topics and performs BLE control writes under an `asyncio.Lock`. Mosquitto + Home Assistant run as containers in the existing buspi Docker stack; HA's `homekit:` integration bridges to Apple Home. HA never touches BLE.

**Tech Stack:** Python 3 (stdlib + `bleak`, `influxdb_client`, `paho-mqtt`), MQTT (Mosquitto), Home Assistant Container, Docker Compose, systemd. Runs under `solix-env` on buspi (arm64).

## Global Constraints

- **calictl is the SOLE BLE consumer** of `hci0`; HA and all HA integrations must never open BLE. (shared adapter with anker/victron/govee readers)
- **Dictionary is the source of truth** for field layout: `protocol/dictionary.yaml` via `calictl.protocol`; manual offsets only in `calictl/overrides.py`.
- **Full-packet control writes**: every control frame resends ALL fields; timer/action fields at their no-op sentinel; carry current values for the rest (see `docs/business-logic/*.md`).
- **Adapter power-cycle stays opt-in** (`CALICTL_ADAPTER_RESET=1`), default OFF.
- **Only `Installed` features are published** to MQTT (mirror the app's data-driven visibility).
- **Roof pop-top control is OUT OF SCOPE** for HomeKit/Siri (dead-man's-switch safety); position is a read-only sensor only.
- **No secrets in open-california.** MQTT creds + HomeKit PIN are instance secrets (buspi-config, gitignored `/etc/buspi/*.env`, 0600 root).
- Stdlib-only in `protocol`/`semantics`/`mqtt`; `bleak`/`influxdb_client`/`paho` imported lazily so offline unit tests run without them.
- Tests: `python3 -m pytest tests/ -q` must stay green; new tests extend `tests/test_calictl.py`.

---

## File structure

| File | Responsibility |
|---|---|
| `calictl/mqtt.py` (modify) | Full per-function MQTT-discovery entity set; one `device` block per function; `Installed`-gating; sensors + lights + switches + climate |
| `calictl/serve.py` (create) | The unified daemon: one poll loop, BLE `asyncio.Lock`, fan-out to InfluxDB + MQTT, MQTT command → control write |
| `calictl/control.py` (create) | Per-function control-frame builders + command→field-values mapping (camping lights/USB, lighting, fridge, air-heater) |
| `calictl/overrides.py` (modify) | Add resolved control offsets for camping (`lg/a`) / lighting (`eg/a`) where the extractor left `MERGED_AMBIGUOUS` |
| `calictl/daemon.py` (modify) | Keep `dry_run`; delegate `run` to `serve` (back-compat) |
| `calictl/influx.py` (modify) | Expose `poll_states()` + `points_for()` for reuse by `serve`; keep `write_once` |
| `calictl/cli.py` (modify) | `calictl serve` subcommand (unified); keep `status/get/raw/set/influx/daemon` |
| `calictl/deploy/calictl.service` (create) | Unified systemd unit (replaces `calictl-influx.service`) |
| `calictl/deploy/homeassistant/docker-compose.snippet.yml` (create) | Generic mosquitto + HA services |
| `calictl/deploy/homeassistant/configuration.yaml` (create) | HA config: MQTT auto-discovery + `homekit:` filter |
| `calictl/deploy/homeassistant/mosquitto.conf` (create) | Broker config (auth on) |
| `calictl/deploy/homeassistant/HOMEASSISTANT.md` (create) | End-to-end setup doc for any owner |
| `tests/test_calictl.py` (modify) | New tests: full-parity discovery, Installed-gating, control frames, command mapping |

**Phasing:** Tasks 1–3 = read-only parity (safe, no writes). Task 4 = control frames (offline). **Task 5 = GATED live write verification.** Tasks 6–8 = control rollout. Tasks 9–10 = buspi instance deploy + Apple Home.

---

### Task 1: Full per-function MQTT entity parity (sensors, device grouping, Installed-gating)

**Files:**
- Modify: `calictl/mqtt.py`
- Test: `tests/test_calictl.py`

**Interfaces:**
- Consumes: `semantics.interpret(function, decoded) -> dict`; `semantics.is_installed(function, decoded)`.
- Produces:
  - `device_block(function) -> dict` — HA MQTT `device` block for a function.
  - `ENTITY_SPECS: dict[str, list[EntitySpec]]` — per-function entity specs (namedtuple `EntitySpec(component, key, name, config)`).
  - `render_discovery(installed: set[str] | None = None) -> dict[str, dict]` — configs only for installed functions (all if `None`).
  - `render_state(function, interp) -> (topic, json)` (unchanged signature).
  - `availability_topic() -> str` returns `"calivan/status"`.

- [ ] **Step 1: Write the failing test**

```python
def test_full_parity_devices_and_installed_gating():
    from calictl import mqtt
    # every installed function gets its own HA device + >=1 entity
    installed = {"water", "energy", "cooler", "campingmode", "airheater", "roof", "lighting"}
    cfgs = mqtt.render_discovery(installed=installed)
    # one device per function, keyed in each entity's config
    devices = {tuple(sorted(c["device"]["identifiers"])) for c in cfgs.values()}
    assert ("vwcamper_water",) in devices and ("vwcamper_energy",) in devices
    # a not-installed function (stairs) publishes nothing
    assert not any("stairs" in t for t in cfgs)
    # roof is present but ONLY as a sensor (no switch/light/command_topic)
    roof = [c for t, c in cfgs.items() if "roof_" in t and "roofair" not in t]
    assert roof and all("command_topic" not in c for c in roof)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_calictl.py::test_full_parity_devices_and_installed_gating -v`
Expected: FAIL (`render_discovery()` takes no `installed` arg / no per-function devices)

- [ ] **Step 3: Implement**

Replace the flat `ENTITIES`/`SWITCHES`/`render_discovery` in `calictl/mqtt.py` with per-function specs and device grouping:

```python
from collections import namedtuple

EntitySpec = namedtuple("EntitySpec", "component key name config")

DEVICE_BASE = {"manufacturer": "Volkswagen", "model": "California T7"}
FUNCTION_NAMES = {
    "water": "Water", "energy": "Energy", "cooler": "Fridge", "campingmode": "Camping Mode",
    "airheater": "Air Heater", "roof": "Roof", "lighting": "Lighting",
    "livingroomheater": "Living-Room Heater", "roofaircondition": "Roof A/C",
    "stairs": "Stairs", "satelliteantenna": "Satellite",
}

def device_block(function: str) -> dict:
    return {"identifiers": ["vwcamper_%s" % function],
            "name": "VW California — %s" % FUNCTION_NAMES.get(function, function),
            "via_device": "vwcamper", **DEVICE_BASE}

_BIN = {"payload_on": "True", "payload_off": "False"}

# Read-only entities per function (control entities added in Tasks 6-8).
ENTITY_SPECS: dict[str, list] = {
    "water": [
        EntitySpec("sensor", "fresh_percent", "Fresh Water", {"unit_of_measurement": "%", "icon": "mdi:water"}),
        EntitySpec("sensor", "waste_percent", "Waste Water", {"unit_of_measurement": "%", "icon": "mdi:water-off"}),
    ],
    "energy": [
        EntitySpec("sensor", "batt2_v", "Leisure Battery", {"unit_of_measurement": "V", "device_class": "voltage"}),
        EntitySpec("sensor", "soc2_level", "Leisure Battery Level", {"icon": "mdi:battery"}),
        EntitySpec("binary_sensor", "dcdc_charging", "DC-DC Charging", {**_BIN, "device_class": "battery_charging"}),
    ],
    "cooler": [
        EntitySpec("binary_sensor", "on", "Fridge On", {**_BIN, "icon": "mdi:fridge"}),
        EntitySpec("sensor", "level", "Fridge Level", {"icon": "mdi:snowflake"}),
    ],
    "campingmode": [
        EntitySpec("binary_sensor", "usb_charger", "USB Charger", {**_BIN, "icon": "mdi:usb"}),
        EntitySpec("binary_sensor", "interior_light", "Interior Light (state)", {**_BIN, "icon": "mdi:lightbulb"}),
        EntitySpec("binary_sensor", "outside_light", "Outside Light (state)", {**_BIN, "icon": "mdi:outdoor-lamp"}),
    ],
    "airheater": [
        EntitySpec("binary_sensor", "running", "Air Heater Running", {**_BIN, "device_class": "running"}),
        EntitySpec("sensor", "level", "Air Heater Level", {"icon": "mdi:radiator"}),
    ],
    "lighting": [
        EntitySpec("binary_sensor", "any_on", "Interior Lights On", {**_BIN, "icon": "mdi:led-strip-variant"}),
    ],
    "roof": [
        EntitySpec("sensor", "position", "Roof Position", {"icon": "mdi:caravan"}),
    ],
}

def _config(function, spec):
    uid = "vwcamper_%s_%s" % (function, spec.key)
    cfg = {"name": spec.name, "unique_id": uid,
           "state_topic": "%s/%s" % (BASE, function),
           "value_template": "{{ value_json.%s }}" % spec.key,
           "availability_topic": "%s/status" % BASE,
           "device": device_block(function)}
    cfg.update(spec.config)
    return "%s/%s/%s/config" % (DISCOVERY_PREFIX, spec.component, uid), cfg

def render_discovery(installed=None) -> dict:
    out = {}
    for function, specs in ENTITY_SPECS.items():
        if installed is not None and function not in installed:
            continue
        for spec in specs:
            topic, cfg = _config(function, spec)
            out[topic] = cfg
    return out

def availability_topic() -> str:
    return "%s/status" % BASE
```

Keep `flatten`, `render_state`, and `BASE`/`DISCOVERY_PREFIX`/`DEVICE` as-is. Remove the old `ENTITIES`/`SWITCHES`/`_uid` and the `command_topics()` (re-added in Task 6). Update `test_mqtt_discovery_and_commands` to the new shape or delete it (superseded by the Task-1 test + Task-6 tests).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_calictl.py -q`
Expected: PASS (new parity test green; adjust/remove the superseded old discovery test)

- [ ] **Step 5: Commit**

```bash
git add calictl/mqtt.py tests/test_calictl.py
git commit -m "mqtt: full per-function entity parity + per-device grouping + Installed-gating"
```

---

### Task 2: Unified `calictl serve` daemon (fan-out + BLE lock)

**Files:**
- Create: `calictl/serve.py`
- Modify: `calictl/influx.py` (extract `poll_states`, `points_for`), `calictl/cli.py` (add `serve`), `calictl/daemon.py` (delegate `run` to serve for back-compat)
- Test: `tests/test_calictl.py`

**Interfaces:**
- Consumes: `protocol.load`, `overrides.apply`, `semantics.interpret`, `device.CamperDevice`, `mqtt.render_discovery/render_state`, `influx.points_for`.
- Produces:
  - `influx.poll_states(funcs, dev) -> dict[str, dict]` (async) — one connection, `{function: interpreted}`.
  - `influx.points_for(states) -> list[Point]`.
  - `serve.Server(addr, mqtt_cfg, influx_cfg, interval)` with `async run()`; a single `asyncio.Lock` guards `dev` access; `poll()` fans out to influx + mqtt; `on_command(function, what, value)` performs a control write via `control.apply` (Task 4/6).
  - `serve.installed_from(states) -> set[str]` — functions whose interpreted `installed` is truthy.

- [ ] **Step 1: Write the failing test** (pure fan-out logic, no BLE/MQTT)

```python
def test_installed_from_states():
    from calictl import serve
    states = {"water": {"installed": True}, "stairs": {"installed": False},
              "energy": {"installed": True}, "roof": {"installed": True}}
    assert serve.installed_from(states) == {"water", "energy", "roof"}

def test_points_for_reuses_numeric_fields():
    from calictl import influx
    pts = influx.points_for({"water": {"installed": True, "fresh": {"percent": 38}}})
    # one Point tagged function=water with a numeric field
    assert any(p._name == "camper" for p in pts)
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_calictl.py -k "installed_from or points_for" -v`
Expected: FAIL (`serve` missing; `influx.points_for` missing)

- [ ] **Step 3: Implement**

In `calictl/influx.py`, split the loop body out of `run`:

```python
async def poll_states(funcs, dev) -> dict:
    raw = await dev.read_all(funcs)
    return {n: semantics.interpret(n, protocol.decode(funcs[n], d)) for n, d in raw.items()}

def points_for(states: dict) -> list:
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
```
(Have `build_points` delegate to `points_for`; keep `_poll` calling `poll_states`.)

Create `calictl/serve.py`:

```python
"""Unified calictl daemon: one BLE owner -> InfluxDB + MQTT + commands."""
from __future__ import annotations
import asyncio, json, os
from . import protocol, semantics, overrides, mqtt, influx, control
from .device import CamperDevice, ConnectionUnavailable

def installed_from(states: dict) -> set:
    return {fn for fn, i in states.items() if i.get("installed")}

class Server:
    def __init__(self, addr=None, *, interval=30.0, influx_enabled=True):
        self.funcs = protocol.load(); overrides.apply(self.funcs)
        self.dev = CamperDevice(addr) if addr else CamperDevice()
        self.interval = interval
        self.influx_enabled = influx_enabled
        self._ble = asyncio.Lock()          # the van allows ONE connection
        self._published = set()             # functions whose discovery is sent
        self._mqtt = None
        self._iw = None                     # influx write_api

    async def poll(self):
        async with self._ble:
            states = await influx.poll_states(self.funcs, self.dev)
        # MQTT discovery for newly-seen installed functions
        inst = installed_from(states)
        new = inst - self._published
        if new and self._mqtt:
            for topic, cfg in mqtt.render_discovery(installed=inst).items():
                self._mqtt.publish(topic, json.dumps(cfg), retain=True)
            self._published |= inst
        for fn, interp in states.items():
            if fn in inst and self._mqtt:
                t, payload = mqtt.render_state(fn, interp)
                self._mqtt.publish(t, payload)
        if self.influx_enabled and self._iw:
            self._iw.write(bucket=os.environ.get("INFLUX_BUCKET", "buspi"),
                           org=os.environ.get("INFLUX_ORG", "home"),
                           record=influx.points_for(states))

    async def on_command(self, function, what, value):
        frame = control.build(self.funcs, function, what, value, self._last.get(function, {}))
        if frame is None:
            return
        async with self._ble:
            await self.dev.write_control(self.funcs[function], frame, verify=False)
```

(Wire the paho client + InfluxDB client setup analogous to `daemon.run`/`influx.run`, storing `self._mqtt`, `self._iw`, and caching `self._last[function]` from each poll's *decoded* state for full-packet writes. Subscribe `mqtt.command_topics()` (Task 6) and route `on_message` → `asyncio.run_coroutine_threadsafe(self.on_command(...), loop)`.)

Add to `calictl/cli.py` a `serve` subcommand that constructs `Server(addr, interval=…)` and runs it; make `daemon.run` call into `serve` (back-compat).

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_calictl.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add calictl/serve.py calictl/influx.py calictl/cli.py calictl/daemon.py tests/test_calictl.py
git commit -m "serve: unified calictl daemon (BLE lock, fan-out to InfluxDB+MQTT, command hook)"
```

---

### Task 3: Deploy assets — Mosquitto + HA + unified service (read-only parity ships here)

**Files:**
- Create: `calictl/deploy/calictl.service`, `calictl/deploy/homeassistant/{docker-compose.snippet.yml,configuration.yaml,mosquitto.conf,HOMEASSISTANT.md}`
- Test: none (config assets); validated by `daemon --dry-run` + a YAML/JSON lint

- [ ] **Step 1: Unified systemd unit** — `calictl/deploy/calictl.service`

```ini
[Unit]
Description=VW California Camper Unit (BLE) -> InfluxDB + MQTT (Home Assistant)
After=bluetooth.target influxdb.service mosquitto.service network-online.target
Wants=bluetooth.target network-online.target

[Service]
ExecStart=/home/pi/solix-env/bin/python -m calictl serve
WorkingDirectory=/home/pi/open-california
EnvironmentFile=/etc/buspi/secrets.env
EnvironmentFile=/etc/buspi/calictl.env
Environment=PYTHONUNBUFFERED=1
Environment=POLL_INTERVAL=30
Restart=always
RestartSec=30
User=pi

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Compose snippet** — `calictl/deploy/homeassistant/docker-compose.snippet.yml`

```yaml
# Merge these into your monitoring docker-compose.yml (alongside influxdb + grafana).
  mosquitto:
    image: eclipse-mosquitto:2
    container_name: mosquitto
    restart: unless-stopped
    ports: ["1883:1883"]
    volumes:
      - ./mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
      - mosquitto-data:/mosquitto/data
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    container_name: homeassistant
    restart: unless-stopped
    network_mode: host                # required for HomeKit mDNS/Bonjour
    volumes:
      - ./homeassistant:/config
      - /etc/localtime:/etc/localtime:ro
    depends_on: [mosquitto]
# volumes: mosquitto-data:
```

- [ ] **Step 3: mosquitto.conf** — `calictl/deploy/homeassistant/mosquitto.conf`

```conf
listener 1883
persistence true
persistence_location /mosquitto/data/
password_file /mosquitto/config/passwd
allow_anonymous false
```

- [ ] **Step 4: HA configuration.yaml** — `calictl/deploy/homeassistant/configuration.yaml`

```yaml
default_config:            # includes the MQTT integration (auto-discovery on `homeassistant/#`)
homekit:
  - name: "VW California"
    mode: bridge
    filter:
      include_domains: [light, switch, climate]   # controllable van features
      include_entities: []                         # add specific sensors if desired
# MQTT broker is configured once via the UI (Settings > Devices > MQTT) or:
# mqtt:
#   broker: 127.0.0.1
#   username: !secret mqtt_user
#   password: !secret mqtt_password
```

- [ ] **Step 5: HOMEASSISTANT.md** — `calictl/deploy/homeassistant/HOMEASSISTANT.md`

Write an end-to-end guide for a fresh owner: (1) copy the compose snippet + `homeassistant/` + `mosquitto/mosquitto.conf`; (2) create a broker user (`mosquitto_passwd`); (3) put `MQTT_HOST/PORT/USER/PASSWORD` in the calictl env + broker creds in HA; (4) `docker compose up -d`; (5) install `calictl.service`; (6) entities auto-appear as devices; (7) HomeKit pairing PIN → Apple Home. Include the "HA never does BLE / calictl is sole owner" note and the phone-app-contention caveat.

- [ ] **Step 6: Validate + commit**

Run: `python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('calictl/deploy/homeassistant/*.y*ml')]" && echo OK`
Expected: `OK`

```bash
git add calictl/deploy/calictl.service calictl/deploy/homeassistant/
git commit -m "deploy: Mosquitto + HA compose, HA config, unified calictl.service, HOMEASSISTANT.md"
```

---

### Task 4: Camping-mode light/USB control frames + command mapping (offline)

**Files:**
- Create: `calictl/control.py`
- Modify: `calictl/overrides.py` (camping control offsets if any unplaced)
- Test: `tests/test_calictl.py`

**Interfaces:**
- Produces:
  - `control.camping_values(state: dict, **changes) -> dict` — full-packet camping control values (carry `State`/`UsbCharger`/lights, apply changes).
  - `control.build(funcs, function, what, value, last_decoded) -> bytes | None` — dispatch to a per-function builder; returns the frame or `None` if unsupported.
  - `LIGHT_ON`, `LIGHT_OFF` constants for the camping light field values (polarity **UNVERIFIED until Task 5** — encode the app's logic: app `K0` writes `(!on)?1:0`, i.e. on→0).

- [ ] **Step 1: Write the failing test**

```python
def test_camping_light_frame_roundtrips():
    from calictl import protocol as P, overrides, control
    funcs = P.load(); overrides.apply(funcs)
    # turn interior light ON, carrying current State=0, USB=1 (raw 0x22 baseline)
    cur = P.decode(funcs["campingmode"], bytes.fromhex("22"))
    frame = control.build(funcs, "campingmode", "interior_light", "on", cur)
    back = P.decode(funcs["campingmode"], frame)
    assert back["UsbCharger"] == cur["UsbCharger"]      # carried
    assert back["InteriorLight"] == control.LIGHT_ON    # changed
    assert back["OutsideLight"] == cur["OutsideLight"]  # untouched (independent)
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_calictl.py::test_camping_light_frame_roundtrips -v`
Expected: FAIL (`control` missing)

- [ ] **Step 3: Implement** `calictl/control.py`

```python
"""Per-function control-frame builders (full-packet, dictionary-driven)."""
from __future__ import annotations
from . import protocol

# Camping light field values. App K0 writes (!on)?1:0 -> on=0. VERIFY live (Task 5).
LIGHT_ON, LIGHT_OFF = 0, 1

def camping_values(state: dict, **changes) -> dict:
    vals = dict(State=state.get("State", 0), UsbCharger=state.get("UsbCharger", 0),
                InteriorLight=state.get("InteriorLight", LIGHT_OFF),
                OutsideLight=state.get("OutsideLight", LIGHT_OFF))
    vals.update(changes)
    return vals

def _camping(funcs, what, value, last):
    on = str(value).lower() in ("on", "true", "1")
    if what == "interior_light":
        ch = {"InteriorLight": LIGHT_ON if on else LIGHT_OFF}
    elif what == "outside_light":
        ch = {"OutsideLight": LIGHT_ON if on else LIGHT_OFF}
    elif what == "usb_charger":
        ch = {"UsbCharger": 1 if on else 0}
    else:
        return None
    return protocol.encode(funcs["campingmode"], camping_values(last, **ch))

BUILDERS = {"campingmode": _camping}

def build(funcs, function, what, value, last_decoded):
    b = BUILDERS.get(function)
    return b(funcs, what, value, last_decoded) if b else None
```

If `protocol.encode(campingmode, …)` raises "unresolved" for a control field, add its offset to `overrides.CONTROL_OFFSETS["campingmode"]` from `lg/a.java` (2-bit fields at `NightTimerSet`-style low positions — read the file; add exact `(offset,width)` pairs) and re-run.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_calictl.py::test_camping_light_frame_roundtrips -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add calictl/control.py calictl/overrides.py tests/test_calictl.py
git commit -m "control: camping-mode light/USB full-packet frame builders (polarity pending live verify)"
```

---

### Task 5: GATED — live control-write verification (camping interior light)

**Files:** none (verification). **Do not proceed to Task 6 until this passes.**

**Preconditions:** van reachable; **phone app disconnected** (it holds the single BLE slot); someone can see the physical light.

- [ ] **Step 1: Snapshot current camping state**

Run (buspi): `cd ~/open-california && ~/cali-venv/bin/python -m calictl raw campingmode`
Record the hex (e.g. `22`) and decode: `python -m calictl get campingmode`.

- [ ] **Step 2: Build both candidate frames (polarity unknown)**

```bash
python3 -c "from calictl import protocol as P, overrides, control; \
f=P.load(); overrides.apply(f); cur=P.decode(f['campingmode'], bytes.fromhex('22')); \
print('on0', P.encode(f['campingmode'], control.camping_values(cur, InteriorLight=0)).hex()); \
print('on1', P.encode(f['campingmode'], control.camping_values(cur, InteriorLight=1)).hex())"
```

- [ ] **Step 3: Write the app's-polarity frame, read back, observe the light**

Write `on0` to control char `1201`, then `python -m calictl get campingmode` and **look at the light**. If it lit and `InteriorLight` reads the expected value → polarity `LIGHT_ON=0` confirmed. If the light did NOT change, try `on1`; whichever lights the lamp defines `LIGHT_ON`.

- [ ] **Step 4: Record the verified polarity**

Update `control.LIGHT_ON/LIGHT_OFF` to the observed values; update the spec's open-item; add a memory note (`vwcamper-control`): "camping InteriorLight ON = <value>, verified live <date>".

- [ ] **Step 5: Restore + commit the verified constant**

Write the original snapshot back (interior light off). Commit:
```bash
git add calictl/control.py && git commit -m "control: verified camping light polarity live (LIGHT_ON=<value>)"
```

**If no write is accepted at all** (not a polarity issue — the unit ignores writes): STOP. The control half of this feature is not achievable as designed; ship Tasks 1–3 (read-only parity) and escalate.

---

### Task 6: Wire camping controls into `serve` + MQTT light/switch entities (HomeKit)

**Files:**
- Modify: `calictl/mqtt.py` (add camping control entities + `command_topics`), `calictl/serve.py` (route commands)
- Test: `tests/test_calictl.py`

**Interfaces:**
- Produces:
  - `mqtt.CONTROL_SPECS: dict[str, list]` — control entity specs `(component, key, what, name, config)`; camping: `light` interior, `light` outside, `switch` usb.
  - `mqtt.command_topics() -> dict[str, (function, what)]` — `calivan/<fn>/set/<what>` map.
  - `render_discovery` also emits control entities (with `command_topic`) for installed functions.

- [ ] **Step 1: Write the failing test**

```python
def test_camping_control_entities_and_command_topics():
    from calictl import mqtt
    cfgs = mqtt.render_discovery(installed={"campingmode"})
    light = cfgs["homeassistant/light/vwcamper_campingmode_interior/config"]
    assert light["command_topic"] == "calivan/campingmode/set/interior_light"
    assert light["state_topic"] == "calivan/campingmode"
    assert mqtt.command_topics()["calivan/campingmode/set/interior_light"] == ("campingmode", "interior_light")
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_calictl.py::test_camping_control_entities_and_command_topics -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Add to `mqtt.py`:

```python
ControlSpec = namedtuple("ControlSpec", "component key what name config")

CONTROL_SPECS: dict[str, list] = {
    "campingmode": [
        ControlSpec("light", "interior", "interior_light", "Interior Light",
                    {"payload_on": "on", "payload_off": "off",
                     "value_template": "{{ 'on' if value_json.interior_light else 'off' }}"}),
        ControlSpec("light", "outside", "outside_light", "Outside Light",
                    {"payload_on": "on", "payload_off": "off",
                     "value_template": "{{ 'on' if value_json.outside_light else 'off' }}"}),
        ControlSpec("switch", "usb", "usb_charger", "USB Charger",
                    {"payload_on": "on", "payload_off": "off",
                     "value_template": "{{ 'on' if value_json.usb_charger else 'off' }}"}),
    ],
}

def _control_config(function, cs):
    uid = "vwcamper_%s_%s" % (function, cs.key)
    cfg = {"name": cs.name, "unique_id": uid,
           "state_topic": "%s/%s" % (BASE, function),
           "command_topic": "%s/%s/set/%s" % (BASE, function, cs.what),
           "availability_topic": "%s/status" % BASE,
           "device": device_block(function)}
    cfg.update(cs.config)
    return "%s/%s/%s/config" % (DISCOVERY_PREFIX, cs.component, uid), cfg

def command_topics() -> dict:
    out = {}
    for function, specs in CONTROL_SPECS.items():
        for cs in specs:
            out["%s/%s/set/%s" % (BASE, function, cs.what)] = (function, cs.what)
    return out
```

Extend `render_discovery` to also loop `CONTROL_SPECS` (gated by `installed`). In `serve.py`, on MQTT connect `subscribe` each `command_topics()` key; `on_message` → `run_coroutine_threadsafe(self.on_command(fn, what, payload), loop)`; after a command, re-poll or publish optimistic state.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_calictl.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add calictl/mqtt.py calictl/serve.py tests/test_calictl.py
git commit -m "mqtt+serve: camping light/USB HomeKit control entities + command routing"
```

---

### Task 7: Interior LED lighting (0x1500) dimmable control

**Files:** Modify `calictl/control.py`, `calictl/overrides.py`, `calictl/mqtt.py`; Test `tests/test_calictl.py`

**Interfaces:** `control._lighting(funcs, what, value, last)` handles `power` (on/off) and `brightness` (0–255 → LED scale); registered in `BUILDERS["lighting"]`. `mqtt.CONTROL_SPECS["lighting"]` adds a `light` with `brightness_command_topic`/`brightness_state_topic`.

- [ ] **Step 1: Test** — `control.build(funcs,"lighting","power","on",last)` yields a frame decoding to the on-state; `"brightness","128"` sets the brightness field to the mapped value. (Write assertions against `eg/a` fields `LightValue`/`Mode`/`Brightness*`; consult `docs/business-logic/lighting-energy-water-sat-roof.md` for the `SET_BRIGHTNESS` value + which `Mode`.)
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement `_lighting` using `protocol.encode(funcs["lighting"], …)`; add any unplaced control offsets to `overrides` from `eg/a.java` `f()`; map HA brightness 0–255 ↔ the LED field range. Add the MQTT dimmable `light` spec.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `"control: interior LED on/off + brightness (0x1500)"`.
- [ ] **Step 6 (live, gated by Task 5 success):** verify one lighting write on the van; record brightness scale.

---

### Task 8: Fridge + air-heater controls + entities

**Files:** Modify `calictl/control.py`, `calictl/mqtt.py`; Test `tests/test_calictl.py`

**Interfaces:** `control._cooler(funcs, what, value, last)` (reuse the verified frames: `power` on/off, `level` 1–5) and `control._airheater(...)` (`power`, `level`); `BUILDERS` entries; `mqtt.CONTROL_SPECS` adds fridge `switch` + `number` (level) and air-heater `switch` + `number`.

- [ ] **Step 1: Test** — fridge `power off` builds `3c44000f1e00` (already-verified bytes) from a baseline decode; `level 3` builds `3d43…`. Air-heater `power on/off` builds a frame decoding to the expected `NormalOperation` bit (derive from `rf/b`/`docs/business-logic/cooler-airheater.md`).
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement `_cooler` (move the `_cooler_values` logic from `cli.py` into `control.py`; have `cli.set` call it), `_airheater`; register in `BUILDERS`; add MQTT specs.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `"control: fridge + air-heater command builders + HomeKit entities"`.

---

### Task 9: buspi instance deploy (compose merge, secrets, service swap)

**Files:** buspi-config repo + buspi filesystem (runbook, needs sudo). **Not a TDD task.**

- [ ] **Step 1:** In `~/monitoring/`: create `mosquitto/mosquitto.conf` (from deploy asset) and `homeassistant/configuration.yaml`; merge the compose snippet into `docker-compose.yml`. `mosquitto_passwd -c mosquitto/passwd calictl` (and a `homeassistant` user).
- [ ] **Step 2:** Create `/etc/buspi/calictl.env` (0600 root) with `MQTT_HOST=127.0.0.1 MQTT_PORT=1883 MQTT_USER=calictl MQTT_PASSWORD=…`.
- [ ] **Step 3:** `docker compose up -d mosquitto homeassistant`; verify `mosquitto` + `homeassistant` healthy; HA reachable `:8123`.
- [ ] **Step 4:** Swap the service: `sudo systemctl disable --now calictl-influx; sudo cp calictl/deploy/calictl.service /etc/systemd/system/; sudo systemctl daemon-reload; sudo systemctl enable --now calictl`. Confirm `journalctl -u calictl` logs polls + `installed` devices published; InfluxDB still receiving.
- [ ] **Step 5:** In HA, configure the MQTT integration (broker `127.0.0.1`, the `calictl`/HA user). Confirm the van appears as devices (one per installed function) with sensors + camping light/switch entities.
- [ ] **Step 6:** Commit buspi-config: compose + a README subsection referencing open-california (pattern of the `calictl-influx` reference). Keep `.env`/passwd gitignored.

---

### Task 10: HomeKit bridge + Apple Home pairing

**Files:** HA config (instance) + verification. **Not a TDD task.**

- [ ] **Step 1:** Ensure `homekit:` block (Task 3 asset) is in HA `configuration.yaml`; restart HA.
- [ ] **Step 2:** HA emits a HomeKit pairing PIN (Settings → Devices → HomeKit, or the log). Store the PIN in buspi Apple Note / `/etc/buspi` (instance secret, not in repo).
- [ ] **Step 3:** In the Apple Home app: Add Accessory → scan/enter PIN → the "VW California" bridge appears with the light/switch accessories.
- [ ] **Step 4:** Verify Siri: "Hey Siri, turn on the van interior light" → light toggles (van reachable, phone app disconnected). Verify Apple Home reflects state within a poll interval.
- [ ] **Step 5:** Document the pairing + the phone-app-contention caveat in buspi-config README; note the Grafana/InfluxDB path is unaffected.

---

## Self-review

**Spec coverage:** parity/grouping → Tasks 1,6–8 (one device per function, Overview sensors + Remote Control entities); `Installed`-gating → Task 1 + `serve` publish logic; unified daemon/BLE lock → Task 2; Mosquitto+HA+host-net → Task 3; repo split → Tasks 3 (open-california) + 9 (buspi-config); control extension per feature → Tasks 4,6,7,8; roof read-only → Task 1 (sensor only, no command); gated live write → Task 5; HomeKit/Siri → Tasks 3,10; testing → per-task offline + Tasks 5/9/10 live. All spec sections mapped.

**Placeholder scan:** live-empirical values (camping polarity, LED brightness scale, exact `eg/a`/`rf/b` fields) are deliberately resolved in their live/implementation steps with the file to read named — not silent TODOs. Tasks 7/8 give interfaces + the authoritative doc to derive exact bytes (the frame math mirrors the fully-worked Task 4 pattern).

**Type consistency:** `render_discovery(installed=…)`, `render_state`, `device_block`, `command_topics`, `control.build(funcs, function, what, value, last_decoded)`, `influx.poll_states/points_for`, `serve.Server`/`installed_from` are used consistently across tasks. `control.camping_values`/`LIGHT_ON`/`LIGHT_OFF` defined in Task 4, consumed in Tasks 5–6.

**Note:** Tasks 7 and 8 intentionally keep live-derived byte specifics in-task (with the exact source file/doc) rather than pre-computing here, because those values must be read from the control models during implementation and the Task-4 worked example is the pattern to copy.
