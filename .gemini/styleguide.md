# Review style guide — open-california

`CLAUDE.md` is the authority; this is the review-facing subset. Flag violations of these repo
invariants — they are the high-value, non-obvious checks a generic reviewer misses. Prefer a few
substantive findings over many nits (CodeRabbit already covers style).

## Hard invariants — flag any violation

1. **Runtime `calictl/*` is stdlib-only at import.** `bleak`, `paho.mqtt`, `influxdb_client`, and
   `yaml` must be imported **lazily inside functions**, never at module top level. A new top-level
   `import bleak` / `import yaml` / `import paho` / `import influxdb_client` in `calictl/` is a bug
   (tests run without these installed). Tooling in `tools/` may import PyYAML freely.
2. **`serve` is the single BLE owner.** Never open a second BLE connection from the daemon path;
   all adapter access is serialized by the `asyncio.Lock` in `serve.py`. Flag any new BLE
   connect/scan that bypasses that lock.
3. **Every `dictionary.yaml` field needs a catalog decision** in `signals.yaml` (`surface` + name,
   or `omit` + reason). A dropped/unaccounted field fails CI — flag added/removed fields with no
   matching catalog entry.
4. **The dictionary is the single source of truth.** `csrc/codec_dict.h` and `tests/vectors/*.json`
   are **generated** — flag any hand-edit; they must be regenerated (`tools/gen_c_dict.py`,
   `tools/gen_codec_vectors.py`) and the C and Python codecs must stay byte-identical.
5. **Control frames are full-packet, MSB-first.** Encoders must resend every field; unchanged
   fields carry the leave-unchanged sentinel (usually `3` for a 2-bit field), not `0`.

## Semantics correctness — the recurring bug class

The coverage guard checks presence + scale, **never interpretation**. Scrutinize any change in
`calictl/semantics.py`:

- A bare `bool(field)` or `field == 1` for a state/control flag is suspect — the app getter may be
  **inverted** (`0` = on, e.g. camping lights) or **combined** (`A AND B`, e.g. `usb_powered` =
  master AND usb_charger). Ask whether the polarity was verified against the app getter, not assumed.
- The state-char **readback is a write-through echo** — it is not proof of actuation. Flag comments
  or logic that treat a readback value as confirmation.
- **Don't attach a unit to an unverified scale** (SoC is a coarse 0–15 level, not %; several
  currents/temps are raw). Flag a `%`/`A`/`°C` label on a value marked UNVERIFIED.

## Safety / privacy — always flag

- **No vendor material or vehicle PII in the diff**: no APK/decompiled sources/VW manuals/captures,
  no real BLE MAC (use the `AA:BB:CC:DD:EE:FF` placeholder), no VIN. (A PreToolUse hook and a CI job
  also enforce this.)
- **Control writes act on a real vehicle** (heaters, roof, loads). Flag any change to the write path
  (`control`, `overrides`, `device.actuate`, the roof/heartbeat logic) that weakens a precondition
  gate, the write-enable flag, or the arming/heartbeat sequence.

## Housekeeping the CI can't see

- Surfacing/renaming a signal? The **Grafana dashboard doesn't auto-update** — flag a signal change
  in `semantics.py`/`signals.yaml`/`mqtt.py`/`influx.py` that doesn't also touch
  `calictl/deploy/camper-dashboard.json`.
- New/changed public functions should carry Sphinx docstrings; requirements are authored as
  `sphinx-needs` `.. req::`/`.. test::` objects in the docstrings and traced to their tests.
