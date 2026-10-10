---
name: real-unit-evidence
description: Use when a recording of the REAL camper unit (phone HCI snoop of the real app, buspi's ~/ble.jsonl trace, btmon, ESP console, an app-screen dump, an owner photo of the unit screen) or a decompile finding must become a protocol fact in open-california — comparing captured frames with calictl, choosing an evidence tier, wording the claim, or updating the evidence ledger, protocol docs and the mock after a capture.
---

# Real-unit evidence: recording → verified fact → docs + simulation

A fact is only as strong as what was actually compared. The decompile says what the app *intends*,
the fake unit says what *we modelled*, only the real unit says what *happens*. This skill turns a
recording into a scoped claim with a tier, lands it in every doc that states it, and makes the
simulation match. How to run the phone: `phone-app-lab`. The emulator + fake unit: `app-lab`.
Reading the app's code: `decompile-app`.

## 1. Sources and what each can prove

| Source | How | Tier it can give |
|---|---|---|
| real app → real unit, HCI snoop | `phone-app-lab` (marks in `~/applog/marks.txt`, `snoop_att` + `gatt.txt`) | **CAPTURE** (frame), plus unit replies/pushes seen on that link |
| buspi's long-running trace | `~/ble.jsonl` (`CALICTL_BLE_TRACE`), weeks of reads/notifies; `python3 -m tools.trace_compare ~/ble.jsonl` | patterns over time (CAPTURE of the unit's frames, calictl's side) |
| buspi's link at HCI level | `sudo btmon -w ~/applog/x.snoop` while `serve` runs | what BlueZ really sent/got (e.g. a "notify" that is BlueZ re-delivering a read) |
| ESP satellite | timestamped console (`thinky-bench`) | **BOARD** / DEVICE rows for the firmware |
| app screen vs calictl | `uiautomator dump` text vs `/api/state` + `last_state.json` pulled right before **and** after | **APP-DISPLAY** (decode + scale, not physics) |
| owner at the van | photo of the unit screen, photons, motor | **DEVICE** — the unit's own display is ground truth |
| real app → fake unit | `app-lab`, `tests/vectors/app/*.jsonl` | **APP-RECORDED** / **APP-OBSERVED** — the app's side only |
| decompile | `decompile-app`, private repo `ckeller42/californiaontour-re` | **DECOMPILE** / **DV** — intent, never wire truth |

**Mine `~/ble.jsonl` for counts, not anecdotes.** Example (a 2026-10-10 count — recompute for your own range): `FreshWaterLevel=1` in
6098 of 10726 `1302` frames = the unit's "not measuring" latch; a real measurement ramps 1 → real
in ~4 s after the water system powers. Count with a short `python3 -c` over the JSONL, quote the
numbers and the date range.

## 2. Compare

```bash
# on buspi, ~/open-california — frame calictl would send, in the state the capture was taken in
python3 -c "
import json, os
from calictl import control, overrides, protocol
funcs = protocol.load(); overrides.apply(funcs)    # forget overrides.apply -> wrong frames
last = json.load(open(os.path.expanduser('~/.cache/calictl/last_state.json')))['last']
print(control.build(funcs, 'cooler', 'level', 4, last).hex())"
# phone snoop -> snoop_att lines "time dir conn op char hex" -> capture_diff's --frames input
python3 -m tools.applab.phone.snoop_att <btsnoop> ~/applog/gatt.txt > ~/applog/att-N.txt
awk '$2==">" && $4=="WRITE_REQ" && $5!="1003" {print $5": "$6}' ~/applog/att-N.txt > ~/applog/frames-N.txt
python3 -m tools.capture_diff ~/applog/frames-N.txt <fn>/<case> --frames   # vs tools/scenarios/<fn>/<case>.yaml
python3 -m tools.trace_compare ~/ble.jsonl                       # real trace replayed through the mock
```

`--recording` is only for app-lab JSONL (`tests/vectors/app/`), not for a phone snoop. Command
names and values: `control.BUILDERS` and `control-and-actuation.md` §5 (e.g. `campingmode usb on`).
`last` must be the state **before** the action: copy `last_state.json` right before the tap
(the file after the tap may already hold the new value, or lag a poll). A frame that differs is a
finding — write the diff, never "identical".

Align clocks first: snoop time vs `marks.txt` (one run was 2 h off). A frame "matching" the wrong
action is a clock error until a mark proves otherwise.

## 3. Write the claim — the claim shape

Every claim you write (ledger row, doc sentence, PR body, commit) has these parts:

1. **What was compared** — the list of actions/frames, by name and hex prefix.
2. **What was not** — exclusions named (e.g. "roof motor frames not compared", "Mode-8 not exercised").
3. **State it was taken in** — parked / ignition / camping / which values were already set. A capture
   validates only that state (a `0` that was the current value is not a sentinel).
4. **Source + version + date** — `buspi:~/applog/<file>`, app `versionName`, unit AmbSw, date.
5. **Tier** — the tier the *source* supports (table above), not a stronger one.

Example (illustrative): "CAPTURE 2026-10-10 (app 5.4.0.3036, unit 0410, parked, camping on;
`buspi:~/applog/att-N.txt`): cooler `power` on/off and `level` 3→4 byte-identical to `control.build`;
camping USB `f3`/`f7` identical. Not compared: roof (no move), air heater."

