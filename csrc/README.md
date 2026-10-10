# csrc/ — the C port of the Camper Unit frame codec

C99, no malloc, no platform dependencies: compiles on any host for the parity tests
and under ESP-IDF for the ESP32 satellite (#154, `firmware/`). Produced for issue #156 —
**one dictionary, two consumers**, no divergent protocol twin.

| File | What |
|---|---|
| `codec_dict.h` | **GENERATED** field tables from `protocol/dictionary.yaml` (+ `calictl/overrides.py`). **Never hand-edit** — regenerate with `python3 -m tools.gen_c_dict` (CI runs `--check`). |
| `codec_chars.h` | **GENERATED** GATT characteristic short-id map (`calictl.protocol` state chars) + aux/heartbeat constants (`calictl/device.py`), for the ESP32 firmware's BLE layer (#154). Same generator/freshness gate as `codec_dict.h`. |
| `pairing_consts.h` | **GENERATED** pinned pairing state-machine enums/timeouts/names, mirroring `calictl/pairing.py`, for the ESP32 firmware's pairing wizard (#154). Same generator/freshness gate as `codec_dict.h`. |
| `net_consts.h` | **GENERATED** WiFi setup constants (`tools/wifi_consts.py`) for the firmware's network layer. Same generator/freshness gate. |
| `control_consts.h` | **GENERATED** from `calictl/control.py`: the ESP write allow-list (control char + exact frame length of the five functions the satellite may write — never the roof's), the lighting commit frame, zone/colour/mode tables, builder constants and the `command_precondition` gate texts, for the C control twin (spec B). Same generator/freshness gate; its golden vectors are `tests/vectors/control.json` (`python3 -m tools.gen_control_vectors --check`). |
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
W reset | W <now_ms> <nf|-> <pf|-> <ng|-> <pg|-> → OK [0|1] | ERR parse
A [key=value ...]                       → OK <bitmask>   | ERR parse
C <seed> <tick_ms> <elapsed_ms>         → OK <ctr> <hex4> | ERR parse
```

- `D`/`E` — the frame codec (`codec_decode` / `codec_encode`), driven by `tests/test_codec_parity.py`.
- `F` — `freshness_implausible_drop` (port of `calictl/freshness.py:implausible_water_drop`): new/previous
  fresh and grey liters, `-` for a missing value; `1` = stale latch. Vectors: `tests/vectors/freshness.json`.
- `W` — `freshness_settle` (port of `calictl/freshness.py:settle_water`, the ramp debounce): the new
  reading and the baseline (`pf`/`pg`, `-` = no baseline) at `now_ms`; `1` = adopt. The candidate
  persists across `W` lines until `W reset`. Vectors: `sequences` in `tests/vectors/freshness.json`.
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

Consumed by the firmware (`firmware/`, see `docs/firmware.md`): the ESP-IDF `csrc` component
compiles `codec.c` for the ESP32-S3 — `codec_decode` for the `SNAP`/`/api/state` path and, since the
control path (#154 B), `codec_encode` for the **control twin** (`firmware/components/cali_core/control.c`, a C port
of `calictl.control`'s five builders held to `tests/vectors/control.json` by
`tests/firmware/test_control_parity.py`; `control_consts.h` above feeds it). The pairing state
machine lives in `firmware/components/cali_core/pairing_sm.c` (replays `tests/vectors/pairing.json`
via `pairing_consts.h`), not here.

Still parked:

- the **postcheck port** — the ESP does no readback check after a write (`applied` is never `true`
  on the satellite); see the "Parked" section of `docs/cross-language-codec.rst`.
