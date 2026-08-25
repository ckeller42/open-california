# Plan: generated protocol documentation views (from dictionary.yaml)

Generate documentation VIEWS from the single source of truth (`protocol/dictionary.yaml` +
`protocol/signals.yaml`) — no hand-maintained parallel schemas. Each view is a generator + an output
committed to the repo + a freshness test (mirrors the existing `screens.json` freshness check in
`ci.yml`), so a stale view fails CI/`tools/ci.sh`.

## Global Constraints (bind every task)

- **Runtime `calictl/*` stays stdlib-only at import.** These are `tools/` scripts — they MAY use
  PyYAML (`import yaml`), like the other `tools/` (e.g. `tools/audit_signals.py`). Do NOT touch
  `calictl/`.
- **Single source:** read ONLY `protocol/dictionary.yaml` and `protocol/signals.yaml`. Never
  hard-code field layouts. Load with `yaml.safe_load`.
- **Deterministic output:** stable ordering (sort functions/fields deterministically) so regenerating
  produces a byte-identical file — the freshness test diffs generated-vs-committed.
- **Each generator:** `python3 -m tools.<name> --out <path>` writing to `docs/protocol/`. A no-arg or
  `--check` mode is NOT required; the freshness test regenerates to a temp path and diffs.
- **Each task adds a freshness test** in `tests/test_protocol_views.py` (create it in Task 1, append
  in later tasks): regenerate to a tmp file, assert it equals the committed output. Use `python3.13`
  semantics; PyYAML is available in the test env.
- **ruff-green** (config in `pyproject.toml`; house style: `%`-formatting fine). Must pass
  `python3 -m ruff check .`.
- Verify with `tools/ci.sh` (selects py3.11+, runs ruff + suite + guards).
- Commit each task (the pre-commit hook runs the gate). Keep commits single-concern.

## Dictionary shape (for implementers)

`dictionary.yaml` maps `functions:`? NO — it's a top-level map of `<function_name>: {state_services:
[..], control_source: .., control_fields: [..], state_fields: [..]}`. Each field is
`{name, offset, width, default?, raw_range?, value_semantics?, ...}` — `offset` may be an int or the
string `MERGED_AMBIGUOUS`. `signals.yaml` records per-field surface/omit decisions + provenance
(inspect its actual shape before relying on keys).

## Task 1 — Markdown protocol reference (view #1)

`tools/gen_protocol_reference.py` → `docs/protocol/reference.md`. Per function: a header (name + state
char UUID(s) from `state_services`, control source), then a table of state_fields and (separately)
control_fields — columns: field name, offset, width, default, raw_range, value_semantics. Note
`MERGED_AMBIGUOUS` offsets explicitly. Deterministic order (functions sorted; fields in
dictionary order). Create `tests/test_protocol_views.py` with the freshness test.

## Task 2 — Frame bit-layout diagrams (view #2)

`tools/gen_frame_layouts.py` → `docs/protocol/frame-layouts.md`. Per characteristic (state + control),
a byte/bit MAP: for each byte offset, which field(s) occupy which bits (MSB-first — the codec is
MSB-first per CLAUDE.md). A Markdown table or fenced ASCII grid (byte | bits 7..0 | field) is fine;
skip fields with `MERGED_AMBIGUOUS` offset from the positional map but LIST them under the char as
"ambiguous offset". Append its freshness test.

## Task 3 — Wireshark Lua dissector (view #3)

`tools/gen_wireshark_dissector.py` → `docs/protocol/vwcamper.lua`. Emit a Wireshark Lua dissector that
registers ProtoFields for each state characteristic's fields (bit-masked per offset/width) so a
captured BTATT value on that char handle self-annotates. It need not be runnable-tested; the freshness
test asserts the generated `.lua` equals the committed one AND that it is non-empty / contains a
`Proto(` and a ProtoField per surfaced state field. Keep it best-effort + clearly commented as
generated. Append its freshness test.

## Task 5 — Provenance / coverage matrix (view #5)

`tools/gen_signal_matrix.py` → `docs/protocol/signal-matrix.md`. From `signals.yaml`: a matrix of every
catalogued field — columns: function, field, decision (surface/omit), surfaced-name (if any),
evidence tier / provenance, semantic-review status — whatever keys `signals.yaml` actually carries
(inspect it). Deterministic order. Append its freshness test.

## Task 6 — "Tested on this hardware" documentation (hand-authored, NOT generated)

Document the hardware this project has been reverse-engineered + tested against. TWO places:
- **Brief, in the repo `README.md`** (a "Tested on" subsection): the vehicle (VW California **T7**
  camper control unit), the installed equipment it was verified against, and the host (Raspberry Pi
  "buspi", aarch64/Debian, Python 3.13, BlueZ/bleak).
- **Detailed, in Sphinx** (`docs/sphinx/` — a new page, e.g. `hardware.rst`, added to the toctree):
  the unit's GATT service map (the `0000XXXX-6c77-4b7d-bbf6-a5e587701f3d` vendor service, per-function
  chars), software/version fields observed (`AmbSwVersion` 0409/0410, `CmSwVersion`,
  `CommunicationVersion` — from `general` char 1001), the equipment profile (INSTALLED: cooler,
  campingmode, water, energy, lighting; NOT installed on this unit: stairs, living-room heater,
  roof-A/C, satellite, solar), and the per-function verification tier (which are live-actuation-
  verified vs decompile/static-only — cross-reference `docs/business-logic/control-and-actuation.md`).

**HARD CONSTRAINT (CI + pre-commit enforce this):** do NOT commit the real vehicle BLE MAC (the OUI
`20:81:9A:…` is blocked by regex) or a VIN (17-char + WV2ZZZ context is blocked). Use a **placeholder**
like `AA:BB:CC:DD:EE:FF` / "«unit MAC»" and name only the generic VW prefix `WV2ZZZ` if a VIN format
must be shown. Verify with `tools/ci.sh vendor-check` before committing. Source the facts from
`CLAUDE.md` "Known state", `docs/business-logic/`, and the memory notes — cite, don't paste vendor
material. No freshness test needed (hand-authored).

## Notes

- Add a short `docs/protocol/README.md` (Task 1) explaining these are GENERATED — edit the yaml +
  regenerate, never hand-edit — and how to regenerate (`python3 -m tools.gen_*`).
- Kaitai `.ksy` (view #4) is deliberately deferred to public-release (a generator over the same yaml).
