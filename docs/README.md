# docs — index

Start with **[../ARCHITECTURE.md](../ARCHITECTURE.md)** (the pipeline + control path + arm
state machine, and the module walkthrough). Root **[../README.md](../README.md)** is the project front door;
**[../AGENTS.md](../AGENTS.md)** holds the hard rules. Requirement traceability builds from
docstrings via the Sphinx build in this directory (`conf.py`, `api.rst`; how-to in
[building-the-docs.md](building-the-docs.md)).

## Reference docs (`business-logic/`)

| Doc | Covers |
|---|---|
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | end-to-end pipeline, control path, arm state machine, module walkthrough |
| [business-logic/index.md](business-logic/index.md) | the RE lab notes — the write gate, signal catalog + scales, alert states, evidence ledger, gap inventory, dated DECISIONS changelog, and the per-function notes (all ~20 indexed there) |

## Function → doc

| BLE function | Primary doc(s) |
|---|---|
| cooler, airheater | [cooler-airheater.md](business-logic/cooler-airheater.md) |
| campingmode | [climate-stairs.md](business-logic/climate-stairs.md), [signals.md](business-logic/signals.md) |
| lighting, energy, water, satelliteantenna, roof | [lighting-energy-water-sat-roof.md](business-logic/lighting-energy-water-sat-roof.md) |
| roof, roofaircondition, stairs, livingroomheater | [climate-stairs.md](business-logic/climate-stairs.md), [re-gap-inventory.md §A3](business-logic/re-gap-inventory.md) |
| vehicle (char 1004), general (1001) | [re-gap-inventory.md §A2/§A6](business-logic/re-gap-inventory.md), [DECISIONS.md](business-logic/DECISIONS.md) |
| control frames / actuation (all) | [control-and-actuation.md](business-logic/control-and-actuation.md) |

## Glossary

Terms are defined once, in [glossary.md](glossary.md) (the Sphinx `glossary`).
