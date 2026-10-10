Cross-language codec (Python + C from one dictionary)
=====================================================

The planned ESP32 satellite (issue `#154 <https://github.com/ckeller42/open-california/issues/154>`_)
reuses the reverse-engineered BLE protocol instead of forking a divergent twin
(issue `#156 <https://github.com/ckeller42/open-california/issues/156>`_). One
source of truth — ``protocol/dictionary.yaml`` (+ ``calictl/overrides.py``) —
feeds two consumers:

- **Python**: :mod:`calictl.protocol` loads the dictionary at runtime (unchanged,
  stdlib-only);
- **C**: ``tools/gen_c_dict.py`` generates ``csrc/codec_dict.h`` (checked in,
  CI-verified fresh), consumed by the hand-ported ``csrc/codec.c`` slicer.

Golden vectors (``tests/vectors/``) are the language-neutral specification:
generated with an *independent* bit slicer, proven against the Python reference,
then replayed byte-identically through the C codec plus a seeded differential
fuzz pass (``tests/test_codec_parity.py``; the ``codec-parity`` CI job). See
``csrc/README.md`` for the build and the ``codec_cli`` line protocol.

Portable decision-logic ports
-----------------------------

Beyond the stateless codec, the water freshness decisions are shared as C in
``csrc/ports.c`` — the correctness-critical part the ESP read path needs, pinned to the Python originals by ``tests/test_ports_parity.py``:

.. req:: The C freshness stale-latch decision matches calictl
   :id: R_PORT_FRESHNESS
   :status: implemented
   :tags: codec, esp32, water

   The C port of the water stale-latch guard
   (:func:`calictl.freshness.implausible_water_drop`) shall reproduce the Python
   ladder exactly over plain integer liters + presence flags, so the ESP never
   publishes the parked latch as truth. Its ramp debounce
   (:func:`calictl.freshness.settle_water`, C ``freshness_settle``) shall match too, step for step
   over the ``sequences`` vectors. The ESP links ``csrc/ports.c`` — one C water guard
   (``session.c`` calls it). Cross-sample state stays platform-native.

The plausibility anchors and the roof SafetyCounter were ported too (#156) and removed
again: the ESP has no roof and runs no anchors, so they stay Python-only
(``calictl/anchors.py``, ``calictl.device``).

Parked: the postcheck port (re-evaluation gate)
-----------------------------------------------

``calictl/postcheck.py`` (the post-write applied-check) is deliberately **not**
ported yet: ``set_check`` reads *interpreted* state and lazily imports
``control``/``semantics``, so a raw-field-space check table would be a redesign,
and the ESP's raw decoded-state store does not exist until #154 lands. The
16-row table maps ``(function, what)`` to interpreted got/want pairs (cooler
numerics additionally read the raw decode); regenerating it in raw space needs
exactly the dictionary + the lighting zone map already recorded in
``calictl/postcheck.py``. **Gate:** port when #154 defines its raw decoded-state
store — generate the raw-space table from the dictionary then, test-first, same
vector treatment.
