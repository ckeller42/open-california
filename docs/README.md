# docs — index

Start with **[architecture.md](architecture.md)** (the pipeline + control path + BLE
connect/actuate diagrams). Root **[../README.md](../README.md)** is the project front door;
**[../CLAUDE.md](../CLAUDE.md)** holds the hard rules. Requirement traceability builds from
docstrings via **[sphinx/](sphinx/)** (`sphinx -b html`).

## Reference docs (`business-logic/`)

| Doc | Covers |
|---|---|
| [architecture.md](architecture.md) | end-to-end pipeline, control path, connect+actuate + heartbeat diagrams |
| [remote-control-connect-gate.md](business-logic/remote-control-connect-gate.md) | **the write gate — SOLVED**: 1003 liveness heartbeat arms actuation; per-feature actuation status; dead ends ruled out |
| [remote-control-write-mechanics.md](business-logic/remote-control-write-mechanics.md) | full-packet frame model, write-type, the one-shot 500 ms release vs roof heartbeat |
| [re-gap-inventory.md](business-logic/re-gap-inventory.md) | open gaps + the RE changelog (what's decoded, what needs a capture) |
| [signal-catalog.md](business-logic/signal-catalog.md) · [signal-scales.md](business-logic/signal-scales.md) | surface/omit decisions; scales (several UNVERIFIED) |
| [alert-states.md](business-logic/alert-states.md) | per-function alert/error decode + ack keys (incl. roof InfoPopUp, clock-sync) |
| [feature-availability.md](business-logic/feature-availability.md) | `Installed`-gating; which functions publish on which vehicle |
| [status-states-audit.md](business-logic/status-states-audit.md) | per-characteristic field/offset reference (largely historical) |

## Function → doc

| BLE function | Primary doc(s) |
|---|---|
| cooler, airheater | [cooler-airheater.md](business-logic/cooler-airheater.md) |
| campingmode | [climate-stairs.md](business-logic/climate-stairs.md), [signal-catalog.md](business-logic/signal-catalog.md) |
| lighting, energy, water, satelliteantenna, roof | [lighting-energy-water-sat-roof.md](business-logic/lighting-energy-water-sat-roof.md) |
| roof, roofaircondition, stairs, livingroomheater | [climate-stairs.md](business-logic/climate-stairs.md), [re-gap-inventory.md §A3](business-logic/re-gap-inventory.md) |
| vehicle (char 1004), general (1001) | [re-gap-inventory.md §A2/§A6](business-logic/re-gap-inventory.md) |
| control frames / actuation (all) | [remote-control-connect-gate.md](business-logic/remote-control-connect-gate.md), [remote-control-write-mechanics.md](business-logic/remote-control-write-mechanics.md) |

## Glossary (quick)

**GATT char** — a BLE attribute (State char = telemetry read/notify; Control char = write).
**arm / heartbeat** — the +1 counter on char 1003 that unlocks actuation; **latch** — the load
stays on after the heartbeat stops (one-shot arm). **leave-unchanged sentinel** — the value a
full-packet frame writes for fields it isn't changing (2-bit: `3`). **terminal-15** — ignition.
**EXLAP** — VW's XML pub/sub protocol, an alternative WiFi/TCP transport. **MERGED_AMBIGUOUS** —
a dictionary field whose offset the extractor couldn't place (resolved in `overrides.py`).
**Installed bit** — per-function flag; only installed functions publish.
