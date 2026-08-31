---
name: protocol-change
description: Use when editing protocol/dictionary.yaml, protocol/signals.yaml, or calictl/overrides.py — any protocol-fact change (new/moved field, scale, valid set, frame length, catalog decision). These files fan out into generated artifacts and guardrails that fail CI when stale.
---

# Protocol change — the regen + guardrail fan-out

One edit to the protocol facts touches several DERIVED artifacts. CI (`codec-parity`,
`test`) and the pre-commit hook enforce freshness mechanically — this skill is the
order of operations so you fix things forward instead of chasing red CI.

## The fan-out map

| You edited | Must regenerate / update | Enforced by |
|---|---|---|
| `protocol/dictionary.yaml` | golden vectors + C header (below) + a catalog decision per new field | `codec-parity` CI, pre-commit 4e, `test_signal_coverage` |
| `calictl/overrides.py` (offsets/ranges/frame bytes) | golden vectors + C header | `codec-parity` CI, pre-commit 4e |
| `protocol/signals.yaml` (catalog) | semantics emit + dashboard/HA if surfacing changed | `test_signal_coverage`, auditor |

## Steps (in order)

1. **Make the protocol edit.** Remember: `vehicle` (char 1004) is HAND-ADDED to
   `dictionary.yaml` — `tools/extract_protocol.py` does NOT emit it, preserve it on
   any regen of the dictionary itself.
2. **Regenerate the codec artifacts** (REQUIRED after any dictionary/overrides edit):
   ```
   python3 -m tools.gen_codec_vectors     # tests/vectors/camper_codec.json
   python3 -m tools.gen_c_dict            # csrc/codec_dict.h — NEVER hand-edit
   ```
   Stage both outputs with your change. Verify: both commands with `--check` exit 0.
   Generation fails loudly (that's correct, fix the cause) if a pinned-length control
   function has an unplaced field or a valid value ≥ 32.
3. **Catalog every new/changed field** — `python3 -m tools.triage` or edit
   `protocol/signals.yaml` (`surface` needs a `name`, `omit` needs a `reason`).
   A dropped/unaccounted field fails `tests/test_signal_coverage.py`.
4. **If a SURFACED signal changed**: emit it in `calictl/semantics.py`, then follow
   the `add-signal` skill (dashboard + HA + auditor). Grafana dashboards do NOT
   auto-update.
5. **Run the guardrails**:
   ```
   python3 -m pytest tests/test_codec_vectors.py tests/test_gen_c_dict.py \
       tests/test_codec_parity.py tests/test_signal_coverage.py -q
   DECOMPILE_SRC=<sources> python3 -m tools.audit_signals --report   # on buspi
   python3 -m pytest tests/ -q                                        # full suite
   ```
6. **Docs**: prose citing `Field@offset` is pinned by `test_doc_offset_consistency` —
   update `docs/business-logic/` notes + `evidence-ledger.md` if the meaning changed
   (the finding-artifact-sync hook lists the mirror targets).

## Gotchas

- The vectors/header are **byte-pinned**: even a comment-only reflow of
  `dictionary.yaml` that changes parsed fields regenerates differently — always run
  step 2 rather than reasoning about whether it's needed.
- Semantics correctness is NOT auto-checked (only presence + scale). An inverted or
  combined app getter needs manual polarity verification against `ui/screens/*.yaml`
  / the decompiled getter — see CLAUDE.md "Semantics correctness".
- The C side ships raw fields; `semantics.py` is deliberately NOT ported. Scale or
  enum changes belong in Python semantics + the signals catalog, never in `csrc/`.
