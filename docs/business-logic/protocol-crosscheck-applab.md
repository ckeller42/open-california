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
| "neutral flush" after a write is a lighting-only mechanism | **every** function gets an all-sentinel frame 500 ms after a write (cooler `ff771e3e1f1f`, heater `3f7b007f1f3f`, camping `ff`, energy `30`, lighting `0e00…`) | CONTRADICTED in spirit → S_SEQ_ACTUATE now documents it for all |

## Per-function frames (`control-and-actuation.md` recipes vs `control.build`)

| Intent | App frame | calictl frame | Verdict |
|---|---|---|---|
| camping master OFF | `fc` | `fc` | OBSERVED identical |
| heater immediate ON / OFF | `3d7b007f1f3f` / `3c7b007f1f3f` | `3d05003c0c00` (current values) | CONSISTENT (targeted bits equal; matches the 2026-07-08 HCI capture) |
| heater continuous OFF | `0f7b007f1f3f` after the confirm dialog | `0f05003c0c00` | CONSISTENT; no ON write exists (switch inert when off) |
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

## Not testable in the lab (unit-side; keep DEVICE/CAPTURE tier)

`0x0E` link drop on out-of-range values; water measurement-gating / stale latch; deep-sleep and
advertising stop when parked; real motor withhold (~3 s) and travel time; `1502` Mode-4 ramp
notifications; readback-is-an-echo; RPA rotation.