Rules that keep the claim honest:

- A **decompile name is a hypothesis** until a caller, a string, or a recording confirms it.
- **The fake unit is not the real unit.** A behaviour seen only against the mock stays APP-OBSERVED /
  mock-tested; never label a unit code from it (roof InfoPopUp 2 was called "in use" from the fake —
  the real unit showed the pre-open checklist).
- **Don't trust the first render** of an app screen: it shows what it held before the fresh frame.
- Before writing "calictl does / does not X", read the code and the ledger row for X.
- Before recording a decompile name, grep `evidence-ledger.md` + `DECISIONS.md` for it: names
  have been retracted before (NightTimerSet, `X1` = setCoolingLevel).
- A long-trace pattern is a count (n of N, date range) plus what the value means, not "most frames".

## 4. Land it — same PR (owner rule)

Always: the ledger row, the protocol doc that states the fact, the crosscheck row, and a CORRECTED
mark on every older row it contradicts. When the mock's behaviour differs from the capture: the mock
change + its test. When calictl's behaviour changes: DECISIONS. No row is optional because it is late.

| What | Where |
|---|---|
| tier row (tier, date, source path) | `docs/business-logic/evidence-ledger.md` |
| frame/sequence/cadence | `docs/protocol-sequences.rst`, `docs/business-logic/protocol-alignment.md`, `signals.md` (field meaning → `protocol-change` skill) |
| command tier | `control-and-actuation.md` §5 Status column |
| doc claim checked | `protocol-crosscheck-applab.md`: OBSERVED / CONSISTENT / CONTRADICTED / NOT TESTABLE |
| behaviour change | `docs/business-logic/DECISIONS.md` entry, newest first |
| simulation | `tools/mock_unit.py` / `tools/fake_unit_peripheral.py` match the capture; test whose docstring cites the capture (`.. test::` id **registered in `docs/api.rst`**); `tools/scenarios/<fn>/<case>.yaml` for a byte-identical frame |
| older contradicted rows | mark **CORRECTED <date>** / superseded with the reason; never delete silently |

Then `tools/ci.sh` (includes the sphinx `-W` docs build) before the PR.

## 5. Privacy

Raw captures, bugreports, `btsnoop_hci.log`, decoded ATT stay on buspi `~/applog/` — cite the path.
VW app screenshots → private `californiaontour-re` `screens/` only; never open or capture the VIN
pages. Never in open-california: VIN, unit MAC (write `20:81:9A…`), passkeys, APKs, images of the
app. Check `gh pr diff N --name-only` for image/video files before pushing.

## Common mistakes

| Mistake | Fix |
|---|---|
| "every non-motor action byte-identical" | list the actions compared; name the ones not compared |
| "calictl's roof sends no heartbeat" written from memory | read the A1 ledger row + `device.py` first — it does |
| roof InfoPopUp 2 = "in use", from the fake unit | label unit codes only from the real unit |
| `control.build` without `overrides.apply(funcs)` | wrong cooler frame (`fd7700000000`) — call it |
| "0 = leave-unchanged" from one capture | the capture only shows the state it was in; test with a non-zero state |
| no app version on the observation | `dumpsys package … grep versionName` with every capture |
| frame matched to an action by eye | align snoop clock to a `marks.txt` mark first |
| docs autodoc section removed with the entry you meant to drop | it carried unrelated entries — edit the entry, keep the section |
| new `.. test::` id, CI fails in sphinx-needs | register the test in `docs/api.rst` |
| old ledger row left contradicting the new one | mark it CORRECTED/superseded in the same PR |
| fact only in DECISIONS/ledger | also the protocol docs + crosscheck + mock (table §4) |
| capture/screenshot staged | unstage; cite `buspi:~/applog/…` instead |
| real-unit capture converted into `tests/vectors/app/` | that dir is app-vs-fake recordings (`app-lab`); a real-unit frame becomes a `tools/scenarios/` case + a ledger row citing buspi |
