# docs — index

Start with **[architecture.md](architecture.md)** (the pipeline + control path + BLE
connect/actuate diagrams). Root **[../README.md](../README.md)** is the project front door;
**[../CLAUDE.md](../CLAUDE.md)** holds the hard rules. Requirement traceability builds from
docstrings via **[sphinx/](sphinx/)** (`sphinx -b html`).

## Reference docs (`business-logic/`)

| Doc | Covers |
|---|---|
| [architecture.md](architecture.md) | end-to-end pipeline, control path, connect+actuate + heartbeat diagrams |
| [control-and-actuation.md](business-logic/control-and-actuation.md) | **the write gate — SOLVED**: 1003 liveness heartbeat + connect handshake, the full-packet frame model, per-feature actuation status |
| [signals.md](business-logic/signals.md) | the catalog guardrail (surface/omit + coverage) and scales (several UNVERIFIED) |
| [alert-states.md](business-logic/alert-states.md) | per-function alert/error decode + ack keys (incl. roof InfoPopUp, clock-sync) |
| [re-gap-inventory.md](business-logic/re-gap-inventory.md) | open gaps + what needs a capture |
| [DECISIONS.md](business-logic/DECISIONS.md) | dated RE changelog (resolved questions + dead ends ruled out) |
| [feature-availability.md](business-logic/feature-availability.md) | `Installed`-gating; which functions publish on which vehicle |
| [status-states-audit.md](business-logic/status-states-audit.md) | per-characteristic field/offset reference (largely historical) |

## Function → doc

| BLE function | Primary doc(s) |
|---|---|
| cooler, airheater | [cooler-airheater.md](business-logic/cooler-airheater.md) |
| campingmode | [climate-stairs.md](business-logic/climate-stairs.md), [signals.md](business-logic/signals.md) |
| lighting, energy, water, satelliteantenna, roof | [lighting-energy-water-sat-roof.md](business-logic/lighting-energy-water-sat-roof.md) |
| roof, roofaircondition, stairs, livingroomheater | [climate-stairs.md](business-logic/climate-stairs.md), [re-gap-inventory.md §A3](business-logic/re-gap-inventory.md) |
| vehicle (char 1004), general (1001) | [re-gap-inventory.md §A2/§A6](business-logic/re-gap-inventory.md), [DECISIONS.md](business-logic/DECISIONS.md) |
| control frames / actuation (all) | [control-and-actuation.md](business-logic/control-and-actuation.md) |

## Glossary (quick)

**GATT char** — a BLE attribute (State char = telemetry read/notify; Control char = write).
**arm / heartbeat** — the +1 counter on char 1003 that unlocks actuation; **latch** — the load
stays on after the heartbeat stops (one-shot arm). **leave-unchanged sentinel** — the value a
full-packet frame writes for fields it isn't changing (2-bit: `3`). **terminal-15** — ignition.
**EXLAP** — VW's XML pub/sub protocol, an alternative WiFi/TCP transport. **MERGED_AMBIGUOUS** —
a dictionary field whose offset the extractor couldn't place (resolved in `overrides.py`).
**Installed bit** — per-function flag; only installed functions publish.
