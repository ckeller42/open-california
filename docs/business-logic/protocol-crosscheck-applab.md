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
| app disconnects if the version check fails | 2026-09-27 (inventory screen 57): `general` (1001) set to implausible versions (AmbSw/CmSw ASCII "9999"/"0000", `CommunicationVersion=99`), disconnect + reconnect → the app **refuses the connection**: full-screen "Connection failure", "App version outdated.", tip "1. Update app", "Error code: ex080", [Try again] [Close]. Baseline versions restored → one transient "Error accessing data" retry prompt, then a fresh Connect reconnects cleanly | OBSERVED (APP-OBSERVED 2026-09-27) — a hard refusal, not a banner; see `alert-states.md` §connect gate, `feature-availability.md` §firmware |

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
| heater timer time picker ("Start heating at" wheel) | 2026-09-27 (screens 35-37): wheel set to 09:31, **OK** → `3f7b007f091f` (`TimerHour=9` byte 4, `TimerMin=31` byte 5, every other field at its sentinel); no confirm, does not arm (Start timer not pressed). Swipes do not turn the wheel; `adb shell input draganddrop x y1 x y2 1500` with a ~145 px throw advances exactly one row | `timer 09:31` → `3f7b007f091f` | OBSERVED identical (was NOT TESTED 2026-09-16); scenario `tools/scenarios/airheater/timer-time.yaml` |
| cooler OFF / manual / automatic quiet | `fc771e3e1f1f` / `ff271e3e1f1f` / `ff471e3e1f1f` | same | OBSERVED identical since 2026-10-06 (ruling R1; was CONSISTENT while calictl carried the readback) |
| cooler timer start | `f7771e3e1f1f` (box off only; hours at sentinel) | `f7771e3e1f1f` | OBSERVED identical since 2026-10-06 |
| lighting All lights ON | `0c10…eeee…` + `0e00…` | same | OBSERVED identical |
| lighting lamp on | `0904…` one nibble = **11 DEFAULT** | `0904…` nibble = 10 | CONSISTENT (value convention differs) |
| lighting save profile | `0104…e00eeeee` + commit, then `0d0c…` (REQUEST_CONFIG @13) + commit | `save_profile 1` identical first frame | OBSERVED identical (app adds a config pull) |
| energy mode Max | `10` → `30` | `10` | OBSERVED identical. **ECO (2026-10-06): OBSERVED on the second visit** to the Energy Mode picker with `PvInstalled=1` (first visit Normal / Max only) — stale first read, decompile confirmed; history: ECO absent from the selector on this profile — and still absent (2026-09-27, screens 58-59) with `energy.PvInstalled=1` and with `vehicle.CarVariant=2` (GRAND_CALIFORNIA): **ECO is not gated by any BLE field the fake serves**. **DECOMPILE-VERIFIED:** ECO is offered only when `zj/c.N0` is true (`ak/a.java:712-716`: `ak/a.b` reads `N0` and conditionally prepends `bf.c.X` ECO_MODE to the selector); `EnergyModeNotSelectable` is never read; **SOURCE RESOLVED (#154, 2026-10-05):** `zj/c.N0` = `yg/j.f31040t1` (`yg/j.java:2870`, lambda index 19 `:2573`) = energy VM `xf/d.U1()` (`xf/d.java:339`) = flow `v1`, set from 1602 bit 10 = **`PvInstalled`** (`xf/d.java:178`, `xf/a.java:217,386`; the field name comes from the debug-log order). The decompile says ECO is offered iff `PvInstalled==1`, which **contradicts** the screen-58/59 observation above. Re-run that check with a fresh connect and confirm on the fake's wire that bit 10 of 1602 is really set. **Likely cause of the contradiction (decompile cross-check 2026-10-06, enigma `46f982d3`, medium confidence): a stale first read.** `ak/a.b` reads `N0`'s raw value without a Compose state read (`ak/a.java:714`) and is composed at `:196`, before `:197` subscribes the same flow; the proxy StateFlow (`yg/j.t1`, `a40/y`) refreshes only while subscribed, so the first visit sees the seed (false) and `b()` is then skipped. App-lab retest owed: with `PvInstalled=1`, a **second visit** to the Energy page in the same app session (or an energy-mode change) |
| lamp → zone map (`LIGHT_ZONES`) | 9 lamps → nibble positions match every entry; no app lamp on `LSix` | OBSERVED (DEVICE map confirmed) |

## Roof (S_SEQ_ROOF, `alert-states.md`)

| Claim | Observation | Verdict |
|---|---|---|
| needs ignition ON | page shows "Switch on the ignition …" and no controls while `TerminalOneFive=0`; 2026-09-27 (screen 41) the full dialog: "Switch on the ignition — Please switch on the ignition to operate the pop-up roof." [Not now] | OBSERVED |
| (new 2026-09-27) screen entry writes a probe frame | opening the roof page (ignition on, `Position=1`, no button pressed) → `WRITE roof 0000097b00` (Up=0 / Down=0 + a SafetyCounter); the counter then streams while the page stays open (S_SEQ_ROOF). Not investigated further; calictl starts its counter on the press instead (hence its ~3 s withhold after the press) | OBSERVED (APP-OBSERVED 2026-09-27) |
| app streams `[dir][counter]` @ ~500 ms while held | page open (no press) streams `[00][counter]` every ~500 ms; **while held the rate is ~8 frames/s** with the counter still +1 per ~500 ms (same value on 4 consecutive frames) | **CONTRADICTED on cadence → S_SEQ_ROOF updated**; the mock's per-frame +1 validity rule was wrong and made the app abort after 2 `01` frames — fixed to monotonic-and-advancing |
| direction bytes open `01` / stop `00` / close `04` | all three seen: `00` idle/pre-validation, `01` while OPEN held, `04` while CLOSE held (the app fell back to `00` once `Position` reached 0) | OBSERVED |
| press-and-hold OPEN moves the roof; app stops at the limit | held 12 s: `01` frames at ~8/s until the fake unit reported `Position=1` (open), then the app itself fell back to `00` frames while still held; page text "The roof is closed" → "The roof is open" | OBSERVED (app-side auto-stop at the limit confirmed) |
| stale `SafetyCounterValid=1` with no stream | app reports "Function in use — another user is already using this function" when the unit still says valid=1 while the app has not validated its own counter | OBSERVED → mock now expires validity 1.5 s after the last frame |
| unit reports `SafetyCounterValid` (1402 bit 7) | unit-side; the app refuses to move until the fake unit raises it | NOT TESTABLE (modelled) |
| InfoPopUp 1/4/5/6/7/10/11 dialogs | all seven dialogs with the tabled texts | OBSERVED |
| InfoPopUp 2/3/9/12 | 2/3/12 → tile "Function currently in use", 9 → "Only possible when stationary" | OBSERVED → added; **DECOMPILE (decompile cross-check 2026-10-06, enigma `46f982d3`)**: dashboard tile `defpackage/i1.java:1652-1673` — "in use" = CALIFORNIA_7 && (InfoPopUp ∈ {2,3,12} or `SafetyCounterValid`), so a stale valid=1 alone also shows it |
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

## Camping-mode page and Level Indicator (session 2026-09-27)

Screens 38-40 and 45 of the 2026-09-27 app inventory. Camping mode on app 5.0.8.3028 has **one
control** (the master toggle); the three rows below it are read-only status rows, not toggles.
The fake unit's `campingmode` fields were set from the console with `State=1` (master on):

| `InteriorLight`, `OutsideLight` | Front-door row ("Opening front door activates the exterior and interior lighting at the front.") | Sliding-door row ("Opening sliding door activates the rear interior lights.") |
|---|---|---|
| 1, 1 | Disabled | Disabled |
| 1, 0 | Disabled | Disabled |
| 0, 1 | Disabled | Disabled |
| 0, 0 | **Enabled** | Disabled |

| Claim | Observation | Verdict |
|---|---|---|
| the lights are ONE inverted toggle, lit iff both fields read 0 (`tf/a` `K0` / `m2`) | the front-door row is Enabled only at (0,0) — exactly `tf/a.m2()` ("true iff both raw fields == 0") | OBSERVED (the model holds; `semantics.campingmode` `lights_on` matches the row) |
| the sliding-door row is a campingmode (1202) field | it never moved in any of the four combinations, nor with `UsbCharger` | OBSERVED negative. **DECOMPILE-SETTLED (#154):** on a T7 the row is the lighting DOOR_CONTACT flag (`wh/c.java:98` → `yg/o.l0` = `dg/h.s4`, set from 1502 Mode 16 / PN 8 / `LightValue==1`), and its toggle (`wh/b.java:91-104`) writes lighting `n4` (Mode 16, PN 8, LightValue 0/1). **OBSERVED 2026-10-06:** after `door_contact on` (Lighting & Sliding door page, `door-contact.jsonl`) the camping page's sliding-door row reads **Enabled**, and still does after a cold app restart (filled from the REQUEST_CONFIG reply's Mode 16 / PN 8 frame) — CONSISTENT with the decompile |
| rear USB row | "Enabled" / "Disabled" follows `UsbCharger` 1 / 0 | OBSERVED |
| the app offers lights / USB toggles | the page has **one switch** (the master; a custom checkable view). The front-door and USB rows render as status rows ("Disabled" / "Rear USB ports are Enabled"), not switches; they were not tapped in this pass. The 2026-09-16 session did get writes from tapping those row **icons** (front-door `0f`, USB `f3`, `control-and-actuation.md` §3) and none from the sliding-door row | OBSERVED (UI shape). No switch for lights / USB, but the 09-16 icon taps did write — so "read-only rows" is NOT established; next step: tap each row icon with the fake unit logging writes. calictl's `lights` / `usb` writes are unaffected |
| master off changes the page | `State=0`: the page body is unchanged; only the dashboard tile loses its "On" | OBSERVED |

| Claim | Observation | Verdict |
|---|---|---|
| the Level Indicator reads roll/pitch from `1004` once ignition is on | the dedicated Level Indicator screen always shows "No data available. The ignition needs to be turned on for data." — with `vehicle.TerminalOneFive=1` (decoded correctly) and `CarLevelRoll`/`CarLevelPitch`/`CarLevelPopUp` set, after two re-entries | OBSERVED negative: the screen's ignition gate is **not** driven by 1004 terminal-15. **DECOMPILE-SETTLED (decompile cross-check 2026-10-06, enigma `46f982d3`):** the gate is 1004 **`CarLevelPopUp`** (bits 4-5, `zf/d.java:328`): 1 = the "No data available / ignition" card with blank gauges (`zf/d.G0` → `ph/a.h0` → `fi/d.n0`), 2 = "Please slow down / less than 10 km/h" dialog, debounced 300 ms (`fi/d.java:101-125`). The phone-API hypothesis is retired. Retest: `CarLevelPopUp=0` with roll/pitch set should show the gauges |

## The hardware side: trace the real unit, replay it through the mock

The lab validates the **app**; the unit's own behaviour (push cadence, countdown rates, coupling,
frame bits the dictionary might miss) is validated from a **trace of the real van**: run buspi's
daemon with `CALICTL_BLE_TRACE=~/ble.jsonl` (`calictl/trace.py` — one JSON line per notify /
read / write / link event; `CALICTL_BLE_TRACE_HEARTBEAT=1` to include the 1003 beats), let it
run through a drive / a heater cycle / a roof move, then `python3 -m tools.trace_compare
~/ble.jsonl`. The report lists: state frames that do **not** round-trip through the dictionary
(bits the unit uses that we don't model), per-char notification cadence (the mock pushes each char once on
subscribe and afterwards only `CHANGE_PUSH_FNS` — campingmode + vehicle — on change, plus the
lighting ramp and water on a measured change; the 2026-07 "energy ~3 Hz" note was contradicted by
the first trace below), `RunningTimeinAction` and
`AgeOneBattValuesMinutes` rates vs the mock's ±1/min, roof `Position` transitions with timings,
and the terminal-15 → `campingmode.Enable`/master-shed coupling delay. A difference is a mock bug
or a new protocol fact — never a reason to touch the trace. Results land in this file's tables.

### First real-unit trace (buspi, van awake, 2026-09-16 17:08–17:20)

| Claim | Observation | Verdict |
|---|---|---|
| every state frame round-trips through the dictionary | 14/14 functions, 88 frames: repack == raw for every frame | OBSERVED (dictionary covers every bit the unit sent) |
| `1602` energy streams ~3×/s while connected | **not observed**: with the persistent session up and the heartbeat ticking, each of the 12 subscribed chars notified **exactly once, right after its CCCD write**, then nothing for the rest of the link (no change-driven push in 150 s; energy values did change between links) | **CONTRADICTED** (the 2026-07 "3×/s" note) → mock/fake now push once on subscribe, not 1 Hz |
| `1003` heartbeat keeps the link up indefinitely | **no unit-side drop found.** The heartbeat-traced run (524 s, 11 links) shows every link is one calictl **poll cycle**: connect → 12 on-subscribe pushes → 14 reads → 5–7 beats (median gap 0.73 s, max 1.32 s) → calictl's own disconnect 0–0.9 s after the last beat; links are 5–6 s long and start every ~39 s (`POLL_INTERVAL=30` + the cycle). The "up twice within 40 s" was the web-driven persistent session being **released for web-UI idleness** (`persistent session released (web UI idle)` ~3 s to 2 min after each `up`) and re-armed by the next `/api/session` nudge. One genuine `read_all: link dropped at airheater` occurred right after the service restart (hci0 contention on start-up), none afterwards | OBSERVED (resolved; the 30–40 s pattern is calictl's cadence, not the unit) |

