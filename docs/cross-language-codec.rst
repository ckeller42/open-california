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

Beyond the stateless codec, three pure calictl decisions are shared as C in
``csrc/ports.c`` — the safety/correctness-critical parts an ESP read/write path
needs, pinned to the Python originals by ``tests/test_ports_parity.py``:

.. req:: The C freshness stale-latch decision matches calictl
   :id: R_PORT_FRESHNESS
   :status: implemented
   :tags: codec, esp32, water

   The C port of the water stale-latch guard
   (:func:`calictl.freshness.implausible_water_drop`) shall reproduce the Python
   ladder exactly over plain integer liters + presence flags, so the ESP never
   publishes the parked latch as truth. Cross-sample state stays platform-native.

.. req:: The C plausibility anchors match calictl
   :id: R_PORT_ANCHORS
   :status: implemented
   :tags: codec, esp32, diagnostics

   The C port of the plausibility anchors (``calictl/anchors.py``) shall apply
   the same physical-range constants and installed gates over plain numbers,
   reporting violations as a stable-ID bitmask, so a decode drift is caught on
   the ESP exactly as on the Pi. Raw→interpreted scaling is the ESP
   application's concern (no semantics port).

.. req:: The C SafetyCounter formula matches calictl
   :id: R_PORT_SAFETY_COUNTER
   :status: implemented
   :tags: codec, esp32, roof, safety

   The C port of the roof SafetyCounter shall compute
   ``(seed + elapsed_ms // tick_ms) & 0xFFFFFFFF`` and its 4-byte big-endian beat
   identically to ``calictl.device`` for integer-millisecond timelines including
   the 2^32 wrap — the write-gate liveness proof must be byte-identical or the
   unit withholds the roof motor. The 500 ms pump orchestration stays
   platform-native.

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
