# Cross-language codec (Python + C from one dictionary)

The {term}`ESP32 satellite <Satellite>` reuses the reverse-engineered BLE protocol instead of
forking a divergent twin. One source of truth — `protocol/dictionary.yaml` (+ `calictl/overrides.py`) —
feeds two consumers:

- **Python**: {mod}`calictl.protocol` loads the {term}`dictionary` at runtime (unchanged,
  stdlib-only);
- **C**: `tools/gen_c_dict.py` generates `csrc/codec_dict.h` (checked in,
  CI-verified fresh), consumed by the hand-ported `csrc/codec.c` slicer.

Golden vectors (`tests/vectors/`) are the language-neutral specification:
generated with an *independent* bit slicer, proven against the Python reference,
then replayed byte-identically through the C codec plus a seeded differential
fuzz pass (`tests/test_codec_parity.py`; the `codec-parity` CI job). See
`csrc/README.md` for the build and the `codec_cli` line protocol.

## Portable decision-logic ports

Beyond the stateless codec, the water freshness decisions are shared as C in
`csrc/ports.c` — the correctness-critical part the ESP read path needs, pinned to the Python originals by `tests/test_ports_parity.py`:

```{req} The C freshness stale-latch decision matches calictl
:id: R_PORT_FRESHNESS
:status: implemented
:tags: codec, esp32, water

The C port of the water stale-latch guard
({func}`calictl.freshness.implausible_water_drop`) shall reproduce the Python
ladder exactly over plain integer liters + presence flags, so the ESP never
publishes the parked {term}`latch <Water latch>` as truth. Its ramp debounce
({func}`calictl.freshness.settle_water`, C `freshness_settle`) shall match too, step for step
over the `sequences` vectors. The ESP links `csrc/ports.c` — one C water guard
(`session.c` calls it). Cross-sample state stays platform-native.
```

The plausibility anchors and the roof {term}`SafetyCounter` are not ported: the ESP drives no
roof and runs no anchors, so they stay Python-only (`calictl/anchors.py`, `calictl.device`).

## Not ported: the postcheck

`calictl/postcheck.py` (the post-write applied-check) is deliberately **not**
ported. The satellite does no readback after a write: its command answer
carries `applied` null for a sent command without a refusal reason (a refusal answers `applied: false` with `refused`; an HTTP error has its own `error` shape; {doc}`firmware`). `set_check` reads *interpreted* state
and lazily imports `control`/`semantics`, so a raw-field-space check table would
be a redesign. The table maps `(function, what)` to interpreted got/want pairs
(cooler numerics additionally read the raw decode); regenerating it in raw space
needs exactly the dictionary + the lighting zone map already recorded in
`calictl/postcheck.py`. If the satellite gets a readback, generate the raw-space
table from the dictionary, test-first, with the same vector treatment.
