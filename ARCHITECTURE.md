# Architecture — how the pieces fit

`open-california` turns the VW California camper's Bluetooth-LE control unit into clean signals and
real control, from a Raspberry Pi. This is the five-minute map: the data flow, where each concern
lives, and how to extend it. For the deep provenance behind any claim, follow the links into
[`docs/business-logic/`](docs/business-logic/).

## Data flow

```mermaid
flowchart TB
    U["Camper unit — BLE GATT"]
    D["device.py — single BLE owner, 1003 heartbeat"]
    P["protocol.py + overrides.py — decode / encode (MSB-first, dictionary.yaml)"]
    S["semantics.py — interpret + derive signals"]
    SV["serve.py — poll, cache, fan-out"]
    W["web.py — web UI + /api/state"]
    M["mqtt.py — Home Assistant discovery"]
    I["influx.py — InfluxDB then Grafana"]
    CT["control.py — full-packet control frames"]
    U -->|raw frames| D
    D --> P
    P --> S
    S --> SV
    SV --> W
    SV --> M
    SV --> I
    CT -->|arm with heartbeat| D
    D -->|write control_char| U
```

One daemon (`serve.py`) owns the single BLE connection and drives everything:

1. **`device.py` — the BLE owner.** Holds the one connection slot (an `asyncio.Lock`), reads each
   characteristic's raw frame, and ticks the `1003` liveness heartbeat so reads return fresh values
   instead of a stale latch. Nothing else opens a second BLE connection.
2. **`protocol.py` — the codec.** Decodes a raw frame into a field dict using the bit layout in
   `protocol/dictionary.yaml` (MSB-first), and encodes control frames the same way. `overrides.py`
   carries the few manual bit offsets the extractor cannot derive.
3. **`semantics.py` — interpretation.** Turns decoded fields into meaningful signals: names, scales,
   and derived signals (e.g. `usb_powered = master AND usb_charger`). This is where a raw bit becomes
   "camping USB is on."
4. **`serve.py` — the daemon.** Polls `device` then `protocol.decode` then `semantics`, caches the
   result with an "as of" timestamp, and fans out.
5. **Sinks.** `web.py` (the web UI + `/api/state`), `mqtt.py` (Home Assistant discovery), `influx.py`
   (InfluxDB, read by Grafana). Same process, one connection.

Supporting the daemon: `session.py` (a supervisor that holds the BLE slot while the UI is active and
releases it when idle), `observer.py` / `automation.py` (passive camping observer + auto-camper),
`firmware.py` / `anchors.py` (firmware-drift capture + plausibility checks), `history.py` +
`freshness.py` (energy history + stale-read handling).

## Control (the write path)

`control.py` builds a **full-packet** frame — every field is resent, and unchanged fields carry their
*current* value or the leave-unchanged sentinel, never a default (writing defaults once sent a garbage
command). `device.actuate` writes it while the `1003` heartbeat ticks — the firmware's arm gate
(issue #2). The state-char **readback is a write-through echo, never proof of actuation** — trust a
human at the device, or the unit's genuine `1502` notifications. See
[`control-and-actuation.md`](docs/business-logic/control-and-actuation.md).

## The signal catalog (why nothing silently drifts)

Every field in `dictionary.yaml` must have a decision in `protocol/signals.yaml`: `surface` (with a
name) or `omit` (with a reason). A field that is dropped or unaccounted **fails CI**
(`tests/test_signal_coverage.py`). The auditor (`tools/audit_signals`) also flags where an app
setter/getter is inverted or combined — because **semantics correctness is not auto-checked**. A naive
`bool(field)` can mislabel a signal (that is how the camping-lights inversion slipped through). Verify
polarity against the app's own getter or the unit's on-screen display, not a guess. See
[`signals.md`](docs/business-logic/signals.md).

## Add a signal (the loop)

1. **Catalog it** — `python3 -m tools.triage` (surface-or-omit, with provenance).
2. **Emit it** in `semantics.py`.
3. **Surface it** — update the Grafana dashboard (`calictl/deploy/camper-dashboard.json`, pushed with
   `push_dashboard.py`) and Home Assistant.
4. **Audit** — `python3 -m tools.audit_signals --report`.
5. **Keep the suite green** — `python3 -m pytest tests/ -q`.

## Invariants (do not break these)

- **Runtime `calictl/*` is stdlib-only at import.** `bleak` / `paho` / `influxdb_client` / `yaml`
  import lazily inside functions, so the whole test suite runs with no BLE or MQTT installed.
- **`serve` is the single BLE owner.** `hci0` is shared with other readers on the Pi — never open a
  second connection from the daemon path.
- **The BLE codec is MSB-first**, and control frames are full-packet (resend every field).
- **Evidence over assertion.** Tag every protocol claim by how it was verified — DEVICE (watched on
  hardware) / CAPTURE (on the wire) / DECOMPILE (from the app) / UNVERIFIED — and never put a unit on
  an unverified scale. The [`evidence-ledger.md`](docs/business-logic/evidence-ledger.md) is the
  running record.

## Where to read next

| You want… | Read |
|---|---|
| Run it on a Pi | [`docs/raspberry-pi-setup.md`](docs/raspberry-pi-setup.md) |
| The wire protocol + frame format | [`docs/protocol.md`](docs/protocol.md) |
| Control recipes + the arm gate | [`docs/business-logic/control-and-actuation.md`](docs/business-logic/control-and-actuation.md) |
| Sequence diagrams | the [rendered docs](https://ckeller42.github.io/open-california/protocol-sequences.html) |
| Per-signal provenance + scales | [`docs/business-logic/signals.md`](docs/business-logic/signals.md) |
| The tested hardware + GATT map | the [hardware reference](https://ckeller42.github.io/open-california/hardware.html) |
| Contributor rules + hard invariants | [`CLAUDE.md`](CLAUDE.md) |
