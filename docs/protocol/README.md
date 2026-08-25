# Protocol documentation views

Everything in this directory is **generated** from `protocol/dictionary.yaml` (+
`protocol/signals.yaml`) by scripts in `tools/`. Never hand-edit these files — edit the
source YAML, then regenerate:

| File | Generator |
|---|---|
| `reference.md` | `python3 -m tools.gen_protocol_reference --out docs/protocol/reference.md` |

`tests/test_protocol_views.py` fails CI if a committed file here doesn't match a fresh
regeneration, so a stale view can't merge silently.
