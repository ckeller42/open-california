# open-california — documentation

```{danger}
**Use at your own risk — no warranty, no liability.** This software sends control
writes to a real vehicle (heaters, roof, electrical loads) and can damage your vehicle,
void your warranty, or create unsafe conditions. It is provided "AS IS" with **NO
WARRANTY and NO LIABILITY** — if it breaks your car, that is your responsibility. Use it
only on a vehicle you own. Independent reverse-engineering project, **not affiliated with
Volkswagen**; no VW intellectual property is distributed. MIT-licensed; see `LICENSE`
and `DISCLAIMER.md`.
```

Reverse-engineered control + monitoring for the VW California T7 camper unit over BLE.
Start with {doc}`features`: per function of the camper unit, what calictl shows, what it can
control, where, and how well that is proven. {doc}`architecture` shows how the pieces fit.

```{toctree}
:maxdepth: 1
:caption: Getting started

What calictl does today <features>
architecture
raspberry-pi-setup
howto-pair-your-camper
howto-esp-first-flash
howto-esp-wifi-setup
howto-esp-standalone
```

```{toctree}
:maxdepth: 1
:caption: Reference

hardware
protocol
firmware
ios-app
ios-app-radio-checklist
ios-app-siri-checklist
ios-development-setup
screenshots
glossary
```

* {ref}`genindex` — every glossary term and API object

```{toctree}
:maxdepth: 1
:caption: For contributors

protocol-sequences
protocol/frame-layouts
protocol/signal-matrix
cross-language-codec
api
simulation-and-testing
mock-unit-guide
howto-android-phone-lab
UI-DESIGN-RATIONALE
building-the-docs
```

## Reverse-engineering lab notes

The dated RE lab notes — how each protocol fact was established, with captures, decompile
citations and dead ends — are **evidence, not product documentation**. They are published
separately from these docs: [Reverse-engineering notes](https://ckeller42.github.io/open-california/business-logic/index.html)
(also browsable [in the repository](https://github.com/ckeller42/open-california/tree/main/docs/business-logic)).

## Requirement traceability

Requirements are `sphinx-needs` objects written inside the code's docstrings (`.. req::`) and
linked to the tests that verify them (`.. test:: … :links:`). A link to a requirement that does
not exist fails the doc build; a requirement without a test builds and shows an empty
"incoming" column. {doc}`building-the-docs` has the details.

Protocol sequence diagrams ({doc}`protocol-sequences`) are `spec` objects that link to the
requirement each one depicts, so the table below shows the diagram → requirement → test chain.

```{needtable}
:types: req, spec, test
:columns: id, title, status, outgoing, incoming
:style: table
```