## Pairing (guided-pairing.md, `calictl/pairing.py` / `pairing_bluez.py`)

Session 2026-09-27 (fake unit with `BUMBLE_LOGLEVEL=DEBUG`, so every SMP PDU is in the log;
the app's side from `logcat`). The pairing rework on `feat/pairing-verification` was already
cross-checked against the fake over a Bumble `LocalLink` (`tests/test_pairing_link.py`) and
against real BlueZ in a VM (`tests/realstack/`); this session adds the **real CaliforniaOnTour
app's own pairing flow and error UX** against the same fake, driven through its `pair on`/
`pair off`/`forget` console commands.

| Claim (calictl side) | Observation (app, 2026-09-27) | Verdict |
|---|---|---|
| a bonded central reconnects over a **rotated RPA** without re-pairing (`connect_bonded`, host-side IRK resolution) | fake restarted → new RPA; app "Connect" → link up, peer resolved, `HCI_ENCRYPTION_CHANGE` with the stored LTK **1 s after connect, no SMP exchange, no prompt**; vehicle page shows the fake's data (fresh water 14/29 l …) | OBSERVED (app tolerates the rotation exactly as calictl does) |
| passkey entry: unit DISPLAYS, central types — calictl pairs as `KeyboardOnly` + MITM + SC, set before any link | app's `SMP_PAIRING_REQUEST`: `io_capability KEYBOARD_DISPLAY`, `auth_req BONDING\|MITM\|SC\|CT2` → fake answers `DISPLAY_ONLY`, `BONDING\|MITM\|SC` → method **PASSKEY**; Android surfaces it as the notification "Bluetooth pairing request — Tap to pair with VWCAMPER" → PIN dialog | CONSISTENT (same association model; the app additionally advertises display capability + CT2, irrelevant against a display-only unit) |
| `Passcode: ---` until a pairing request arrives | the fake generates its passkey only in `generate_passkey()` i.e. when the app's request lands (1.4–3 s after connect); the app's flow works with a code that exists only from that moment | CONSISTENT (fake-side; the unit's own screen stays owner-photo OBSERVED) |
| **unit Bluetooth reset mid-ownership** — calictl's wizard probes the bond, removes the stale one, re-pairs, and its GUI hints "Bluetooth reset / re-pair" | `forget` on the fake, app Disconnect → Connect: link up, stored LTK unusable → the app (Android) starts a **fresh SMP pairing by itself** 1.4 s after connect; **no guidance in the app**, only a spinner in the Connect button plus the OS notification. Unanswered, the phone drops the link 33 s later (`REMOTE_USER_TERMINATED`, 0x13), the app **silently** returns to "Connect" and the stale notification lingers. Tapping Connect again → new request → passkey typed → bond restored (`BluetoothPairingService: Bond state change : 12`), data flows | OBSERVED — outcome CONSISTENT with calictl's self-heal (both end bonded after one passkey), mechanism differs (Android re-pairs on LTK failure; BlueZ needs the explicit `remove_bond`), and calictl's hint text is richer than the app's (which has none) |
| **unit not in "Gerät verbinden"** — calictl ends `pairing_failed` with a "put the unit in pairing mode" hint | `pair off` + `forget`: app connects, discovers, MTU, sends its pairing request → fake `SMP_PAIRING_FAILED PAIRING_NOT_SUPPORTED` → Android closes the GATT 3 s later (`onClientConnectionState status=22`) → the app **immediately retries with `autoConnect=true`** (`status=133`) and keeps the spinner ≥ 60 s; **no text, no dialog, no notification** | OBSERVED — CONSISTENT with the fake's refusal model; calictl's explicit failure + hint is the better UX (the app gives the user nothing to act on) |
| HCI 0x3e under a co-resident full-duty scanner (2026-09-25 btmon on buspi) | netsim is a virtual link with no airtime contention — cannot be provoked here | NOT TESTABLE (stays CAPTURE-tier on real buspi hardware) |

Takeaways for this repo's pairing UX: the association model and the "re-pair after a unit reset"
outcome are validated against the app; the wizard's hints (`connect_failed`, `pairing_failed` +
"open Gerät verbinden", "Bluetooth reset / re-pair") have **no app equivalent** — the app spins
and retries silently — so they are calictl's own contribution, not a mirror of app behaviour.

## Not testable in the lab (unit-side; keep DEVICE/CAPTURE tier)

`0x0E` link drop on out-of-range values; water measurement-gating / stale latch; deep-sleep and
advertising stop when parked; real motor withhold (~3 s) and travel time; `1502` Mode-4 ramp
notifications; readback-is-an-echo; RPA rotation.

## Automated recordings (harness)

The frames in this document were captured by hand. The app-fidelity harness regenerates them as
committed recordings: `tools/applab/walk.py <scenario>` writes `tests/vectors/app/<scenario>.jsonl`
and CI replays it against `control.build` (`tests/test_app_recordings.py`,
`python3 -m tools.capture_diff <file> --recording`); `tools/app_parity.py` reports the connection
lifecycle. See `simulation-and-testing`.

Recording sessions (2026-10-05, app 5.0.8.3028, thinky lab34 — see `tools/applab/README.md`
"Linux host"): all ten scenarios of that day are **APP-RECORDED** and replay clean against `control.build`:

- `tests/vectors/app/energy-mode.jsonl` — Max `10` and Normal `00` (+ neutral `30`), both matched.
- `tests/vectors/app/campingmode.jsonl` — master OFF `fc` / ON `fd` (+ neutral `ff`), both matched.
- `tests/vectors/app/airheater-permanent-on.jsonl` — the greyed Permanent-Heating switch is inert
  (no write), confirming the "can be activated only in the vehicle" gate.
- `tests/vectors/app/session.jsonl` — the two-connection lifecycle (connect → background drop →
  foreground reconnect); `tools/app_parity` prints one section per connection + the reconnect gap.
- `tests/vectors/app/airheater.jsonl` — temperature 8 `3f78007f1f3f`, run time 60 `3f7b003c1f3f`,
  Immediate heating ON `3d7b007f1f3f`.
- `tests/vectors/app/lighting-zone.jsonl` — All lights OFF→ON `0c10…` (LIGHTS_ON), Kitchen >
  *Cooking* 50 % `0904…eeeeeee5…` (L7 = calictl `kitchen`).
- `tests/vectors/app/cooler.jsonl` — power on `fd77…`, level 5 `ff75…`, manual quiet on/off
  `ff27…`/`ff07…`, automatic quiet `ff47…`, power off `fc77…`, timer start `f777…` / cancel `df77…`.
  The app absorbs the first tap on the timer switch after arming; the second tap cancels.
- `tests/vectors/app/roof-hold.jsonl` — ignition on, the rocker's upper half held 16 s: `Up=1` frames
  with an incrementing SafetyCounter until the mock reports the roof open.
- `tests/vectors/app/lighting-profile.jsonl` — press-and-hold tile A = `save_profile 1`
  `010400000000000000000005e00eeeee` (current zone levels, NOT_EQUIPPED → 14), matching calictl.
- `tests/vectors/app/lighting-wakeup.jsonl` — wake-up time 07:00 via the wheel + OK writes the Mode-20
  frame `0e146ac49c701100…` (Timestamp = the next 07:00 packed as if UTC, LightValue `0x1100` = warm
  white, area 1, brightness 0, enabled 0). Since A2 calictl's `wakeup` builder reproduces it, so it
  replays as `action` (`capture_diff.GAPS` is empty).

A2 session (2026-10-06, thinky lab34, emulator in UTC, the A2 mock; `-gpu swangle_indirect`): three more
recordings, every write `action`/`flush`/`app-only` (12 recordings in all):

- `tests/vectors/app/lighting-wakeup.jsonl` (re-recorded) — time 07:00 `0e146ac5edf01100…`, switch ON
  `…1101`, time 08:00 while ON `0e146ac5fc001101…`, switch OFF `…1100`. OBSERVED: the time edit while ON
  keeps enabled=1 (ruling R3, CONSISTENT with `si/h.j`); with the mock's Mode-20 echo the app keeps each
  write (no revert / toast); reopening the page shows 08:00.
- `tests/vectors/app/door-contact.jsonl` — the "Opening sliding door…" status row toggles: `0810…01` /
  `0810…00`, the row text follows the mock's Mode 16 / PN 8 echo (Enabled / Disabled).
- `tests/vectors/app/lighting-favourite.jsonl` — save A (`0104…`, then REQUEST_CONFIG), all lights off
  `0010…`, tap A `0110…` (SET_PROFILE PN 1). OBSERVED: after the save tile A is the active profile and a
  tap on it writes nothing; tile A turns from "+" to filled from the REQUEST_CONFIG reply's bit 0.

Other observations of that session (CONSISTENT unless noted):

| Claim | App | Verdict |
|---|---|---|
| wake-up no-config seed = no areas (decompile `F0` seed) | the first time edit with no Mode-20 frame reported writes `LightValue 0x1100` = **area 1** ("Living area reading lights" ticked on the page) | **CONTRADICTED** (decompile reading); `WAKEUP_DEFAULT` area 1 matches the app |
| wake-up area bits A1–A4 = `ti/b` T7 labels | ticking each entry alone: Living area reading = A1, Kitchen background = A2, Pop-up roof reading = A3, Pop-up roof background = A4 | OBSERVED, CONSISTENT (web UI `WAKE_AREAS`) |
| the app reads its lighting config from the REQUEST_CONFIG reply | cold app restart + re-pair against a mock holding favourite 1, door on, wake-up 07:00 on: tile A filled, door row Enabled (lighting + camping page), wake-up page 07:00 with the switch on | OBSERVED (Mode 12 / Mode 20 / Mode 16 PN 8 all read) |
| (new) wake-up switch clock check | every switch tap shows "Different time settings." — App: phone date/time, Vehicle: the 1004 RTC, "Please check the time in the vehicle and on your smartphone to make sure that all the functions operate correctly." [OK]; the write is sent regardless | OBSERVED (the fake's RTC is the baseline's 2026-08-28) |
| ECO follows `PvInstalled` | first visit Normal / Max, second visit ECO / Normal / Max | OBSERVED, CONSISTENT (stale first read) |
