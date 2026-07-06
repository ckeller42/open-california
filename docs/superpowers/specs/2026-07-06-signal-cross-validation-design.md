# Signal cross-validation: catalog + four-source audit + coverage guardrail

**Date:** 2026-07-06
**Status:** Design approved, pre-implementation
**Goal:** Make it impossible for a decoded VW California BLE signal to be silently
missing or mislabeled. Triangulate every field (all functions, **state and
control**) against four independent sources — decompiled app, live captures, GUI
specs, VW documentation — record the verdict in a single catalog, and enforce it
with an automated guardrail so gaps like the missing `batt2_current` (leisure
battery draw) and the mislabeled/mis-suppressed `solar` cannot recur.

## Problem

The `protocol/dictionary.yaml` extractor captures **every** field, but the
`semantics` (state) and `control` (command) layers hand-pick a curated subset and
**silently drop the rest with no trace**. A count of dictionary state fields never
referenced in `semantics.py` found **~85 decoded-but-unsurfaced signals** across
the 13 functions — e.g. `livingroomheater` `TemperatureAir/TemperatureWater/
StateAir/StateWater`, `lighting` 18 per-zone brightnesses, `roofaircondition`
`Fanspeed/Temperature`, `satelliteantenna` `SignalLevel`, `airheater`
`RunningTime/AirDistribution`, energy `EnergyMode/SleepWarning/derating`. Some are
genuine noise (INFO-popup/fault-detail bits), many are real signals. `batt2_current`
was simply the one the owner happened to notice. Nothing enforces that a field is
*accounted for*, and nothing cross-checks a surfaced field's label/scale.

## Success criteria

- **Coverage:** every dictionary field (state + control, all functions) has an
  explicit `surface` or `omit` decision; no field can be silently absent.
- **Correctness:** every `surface` field is actually emitted by the runtime, with
  a label/unit/scale cross-checked against the four sources; disagreements are
  reported and resolved (or flagged `UNVERIFIED` with the reason recorded).
- **Non-recurrence:** adding a newly-extracted field, or dropping a surfaced one,
  **fails CI** until reconciled.
- **Provenance:** each decision is traceable to its four-source evidence.

## Component 1 — the catalog (`protocol/signals.yaml`)

Single source of truth. One entry per field, keyed `<function>.<state|control>.<FieldName>`:

```yaml
energy:
  state:
    ITwoBattBemAfs:
      decision: surface           # surface | omit
      name: batt2_current         # canonical name emitted by semantics (surface only)
      unit: A                     # human unit or null
      scale: "0.1"                # multiplier applied to raw; "raw" or UNVERIFIED allowed
      kind: leisure_battery       # grouping: battery|source|climate|level|state|fault|timer|info
      sources:
        app:  "getter ×0.1, log 'ITwoBatt' (xf/<cls>.java:NNN)"
        live: 319                 # a sampled decoded value (sanity anchor)
        gui:  infoPage_battery_value_text   # string key / widget id, or null if not shown
        vwdoc: "T7 Handbuch: Zweitbatterie" # section/term citation, or null
      confidence: high|medium|low
    LandNotAvailable:
      decision: omit
      reason: "fault/status bit — surfaced via the faults[] aggregate, not standalone"
      sources: {app: "...", gui: null, vwdoc: null}
  control:
    State:
      decision: surface
      name: state
      ...
```

Rules: `surface` requires `name`; `omit` requires `reason`. `scale`/`unit` may be
`UNVERIFIED` but must say so. The catalog is our authored artifact (no app source),
committed.

## Component 2 — the auditor (`tools/audit_signals.py`, stdlib-only)

Reads the dictionary + the four sources, seeds/updates the catalog, and prints a
ranked **discrepancy report**. Per field it gathers:

1. **App (authoritative):** from the decompiled tree — the field's getter method and
   **scale** (×0.1 etc.), its debug-log name, and any enum class. Extends the
   extractor (which has name/offset/width) with the *scale + provenance* that was
   missing. Merged-class getters keyed by the per-function control/state char.
2. **Live:** sample each field's decoded value from a small **capture set** across
   vehicle states (engine-off, engine-on, charging — reuse existing captured frames
   plus new `calictl raw` snapshots), then **range-sanity** by `kind`
   (voltage 8–16 V, percent 0–100, level 0–15, current within a sane band). Flags
   scale/sign errors (e.g. a raw 16-bit current read as garbage).
