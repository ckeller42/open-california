# csrc/ — the C port of the Camper Unit frame codec

C99, no malloc, no platform dependencies: compiles on any host for the parity tests
and under ESP-IDF for the planned ESP32 satellite (#154). Produced for issue #156 —
**one dictionary, two consumers**, no divergent protocol twin.

| File | What |
|---|---|
| `codec_dict.h` | **GENERATED** field tables from `protocol/dictionary.yaml` (+ `calictl/overrides.py`). **Never hand-edit** — regenerate with `python3 -m tools.gen_c_dict` (CI runs `--check`). |
| `codec_chars.h` | **GENERATED** GATT characteristic short-id map (`calictl.protocol` state chars) + aux/heartbeat constants (`calictl/device.py`), for the ESP32 firmware's BLE layer (#154). Same generator/freshness gate as `codec_dict.h`. |
| `pairing_consts.h` | **GENERATED** pinned pairing state-machine enums/timeouts/names, mirroring `calictl/pairing.py`, for the ESP32 firmware's pairing wizard (#154). Same generator/freshness gate as `codec_dict.h`. |
| `codec.h` / `codec.c` | The dictionary-driven bit slicer — a line-for-line semantic port of `calictl/protocol.py` (`decode`/`encode`, MSB-first `bit i = byte[i/8] >> (7-i%8) & 1`, same validation order and error cases). |
| `ports.h` / `ports.c` | Portable decision logic shared with `calictl` (freshness stale-latch guard, plausibility anchors, roof SafetyCounter formula). |
| `codec_cli.c` | Batched line-protocol driver used ONLY by the two parity harnesses (`tests/test_codec_parity.py` for the codec, `tests/test_ports_parity.py` for the ports) — not part of the ESP build. |

## Build (host)

```
cc -std=c99 -Wall -Wextra -Werror -O1 csrc/codec.c csrc/ports.c csrc/codec_cli.c -o codec_cli
```

The pytest harnesses run exactly this command into a temp dir (session-scoped) and
skip cleanly when no C compiler is present — the `codec-parity` CI job is the
enforcement point.

## Regenerate / check / test

```
python3 -m tools.gen_c_dict               # rewrite codec_dict.h from the dictionary + overrides
python3 -m tools.gen_c_dict --check       # exit 1 if the checked-in header is stale
python3 -m tools.gen_codec_vectors        # rewrite tests/vectors/camper_codec.json
python3 -m tools.gen_codec_vectors --check
python3 -m pytest tests/test_codec_vectors.py tests/test_gen_c_dict.py \
                  tests/test_codec_parity.py tests/test_ports_parity.py -q   # what codec-parity runs
```

Both `--check` runs are also pinned by pytest (`test_checked_in_header_is_fresh`,
`test_checked_in_vectors_are_fresh`), so a plain `python3 -m pytest tests/` catches a
stale artifact even without a C compiler.

## Correctness contract

The golden vectors (`tests/vectors/*.json`) are the specification. They are
generated with an **independent** bit slicer (`tools/gen_codec_vectors.py`), proven
against the Python reference (`tests/test_codec_vectors.py`), and then every vector
plus a seeded differential fuzz pass must produce identical results from Python and
this C code (`tests/test_codec_parity.py`). Any dictionary change requires
regenerating both the vectors and `codec_dict.h`; stale artifacts fail CI.

## codec_cli line protocol

One output line per input line, in order. `-` denotes an empty frame.

```
D <func> <hex|->                        → OK Name=1 ...  | ERR nofunc|parse
E <func> <frame_bytes> [Name=val ...]   → OK <hex>       | ERR width|range|nodefault|frame|nofunc|parse
F <nf|-> <pf|-> <ng|-> <pg|->           → OK 0|1         | ERR parse
A [key=value ...]                       → OK <bitmask>   | ERR parse
C <seed> <tick_ms> <elapsed_ms>         → OK <ctr> <hex4> | ERR parse
```

- `D`/`E` — the frame codec (`codec_decode` / `codec_encode`), driven by `tests/test_codec_parity.py`.
- `F` — `freshness_implausible_drop` (port of `calictl/freshness.py:implausible_water_drop`): new/previous
  fresh and grey liters, `-` for a missing value; `1` = stale latch. Vectors: `tests/vectors/freshness.json`.
- `A` — `anchors_check` (port of `calictl/anchors.py:check`): keys `batt2_v soc2_level cooler_installed
  cooler_level quiet_from quiet_to roof_installed roof_position level_roll level_pitch`; answers the
  violation bitmask (`ANCHOR_*` in `ports.h`, `0` = clean).
- `C` — `roof_safety_counter` + `roof_beat_bytes` (port of `calictl/device.py`'s `_roof_safety_counter` /
  `_beat_bytes`): the counter and its 4-byte big-endian encoding. Vectors: `tests/vectors/safety_counter.json`.

`F`/`A`/`C` are driven by `tests/test_ports_parity.py`. Malformed input answers `ERR parse` (or
`ERR width` for an `E` value above 32 bits) and never crashes (the fuzz pass feeds it garbage on purpose).

## #154 status

Done (#156): the dictionary-driven codec plus three decision ports (freshness stale-latch,
plausibility anchors, roof SafetyCounter), each with golden vectors proven against the Python
original and replayed through the C build in CI.

Planned, not in `csrc/` yet:

- the **pairing state machine** in C — `calictl/pairing.py` is written for it (no strings, clock or
  addresses; pinned enum values), and `tests/vectors/pairing.json` is the language-neutral sequence
  spec it must replay (today only `tests/test_pairing_sm.py` runs it, against Python);
- the **postcheck port** — parked until #154 defines its raw decoded-state store (see the
  "Parked" section of `docs/cross-language-codec.rst`);
- the **ESP-IDF build** itself: nothing here is compiled for the ESP32 yet; only the host build
  above runs, in CI.
