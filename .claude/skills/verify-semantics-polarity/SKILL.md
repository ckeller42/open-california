---
name: verify-semantics-polarity
description: Verify the polarity/combination of a decoded field against the app's own getter and the unit's on-screen display before trusting it. Use whenever you add or change an interpretation in calictl/semantics.py, or when the auditor flags SEMANTIC-REVIEW-NEEDED — this is the repo's #1 recurring bug class (inverted/combined getters).
---

# Verify semantics polarity

The coverage guardrail (`tests/test_signal_coverage.py`) validates **presence + scale**, never
**interpretation**. So an inverted or combined getter passes CI silently — this is how the
camping-lights inversion, the `usb_powered` master-gate, the `quiet_scheduled` Mode-4 mislabel, and
the lamp-map error all shipped before an owner photo caught them. A raw `bool(field)` is a guess
until checked against the app's real getter AND the unit's screen.

Do this whenever you touch an interpretation in `calictl/semantics.py`, or when
`python3 -m tools.audit_signals --report` prints `SEMANTIC-REVIEW-NEEDED` for a field.

## The three ground-truth sources (in trust order)

1. **The unit's own on-screen display** — the highest authority. An owner photo of the camper
   screen catches mislabels a readback echo hides. If you have one, it wins over everything below.
2. **The app's decompiled getter** — the real polarity/combination logic. On buspi (the canonical
   decompile machine): `DECOMPILE_SRC=<sources>` then trace the getter for the field, or
   cross-check `mapping.enigma`. `ui/screens/*.yaml` is the machine-usable mirror of the app UI —
   **authoritative for app-UI semantics**.
3. **The readback state-char** — a **write-through echo**, NOT proof. Never conclude polarity from
   "I wrote 1 and read back 1." The `1502`/`1102` Mode-4 notifications are the truthful feedback
   channel; the readback lies.

## Checklist

1. **Run the auditor**: `DECOMPILE_SRC=<sources> python3 -m tools.audit_signals --report`. Any
   `SEMANTIC-REVIEW-NEEDED` line names a field whose app setter/getter is **inverted** or
   **combined** — those are the ones this skill exists for.
2. **Find the app getter** (not a naive bool): what does the app return for this field? Look in
   `ui/screens/*.yaml` first, then the decompiled getter on buspi. Watch for:
   - **Inversion** — `0` = on (e.g. camping lights). The field name lies about polarity.
   - **Combination / gating** — the true signal is `A AND B`, not `A` alone (e.g. rear USB is
     physically off unless the camping master is on → surface `master_on AND usb_charger` as
     `usb_powered`, PR #111). Grep memory / `docs/business-logic/signals.md` for the field.
   - **Mode enums** — a "scheduled/night" state is a *mode value*, not a separate boolean
     (`quiet_scheduled` = Mode 4, not a `NightTimerSet` flag).
3. **Cross-check the screen**: if an owner screen photo exists for this state, it overrides the
   getter. If none exists, note the check as decompile-only in the evidence ledger and flag it as
   owed at the van.
4. **Encode the finding in code**, not a comment: the `semantics.interpret` logic must apply the
   real polarity/combination. Add a derived field for combined signals (like `usb_powered`).
5. **Record provenance**: update `docs/business-logic/signals.md` + `evidence-ledger.md` (tier:
   DECOMPILE vs CAPTURE vs SCREEN), and supersede any now-wrong `inferred/UNVERIFIED` claim. The
   `finding-artifact-sync` hook will remind you of the other artifacts (GUI labels, dashboard).
6. **Add a test** pinning the corrected polarity so it can't silently regress.

## Red flags that mean STOP and verify

- The field name implies a polarity (`*_on`, `*_enabled`) but you haven't read the getter.
- You're about to write `bool(field)` or `field == 1` for a control/state flag.
- The auditor said `SEMANTIC-REVIEW-NEEDED` and you're tempted to trust the readback echo.
- A value "confirms" only because you wrote it and read it back — that's the echo, not actuation.
