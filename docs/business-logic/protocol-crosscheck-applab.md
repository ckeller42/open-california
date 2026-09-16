# Protocol docs × app lab — claim-by-claim cross-check

Every protocol claim this repo makes, checked against the **real CaliforniaOnTour app** running in
the Android emulator against `tools/applab/fake_unit_ble.py` (the fake unit = `tools/mock_unit.py`
over the unit's GATT layout). The lab shows what the *app* does on the wire; it cannot show what
the *unit* does (those rows say NOT TESTABLE and keep their DEVICE/CAPTURE tier). Verdicts:
**OBSERVED** (seen directly, frame/log cited), **CONSISTENT** (calictl's behaviour is compatible
with what the app does), **CONTRADICTED** (doc fixed in the same change), **NOT TESTABLE**.

App version 5.0.8.3028 (`apkeep`, apk-pure), emulator API 34 arm64, fake unit seeded from
`tests/scenarios/firmware/baseline-0410.json`. Owner rule: re-run when the app version changes.

## Session (`docs/protocol-sequences.rst` S_SEQ_CONNECT / S_SEQ_NOTIFY)

| Claim | Observation (2026-09-16) | Verdict |
|---|---|---|
| connect → discoverServices → requestMtu | logcat: `connect()` → `refresh()` → `discoverServices()` → `onSearchComplete` → `configureMTU(26)` | OBSERVED |
| MTU requested = 26 | the app asks for 26; Android 14's stack sends `client_rx_mtu 517` on the wire and negotiates 517 (`GATTC_ConfigureMTU … mtu:517 user mtu 26`) | OBSERVED — doc nuance added (app value vs wire value) |
| `1002` is not read/validated during connect | **it is**: first read after MTU is handle 0x0013 = `1002`; value compared with `SHA-256(VIN)[16:32]`; mismatch → disconnect ~20 ms later + "Wrong vehicle found" | **CONTRADICTED → fixed** (S_SEQ_CONNECT, protocol-alignment) |
| then read `1001` VERSION, then `1004` (auth-gated) | `READ general` then `1004` → ATT error 0x05 (insufficient authentication) → SMP pairing (LE Secure Connections, passkey entry) | OBSERVED |
| `1004` read forces bonding, Android shows the pairing UI | pairing request arrives as a notification ("Pairing request") → PIN-style dialog; 30 s SMP timeout if not answered | OBSERVED |
| subscribe-all = exactly 12 notifiable chars | 12 CCCD writes with `0100` (handles 0x001B 1004, 0x0023 1102, 0x002B 1202, 0x0030 1302, 0x0038 1402, 0x0040 1502, 0x0048 1602, 0x0050 1702, 0x0058 1802, 0x0060 1902, 0x006E 2002, 0x0076 2102) + `0200` on the GATT Service-Changed indication | OBSERVED |
| every state char read once after subscribing | `READ` of all 14 state chars within ~1 s of CONNECTED, `general` read repeatedly | OBSERVED |
| writes are WRITE_TYPE_DEFAULT (with response) | 1798 `ATT_WRITE_REQUEST`, 0 `ATT_WRITE_COMMAND` | OBSERVED |
| app disconnects if the version check fails | not exercised (baseline versions accepted) | NOT TESTED |

## Heartbeat / arming (S_SEQ_ACTUATE, `control-and-actuation.md` §2)

| Claim | Observation | Verdict |
|---|---|---|
| `1003` = 4-byte BE +1 counter, seed 0 | writes to 0x0016 start at `00000000` and increment by 1 | OBSERVED |
| cadence 500 ms (`zf/d J0=500L`) | wire cadence **0.75–0.77 s** (timer + GATT round-trip; write-with-response serialised) | OBSERVED — doc notes observed cadence |
| the unit drops a link with no heartbeat ~15 s | unit-side; the fake unit implements it (needed: Android kept a stale bonded link that blocked advertising → "No vehicle found") | NOT TESTABLE (modelled) |
| a control write is a full-packet frame | every write is the full frame length (6 / 1 / 16 / 1 / 5 bytes) | OBSERVED |
| untargeted fields = leave-unchanged sentinels | 2-bit fields at 3, wider fields at the model default (`7b 00 7f 1f 3f`, `77 1e 3e 1f 1f`) | OBSERVED — mock fixed to honour them |
| the 2026-07-05 failed cooler write failed because "model defaults = a garbage command" (Level 7, actions 3) | the app itself sends exactly those defaults in every frame (`fc771e3e1f1f`) and the unit accepts them; the 2026-07-05 write was made **before the 1003 heartbeat arm was known** — the missing arm explains it, not the defaults | CONTRADICTED (explanation superseded; the arm-gate finding of 2026-07-07 stands) |
| "neutral flush" after a write is a lighting-only mechanism | **every** function gets an all-sentinel frame 500 ms after a write (cooler `ff771e3e1f1f`, heater `3f7b007f1f3f`, camping `ff`, energy `30`, lighting `0e00…`) | CONTRADICTED in spirit → S_SEQ_ACTUATE now documents it for all |

## Per-function frames (`control-and-actuation.md` recipes vs `control.build`)

| Intent | App frame | calictl frame | Verdict |
|---|---|---|---|
| camping master OFF | `fc` | `fc` | OBSERVED identical |
| heater immediate ON / OFF | `3d7b007f1f3f` / `3c7b007f1f3f` | `3d7b007f1f3f` / `3c7b007f1f3f` | OBSERVED identical (was CONSISTENT-only while calictl carried the readback's level/run-time; since 2026-09-16 every untargeted heater field is the app's sentinel) |
| heater continuous OFF | `0f7b007f1f3f` after the confirm dialog | `0f7b007f1f3f` | OBSERVED identical; no ON write exists (switch inert when off) |
| heater temperature / run-time sliders | `3f78007f1f3f` (level 8) / `3f7b003c1f3f` (60 min) | same | OBSERVED identical |
| heater **departure timer** arm / stop | "Start timer" → `3f3b017f1f3f` (`OperationModeAirHeater=3`, `OperationModeCombined=1`); "Stop" → `3f0b007f1f3f` (Mode 0); status bar "Inactive • Timer: 12:00" only while armed | `timer_start` / `timer_cancel` → same bytes (new 2026-09-16) | OBSERVED → **the doc's "a2() is combined-heater-only / no heater timer trigger exists" claim was wrong**; `a2(AIR_HEATER)` IS the timer arm on this AirHeater-only van (`uh/d.java` toggle → `rf/b.java` a2/j4) |
| heater run-time picker / timer time picker | "Start heating at" wheel: swipes did not move it in the emulator (no frame captured); the slider path `B0` stays decompile-verified | `timer HH:MM` → `3f7b007f161e` | NOT TESTED (picker interaction) |
| cooler OFF / manual / automatic quiet | `fc771e3e1f1f` / `ff271e3e1f1f` / `ff471e3e1f1f` | `3c4309001606` / `3d2309001606` / `3d4309001606` | CONSISTENT (Mode 2 / 4 confirmed) |
| cooler timer start | `f7771e3e1f1f` (box off only; hours at sentinel) | `354309001606` | CONSISTENT |
| lighting All lights ON | `0c10…eeee…` + `0e00…` | same | OBSERVED identical |
| lighting lamp on | `0904…` one nibble = **11 DEFAULT** | `0904…` nibble = 10 | CONSISTENT (value convention differs) |
| lighting save profile | `0104…e00eeeee` + commit, then `0d0c…` (REQUEST_CONFIG @13) + commit | `save_profile 1` identical first frame | OBSERVED identical (app adds a config pull) |
| energy mode Max | `10` → `30` | `10` | OBSERVED identical; ECO absent from the selector on this profile |
| lamp → zone map (`LIGHT_ZONES`) | 9 lamps → nibble positions match every entry; no app lamp on `LSix` | OBSERVED (DEVICE map confirmed) |

## Roof (S_SEQ_ROOF, `alert-states.md`)

| Claim | Observation | Verdict |
|---|---|---|
| needs ignition ON | page shows "Switch on the ignition …" and no controls while `TerminalOneFive=0` | OBSERVED |
| app streams `[dir][counter]` @ ~500 ms while held | page open (no press) streams `[00][counter]` every ~500 ms; **while held the rate is ~8 frames/s** with the counter still +1 per ~500 ms (same value on 4 consecutive frames) | **CONTRADICTED on cadence → S_SEQ_ROOF updated**; the mock's per-frame +1 validity rule was wrong and made the app abort after 2 `01` frames — fixed to monotonic-and-advancing |
| direction bytes open `01` / stop `00` / close `04` | all three seen: `00` idle/pre-validation, `01` while OPEN held, `04` while CLOSE held (the app fell back to `00` once `Position` reached 0) | OBSERVED |
| press-and-hold OPEN moves the roof; app stops at the limit | held 12 s: `01` frames at ~8/s until the fake unit reported `Position=1` (open), then the app itself fell back to `00` frames while still held; page text "The roof is closed" → "The roof is open" | OBSERVED (app-side auto-stop at the limit confirmed) |
| stale `SafetyCounterValid=1` with no stream | app reports "Function in use — another user is already using this function" when the unit still says valid=1 while the app has not validated its own counter | OBSERVED → mock now expires validity 1.5 s after the last frame |
| unit reports `SafetyCounterValid` (1402 bit 7) | unit-side; the app refuses to move until the fake unit raises it | NOT TESTABLE (modelled) |
| InfoPopUp 1/4/5/6/7/10/11 dialogs | all seven dialogs with the tabled texts | OBSERVED |
| InfoPopUp 2/3/9/12 (untraced) | 2/3/12 → tile "Function currently in use", 9 → "Only possible when stationary" | OBSERVED → added |
| 8/13/14 | nothing shown | OBSERVED |

## Fault dialogs and equipment gating (fake unit `set <fn> <Field>=<v>` while the app watches)

Every fault code the dictionary knows was injected one at a time; the app pops a toast the moment
the readback changes, on whatever page is open. Texts are verbatim in `alert-states.md` (§4
energy, §5 water) and `cooler-airheater.md` (heater ErrorCode); the web UI's `*_MSG` tables
carry them, and `semantics.water` now surfaces `fresh_alert` / `waste_alert`.

| Claim | Observation | Verdict |
|---|---|---|
| heater `ErrorCode` 1–5 = low battery / low fuel / system error / heating-time exceeded / not possible | five distinct dialogs; 4 is worded **"Emission limit exceeded"** (switched off automatically, on again at ≥ 5 km/h), 5 **"deactivated"** (not while the engine or the auxiliary water heater runs); `FaultTriggerBit=1` alone shows nothing | OBSERVED (meanings sharpened → web texts) |
| cooler `Error` 1–3 = error / emergency mode / door open | "Please visit a workshop." / "Refrigerator box in emergency mode." / "Please close the refrigerator box door fully." | OBSERVED identical to `COOLER_FAULT_MSG` |
| energy flags → alert IDs (alert-states §4) | 11 of 13 flags pop a dialog; `WarningLevelTwo` and `EnergyModeNotSelectable` none. `SleepWarning` = **charging cable still plugged in** | OBSERVED (`SleepWarning` meaning corrected; web "Issues" readout now shows the texts, not flag names) |
| water `FreshWaterInfoPopUp` 1/2/3+7/4/5, `WasteWaterInfoPopUp` 1/2/3 | all dialogs as tabled; fresh 6 and 8–15 nothing | OBSERVED → fields surfaced (`fresh_alert`, `waste_alert`, catalog + Influx codes) |
| SoC display = level × 10 % for 0–10, nothing above | 0–10 → 0–100 %; **11, 12, 15 → "0 %"** | OBSERVED (calictl keeps `None` for 11–15) |
| `Installed=0` hides a function | cooler / heater / roof tiles vanish from the overview the moment their `Installed` bit drops | OBSERVED |
| stairs / roof-A/C / LR-heater / satellite screens exist in the app (not fitted on this van) | `Installed=1` on the fake unit adds the tiles: **Step** "Folded"; **Living area heating** "Off • Level 20 • Mix 6A (fuel and current)" + **Hot Water Mode** "Off • Eco • Mix 6A (fuel and current)"; **Satellite system** "Aerial switched off"; **roof A/C** renders raw resource keys `!ROOF_AIR_CONDITION` / `!OFF • !LEVEL 0 • !COOLING • !SPEED 0` (unlocalised — the app's roof-A/C surface is unfinished in this build) | OBSERVED (vocabulary for the four unverified functions: Step, Living area heating, Hot Water Mode, Satellite system) |

## The hardware side: trace the real unit, replay it through the mock

The lab validates the **app**; the unit's own behaviour (push cadence, countdown rates, coupling,
frame bits the dictionary might miss) is validated from a **trace of the real van**: run buspi's
daemon with `CALICTL_BLE_TRACE=~/ble.jsonl` (`calictl/trace.py` — one JSON line per notify /
read / write / link event; `CALICTL_BLE_TRACE_HEARTBEAT=1` to include the 1003 beats), let it
run through a drive / a heater cycle / a roof move, then `python3 -m tools.trace_compare
~/ble.jsonl`. The report lists: state frames that do **not** round-trip through the dictionary
(bits the unit uses that we don't model), per-char notification cadence (the mock pushes energy
once a second — the unit was seen at ~3 Hz live), `RunningTimeinAction` and
`AgeOneBattValuesMinutes` rates vs the mock's ±1/min, roof `Position` transitions with timings,
and the terminal-15 → `campingmode.Enable`/master-shed coupling delay. A difference is a mock bug
or a new protocol fact — never a reason to touch the trace. Results land in this file's tables.

### First real-unit trace (buspi, van awake, 2026-09-16 17:08–17:20)

| Claim | Observation | Verdict |
|---|---|---|
| every state frame round-trips through the dictionary | 14/14 functions, 88 frames: repack == raw for every frame | OBSERVED (dictionary covers every bit the unit sent) |
| `1602` energy streams ~3×/s while connected | **not observed**: with the persistent session up and the heartbeat ticking, each of the 12 subscribed chars notified **exactly once, right after its CCCD write**, then nothing for the rest of the link (no change-driven push in 150 s; energy values did change between links) | **CONTRADICTED** (the 2026-07 "3×/s" note) → mock/fake now push once on subscribe, not 1 Hz |
| `1003` heartbeat keeps the link up indefinitely | **no unit-side drop found.** The heartbeat-traced run (524 s, 11 links) shows every link is one calictl **poll cycle**: connect → 12 on-subscribe pushes → 14 reads → 5–7 beats (median gap 0.73 s, max 1.32 s) → calictl's own disconnect 0–0.9 s after the last beat; links are 5–6 s long and start every ~39 s (`POLL_INTERVAL=30` + the cycle). The "up twice within 40 s" was the web-driven persistent session being **released for web-UI idleness** (`persistent session released (web UI idle)` ~3 s to 2 min after each `up`) and re-armed by the next `/api/session` nudge. One genuine `read_all: link dropped at airheater` occurred right after the service restart (hci0 contention on start-up), none afterwards | OBSERVED (resolved; the 30–40 s pattern is calictl's cadence, not the unit) |

## Not testable in the lab (unit-side; keep DEVICE/CAPTURE tier)

`0x0E` link drop on out-of-range values; water measurement-gating / stale latch; deep-sleep and
advertising stop when parked; real motor withhold (~3 s) and travel time; `1502` Mode-4 ramp
notifications; readback-is-an-echo; RPA rotation.
