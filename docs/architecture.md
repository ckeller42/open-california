# Architecture

How a byte on the wire becomes a Grafana point / Home-Assistant entity, and how a
`set` becomes an actuation. Source of truth for bit layout is `protocol/dictionary.yaml`;
surface/omit decisions live in `protocol/signals.yaml`.

## Data pipeline

```mermaid
flowchart LR
  APK["VW APK (local only,\ngit-ignored)"] -->|tools/decompile.sh + extract_protocol| DICT[protocol/dictionary.yaml]
  DICT --> LOAD["protocol.load()\n+ overrides.apply()"]
  OVR[overrides.py\nmanual offsets/ranges] --> LOAD
  LOAD --> DECODE["protocol.decode\n(MSB-first bits)"]
  DECODE --> SEM["semantics.interpret\n(per-function meaning)"]
  SEM --> CAT{"signals.yaml catalog\n(surface | omit)"}
  CAT -->|numeric| INFLUX[influx.numeric_fields] --> IDB[(InfluxDB)] --> GRAF[Grafana]
  CAT -->|entities| MQTT[mqtt.render_state] --> HA[Home Assistant]
  DICT -. every field needs a decision .-> GUARD[[tests/test_signal_coverage\n+ tools.audit_signals]]
  CAT -. CI fails on drift .-> GUARD
```

The **coverage guardrail** (`test_signal_coverage.py`) enforces three invariants in CI:
every dictionary field has a catalog decision; every *surfaced* state field is emitted by
semantics; every *surfaced* control field is placed. A dropped or mislabeled signal fails CI.

## Control path (`calictl set` / HA command)

```mermaid
flowchart LR
  CLI["calictl set cooler power on"] --> BUILD["control.BUILDERS[fn]\n(full-packet frame,\nunchanged fields = sentinel)"]
  HA["HA command (MQTT)"] --> BUILD
  BUILD --> ENC["protocol.encode\n(validates width + CONTROL_RANGES)"]
  ENC --> ACT["device.actuate\n(under serve's asyncio.Lock)"]
  ACT --> UNIT[(Camper unit\nchar 1101/1201/1501/…)]
  ACT --> RB["read state char back\n-> report OK / NOT APPLIED"]
```

## BLE connect + actuate (the arm sequence)

The firmware ignores control writes unless a **liveness heartbeat** is ticking on char
`00001003` (issue #2). Actuation is **one-shot arm**: the load latches, so the heartbeat only
spans the write window — `serve` did not need a persistent-connection redesign.

```mermaid
sequenceDiagram
  participant C as calictl (buspi)
  participant U as Camper unit
  C->>U: connect (LE)
  C->>U: read 00001001 (version), 00001004 (auth -> bonds)
  C->>U: subscribe (CCCD) all 12 status chars
  Note over C,U: CONNECTED
  loop every ~0.6 s (arm window)
    C->>U: write 00001003 = +1 counter (BE, with response)
  end
  C->>U: write control char (e.g. 1101 State=1) [with response]
  U-->>C: status notification (1102 on=true)
  Note over C,U: load LATCHES — heartbeat can now stop
  C->>U: disconnect
```

```mermaid
stateDiagram-v2
  [*] --> Disconnected
  Disconnected --> Connected: connect + handshake + subscribe-all
  Connected --> Armed: start 1003 heartbeat (+1 BE @0.6s)
  Armed --> Written: write control frame (with response)
  Written --> Latched: unit acts; load holds
  Latched --> Idle: stop heartbeat + disconnect
  Idle --> [*]
  note right of Latched: one-shot arm, NOT a deadman —\nload stays on after heartbeat stops
```

## Runtime invariants (see CLAUDE.md)

- **stdlib-only at import** — `bleak`/`paho`/`influxdb_client`/`yaml` are imported lazily
  inside functions; `import calictl.*` stays cheap and offline-safe (CI asserts this).
- **single BLE owner** — one daemon holds the vehicle's single connection slot; an
  `asyncio.Lock` created inside `serve.run()`'s loop serializes `poll` and `on_command`, so a
  control write never races a poll.
- **full-packet control frames** — resend every field; unchanged fields carry the
  leave-unchanged sentinel. `protocol.encode` validates each value against its bit-width and a
  curated semantic range before the frame is sent.
