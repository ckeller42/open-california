---
name: add-signal
description: Surface a new BLE signal in calictl end-to-end (catalog → semantics → dashboard/HA → audit). Use when adding or re-scaling a field from protocol/dictionary.yaml so it reaches Grafana/Home Assistant without tripping the coverage guardrail.
---

# Add / re-surface a signal

The pipeline is `dictionary.yaml → protocol.decode → semantics.interpret → signals.yaml
catalog → influx/mqtt`. Every dictionary field MUST have a catalog decision or CI fails.
See `ARCHITECTURE.md`.

## Steps

1. **Confirm the field exists** in `protocol/dictionary.yaml` (name/offset/width). If it's
   `MERGED_AMBIGUOUS`, resolve its offset in `calictl/overrides.py` from the app's `f()`
   builder — do NOT guess; the `sg.a(value, type)` 2nd arg is the TYPE (type 4=2-bit,
   6=4-bit, 3=16-bit, 7=8-bit, 5=32-bit, 1=16-bit), not the width.
2. **Catalog it** in `protocol/signals.yaml` (or `python3 -m tools.triage`): `decision:
   surface` needs a `name:`; `decision: omit` needs a `reason:`. Keys must exactly match the
   dictionary fields (both directions — no stale entries).
3. **Emit it** from the function's interpreter in `calictl/semantics.py` under the catalog
   `name`. Verify polarity against `ui/screens/*.yaml` / the decompiled getter when the app
   setter is inverted or combined (this is how the camping-lights inversion was missed).
4. **Run the guardrail**: `python3 -m pytest tests/ -q` and `python3 -m tools.audit_signals
   --report` (must be `clean`). The three invariants: every field has a decision; every
   surfaced state field is emitted; every surfaced control field is placed.
5. **Update sinks if surfaced**: Grafana panels don't auto-update — edit
   `calictl/deploy/camper-dashboard.json` (data auto-flows to InfluxDB by field name; only
   titles/units/new-panels need JSON edits) and push with `deploy/push_dashboard.py` (needs
   Grafana creds on buspi). HA MQTT discovery is automatic for installed functions.
6. **Don't label unverified scales with a unit** — several scales are UNVERIFIED
   (`docs/business-logic/signal-scales.md`).

## Gotchas
- Adding an emitted key that is NOT in the catalog is fine (guardrail only checks
  surfaced⇒emitted). Removing/renaming a surfaced key breaks CI.
- Bools/lists become InfluxDB fields (`influx.numeric_fields`: bool→1/0, list→`_count`).
