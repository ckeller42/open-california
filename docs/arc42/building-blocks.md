# Strategy and building blocks

## 4 Solution strategy

| Goal | Approach |
|---|---|
| Truthful values | A dictionary-driven codec: `protocol/dictionary.yaml` is the source of truth for bit layout, and manual offsets live only in `overrides.py`. A separate `semantics` layer gives fields meaning, so decode never guesses. |
| Drift is caught | A signal catalog (`protocol/signals.yaml`) records a `surface` or `omit` decision for every field, enforced by `tests/test_signal_coverage.py` and `tools.audit_signals`. |
| Safe actuation | Control frames are built by `control.BUILDERS`, validated by `protocol.encode` against bit widths and curated ranges, and written under the single BLE lock while the `1003` heartbeat ticks. Readback is a write-through echo, so it is never reported as proof. |
| One BLE owner | One daemon (`serve`) owns the connection. The web UI, MQTT and InfluxDB are sinks inside that process. |
| Absent unit | `serve` persists the last state with an "as of" timestamp, and the UI shows an offline banner. `freshness` holds the last plausible value for measurement-gated fields such as water. |
| Evidence over assertion | Every protocol claim carries a verification tier (DEVICE, CAPTURE, DECOMPILE, UNVERIFIED) in the evidence ledger. |

## 5 Building block view

### Level 1: containers

```mermaid
flowchart TB
  owner(["Owner"])
  unit[("Camper unit<br/>BLE")]
  subgraph pi["buspi (Raspberry Pi)"]
    daemon["calictl daemon<br/>serve: polls, decodes,<br/>interprets, actuates"]
    webui["Web UI<br/>un-built JS, served by the daemon"]
    cli["calictl CLI<br/>status, set, serve"]
    mqtt["Mosquitto<br/>MQTT broker"]
    ha["Home Assistant"]
    idb[("InfluxDB")]
    graf["Grafana"]
  end
  esp["ESP32 satellite<br/>CoreS3 firmware, work in progress"]
  tools["Tooling and mocks<br/>mock unit, applab, trace_compare"]
  owner -->|browser| webui
  owner -->|terminal| cli
  webui -->|HTTP /api| daemon
  cli -->|direct or via daemon| daemon
  daemon ---|BLE| unit
  daemon --> mqtt
  mqtt --- ha
  daemon --> idb
  idb --> graf
  esp -.-|BLE, own NimBLE stack| unit
  owner -->|WiFi, station mode| esp
  tools -.->|fake peripheral replaces the unit in tests| daemon
  classDef person fill:#08427b,color:#fff,stroke:#052e56
  classDef container fill:#438dd5,color:#fff,stroke:#2e6295
  classDef ext fill:#999,color:#fff,stroke:#6b6b6b
  class owner person
  class daemon,webui,cli,esp,tools container
  class unit,mqtt,ha,idb,graf ext
```

| Container | Technology | Responsibility |
|---|---|---|
| calictl daemon | Python 3.11+, `asyncio`, `bleak` | The single BLE owner. Polls, decodes, interprets, caches, actuates, fans out to the sinks. |
| Web UI | Vanilla JS, no build step, `tsc --checkJs` gate | Dashboard and controls in English and German, served by the daemon. Also served by the ESP satellite. |
| CLI | Python | One-shot reads and writes, `serve`, pairing helpers. Never opens a second connection when the daemon is up. |
| ESP32 satellite | C, NimBLE, ESP-IDF | Independent implementation of pairing, read and a restricted write path, tied to calictl by generated headers and golden vectors. It never talks to buspi. |
| Tooling and mocks | Python, Bumble | The mock unit (a BLE peripheral with real SMP pairing), the real app in an emulator, and a trace comparer. They make the unit reproducible. |

The sinks (Mosquitto, Home Assistant, InfluxDB, Grafana) are deployed next to the daemon but are
off-the-shelf components. They are drawn grey because this repository configures them and does not
implement them.

### Level 2: components of the calictl daemon

```mermaid
flowchart LR
  subgraph calictl["calictl package"]
    device["device<br/>BLE connection, heartbeat, actuate"]
    session["session<br/>holds the slot while the UI is active"]
    protocol["protocol<br/>decode and encode, MSB-first"]
    overrides["overrides<br/>manual offsets"]
    semantics["semantics<br/>names, scales, derived signals"]
    control["control<br/>BUILDERS, preconditions"]
    freshness["freshness<br/>stale-read guards"]
    serve["serve<br/>daemon, asyncio.Lock, cache"]
    web["web"]
    mqttc["mqtt"]
    influx["influx"]
    pairing["pairing and pairing_bluez<br/>guided pairing"]
    postcheck["postcheck<br/>applied-check after a write"]
  end
  dict[["protocol/dictionary.yaml"]]
  cat[["protocol/signals.yaml"]]
  dict --> protocol
  overrides --> protocol
  device --> protocol
  protocol --> semantics
  semantics --> freshness
  freshness --> serve
  cat -.-> semantics
  session --> device
  serve --> session
  serve --> web
  serve --> mqttc
  serve --> influx
  web --> control
  control --> protocol
  serve --> control
  serve --> postcheck
  pairing --> device
```

Rules that shape these dependencies:

- `protocol` and `semantics` are pure. They import no I/O libraries, which is what lets the whole
  suite run without BLE or MQTT installed.
- Only `device` and `pairing_bluez` touch BLE, and only `serve` creates the `asyncio.Lock` (inside
  the running loop) that serialises every access.
- `semantics` is the one place that gives a field its name and scale. The sinks consume its output
  instead of re-deriving meaning.
- Control reaches the unit only through `control` then `protocol.encode` then `device.actuate`,
  entered via `serve.on_command`. The web layer checks `control.command_precondition` first.

The dictionary-to-sink pipeline, with the guard that fails CI on a dropped field, is drawn in the
[architecture overview](../architecture.md).
