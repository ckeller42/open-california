---
name: add-control-command
description: Use when adding or changing a calictl control command (a frame calictl WRITES to the unit) — a new `control.BUILDERS` action, a gate in `command_precondition`, a CLI/API/web control, the mock's reaction to a write, or an app recording that must replay byte-exact. The read-side twin is `add-signal`.
---

# Add a control command (the write path, end to end)

A command is done when calictl sends **exactly the app's frame**, refuses what the app would not
send, the mock reacts like the unit, and an app recording replays clean. Every step below exists
because skipping it shipped a bug once (A2, 2026-10, PR #241: 8 tasks, 5 review rounds).

## Order

| # | Step | Where | Done when |
|---|---|---|---|
| 1 | **Decompile the write site** — the setter AND its UI caller | `decompile-app` skill ("Why passes miss logic") | every argument classified: unit-reported state / UI-local / constant / user input; what the app does after the write (ack wait, follow-up pull, toast) is written down |
| 2 | **Builder** | `calictl/control.py` (`BUILDERS`, lighting in `control/_lighting`) | full-packet frame; every untargeted field at its **leave-unchanged** value (2-bit = 3, wider = the dictionary default — e.g. cooler TimerHour/Min 30/62) |
| 3 | **Gate** | `command_precondition` in `control.py` | mirrors the app's UI gate with the unit's own wording; raises `control.CommandError` → `/api/command` 400 |
| 4 | **Daemon** | `calictl/serve.py` | one connection, one arm (a preface + write goes through `actuate(..., preface=...)`, never two sessions); under the `_ble` lock |
| 5 | **Mock** | `tools/mock_unit.py` | reacts like the unit: acks/echoes as **one-off** 1502-style events (`push(..., event=True)`), refusals as ACK-and-ignore |
| 6 | **Semantics** (if the command has readable state) | `semantics.py` + `calictl/webui/semantics.js` + `tools.gen_semantics_vectors` | `add-signal` + `verify-semantics-polarity` |
| 7 | **CLI / API / web UI** | `cli.py`, `serve.py`, `webui/app.js` | `webui-change` skill |
| 8 | **App recording** | `tools/applab/scenarios.py` → `tests/vectors/app/<name>.jsonl` | `tools/capture_diff.py` replays it: every write `action`/`flush`/`app-only`, no `gap`, no `error` (`app-lab`, `thinky-bench`) |
| 9 | **Docs, same PR** | `control-and-actuation.md`, the function's business-logic doc, `evidence-ledger.md` tier, `protocol-alignment.md`, `protocol-crosscheck-applab.md`, `ui/screens/*.yaml`, AGENTS.md if a rule changed | the tier says what was really shown: DECOMPILE / mock-tested / APP-RECORDED / DEVICE |
| 10 | **ESP twin** (when the ESP may send it) | `firmware/components/cali_core` builders + generated vectors | byte-identical to `control.build`; never a roof builder |

## Rules that each cost a review round

- **State comes from the unit, never from our own write.** The daemon's latch (`serve._last`) is fed
  by the unit's own frames (poll + push). Latching the frame we just wrote makes "unit-reported" mean
  "our guess" when the unit ACKs but does not apply (R4). A readback echo is never proof.
- **Unknown state → refuse, never default.** A command that needs current state (e.g. wake-up
  `enabled`) must not fill a gap with a default: a time-only wake-up edit with no known config would
  silently disarm an alarm set from the phone. Fetch it the way the app does (REQUEST_CONFIG), then
  refuse if still unknown (R5). Keep the builder pure so a recorded frame still replays.
- **Edits carry the app's provenance.** If the app builds a field as "changed value, else the
  unit-reported config", calictl does the same — not "else UI state", not "else 0".
- **The replay must latch like the daemon.** `capture_diff` keeps state across frames the same way
  `serve` does (`semantics.lighting_config` merge); otherwise a later unrelated notify wipes the
  state the next recorded write depends on.
- **Mock acks are events, not state.** A sticky ack (e.g. Mode/ProfileNumber left in the state frame)
  makes the UI show "favourite N active" after a save. Match the app's ack rules from step 1 exactly
  (e.g. save acked only by Mode 4 + PN N; activate by PN == N).
- **Pin the clock.** A builder that packs time (next local HH:MM as UTC) needs a frozen clock across
  *every* `control.build` call in a test and the replay, plus TZ and year-end cases.
- **Tests must fail without the code.** Show red first. For e2e, see `webui-change` (async
  `wait_for_function` never waits; overlay-only asserts prove nothing).

## Quick checks

```bash
python3.13 -m pytest tests/ -q                                  # incl. e2e
python3.13 -m tools.capture_diff tests/vectors/app/<name>.jsonl --recording
tools/ci.sh ci && tools/ci.sh webcheck
python3.13 -m tools.gen_semantics_vectors --check && python3.13 -m tools.gen_c_dict --check
```

## Common mistakes

| Symptom | Cause |
|---|---|
| App shows "Something went wrong" after a write against the mock | mock treated a leave-unchanged sentinel as a value, or never echoed what the app verifies (wake-up: echo within ~3 s) |
| Replay `gap` / wrong frame for a recorded write | replay state lost between frames, or clock not pinned |
| UI shows a value the unit never reported | latch fed from our own write, or the UI invented a default when state was null |
| Two BLE connections for one command | preface and write in separate `actuate` calls — the unit has one slot |
