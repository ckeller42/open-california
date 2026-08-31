# csrc/ — the C port of the Camper Unit frame codec

C99, no malloc, no platform dependencies: compiles on any host for the parity tests
and under ESP-IDF for the planned ESP32 satellite (#154). Produced for issue #156 —
**one dictionary, two consumers**, no divergent protocol twin.

| File | What |
|---|---|
| `codec_dict.h` | **GENERATED** field tables from `protocol/dictionary.yaml` (+ `calictl/overrides.py`). **Never hand-edit** — regenerate with `python3 -m tools.gen_c_dict` (CI runs `--check`). |
| `codec.h` / `codec.c` | The dictionary-driven bit slicer — a line-for-line semantic port of `calictl/protocol.py` (`decode`/`encode`, MSB-first `bit i = byte[i/8] >> (7-i%8) & 1`, same validation order and error cases). |
| `ports.h` / `ports.c` | Portable decision logic shared with `calictl` (freshness stale-latch guard, plausibility anchors, roof SafetyCounter formula). |
| `codec_cli.c` | Batched line-protocol driver used ONLY by the test harness (`tests/test_codec_parity.py`) — not part of the ESP build. |

## Build (host)

```
cc -std=c99 -Wall -Wextra -Werror -O1 csrc/codec.c csrc/ports.c csrc/codec_cli.c -o codec_cli
```

The pytest harness runs exactly this command into a temp dir (session-scoped) and
skips cleanly when no C compiler is present — the `codec-parity` CI job is the
enforcement point.

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
```

Malformed input answers `ERR parse` and never crashes (the fuzz pass feeds it
garbage on purpose).