3. **GUI:** match each field to a `ui/screens/*.yaml` widget / composeResources
   string key. **A field the GUI displays MUST be `surface`**; the GUI label is the
   canonical human name.
4. **VW doc:** keyword-match the concept to the fetched T7 manual for terminology and
   units.

Report ranks: **coverage gaps** (dictionary field with no catalog decision) and
**disagreements** (app scale vs catalog scale; GUI-shown but semantics-dropped;
label mismatch; live value out of range). Deterministic; re-runnable.

## Component 3 — the guardrail (`tests/test_signal_coverage.py`, pytest)

Three invariants, enforced on every commit:

1. **Coverage:** `set(dictionary fields) == set(catalog entries)` for both state and
   control, all functions. A newly-extracted field with no decision **fails**; a
   stale catalog entry **fails**.
2. **Surfaced ⇒ emitted:** for every `decision: surface` state field, its `name`
   appears in `semantics.interpret(function, decoded)` output (flattened) for that
   function; for every surfaced control field, its `name` is produced by that
   function's `control` builder / accepted command set. Dropping a surfaced field
   **fails** — the exact check that would have caught `batt2_current`.
3. **Scale/label consistency:** where the catalog `confidence: high`, its
   `scale`/`unit` must match the app-getter evidence recorded by the auditor;
   `UNVERIFIED` is tolerated but asserted to be explicitly marked.

## Component 4 — applying it (the sweep)

1. Run the auditor to seed `signals.yaml` with all fields + four-source evidence.
2. Triage each field: **surface** the real signals (LR-heater temps, lighting zone
   brightness, roof-AC fan/temp, satellite signal, air-heater runtime, energy mode,
   the leisure/source currents already added) with canonical names/units/scales;
   **omit** the noise (popup/fault-detail bits) with reasons.
3. Update `semantics` (state interpreters) and `control` + `mqtt`/`influx` to emit
   the newly-surfaced signals. Add HA entities for the ones worth surfacing there.
4. Guardrail goes green and stays green.

## Component 5 — VW documentation

Fetch the official VW California T7 digital owner's manual (web). Store **only
citations** — section titles, terms, units, URLs — in the catalog's `vwdoc` fields.
**Never** commit the copyrighted PDF or reproduce long passages (one short quoted
term max, attributed). It is the human-facing cross-check (terminology/units), the
weakest of the four for bit-level mapping and treated as such.

## File structure

| File | Responsibility |
|---|---|
| `protocol/signals.yaml` (create) | The catalog — per-field decision + four-source provenance |
| `tools/audit_signals.py` (create) | Auditor: gather 4 sources, seed/update catalog, print discrepancy report |
| `tools/app_scales.py` (create) | Harvest getter scales/enums from the decompiled tree (used by auditor) |
| `tests/test_signal_coverage.py` (create) | The three guardrail invariants |
| `calictl/semantics.py` (modify) | Surface the newly-catalogued state signals |
| `calictl/control.py` (modify) | Surface/validate catalogued control fields |
| `calictl/mqtt.py`, `calictl/influx.py` (modify) | Expose worth-surfacing signals as entities/points |
| `docs/business-logic/signal-catalog.md` (create) | Human-readable rollup of the audit findings + method |

## Testing

- **Unit:** auditor source-parsers (app scale extraction, GUI key matching, range
  rules) on fixtures; catalog schema validation.
- **Guardrail:** the three invariants (Component 3) — these are the regression net.
- **Live (gated):** the capture-set sampling reads the vehicle (engine off/on where
  possible); range-sanity results reviewed once, not in CI.

## Out of scope (YAGNI)

- Auto-generating `semantics` from the catalog (keep interpreters hand-written; the
  catalog *checks* them, doesn't replace them).
- Fixing control-write enablement (issue #2) — this validates control-field
  *definitions*, not whether writes take effect.
- Per-value semantic verification of every enum against live actuation.

## Open items (resolve during implementation)

- Exact current/power **scales** for the 16-bit `I*Afs` fields (app getter vs live).
- Which decompiled getter classes carry the scale for each measurement family.
- Whether `generalpurposesignals` (21 opaque `BitZero*` fields) is entirely `omit`
  (no GUI, no getter) or has any surfaced meaning.
