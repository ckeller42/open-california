# Signal catalog — four-source cross-validation

**What this is.** A guardrail so a decoded BLE signal can never again be silently
missing or mislabeled. Every protocol field (state + control, all functions) has an
explicit **`surface`** or **`omit`** decision recorded in
[`protocol/signals.yaml`](../../protocol/signals.yaml), cross-validated against four
independent sources. A pytest guardrail enforces it on every commit.

This was built after two real gaps surfaced by inspection: the leisure-battery
current (`ITwoBattBemAfs` → `batt2_current`) was decoded but never emitted, and
`solar_power` was briefly mis-suppressed. The audit found **~85 decoded-but-unsurfaced
signals** in total; this catalog resolves them and prevents recurrence.

## The four sources

1. **Decompiled app** (authoritative): field getters + scales (`×0.1` etc.), debug-log
   names, enum classes. Harvested by [`tools/app_scales.py`](../../tools/app_scales.py).
2. **Live captures**: sampled decoded values across vehicle states, range-checked by
   `kind` (voltage 8–16 V, percent 0–100, …) — the `OUT-OF-RANGE` check in the auditor.
3. **GUI**: `ui/screens/*.yaml` widgets + composeResources string keys. A field the GUI
   displays *must* be surfaced.
4. **VW documentation**: the official T7 California owner's manual (citations only — the
   copyrighted PDF is never committed). Confirms terminology/units: 80-Ah lithium
   *leisure* battery monitored by **voltage and current**, starter+leisure via a
   split-charge relay (the DC-DC "vehicle power"), electrohydraulic hold-to-move pop-up
   roof, separate fresh/waste water indicators.

## The tools

- `python3 -m tools.audit_signals --report` — triangulate + print discrepancies
  (`COVERAGE-GAP`, `STALE`, `GUI-SHOWN-BUT-OMITTED`, `SCALE-MISMATCH`, `OUT-OF-RANGE`).
  Currently **clean**.
- `python3 -m tools.audit_signals --seed` — add a stub entry for any new field.
- `tools/triage.py` — documents every surface/omit decision (re-runnable, deterministic).
- `tests/test_signal_coverage.py` — **the guardrail**, three invariants:
  1. **Coverage** — every dictionary field has a catalog decision (new field ⇒ CI fails).
  2. **Surfaced ⇒ emitted** — every `surface` state field's canonical name is actually
     emitted by `semantics.interpret` (the check that would have caught `batt2_current`).
  3. **Control-placed** — surfaced control fields have a resolved bit offset.

## Coverage (87 surfaced / 130 omitted of 217 fields)

| function | surface | omit |
|---|---|---|
| airheater | 7 | 17 |
| campingmode | 5 | 5 |
| cooler | 5 | 19 |
| energy | 23 | 15 |
| general | 3 | 0 |
| generalpurposesignals | 0 | 21 |
| lighting | 19 | 21 |
| livingroomheater | 6 | 7 |
| roof | 2 | 5 |
| roofaircondition | 5 | 5 |
| satelliteantenna | 5 | 6 |
| stairs | 3 | 4 |
| water | 4 | 5 |

**Newly surfaced (were dropped):** leisure-battery current + per-source currents
(`dcdc/shore/solar_current`), leisure remaining-time, starter battery, `energy_mode` /
`derating_temp_active` / `sleep_warning`; living-room-heater air/water on + temperatures;
roof-A/C on/mode/fan/target-temp; satellite dish/selection/signal; air-heater
mode/air-distribution/runtime; all 16 lighting zone brightnesses; general SW versions.

**Omitted, with reasons:** `generalpurposesignals` (21 opaque `BitZero*`/`ByteZero*`
with no GUI or getter); `*InfoPopUp` (UI-transient); fault/warning bits (aggregated into
`faults[]`); feature-availability `*Installed` flags (surfaced as `installed`); granular
timer detail; and all **control** fields (definitions captured; command surfacing
deferred while control writes are blocked — see the write-gate issue).

**Delivery:** all 87 surfaced signals flow to InfluxDB (broad, via `numeric_fields`); a
curated subset is exposed as Home Assistant entities (`mqtt.ENTITY_SPECS`); not-installed
functions are `Installed`-gated, so they appear only on vans that have them.

Sources: [VW California owner manuals](https://en.volkswagenclub.net/manuals.php?ddlb_model=45),
[VW California Owners Club — user manuals](https://vwcaliforniaclub.com/resources/categories/california-user-manuals.7/).
