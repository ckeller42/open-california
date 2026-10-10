# Evidence ledger — how strongly is each protocol fact proven?

Every protocol claim in this repo sits at one of three **evidence tiers**. The goal is to drive
everything toward the top tier. The decompile tells us what the app *intends*; only a wire capture
proves what actually happens.

| Tier | Meaning | How it's checked |
|---|---|---|
| **CAPTURE** | Matched byte-for-byte against a real HCI/PacketLogger capture of the app | `tools/scenarios/<fn>/*.yaml` (fed to `tools/capture_diff.py`) + `tests/test_capture_diff.py` |
| **DECOMPILE** | Grounded in the app's decompiled decode/setter + the enigma mapping, but never seen on the wire | agent cross-checks; `mapping.enigma` (54 verified classes) |
| **DEVICE** | Physically observed on the van (photons / a human at the hardware), frame not necessarily diffed | owner report, dated |
| **BOARD** | Firmware behaviour seen on the real ESP32 board (CoreS3) on the bench, against the **mock** unit — proves the firmware, not a unit-protocol fact | console log + remote `screenshot`, dated (section below) |
| **HOST-E2E** | Firmware behaviour seen on the Linux NimBLE host build (`cali-host`, upstream NimBLE 1.10) against the **Bumble fake unit**, in CI — proves the firmware's C code and its bytes on a real GATT link, not the esp-nimble port, a radio, or a unit-protocol fact | `tests/firmware/test_*_e2e.py` (CI `firmware-host-e2e`), the fake unit's recording (section below) |

Automated ties that keep this honest: `test_signal_coverage.py` (dictionary ↔ catalog),
`test_doc_offset_consistency.py` (prose/comment `Field@offset` citations ↔ dictionary),
`test_capture_diff.py` (our frames ↔ captured app frames).

## Owed a capture (currently DECOMPILE-only — do not trust as verified)

| Fact | Tier now | Capture that would verify it |
|---|---|---|
| cooler `timer_set`, `timer_start`/`cancel` (start-at cooling timer) | **APP-RECORDED** 2026-10-05 for `timer_start` `f7771e3e1f1f` / `timer_cancel` `df771e3e1f1f` (`tests/vectors/app/cooler.jsonl`, both match `control.build`; the app absorbs the first tap on the switch after arming, the second cancels). APP-OBSERVED (tools/applab 2026-09-16): timer start `f7771e3e1f1f`, time picker 04:02 `ff7704021f1f` — calictl's frames are byte-identical since 2026-10-06 (`f7771e3e1f1f` / `timer_set 04:02` → `ff7704021f1f`; until then only the targeted fields matched, `control-and-actuation.md` §3, `protocol-crosscheck-applab.md`) | unit-side: does the box switch on at TimerHour:TimerMin (the 2026-08-30 DEVICE read confirms the stored time, not the switch-on) |
| cooler `mode` quiet=2(manual)/4=scheduled — DISPLAY-CONFIRMED 2026-08-26: the unit's Flüstermodus screen shows "Ein/Aus"(manual=Mode2) + "Automatisch"(scheduled=Mode4) toggles; scheduled quiet = Mode 4 (vf/c L0), decompile-cross-checked end to end (yh/e QuietModeViewModel). No physical compressor-audible confirm yet | DEVICE (display) | a human hearing the compressor quieten in the window |
| cooler `NightTimerSet` bit — meaning UNKNOWN: decoded (1102 bit3) + plumbed into a StateFlow (vf/c D3) but NEVER rendered (dead-end, zero UI consumers) and NEVER written by any cooler path (only air-heater rf/b.H3 stages that shared frame slot). NOT the schedule-arm bit (that's Mode 4); "within-window active flag" hypothesis **REFUTED** DEVICE 2026-08-26 — read 0 with the unit RTC at 22:06 INSIDE the armed 22:00–06:00 window (also 0 outside it). Vestigial on this unit, or asserts only under some unseen condition. Not surfaced | DEVICE (refuted) + **RETIRED by call stack (#154):** `vf/c` J0 → `D3()` → `yg/g.w0` (`yg/g.java:1014,1026`) has no reader outside the facade lambdas and no cooler writer | — |
| airheater `runtime` (`3f7b003c1f3f`), `timer_start` (`3f3b017f1f3f` = Mode 3 + Combined 1), `timer_cancel` (`3f0b007f1f3f`) | APP-OBSERVED (tools/applab 2026-09-16; frames identical to calictl's) | unit-side: does the heater actually start at TimerHour:TimerMin, and what Mode does 1702 report after it fires (mock assumes 0) |
| airheater `timer` HH:MM (`B0`, TimerHour/TimerMin) | APP-OBSERVED (2026-09-27, inventory screens 35-37): the "Start heating at" wheel set to 09:31 + OK → `3f7b007f091f` (`TimerHour` byte 4 = `0x09`, `TimerMin` byte 5 = `0x1f`), **identical** to calictl's `timer 09:31`; written on OK, no confirm, does not arm. Pinned by `T_AIRHEATER_TIMER_TIME` + `tools/scenarios/airheater/timer-time.yaml` | unit-side: does the unit store TimerHour/TimerMin, and does 1702 read them back (mock assumes yes) |
| fault dialogs: heater ErrorCode 1–5 (6/7 new in app 5.4.0, DECOMPILE only — section below), cooler Error 1–3, 11 energy flags, water InfoPopUps (fresh 1–5/7, waste 1–3) → the app's exact dialog texts | APP-OBSERVED (fake unit injection 2026-09-16; `alert-states.md`, `cooler-airheater.md`) | unit-side: which real conditions raise each code (only cooler door-open and heater codes have ever been seen live) |
| `Installed` bit gates a function's tile; stairs / LR-heater / satellite / roof-A/C screens + vocabulary | APP-OBSERVED (Installed flipped on the fake unit) | — (not fitted on this van) |
| airheater **permanent-ON** | **APP-RECORDED** 2026-10-05 (`tests/vectors/app/airheater-permanent-on.jsonl`): the Permanent-Heating switch is **inert** when continuous heating is off — greyed "can be activated only in the vehicle", and tapping it sends **no frame** **Call stack (#154):** the app's only `PermanentOperationRequest` write is `rf/b.E3` → 0 (`rf/b.java:210-214`, callers `ni/a.java:419,466`). There is no ON write site anywhere | in-vehicle only: the ON value is not reachable from the app, so it cannot be learned here |
| energy `mode` — Normal `00` / Max `10` (EnergyModeSet 0/1; +neutral `30`) | **APP-RECORDED** 2026-10-05 (`tests/vectors/app/energy-mode.jsonl`): both match `control.build` on the targeted field | — (ECO not offered on this profile / not BLE-reachable, §ECO) |
| lighting `profile` activate + `save_profile N [colour]` (favourites) | `save_profile` **APP-RECORDED** 2026-10-05 (`tests/vectors/app/lighting-profile.jsonl`): press-and-hold tile A writes `010400000000000000000005e00eeeee` (SET_BRIGHTNESS, ProfileNumber 1, every equipped zone at its current level, NOT_EQUIPPED → 14) — matches `control.build("lighting","save_profile",1)`; **call stack (#154):** save = `dg/h.l3` (`dg/h.java:564-700`): an optional SET_COLOR (Mode 6, PN=N), then ONE SET_BRIGHTNESS with **PN=N (not 9)**, then REQUEST_CONFIG. calictl (A2): `save_profile N <colour>` sends the SET_COLOR preface (`control.preface_for`) on the same armed link — colour **DECOMPILE-only** (colour UI not shown on a T7); calictl does not wait for the SET_COLOR ack nor send the trailing REQUEST_CONFIG. `profile N` activate: **APP-RECORDED** 2026-10-06 (`tests/vectors/app/lighting-favourite.jsonl`: save tile A, all lights off, tap A → `0110000000000000eeeeeeeeeeeeeeee` = SET_PROFILE PN 1, byte-exact vs `control.build("lighting","profile",1)`; right after a save tile A is the active profile and a tap on it writes nothing) — `dg/h.u0` (`dg/h.java:944-992`), one frame Mode 16 / PN=N then a wait for the 1502 ack; the A2 mock now stores 7 favourites (empty = ACK-and-ignore) and acks an activate on 1502. **Ack rules + tiles DECOMPILE (decompile cross-check 2026-10-06, enigma `46f982d3`):** activate is acked by any 1502 frame with PN == N, the save only by an exact Mode 4 / PN N frame, the trailing REQUEST_CONFIG's result is ignored; tiles A/B/C/D = favourites **1/5/6/7**, drawn filled from bits 0/4/5/6 (`hi/f`, `gv/z0`) | van: the unit applies the stored levels, and its REQUEST_CONFIG reply carries the favourite bits (the app fills the tiles from that reply — app lab 2026-10-06, cold restart) |
| lighting **wake-up** (`m0`, Mode 20) — time edit + on/off | time edit **APP-RECORDED** (`tests/vectors/app/lighting-wakeup.jsonl`): wheel 07:00 + OK on 2026-10-05 → `0e146ac49c701100…ee` — Mode 20, ProfileNumber 14 (unstaged), Timestamp `0x6ac49c70` = 2026-10-06 07:00 read as UTC, **LightValue `0x1100`** = warm white, area 1, brightness 0, no ramp, **enabled 0** (corrects the earlier `0x11` reading). **Packing settled by call stack (#154):** `si/h.j` → `dg/h.m0` (`dg/h.java:778-874`), exact inverse in the 1502 decode (vineflower `dg/a.java:311-415`): `LightValue` = `colour:4 \| A4 A3 A2 A1 \| brightness:4 \| wakeMode:4`, wakeMode = `(rampMin/10)<<1 \| enabled`. calictl's `wakeup` builder (A2) reproduces the recorded frame byte-exact (`T_LIGHT_WAKEUP`; `capture_diff` `GAPS` now empty). **Re-recorded 2026-10-06 (APP-RECORDED, all byte-exact):** time 07:00 (no config reported) `0e146ac5edf01100…`, switch ON `…1101` (time kept), time 08:00 **while ON** `0e146ac5fc001101…` — **R3 on the wire: the edit keeps enabled=1** — switch OFF `…1100`; the page shows 08:00 on reopen and, after a cold app restart, the time + switch from the REQUEST_CONFIG reply's Mode-20 frame. No-config seed: the app writes **area 1** ("Living area reading lights" ticked) — `WAKEUP_DEFAULT` area 1 stands. Every switch tap first shows a "Different time settings." dialog (phone clock vs 1004 RTC). The "edit keeps the unit-reported enabled bit" rule (R3) is also **DECOMPILE-CONFIRMED** (decompile cross-check 2026-10-06, enigma `46f982d3`: `si/h.j` `si/h.java:387-414` takes each field from the edit else `dg/h.F0`, written only by the Mode-20 decode; no config → the `F0` seed, enabled OFF + 00:00; calictl's gate refuses such an edit — R5 — after a REQUEST_CONFIG pull). The app then expects a Mode-20 echo within ~3 s or reverts with "Something went wrong" (the mock does; the app kept the switch state, no toast) | van: the unit echoes Mode 20 within ~3 s of a write, and the light ramps at that time |
| lighting **door contact** (`n4`, Mode 16 / PN 8, LightValue 1/0) | **APP-RECORDED** 2026-10-06 (`tests/vectors/app/door-contact.jsonl`: the "Opening sliding door…" row on Lighting & Sliding door → `0810000000000001…` on / `…0000` off, byte-exact vs `door_contact on|off`; the camping page's sliding-door row then reads Enabled, also after a cold app restart from the REQUEST_CONFIG reply) — decompile: `dg/h.n4` (`dg/h.java:877-884`), read side = a 1502 Mode 16 / PN 8 frame (vineflower `dg/a.java:293-306`); calictl `door_contact on|off` (`T_LIGHT_DOOR_CONTACT`), the mock stores the flag and echoes it on 1502; the camping page's sliding-door row reads it on every variant except Grand California, no equipment gate (decompile cross-check 2026-10-06, enigma `46f982d3`) | van: the sliding door actually lights, and the real unit echoes the flag on 1502 |
| roof drive end-to-end (frames match app; motor never driven by calictl) | DECOMPILE + partial DEVICE; app hold-to-open **APP-RECORDED** against the mock 2026-10-05 (`tests/vectors/app/roof-hold.jsonl`: 16 s hold streams `Up=1` + an incrementing SafetyCounter, matches `control.build("roof","open")` on `Up`). Frames are call-stack-backed (`ig/c` move heartbeat, `w8/a` counter, move gate `ig/c.j` `ig/c.java:707-713`, evaluated in the ctor and on every 1402 notify). That the motor moves **cannot be proven from the decompile**. Since 2026-10-10 calictl follows the real app's roof screen (CAPTURE 2026-10-10, owner decision): a STOP stream while the web UI's roof page is open, a press on the same counter (`R_ROOF_VIEW_STREAM`) — mock-tested only; **calictl's own move is still NEVER driven on the real unit** | calictl drives the roof with ignition on, owner-watched (#230): the roof view's STOP stream, then a press, motor starting ~1.2 s after it |
| roof arm: **app-faithful** — the 1003 heartbeat ticks during the move (inside the live session, or a fresh connection that starts it) and the SafetyCounter streams immediately, no `ARM_DELAY_S` (A1, 2026-10-05; replaces the #150 "no-heartbeat" arm and the #198 handover); motor still never driven by calictl | DECOMPILE + APP-RECORDED: the app's 1003 ticker is **session-global** (`zf/d.java:183` → `d2/s.java:795-802` → `mj/d.java:247`, random 750–850 ms period + seed in `c/i.java:349-367`, #235), and `tests/vectors/app/roof-hold.jsonl` shows 11 beats during a 9.1 s hold. The no-gap/immediate-counter part (#150) stands. calictl keeps its fixed 0.6 s period. **App side DEVICE-verified 2026-10-10** (CAPTURE of the real app on the real unit, owner-held full open + close: the 1003 heartbeat ran through the whole move, ~0.76 s; row 2026-10-10 below); calictl's own move is still never driven | first owner-watched calictl roof move (#157/#230) |
| water (and every state char) is read **after** subscribing; read and notify share one decoder, **last frame wins**; no push-only rule, no water cache, no periodic 1302 re-read (calictl `R_READ_LAST_FRAME_WINS`, 2026-10-07; replaces the 2026-07-14 "water is push-only for freshness" and `PUSH_ONLY_FUNCS`) | DECOMPILE (enigma `38a0d6b`: `jb/b` subscribe-then-read, `i2` tag 26 re-emits the read into the notify flow, `qg/b.e` sets unconditionally) + the 2026-07 PacketLogger capture, where the app got the fresh 11 L from a plain read (`value-freshness.md`) | #230 van check: calictl vs the app while parked, with `CALICTL_BLE_TRACE` showing the 1302 read bytes (`remaining-captures.md` §1) |

## Already at CAPTURE / DEVICE (examples, keep as the model)

- cooler power/level frames, airheater on/off — CAPTURE (HCI 2026-07-08/14; `tools/scenarios/`). Cooler
  `level` DEVICE only for the pre-R1 state-carry frame; the app-faithful `State=3` frame calictl sends
  since 2026-10-06 (APP-RECORDED) is CAPTURE 2026-10-10: the real app's byte-identical frames
  actuated on the real unit (#230, closed). calictl itself has not written it to the unit yet.
- cooler `night_on`/`night_off` + `mode` timer_quiet(4) — DEVICE (live writes 2026-08-26, PR #112): hours
  + Mode are stored on the unit (survive reconnects) and every change is **broadcast as an unsolicited
  1102 notification**.
- cooler night-schedule bytes are **LITERAL, not optional** — DEVICE (2026-08-26): a frame carrying
  `NightTimerHourOn=0` clobbered a just-set 22 (unit pushed `quiet_from 22->0`). ⚠️ CORRECTS the earlier
  ledger line "NightTimer sentinels = 0 — CAPTURE": the captured power-on frame *did* send 0s, but that
  van had **no schedule set**, so 0 was simply the current value — the capture never showed that 0 means
  leave-unchanged (it doesn't; the leave-unchanged sentinels are `v()`'s 31/3, and the app re-sends them
  in its 500 ms post-write neutral frame). **A capture only validates the state it was taken in.**
  Since 2026-10-06 (ruling R1) every cooler write but `night_on`/`night_off` sends the app's 31
  (`fd771e3e1f1f` = power on, APP-RECORDED); those two still carry the current schedule — and are
  **refused while no cooler state is known** (ruling R4, `REASON_COOLER_STATE_UNKNOWN`: the carry
  would otherwise be the default-filled frame that did the clobbering). Same day, ruling R3: an
  on/off command whose value is not `on`/`off`/`true`/`false`/`1`/`0` (`null`, `""`, `"x"`) is
  refused (`REASON_NOT_ONOFF`), never built as the OFF frame. Both rulings are in the ESP control
  vectors as `refused`, so the satellite's C twin answers the same words (tier: CI, below).
- lighting per-zone SET + power — DEVICE (photon-verified 2026-08-16).
- general(1001) SW-version decode + DC-DC +2 — DEVICE (live-read `0410`, `dcdc_current` −2→0, 2026-08-17).
- roof InfoPopUp `5` = DRIVING (`_ROOF_ALERT`) + the web move-gate's block set {child_lock, error,
  driving, emergency_locked, not_possible, low_battery, Position==15} — DECOMPILE (2026-09-15,
  `ig/c.java` `j()` movable-check; sensor_error is warn-only there). Call stack (#154): `j()` (`ig/c.java:707-713`:
  Position 15 or InfoPopUp in {1,4,5,7,10,11} → not movable; `k()` `:720-723` warns on {2,3,12}) runs in the
  ctor and on every 1402 notification `e()` (smali `ig/c.smali:494,1549`). Not yet seen live: the van has
  never reported 5 while calictl was polling. Owed: one drive with the roof screen open.
- roof InfoPopUp 2 = pre-open safety-checklist prompt (`open_checklist`, no move block), 12 moving,
  8 end of travel, 3 stopped mid-travel (progress codes, no alert) — CAPTURE 2026-10-10 (real app,
  real unit, HCI snoop of a full open + close: `0302` → checklist → `030c`/`230c` → `2308` →
  `1300`/`0300`; release mid-travel `2303` → `2300`). Supersedes the 2026-09-16 fake-unit `in_use`.
- roof InfoPopUp → dialog texts (1 over-use cooldown, 5 roof-open-while-driving, 6 jammed/blocked,
  7 secure manually, 10 unavailable, 11 low battery/run engine) — DECOMPILE (2026-09-16, `ig/c.java`
  switch → `ea/j`/`ea/n` string accessors → `.cvr` EN/DE tables).
- campingmode `Enable` (1202 bit 3) = terminal-15, **one poll behind** 1004 `TerminalOneFive` —
  DEVICE (2026-09-16, camping-watch: 8 paired `ignition 0→1` / `enable 0→1` / `master_on 1→0`
  transitions; owner screenshot of the lag). Decompile: `tf/a.java n0()` ignitionTerminal15Flow.
- air-heater run-time cap 120 min + ErrorCode IDs 1–5; cooler quiet-needs-ON / timer-needs-OFF
  gates — DECOMPILE + string tables (2026-09-16, `rf/b.java`, `infoPage_heating_immediate_description`,
  `coolboxPage_*` strings). Not live-provoked.
- **APP-OBSERVED tier (new, 2026-09-16):** the real CaliforniaOnTour app running in an Android
  emulator against `tools/applab/fake_unit_ble.py` (a Bumble peripheral serving `tools/mock_unit.py`).
  Not the unit — but the app's genuine frames, dialogs and gates. Rows: `1002` = `SHA-256(VIN)[16:32]`
  (mismatch → "Wrong vehicle found"); heater ON `3d7b007f1f3f` + neutral `3f7b007f1f3f` @ +500 ms,
  continuous-heating OFF `0f7b007f1f3f`, untargeted fields at their defaults; roof page streams
  `Up=0 Down=0 SafetyCounter+1` every ~500 ms and requires `SafetyCounterValid`; roof `InfoPopUp`
  1–15 → dialog/tile texts (2/3/12 "in use" tile — fake unit only, see CAPTURE 2026-10-10 above; 9 not stationary, 8/13/14 nothing); heater sliders 1–9+HI
  and 10–120; `1003` heartbeat cadence ~750 ms while the app is connected; lighting All-lights
  frames byte-identical to calictl's, lamp taps write nibble value 11 (DEFAULT), and the app's lamp →
  nibble positions confirm calictl's DEVICE-verified `LIGHT_ZONES` for all nine app lamps (the
  cabinet light `LSix` has no app control); cooler
  OFF/quiet/timer frames and camping master OFF (`fc`, identical) as tabled in
  `control-and-actuation.md`.
- **APP-OBSERVED (2026-09-27, app 5.0.8.3028 against the fake unit; screen numbers = the session
  inventory):** (a) **ex080 connect gate** — implausible `general` (1001) versions (AmbSw/CmSw
  "9999"/"0000", `CommunicationVersion=99`) → the app refuses the connection: "Connection failure",
  "App version outdated.", "Error code: ex080" (screen 57); restored versions reconnect. (b) **ECO
  is not BLE-reachable** — the Energy Mode picker offers Normal / Max only, with
  `EnergyModeNotSelectable=0`, `PvInstalled=1` and `CarVariant=2` alike (screens 58-59). **DECOMPILE-VERIFIED:** ECO is offered only when `zj/c.N0` is true (`ak/a.java:712-716`: `ak/a.b` reads `N0` and conditionally prepends `bf.c.X` ECO_MODE to the selector); `EnergyModeNotSelectable` is never read; the SOURCE of `N0` is **RESOLVED (#154, 2026-10-05): 1602 bit 10 `PvInstalled`** (`zj/c.N0` = `yg/j.f31040t1` = `xf/d.U1()` = `v1`, set at `xf/a.java:386`). This contradicts the screen-58/59 observation, so the ECO lab check is owed again (enigma + call-stack notes: californiaontour-re 30abb201). **APP-OBSERVED 2026-10-06 (settles it):** with `PvInstalled=1` on the wire (1602 byte 1 = `0xf0`), the first visit to Vehicle Information > Energy Mode offers Normal / Max only; leaving and opening it again in the same app session offers **ECO / Normal / Max** — the stale-first-read mechanism (decompile cross-check 2026-10-06, `ak/a.java:714`). ECO **is** gated by `PvInstalled`; screens 58-59 were first visits. **Likely cause of the contradiction (decompile cross-check 2026-10-06, enigma `46f982d3`, medium confidence): a stale first read.** `ak/a.b` reads `N0`'s raw value without a Compose state read (`ak/a.java:714`) and is composed at `:196`, before `:197` subscribes the same flow; the proxy StateFlow (`yg/j.t1`, `a40/y`) refreshes only while subscribed, so the first visit sees the seed (false) and `b()` is then skipped. App-lab retest owed: with `PvInstalled=1`, a **second visit** to the Energy page in the same app session (or an energy-mode change). (c) **camping front-door row = `tf/a.m2`** — "Enabled" only when
  `InteriorLight`=`OutsideLight`=0 (screens 38-40); the sliding-door row never follows 1202
  (**DECOMPILE-settled #154:** on a T7 the row is the lighting DOOR_CONTACT flag, `wh/c.java:98` → `dg/h.s4`; its toggle writes lighting `n4` = Mode 16 / PN 8 / LightValue 0/1). (d) **roof ignition dialog** — ignition off → "Switch on the
  ignition — Please switch on the ignition to operate the pop-up roof." [Not now] (screen 41), and
  the roof page writes `0000097b00` on entry. (e) **heater timer wheel** frame above. (f) The Level
  Indicator screen's ignition gate is not 1004 terminal-15 (screen 45). **DECOMPILE-SETTLED (decompile cross-check 2026-10-06, enigma `46f982d3`):** it is 1004
  `CarLevelPopUp` (bits 4-5, `zf/d.java:328`): 1 = the "No data available / The ignition needs to be turned on"
  card with blank gauges, 2 = the debounced "Please slow down" dialog.
  Tables in `protocol-crosscheck-applab.md`.
- **pairing, APP-OBSERVED (2026-09-27, fake unit at SMP debug level):** the app pairs with
  `io_capability KEYBOARD_DISPLAY`, `auth_req BONDING|MITM|SC|CT2` → passkey entry against the
  display-only unit (calictl's `KeyboardOnly`+MITM+SC is the same association model); a bonded
  app reconnects over a rotated RPA with the stored LTK and no prompt; after a unit-side bond
  loss (`forget`) Android starts a fresh pairing on its own 1.4 s after connect, the app shows only
  a spinner (no guidance), drops the link 33 s later if the passkey isn't typed, and re-pairs on
  the next Connect; with the unit refusing pairing (`PAIRING_NOT_SUPPORTED`, "not in Gerät
  verbinden") the app retries silently with `autoConnect` and never shows an error. Table in
  `protocol-crosscheck-applab.md` "Pairing".
- **APP-RECORDED (2026-10-05, app 5.0.8.3028, thinky lab34 — the app-fidelity harness, #154):** the
  first committed recordings under `tests/vectors/app/`, each replayed against `control.build` in CI
  (`tests/test_app_recordings.py`): `energy-mode.jsonl` (Max `10` / Normal `00` / neutral `30`,
  matched), `campingmode.jsonl` (master OFF `fc` / ON `fd` / neutral `ff`, matched),
  `airheater-permanent-on.jsonl` (the Permanent-Heating switch is inert when continuous heating is
  off — no frame), and `session.jsonl` (two-connection lifecycle: connect → background drop →
  foreground reconnect, `conn` 1 then 2).
- **APP-RECORDED (2026-10-05, same harness, task 6b):** `lighting-zone.jsonl` — All-lights master
  OFF→ON writes `0c10` (profile `LIGHTS_ON`, matches `control.build("lighting","power","on")`), and
  the **Kitchen** zone's *Cooking* lamp at 50 % writes `0904…eeeeeee5…` (`BrightnessLSeven`=L7=5,
  matches `control.build("lighting","kitchen",5)`). Both replay clean. What the real unit does with
  the lamps on `LIGHTS_ON` is **unverified** (fixed level, or each lamp's last level). The mock
  models it as "every equipped zone that is off goes to `LIGHT_ON_BRIGHTNESS`" (2026-10-07). The frame
  itself is CAPTURE 2026-10-10 (the real app sent `0c10…` to the real unit); what the lamps do on
  `LIGHTS_ON` is still owed (#157). The app groups lamps into
  named zones (Reading Lights, **Kitchen** = *Background Lighting*=L5 + *Cooking*=L7, Pop-up roof,
  Exterior Light), confirming the DEVICE `LIGHT_ZONES` map below with the app's own EN labels. The
  lighting control screen renders fine under the emulator's NVIDIA `-gpu host` (the crash was a
  SwiftShader-only software-render fault). And `airheater.jsonl` — immediate heating set in the
  INACTIVE state: temperature level 8 `3f78007f1f3f`, run time 60 min `3f7b003c1f3f`, then Immediate
  heating ON `3d7b007f1f3f`, all matching `control.build` (once active the app locks the run-time
  slider and shifts the toggle, so those are the reachable writes).
- **APP-RECORDED (2026-10-05, same harness, after the #234 mock fix):** `cooler.jsonl` — power on
  `fd77…`, level 5 `ff75…`, manual quiet `ff27…` / off `ff07…`, automatic quiet `ff47…` (Mode 2/0/4),
  power off `fc77…`, timer start `f777…` / cancel `df77…`, all matching `control.build` — **byte for
  byte since 2026-10-06** (ruling R1: every untargeted field at the app's leave-unchanged value; the
  replay now compares whole frames for every function but the roof);
  `roof-hold.jsonl` — ignition on, the rocker's upper half held 16 s streams `Up=1` until the mock
  reports open (matches `control.build("roof","open")` on `Up`; the SafetyCounter is not a targeted
  field); `lighting-profile.jsonl` — press-and-hold tile A = `save_profile 1` (matches calictl);
  `lighting-wakeup.jsonl` — the Mode-20 wake-up frame for 07:00 (was a declared `GAPS` entry; since A2
  calictl's `wakeup` builder reproduces it, so it replays as `action`).
- **APP-RECORDED (2026-10-06, A2 Task 7, thinky lab34, emulator in UTC, A2 mock):** `lighting-wakeup.jsonl`
  re-recorded (time 07:00 → switch on → time 08:00 while on → switch off), `door-contact.jsonl` (on, off),
  `lighting-favourite.jsonl` (all on, Cooking 50 %, save A, all off, activate A). Every write replays as
  `action`/`flush`/`app-only`; all 12 recordings replay clean.
- energy current scales — DECOMPILE (2026-09-07): `ITwoBattBemAfs`/`ILandAfs`/`IPvAfs` ÷10 → A
  (`xf/d.java:159,173,175`, holders bound `xf/a.java:150-157,239,307`), `IDcdcAfs` unscaled A + the
  SW-0409/0410 `+2` (`xf/d.java:171`). Plausibility from 14 d telemetry: `batt2_current` raw −49…318
  → −4.9…31.8 A with mean ≈ 0 (balanced leisure battery) — consistent, not a calibration. Owed: one
  metered shore-charging read to upgrade `shore_current` to DEVICE.
- roof `Installed=1` — DEVICE (live read 2026-08-26, #106): the pop-top IS installed (motor never driven).

Captures come from the Mac "bar" (PacketLogger/tshark) or buspi HCI. When one lands: add a
`tools/scenarios/<fn>/<case>.yaml`, assert our frame matches in `test_capture_diff.py`, flip the row
to CAPTURE, and drop the GUI "not verified" confirm for that control. See the memory
`capture-evidence-tier`. A scenario is one (function, action) capture target for
`python3 -m tools.capture_diff <capture> <fn>/<case> [--frames]`: keys `function`, `what`,
`value` (the `calictl set` arguments that should reproduce the app's write), `control_char` (short
UUID the app writes, e.g. `"1501"`), optional `handle` (ATT handle, if known), `capture_label`, and
`state` (decoded state at capture time, carried into the full-packet `control.build`); a leading
comment says what to do at the van (see `tools/scenarios/lighting/kitchen-50.yaml`).

- lighting **zone 9 = pop-top roof READING light** — DEVICE (2026-08-30, single-light isolation:
  unit screen "Dach Ein/Aus" on, live read `zone_9=2`, all other zones 0/13). Exposed an `any_on`
  bug (a zones-1..8 whitelist excluded it — fixed same day). NB conflicts with the 2026-08-27
  "pop-roof = zone 5" toggle note — likely two distinct roof fixtures; map still needs the full pass.
- lighting **full 10-lamp map** — DEVICE (2026-08-30, single-light isolation + owner-watched writes):
  L1=Lesen-R, L2=Lesen-L, L3=Umgebung-hinten, L4=Lesen-vorne, L5=Küche-Ambient, L6=Küche-Schrank,
  L7=Küche-Kochen, L8=Dach-Ambient, L9=Dach-Lesen, L12=Eingang. Fixed the roof-reading mislabel
  (was L6→ now L9; L6=cabinet) and added L12 to the real-zone set. The L6 write was calictl→unit,
  owner-confirmed the cabinet lamp lit (bonus live actuation check).
- BlueZ needs a quiet radio to make a new LE connection: **0x3e under full-duty scanning** —
  CAPTURE (btmon on buspi, 2026-09-25): `LE Connection Complete`, then BlueZ re-enabled active
  scanning at 100% duty (window == interval, 11.25 ms) because another client held discovery,
  ~300 ms later `Connection Failed to be Established (0x3e)`; with all scanners stopped the same
  connect succeeded. See `guided-pairing.md` "Environment the wizard needs".
- the camper unit **advertises from a rotating address** — CAPTURE (observed 2026-09-25):
  four different advertising addresses seen over about 45 minutes of continuous scanning; only
  the bonded identity address is stable, and only becomes known once bonded.
- the unit **refuses Just Works pairing** — CAPTURE (btmon on buspi, 2026-09-26): a Pairing
  Request with `IO capability: NoInputNoOutput`, `No MITM` was answered by the unit dropping the
  link (`Remote User Terminated Connection`, 0x13) within ~0.5 s. Cause on our side: the LE link
  was created before the KeyboardOnly agent was registered. See `guided-pairing.md`.
- the unit **ignores links from an unbonded central outside "Gerät verbinden"** — HYPOTHESIS
  (btmon on buspi, 2026-09-26): with every other scanner stopped and scanning DISABLED at each
  `LE Create Connection`, a dozen consecutive links failed with 0x3e (link never answered) ~2 s
  apart; the first link that came up did so right after the owner opened "Gerät verbinden".
  Timing correlation only — so a quiet radio is necessary but not sufficient; open the screen
  BEFORE starting the wizard.
- the unit's own screen shows **"Passcode: ---" until a pairing request arrives** — OBSERVED
  (owner photo, 2026-09-25): the placeholder stays literal `---` until a central starts pairing
  against the unit, then is replaced by the 6-digit passcode.
- roof **Position decode + L9=roof-reading** — DEVICE (2026-08-30, roof physically opened): live read
  `roof.Position=1 -> position_name "open"` (first live confirm — roof was never driven before), and a
  `roof-reading`(L9) write lit the pop-top reading lamp only with the roof up. Gates the write: L9 is
  unpowered while the roof is closed.
- cooler **cooling-timer decode** — DEVICE (2026-08-30, owner set Startzeit 09:00): live wire
  `timer_active=True, timer_hour=9, timer_min=0` matched the unit screen (was decompile-only). The
  timer can only be armed while the fridge is off — gated.

## APP-DISPLAY rows — real app 5.4.0 on the real unit (2026-10-10)

Tier **APP-DISPLAY 2026-10-10 (real app 5.4.0 on the real unit)**: every number/state the real
CaliforniaOnTour app (5.4.0.3036, Fairphone 6, connected to the unit) shows, read as screen text
(`uiautomator dump`, navigation only — no control touched), compared with buspi's `/api/state` and
the raw fields in `last_state.json` pulled right before and right after each dump (10:39–10:46 UTC).
Van parked, ignition off, camping mode on. Proves calictl's decode + scale against what the app
shows for the same frame — not against a physical meter.

| App screen · label | App shows | calictl raw → decoded | Verdict |
|---|---|---|---|
| Vehicle · Fresh water | `20 / 29 l` | `FreshWaterLevel` 20, `FreshWaterVolume` 29 → `fresh.liters` 20, `capacity_l` 29 | MATCH (Level = litres, Volume = capacity) |
| Vehicle · Waste water | `0 / 22 l` | `WasteWaterLevel` 0, `WasteWaterVolume` 22 → 0 / 22 | MATCH |
| Vehicle · Second battery | `40% • 23 h` | `SocTwoBattAfs` 4 → `soc2_pct` 40; `tTwoBattRemainingh` 23 → `batt2_remaining_h` 23 | MATCH (% = level×10; h = raw hours, minutes not shown) |
| Vehicle Information · Second battery | `13.2 V • 2.0 A` (later `1.9 A`) | `UTwoBattBemAfs` 132 → 13.2 V; `ITwoBattBemAfs` 18–20 → 1.8–2.0 A | MATCH (×0.1 V, ×0.1 A; the 0.1 A steps tracked calictl's polls) |
| Vehicle Information · Vehicle Battery | `40%` and `-- V • -- A` | `SocOneBattAfs` 4 → `soc1_pct` 40; `UOneBattBemAfs` 48, `IOneBattBemAfs` 129 (0x81), `AgeOneBattValuesMinutes` 25–28 → `batt1_v`/`batt1_current` None | MATCH — the app also blanks the starter V/A with ignition off but keeps its SoC |
| Vehicle Information · Charging · Energy Mode | `Normal` | `EnergyMode` 0 → `energy_mode` 0 | MATCH |
| Vehicle Information · Shore power | `0.0 W • 0.0 A` · `Inactive` | `PLandAfs` 0, `ILandAfs` 0, `StateLandAfs` 0 → 0 W, 0.0 A, inactive | MATCH (zero only — scale not exercised) |
| Vehicle Information · Vehicle Power | `0.0 W • 0.0 A` · `Inactive` | `PDcdcAfs` 0, `IDcdcAfs` 65534 (−2), `StateDcdcAfs` 0 → 0 W, `dcdc_current` 0 (−2 + 2 on amb 0410), inactive | MATCH — the app applies the same +2 SW-0410 correction |
| Refrigerator box | switch off, `Off • Level 3/5`, big `3`, `Timer: Off`, `Quiet mode: Off` | `State` 0, `Level` 3, `TimerState` 0, `Mode` 0 → `on` false, `level` 3, `timer_active` false, `quiet_mode` off | MATCH |
| Auxiliary air heater (overview + Heating page) | `Off • Level 6/10`; page `Inactive`, level scale `1…9, HI`, `Run Time: 60 min` (slider 10–120), `Immediate heating` switch off, `Timer: Off` | `HeatingLevel` 6, `RunningTime` 60, `NormalOperation`/`PermanentOperation` 0 → `level` 6, `running_time` 60, `running` false, `timer_armed` false | MATCH (RunningTime = minutes; level 10 is shown as `HI`) |
| Camping mode | `On`; master switch on; front-door lights `Enabled`; sliding-door rear lights `Enabled`; `Rear USB ports are Enabled` | `State` 1, `InteriorLight` 0, `OutsideLight` 0, `UsbCharger` 1; lighting door contact 1 → `master_on`, `lights_on` (inverted: 0/0 = lit), `usb_powered`, `door_contact` all true | MATCH (front-door row = the inverted combined light pair; sliding-door row = the lighting door-contact flag) |
| Lighting | `All lights` off; tile A filled, B/C/D show `+`; every zone `0%` (Reading Left/Right/Front Passenger, Kitchen Background/Cooking, Pop-up roof Background, Exterior Rear Surroundings); Pop-up roof Reading Light `Only available when the pop-up roof is open.` | all equipped `Brightness…` 0, 13 on the not-fitted zones; favourites bit 0 → `any_on` false, `favourites_stored` [1]; roof `Position` 0 | MATCH (0 % only — the 1–10 → 10–100 % scale not exercised here) |
| Pop-up roof (overview) | `Closed` | `Position` 0 → `position_name` closed | MATCH |
| Level Indicator | `-.-°` / `-.-°`, `Signal unavailable. Ignition is off.` | `TerminalOneFive` 0, `CarLevelRoll`/`CarLevelPitch` 0 → `level_roll`/`level_pitch` **0.0** | DIFF then, MATCH since #282: calictl now reports no value with ignition off, like the app. Scale verified: 0.01° per LSB (HCI 2026-07-08). The sign against the app's display is still to confirm with ignition on |

Caveat seen on the way: the first dump right after opening *Vehicle Information* showed old numbers
(`12.2 V • -7.0 A` starter, `13.1 V • 1.0 A` second battery) that calictl's raw frame did not
contain; every later dump (4 over 70 s) matched calictl. The app renders what it last held before
the fresh frame arrives — never use the first render of an app screen as evidence.

## DECOMPILE 5.4.0 rows — app 5.0.8 → 5.4.0 re-decompile (2026-10-10)

Tier **DECOMPILE 5.4.0**: a static diff of the app 5.4.0.3036 decompile against 5.0.8.3028 (private
RE repo `notes/2026-10-10-diff-5.0.8-to-5.4.0.md`; class cites are 5.4.0 names). It shows what the
current app *intends*; nothing below is wire-captured unless the row says so. Details:
`protocol-alignment.md` "App 5.4.0".

| Fact | Tier | What would raise it |
|---|---|---|
| T7 / CommunicationVersion 2 protocol unchanged: every 5.0.8 write site 1:1; `1003` heartbeat random 750–850 ms, seed 1..1e6; 500 ms neutral re-write; roof SafetyCounter 450–550 ms; frames, sentinels, scales identical (energy 1602 V2 slices `og/e.java:1396-1441`) | DECOMPILE 5.4.0; handshake + heartbeat also CAPTURE 2026-10-10 (5.4.0 on the real unit, `protocol-sequences` "Session foundation") | — |
| heater `ErrorCode` 6 = engine running, 7 = auxiliary heater active (`ig/b.java:606-700`) → calictl `engine_running` / `aux_heater_active` + the app's EN/DE dialog texts | DECOMPILE 5.4.0 | fake-unit injection with 5.4.0 (dialog text), and whether the real unit ever reports 6/7 instead of 5 |
| Protocol-version layer: CommunicationVersion → UNKNOWN / V2 / V3 / V4, max accepted 2 → 3; only energy, F001/F002 and VirtualBattery branch on it. calictl flags `comm_version != 2` as `firmware_untested` | DECOMPILE 5.4.0 | a unit reporting 3 (none known) |
| `1603` energy measurements, V3 64-bit `1602`, named V3 `F001` fields — **not on this van** | DECOMPILE 5.4.0 | mock reporting CommunicationVersion 3 in the app lab |
| `F002` general-purpose write (heater night reduction) — **not on this van; never write it to a V2 unit** | DECOMPILE 5.4.0 | app-lab recording with the mock at CommunicationVersion 3 |
| `2200`/`2201`/`2202` VirtualBattery — California Next + V4 only, unreachable in this build | DECOMPILE 5.4.0 | — |
| CarVariant 2 = CALIFORNIA_NEXT; lighting zones 29–36 → existing 1501 fields (29→L7, 30→L4, 31→L1, 32→L2, 33→L9, 34→L8, 35→L3, 36→L5) | DECOMPILE 5.4.0 | — (other vehicle) |
| Per-variant subscribe gate: a T7 app does not subscribe stairs / satellite / roof A/C / LR heater | DECOMPILE 5.4.0 + CAPTURE 2026-10-10 (8 subscriptions) | — |
| rear USB: F001 bit 6 `IsRearUsbInTSevenAlwaysOn` decoded only for V3+, so always false on V2 → the master gate always applies (calictl `usb_powered`) | DECOMPILE 5.4.0 | app lab 5.4.0: the camping rear-USB row with master off |
| water: no stale/plausibility filter, one read at connect, no periodic 1302 re-read (5.0.8 and 5.4.0); 5.4.0 clamps the level math and persists last-known values ("Updated: x ago") | DECOMPILE 5.4.0 | — |

## ESP32 satellite — the control path (#154 B)

The satellite writes control frames for cooler (`1101`), camping mode (`1201`), lighting (`1501`),
energy (`1601`) and air heater (`1701`) — never the roof (`1401`); since 2026-10-07 the lighting
writes include the wake-up light (with the web page's clock `local_now` and the unit's own latched
config, `R_FW_WAKEUP`). The bytes are **calictl's**: `tests/vectors/control.json` is generated from `calictl.control` and the C
twin must reproduce it, so the satellite inherits every tier below from calictl's rows above and
adds nothing to the unit-protocol evidence. What the satellite's own tiers prove is that *its*
bytes equal calictl's and that the allow-list holds.

| Fact | Tier | Evidence |
|---|---|---|
| ESP control frames = calictl's (= the app's on every recorded action of the five functions, whole frame since R1) — the C twin plans the same gates, frames and lighting commits on 1452 grid vectors (since 2026-10-07 incl. the wake-up grid × page clocks × latch variants) + every app-recorded action | **CI** (`tests/test_control_vectors.py` freshness + app coverage, `tests/firmware/test_control_parity.py` byte parity, `--check` on `control_consts.h` / `control.json`) | vectors regenerated from Python; a wording or builder change fails CI until regenerated |
| The allow-list is exactly `1101`/6, `1201`/1, `1501`/16, `1601`/1, `1701`/6 — `1401` and `1003` never pass `cali_ctl_write_ok` | **CI** (`T_FW_WRITE_ALLOWLIST_PURE`: exhaustive scan 0x0000–0xffff × 0–33 bytes) | `test_control_parity.py` |
| Every app-recorded cooler/camping/lighting/air-heater/energy action sent through `POST /api/command` reaches the unit **byte-exact**, lighting commits ≥ 300 ms after their frame; the 4 recorded wake-up edits were then refused "Only via buspi or the app" with no write (superseded 2026-10-07, next row) | **HOST-E2E** 2026-10-06 (`tools/esplab_control_walk.py` over the fake unit's recording, `test_app_recorded_actions_over_api_command`) | `tests/firmware/test_control_e2e.py`; mutants "commit without delay" and "commit byte off" killed |
| The 4 app-recorded wake-up edits through `POST /api/command` with the recording's `local_now` reach the unit **byte-exact** over real NimBLE: `lighting-wakeup.jsonl:258`, `:337`, `:355` with the unit's Mode-20 config latched first; `:239` (no config reported) as the REQUEST_CONFIG pull (`0d0c…` + commit) then the `WAKEUP_UNKNOWN` refusal with nothing more written, and as the explicit `07:00 off` form byte-exact `0e146ac5edf01100…` + commit (app action 1) | **HOST-E2E** 2026-10-07 (`test_app_recorded_actions_over_api_command`, the walker's `latch_frames`/`config_pull`) | `tests/firmware/test_control_e2e.py`; mutant "no latch injected" fails the case |
| A wake-up with no `local_now` → 200 refused with the clock reason; `local_now` as a string, `1000`, `2**32` or a 30-digit integer → `400 bad_value`; nothing reaches the unit; console `set lighting wakeup …` refused with the clock reason | **HOST-E2E** 2026-10-07 (`test_wakeup_clock_refusals_reach_no_unit`, `test_unit_never_sees_a_roof_or_unknown_write`) + session-fake (`test_console_wakeup_is_refused_for_want_of_a_clock`) | `test_control_e2e.py`, `test_session_fake.py` |
| The satellite's wake-up card in Chromium: live once the unit reported its config, an edit 06:00 → 08:00 posts `local_now` and lands at the fake unit as `control.build`'s frames byte-exact; a browser in `Pacific/Auckland` posts its wall clock read as UTC; until the unit reports, *not known yet* shows and only the time is live — a time-only edit pulls REQUEST_CONFIG and lands with the unit-reported config (2026-10-08, `test_wakeup_time_edit_with_no_config_pulls_then_lands`), or is refused with `WAKEUP_UNKNOWN` shown (daemon: `test_wakeup_time_edit_with_no_config_pulls_then_shows_the_refusal`) | **HOST-E2E** 2026-10-07 (`T_FW_UI_LIVE_WAKEUP`, `tests/firmware/test_web_e2e.py`) + stub-level e2e (`T_SAT_UI_WAKEUP`, `tests/e2e/test_satellite.py`) | fake unit's recording; mutant "localNow without the tz offset" fails the Auckland case |
| The wake-up light from the satellite on the **real CoreS3**: the app's 4 wake-up edits byte-exact (`:239` as pull + refusal and as `07:00 off`), a card edit from Chromium with the config latched and with none (REQUEST_CONFIG pull, then the frame, *✓ Applied*) | **BOARD** 2026-10-08 (thinky CoreS3 vs the mock unit, CI image of `6ac867c`, never the real unit) | dated rows in "ESP32 satellite wake-up — BOARD rows" below |
| Roof open/stop, stairs, wake-up, unknown control → refused/400, console `set roof close` refused; a roof `1401`, heartbeat `1003`, 15-byte lighting or empty frame handed straight to the NimBLE transport's `write` is refused at `t_write` (`LOG ble: write …/… refused: not on the control allow-list`), nothing at the unit; an allowed frame through the same hook does arrive | **HOST-E2E** 2026-10-06 (`test_unit_never_sees_a_roof_or_unknown_write`, cali-host's test-only `twrite` line) | `test_control_e2e.py`; mutant "choke point bypassed" killed |
| `403 setup_mode` over the setup hotspot with an armed link and zero writes; `409 busy` while a command is on the air (the running one still completes); ATT Write-Not-Permitted → `502 write_failed` with no commit after; ACK-and-ignore (empty favourite) → 200 `applied: null`; the `1003` heartbeat keeps its period through 5 back-to-back commands | **HOST-E2E** 2026-10-06 | `test_control_e2e.py` (`test_command_refused_in_setup_mode`, `test_second_command_while_one_is_on_the_air_is_busy`, `test_refused_writes_nack_is_502_ack_and_ignore_is_unconfirmed`, `test_heartbeat_keeps_ticking_through_commands`) |
| A fridge toggle in the shared UI (Chromium) lands as `control.build("cooler","power",…)` at the unit; roof + wake-up greyed with the hint (EN + DE; the wake-up card is live since 2026-10-07, row above); a real `409` shows the retry sentence; no offline banner while a 2.5 s command pends | **HOST-E2E** 2026-10-06 (`T_FW_UI_LIVE_CONTROL`, `tests/firmware/test_web_e2e.py`) + stub-level e2e (`tests/e2e/test_satellite.py`, CI `test`) | fake unit's recording = `[("1101", <frame>)]` |
| The control path on the **real CoreS3** (esp-nimble write-with-response, the 31 app cases' value parsing on the Xtensa build (the 1078-case grid ran only on the host, incl. an `-m32` build), the walk, the UI toggle, `403` over the real hotspot) | **BOARD** 2026-10-07 (thinky CoreS3 vs the mock unit, never the real unit) | dated rows in "ESP32 satellite control path — BOARD rows" below |
| The satellite's frames on the **real camper unit** | **DEVICE since 2026-10-08** (paired to the real unit; cooler/wake-up/door-contact live-verified, see 2026-10-08/09 rows) — was: never. Not needed for the bytes (they are calictl's; the real app's identical cooler `level`/`mode`/`timer_*` frames with `State=3` actuated on the real unit, CAPTURE 2026-10-10) — but the real esp-nimble link behaviour under a write is a device question too | first owner-watched satellite session at the van |

## ESP32 satellite pairing wizard — HOST-E2E rows (2026-10-08)

`GET/POST /api/pairing` on the satellite (`R_FW_PAIRING_WIZARD`), over the Linux NimBLE host build
and the Bumble fake unit (`tests/firmware/test_pairing_web_e2e.py`, Docker `oc-fw-host` /
`oc-fw-host-pw` on the Mac). Proves the firmware's endpoint and the shared wizard against the
fake's real SMP passkey pairing — not the esp-nimble port, a radio or the real unit.

| Fact | Tier | Evidence |
|---|---|---|
| start → `waiting_passkey` → the fake's code → `bonded` with the identity; `SNAP` flows; a second `start` changes nothing — in station mode and over the setup hotspot (WiFi stays in setup) | **HOST-E2E** 2026-10-08 | `test_wizard_over_http_bonds_then_snap_flows`, `test_wizard_over_the_setup_hotspot` |
| A wrong passcode: the unit refuses, `attempts` 1, a new code is asked for and bonds | **HOST-E2E** 2026-10-08 | `test_wrong_passkey_retries_then_bonds` |
| The unit's pairing screen closed (the fake's `pair off`): `error` / `pairing_failed` after 3 attempts; *Try again* from error bonds once it is open | **HOST-E2E** 2026-10-08 | `test_unit_not_in_pairing_mode_is_pairing_failed` |
| Stale bond (the fake's `forget_bonds` = "Bluetooth zurücksetzen"): reconnects refused, the page still shows the address, start → the probe proves the key stale (`LOG pair: the stored bond is stale …`), the bond is dropped, passkey bonds afresh, `SNAP` again | **HOST-E2E** 2026-10-08 | `test_stale_bond_repairs_over_http` |
| Probe before replace: Connect now on a bonded satellite with the unit's pairing screen closed (the fake's `pair off`) re-encrypts with the stored key → `bonded` without passkey or SMP, the bond kept, `SNAP` flows; an unreachable unit ends `connect_failed` with the bond kept | **HOST-E2E** 2026-10-08 | `test_connect_now_on_a_working_bond_keeps_it` (`T_FW_PAIRING_PROBE_KEEPS_BOND`), `test_failed_repair_resumes_the_bonded_session` |
| The probe's stale proof as the unit's disconnect reason: the unit hangs up on the stored key with `0x05` → `LOG pair: the stored bond is stale (link dropped, reason 0x05)`, bond dropped, the retry pairs by passkey; a hang-up with `0x13` keeps the bond and the retry's probe bonds without a passkey | **HOST-E2E** 2026-10-08 | `test_probe_hang_up_with_an_auth_reason_drops_the_bond`, `test_probe_hit_by_a_link_drop_keeps_the_bond` |
| Connect now while the session's `connect_bonded` is pending (the unit out of reach): the pending connect is cancelled, the scan starts after the cancel, the unit is found when it returns and the flow ends bonded (watch item 10, was `BLE_HS_EBUSY` + `timeout` on the board) | **HOST-E2E** 2026-10-08, **BOARD** 2026-10-08 (`013877a`) | `test_start_while_the_session_reconnects_scans`; "ESP32 satellite probe before replace — BOARD rows" below |
| The probe on the CoreS3 (esp-nimble + the ESP32-S3 controller's resolving list; PSA `ah()` on Mbed TLS 4): working bond kept with the pairing screen closed, a forgotten bond re-paired by passkey, an absent unit leaves the bond kept | **BOARD** 2026-10-08 | "ESP32 satellite probe before replace — BOARD rows" below |
| The probe's stale proof on the board (HCI `0x05`/`0x06` as the encryption result → `LOG pair: the stored bond is stale …`), `connect_failed` for a unit found but not connectable | **BOARD — owed** | the mock's forgotten bond went straight into SMP without an auth-class status; a stopped mock is never found (`timeout`), see the rows below |
| cancel mid-flow → idle; reset without `confirm` → 400 (bond kept), with it → idle, no address; a fresh pair works | **HOST-E2E** 2026-10-08 | `test_cancel_and_reset` |
| `409 busy` for start/reset while a console `set` holds the link; accepted again once it ended | **HOST-E2E** 2026-10-08 | `test_start_while_a_command_runs_is_busy` |
| The wizard clicked in Chromium against the host firmware: EN over the hotspot's `/app`, DE at `/` in station mode | **HOST-E2E** 2026-10-08 | `test_wizard_in_browser` (`T_FW_PAIRING_WIZARD_UI`) |
| A cancelled (before the pair) or failed (no link: `connect_failed`) re-pair on a bonded satellite keeps the bond and the session reconnects by it — `SNAP` flows again; a `cancel` outside a running flow changes nothing (review I1) | **HOST-E2E** 2026-10-08 | `test_cancelled_repair_resumes_the_bonded_session`, `test_failed_repair_resumes_the_bonded_session`; handler `test_cancel_outside_a_flow_is_a_no_op` |
| The same on the **CoreS3** over a home network and over the setup hotspot | **BOARD** 2026-10-08 | "ESP32 satellite pairing wizard — BOARD rows" below |
| The wizard on the **real camper unit** (its *Gerät verbinden* screen, its passcode, a phone at the van) | **DEVICE — never** (the 2026-10-08 real-unit bond was made from the console, not the wizard) | first owner-watched satellite session at the van |

## ESP32 satellite pairing wizard — BOARD rows (2026-10-08)

CI image `firmware-esp32s3` of PR #261 at `ab47e22` (built from the PR merge ref, the board reports
`fw aefc95f`), flashed with `tools/esplab/flash.sh` over a bonded board (the bond and WiFi
credentials survived the flash; the ESP reconnected by bond). Mock unit `tools/applab/fake_unit_ble.py`
from the same commit on the thinky UB500 dongle with `FAKE_UNIT_RECORD`, the existing keystore and
a pinned passkey. Chromium driven by Playwright on thinky (EN), `/api/pairing` polled alongside.
Never the real unit.

| Date | Fact | Evidence |
|---|---|---|
| 2026-10-08 | Station mode (home network, `/`): ⋮ → *Unpair…* → the confirm dialog (*Unpair removes the working bond; telemetry stops until re-paired. Continue?*) → `POST {"action":"reset","confirm":true}` → `idle`, `address` null, the unpaired banner. *Set up remote control* → *I'm on that screen* → *Connect now* → `scanning` → `connecting` → `waiting_passkey` (≈ 3 s) → the mock's code → `bonded` (≈ 13 s after the code) → *✓ Paired — …*; `/api/state` `link.up` with a fresh read-all (all 14 functions) within seconds | Playwright run + the polled snapshots; no page errors |
| 2026-10-08 | Wrong passcode (station mode): the wizard's *Bluetooth reset / re-pair* (confirm dialog) → `idle` without a bond → *Connect now* → `000000` → the mock refused (`bumble.smp: pairing failure (CONFIRM_VALUE_FAILED)`, link dropped), the runner reconnected and asked again: `waiting_passkey`, `attempts` 1 → the right code → `bonded`, `attempts` 1, state flows | Playwright run + the mock's log |
| 2026-10-08 | Setup hotspot: `wifi forget` → `LOG wifi: setup hotspot up`; the bench stick on the hotspot; `GET /` = the setup page, `GET /app` 200, the device page links to `/app`. At `http://192.168.4.1/app`: *Bluetooth reset / re-pair* (confirm) → *Connect now* → code → `bonded` in ≈ 11 s → *✓ Paired — …*, link up and reading; WiFi stayed `setup` throughout, `device.control.writes` false | Playwright run + `/api/state` |
| 2026-10-08 | Over the hotspot, `POST /api/command` (cooler power on) → **`403 {"ok":false,"error":"setup_mode"}`**; across the whole session (four pairings, the reset/unpair, the 403) the mock saw writes on **`1003` only** — pairing writes no control characteristic | curl answer + the mock's recording (written chars = `{1003}`) |
| 2026-10-08 | `POST /api/wifi` over the hotspot (credentials from a file, piped) → 200 → `LOG wifi: station …` (the home network), `setup hotspot closed`; the bond kept: `bonded`, link up, `control.writes` true at `http://calictl-esp.local` | console + `/api/state` |
| 2026-10-08 | Not run on the board (host tier only): the unit's pairing screen closed (`pairing_failed` after 3 attempts), `cancel` mid-flow, `409 busy` during a command, the stale bond after the unit's Bluetooth reset, the DE wizard | — |

## ESP32 satellite probe before replace — BOARD rows (2026-10-08)

CI image `firmware-esp32s3` of PR #261 at `23e4cd7` (built from the PR merge ref, the board reports
`fw 9ab53b7`), flashed with `tools/esplab/flash.sh` over the bonded board (bond + WiFi kept). Mock
unit `tools/applab/fake_unit_ble.py` from `23e4cd7` on the thinky UB500 dongle, the existing
keystore, FIFO and pinned passkey; it advertises from a resolvable private address. Wizard driven by
`POST /api/pairing` from thinky (the page's *Connect now* = `start`), console captured with
`tools/esplab/esp_cmd.py`. Station mode on the home network. Never the real unit.

| Date | Fact | Evidence |
|---|---|---|
| 2026-10-08 | **(a) Working bond, pairing screen closed** (the mock's `pair off`): `start` from `idle` with the bond kept → `scanning` → `connecting` → `LOG pair: probing the stored bond` → `pairing` → `LOG pair: the stored bond works, keeping it` → `verifying` → `bonded` with the same identity in ≈ 5 s; no `waiting_passkey`, no SMP; `SNAP` flows, `link.up` | console `STATE`/`LOG` lines + `/api/pairing`, `/api/state` |
| 2026-10-08 | On the S3 the controller resolves the mock's RPA **in the scan**: the found address carries the identity with the resolved type `BLE_ADDR_PUBLIC_ID` (2) while the bond is stored as `BLE_ADDR_PUBLIC` (0), so `found_is_bonded()` does not match and its `ah()` is not reached (it returns 0 for a non-RPA); the connect to that resolved address lands on the bonded identity and the probe runs. PSA `ah()` on Mbed TLS 4 checked on the board against the Core spec sample (Vol 3 Part H, IRK `ec0234a3…7d9b`, prand `708194`, hash `0dfbaa`): match 1, one hash bit flipped 0 | a diagnostic build of `23e4cd7` with extra `cali_log`s (local IDF v6.1, not committed), same flow as (a) twice; then the CI image re-flashed |
| 2026-10-08 | **(b) Unit forgot its bonds** (the mock's `forget`, then `pair on`): `start` → probe → `waiting_passkey` → the mock's code → `bonded`, `attempts` 0; the mock showed the code, `SNAP` flows; after a later mock restart the satellite reconnected by the new bond. The mock went straight into SMP — no auth-class encryption status reached the probe, so the stale-proof log did not print, and the probe's ENC event after SMP logged `the stored bond works, keeping it` (misleading: `s_probing` survives the SMP) | console + the mock's log (`bonds forgotten`, `PASSKEY …`) |
| 2026-10-08 | **(c) Unit gone** (mock stopped): the session logs `link lost` and keeps retrying `connect_bonded`; `start` → `scanning` → `error` / **`timeout`** (an absent unit is never found, so not `connect_failed`), the bond kept (`address` still set); after restarting the mock the satellite reconnected on its own, `SNAP` flows | console + `/api/pairing`, `/api/state` |
| 2026-10-08 | **Found:** `start` while the session's `connect_bonded` is pending → `E NimBLE: ble_gap_disc_ext_validate rc=15` (`BLE_HS_EBUSY`): the runner's `START_SCAN` runs before `on_state` stops the session, so no scan ever runs; with the mock started ≈ 10 s into the flow it was still never found → `timeout`. 2/2 | console; `docs/firmware.md` hardware watch item 10 (fixed in `013877a`, re-checked below) |
| 2026-10-08 | Not run on the board: the stale proof via HCI `0x05`/`0x06`, `connect_failed` for a unit found but not connectable, the wizard clicked in Chromium for these cases | — |
| 2026-10-08 | **Re-check of fix round 3** (CI image of `013877a`, board reports `fw 5a39bf5`; mock from `013877a`): **(1) watch item 10** — mock stopped, the session retrying `connect_bonded` (`LOG ble: connect_bonded started`); `start` → `scanning` with **no `rc=15` / `BLE_HS_EBUSY`** in the console; mock restarted ≈ 10 s later → `LOG pair: the unit found is our bonded peer, connecting by its identity` → probe → `the stored bond works, keeping it` → `bonded`, `attempts` 0, no passkey, `SNAP` flows. 1/1 | console (`rc=15` count 0) + `/api/pairing` |
| 2026-10-08 | **(2) Connect now on a working bond** (mock `pair off`, satellite reset to `idle` with the bond): `start` → `LOG pair: the unit found is our bonded peer, connecting by its identity` (the type-agnostic match now fires on the S3's resolved `PUBLIC_ID` address) → `probing the stored bond` → `the stored bond works, keeping it` → `bonded` without passkey, `SNAP` flows; the mock showed no code. 1/1 | console + `/api/pairing`, `/api/state`, the mock's log |
| 2026-10-08 | **(3) Fresh pair after the mock's `forget`** (+ `pair on`): `start` → match → probe → `waiting_passkey` → the code → `verifying` → `bonded`, `attempts` 0, `SNAP` flows; **no `the stored bond works, keeping it`** after the SMP (round 3 clears `s_probing` on SMP start). Two earlier attempts failed only because the code was typed > 30 s after it was shown (SMP timeout, the satellite hangs up `0x13`, then `pairing_failed`); the bond stayed stored through those (no stale proof) | console + `/api/pairing` |

## ESP32 satellite wake-up — BOARD rows (2026-10-08)

CI image `firmware-esp32s3` of PR #256 at `6ac867c`, flashed with `tools/esplab/flash.sh`; mock unit
`tools/applab/fake_unit_ble.py` from the same commit on the thinky UB500 dongle with
`FAKE_UNIT_RECORD`, the existing keystore and pinned passkey (the ESP reconnected by bond, no
re-pair), the ESP on a 2.4 GHz home network. The mock was restarted before every walk (same
keystore/FIFO/passkey) — a second walk against the same mock fails `lighting-wakeup.jsonl:239`,
because the ESP's latch and the mock's stored wake-up survive it. Never the real unit.

| Date | Fact | Evidence |
|---|---|---|
| 2026-10-08 | `tools/esplab_control_walk.py` → **`"problems": []` on 3 walks** of 32 cases (147 control writes): every app-recorded action byte-exact, incl. the 4 wake-up edits with the recording's `local_now` — `:258`/`:337`/`:355` with the unit's Mode-20 config latched first, `:239` as the REQUEST_CONFIG pull (`0d0c…` + commit) then the `WAKEUP_UNKNOWN` refusal with nothing more written, and as `07:00 off` byte-exact. **Zero `1401` writes.** Lighting commit 397–550 ms after its frame over 51 commits; the `1003` heartbeat median 0.60 s (one ~3.2 s gap during each link's first read-all, before any command) | the walker's JSON + the mock's recordings |
| 2026-10-08 | Chromium (Playwright on thinky), config latched (the unit pushed Mode 20 at 06:00): the card shows *06:00*, edit → 08:00 posts `"08:00 1 0 0"` with `local_now`; the mock received `control.build("lighting","wakeup",…)`'s frame + commit **byte-exact**; toast *✓ Applied*; no page errors | Playwright check + the recording vs `gen_control_vectors.expect` |
| 2026-10-08 | Chromium, **no config** (ESP rebooted; the mock holds a wake-up it has not pushed): time field empty but live, *Wake-up light* switch greyed; edit → 08:00 posts `"08:00"` alone; the mock received the REQUEST_CONFIG pull + commit, then (after its Mode-20 reply) the wake-up frame for that config + commit **byte-exact**; toast *✓ Applied* | Playwright check + the recording |
| 2026-10-08 | Same staging with a mock that holds **no** wake-up: the time-only edit → pull + commit, nothing more written; the toast shows *wake-up config not known yet (the unit has not reported it): give on\|off with the edit* | recording + screenshot |
| 2026-10-08 | Bench finding (tooling, not firmware): restarting the fake unit right after the previous one exits, Bumble's HCI reset on the UB500 failed on the first try about half the time (`'HCI_StatusReturnParameters' object has no attribute 'supported_commands'`); bring the interface down, wait and start again | the fake unit's log |

## ESP32 satellite control path — BOARD rows (2026-10-07)

CI image `firmware-esp32s3` of PR #245 at `8b1eda0` (built from the PR merge ref, the board reports
`fw 6831c4f`), flashed with `tools/esplab/flash.sh`; mock unit `tools/applab/fake_unit_ble.py` on
the thinky UB500 dongle with `FAKE_UNIT_RECORD`, fresh passkey pairing (first attempt), the ESP on a
2.4 GHz home network. One BLE link for the whole session (≈ 20 min, no disconnect). Never the real
unit.

| Date | Fact | Evidence |
|---|---|---|
| 2026-10-07 | Every app-recorded cooler/camping/lighting/air-heater/energy action through `POST /api/command` → the mock received calictl's frames **byte-exact**, nothing else: 31 cases (27 frame cases = 39 writes on `1101`/`1201`/`1501`/`1601`/`1701`, 4 wake-up edits refused "Only via buspi or the app" with no write), every answer `applied: null`; **3 clean walks** (`"problems": []`). The lighting commit came 399–550 ms after its frame over 48 commits (calictl waits 300; the ESP's tick bound is 300–400 after the ACK, measured write to write, so the tail above 400 ms is likely ACK lag — not measured) | `tools/esplab_control_walk.py --url http://calictl-esp.local --fifo … --record …`; the mock's recording |
| 2026-10-07 | Roof open/stop, wake-up and stairs over `/api/command` → 200 `refused: "Only via buspi or the app"`; console `set roof stop` / `set roof close` → `LOG control: roof/… refused: Only via buspi or the app`; **zero `1401` writes** in the whole recording | curl answers, console log, `grep -c '"1401"'` = 0 |
| 2026-10-07 | Console `set cooler power off` → `LOG control: cooler/power sending` / `sent`, one `1101` write `fc771e3e1f1f` = `control.build("cooler","power","off", <the ESP's /api/state>)` | console log + recording + calictl on the same state |
| 2026-10-07 | `wifi forget` → setup hotspot; bench stick joined it; `POST /api/command` (cooler power on) to `http://192.168.4.1` → **`403 {"ok":false,"error":"setup_mode"}`**, `device.control.writes` false, no control write at the mock; `POST /api/wifi` over the hotspot rejoined the home network | curl answers + recording |
| 2026-10-07 | The `1003` heartbeat through ≈ 1170 s of commands: median period 0.6 s, longest gap 0.85 s | the mock's recording |
| 2026-10-07 | Water `1302` re-read every 30 s on the live link (40 reads, 29.9–30.3 s apart); `/api/state` `water` = the mock's frame. The *stale push then correct read* ordering was not staged on the board (the mock's scenario console has no "push a frame the read does not return"); it stays session-fake tier (`T_FW_SESSION_WATER_*`) | the mock's recording + `show water` |
| 2026-10-07 | The shared UI from Chromium (Playwright on thinky): Cooler → *Refrigerator box* toggle → toast *Sent — the unit didn't confirm it* and one `1101` write `fd771e3e1f1f`; Roof open/close/stop disabled with the hint, *Wake-up light* disabled, *Sliding door lighting* enabled; no page errors. First load (3 cold runs): median `loadEventEnd` 1030 ms, live state 1138 ms (2026-10-02: 960 / 1065 ms over 5 runs) | `tools/esplab_ui_load.py --runs 3` + a Playwright check |
| 2026-10-07 | Bench finding (tooling, fixed): the walker's documented `python tools/esplab_control_walk.py` failed to import `calictl`, and its "held" check compared whole function dicts, so a field the mock's clock moves (`TimerCounterMin` of `cooler.jsonl:259`) made one case flake on a second walk | `tests/test_esplab_control_walk_cli.py` |

## ESP32 satellite — BOARD rows (CoreS3 on the thinky bench, mock unit)

Firmware `4bfd38e` (#154 status display), mock unit `tools/applab/fake_unit_ble.py` on a USB BLE
dongle, walk `tools/esplab_display_walk.sh`. Screens are LVGL snapshots (the framebuffer, so they
show the content, not the backlight level).

| Date | Fact | Evidence |
|---|---|---|
| 2026-10-01 | Setup screen (`4bfd38e`): `wifi forget` + `forget` → WLAN amber *Einrichtungs-Hotspot calictl-esp-setup …* (cut with "…"; two-line rows since `25723ce`, below), Camper grey *nicht gekoppelt*, footer *WLAN calictl-esp-setup · Passwort calictl-setup* | `LOG wifi: setup hotspot up`, `STATE … idle`; screenshot (`docs/screenshots/esp-screen-setup.png`) |
| 2026-10-01 | `POST /api/wifi` over the setup hotspot → WLAN amber *verbinde mit minsel* → green *minsel · \<LAN IP\> · −32…* (RSSI cut on `4bfd38e`) | `{"ok":true}`, `LOG wifi: online <LAN IP>`; screenshots taken right after the POST and 15 s later |
| 2026-10-01 | `pair` → Camper amber *Code der Einheit eingeben* (`waiting_passkey`); `passkey 123456` → green *verbunden · Daten vor 1 s* | `STATE` scanning → connecting → pairing → waiting_passkey → pairing → verifying → bonded; screenshot (`docs/screenshots/esp-screen-connected.png`) |
| 2026-10-01 | Stale rule: mock frozen with SIGSTOP (link up, no data) → Camper red *keine Daten seit 14 s* | screenshot ~13 s after the freeze; the link dropped later (`LOG session: link lost (event 6, status 531)`) |
| 2026-10-01 | Mock stopped → Camper red *Verbindung verloren, verbinde neu*; the reconnect attempts (2 s → 32 s backoff) keep the same class, no flicker | `LOG session: link lost (event 2, status 13)`, `reconnect in …`; screenshot |
| 2026-10-01 | Dimming: last class change (link lost) ~20:23:16 → `LOG display: brightness 10` at 20:24:16 (60 s); mock back → bond reconnect → `LOG display: brightness 100` at 20:25:09; boot → `brightness 100` | timestamped console log of the walk |
| 2026-10-01 | `screenshot` with no reader (sender closes the port at once, 3×): BLE unaffected — mock reads +22 in 20 s, `/api/state` `device.link.up` true, no reboot; next `screenshot` with a reader complete | console + mock log + `/api/state` |
| 2026-10-01 | `CONFIG_CALI_DISPLAY_FORCE_FAIL=y` build: `LOG display: unavailable (forced)`, `screenshot` → `LOG display: screenshot failed (no screen)`, `/api/state` HTTP 200 with `link.up` true, mock reads +15 in 15 s | console + mock log + `/api/state` |
| 2026-10-01 | Two-line rows (`25723ce`, final review I1/R9): re-walk shows every spec text whole, wrapped at a word, no "…" and no label/value overlap — *Hotspot calictl-esp-setup · 192.168.4.1*, *minsel · \<LAN IP\> · −36 dBm* (dBm on line 2), *Code der Einheit eingeben*, *verbunden · Daten vor 1 s*, *keine Daten seit 13 s*, *Verbindung verloren, verbinde neu*; `fw 25723ce` clear of the title | screenshots of `tools/esplab_display_walk.sh` + a manual pair/stale/link-lost pass (the walk's double `forget` hit the R10 `error/timeout` on the live link, so its camper shots showed *Kopplung fehlgeschlagen*) |
| 2026-10-01 | Reconnect after a mock restart (final review M11): sampled every ~6 s from the restart, the camper row went red *Verbindung verloren, verbinde neu* → green *verbunden · Daten vor 1 s*; no red *keine Daten* frame caught (a < 1 s flash can fall between samples) | 8 screenshots + `STATE bonded` |

## ESP32 satellite — first DEVICE rows (van, real unit, 2026-10-08)

ESP (CoreS3, fw from CI `51f1bee`) on the van WiFi, buspi's calictl polling the same unit. Owner
at the unit as the eyes; unit-screen photos are the ground truth.

| Date | Fact | Evidence |
|---|---|---|
| 2026-10-08 | **The unit holds two LE links at once**: with buspi's persistent session up (`/api/session` `up`, polls fresh) the ESP's pairing flow, started from the USB console (not the web wizard, see the wizard row above), connected, paired (passkey from the unit's screen) and bonded — buspi's session stayed `up` through SMP, verify and beyond, and both read state afterwards | pairing states `scanning → … → bonded 20:81:9A…` polled in lockstep with buspi's `_meta.session`; both `/api/state` fresh |
| 2026-10-08 | First **ESP control write to the real unit**: `POST /api/command` cooler power on → unit `State 0→1`, Kühlbox screen *Ein/Aus* on + snowflake in the status bar; power off restored it. buspi's own next poll read `cooler.on: true` — cross-link state, not a write-through echo | ESP + buspi `/api/state`, owner photo of the Kühlbox screen |
| 2026-10-08 | **Wake-up on the real unit** (`R_FW_WAKEUP`): ESP `/api/command` `lighting/wakeup "07:45 on"` with the page-clock `local_now` → the unit latched `WakeupTimestamp 1791531900` = the next 07:45 local, exactly the app's browser-clock convention; restored to the owner's 07:40 off | ESP `/api/state` Mode-20 latch before/after |
| 2026-10-08 | **`door_contact` live** (first fire of the A2 builder): off → entrance light stays dark with the door open (`DoorContact: 0` in the unit's frame); on → entrance light physically on. The door lamp burns while `BrightnessLOneTwo` stays 0 — the door-contact light is not driven through the L12 zone value | ESP writes + unit state + owner observation |
| 2026-10-08 | **L5 = Küche Ambientelicht re-confirmed on the unit's own screen**: a `kitchen-ambient` (BrightnessLFive) write from buspi flipped *Küche → Ambientelicht* on with the slider at the written level, *Kochen* untouched; the 2026-08-27 "pop-up roof light = zone 5" labeled-toggle observation is retired | owner photo of the Licht/Küche screen during the write |
| 2026-10-08 | buspi-side root cause of the day's `connect_failed`/dead reconnects: **Raspberry Pi OS kernel 6.18.50 never issues `LE Create Connection`** for a bonded RPA-rotating peer on the CYW43455 (passive accept-list scan + `MGMT Connect Failed`, no HCI connect on air); kernel 6.18.34 connects to the resolved RPA directly. buspi pinned to 6.18.34 | btmon captures on buspi; `docs/raspberry-pi-setup.md` known issue |
| 2026-10-09 | **SUPERSEDED 2026-10-10 (#279), see CORRECTED below. The parked unit (ignition off) terminates an idle held link with HCI 0x13** ("remote user terminated", NimBLE-encoded 531) ~30 s after each connect (first logged as ~15–20 s; 30.0 s / 31.3 s measured 2026-10-10), every cycle — while tolerating buspi's 30 s connect→read-all→release poll on a second link indefinitely. With ignition on (previous evening) the same two parallel persistent links held for hours. Two-link tolerance is **state-dependent**; the unit enforces the app's own pattern (no idle holds) — **CORRECTED 2026-10-10:** not a unit policy. The unit sends every central an ATT Exchange MTU Request and drops it when that goes unanswered for 30 s; the ESP (NimBLE without GATT server) never answered (fixed #279). The real app held an idle link 46 min, buspi a held session 3 min, both parked | ESP log `session: link lost (event 6, status 531)` repeating with ~15–20 s up-windows; buspi journal steady `persistent session up`/`released` through the same minutes |
| 2026-10-10 | **ESP parked link HOLDS after #279** (NimBLE GATT server on → the unit's ATT MTU request is answered): flashed faaf7b0 13:11, van parked + locked; `connect_bonded` 13:11:28 → read-all SNAP 13:11:34 → water re-read SNAP every 30 s through 13:14:04, **no `link lost`** (before: 0x13 at 30.0 s / 31.3 s after every connect). ESP water 20/29 L live (`water_held` false, #274 rule) = buspi = app | timestamped ESP console + `/api/state` from buspi |
| 2026-10-10 | **Water `1` = not measuring; measurements ramp.** FreshWaterLevel `1` in 6 098 of 10 726 `1302` frames; a measurement ramps `1→2→…→real` in ~4 s (a notify every ~0.1–0.5 s; 2026-10-09 `2` at 08:54:38.328 … `19` at 08:54:42.228, `20` from 08:55:22), holds ~1–2 min, drops back to `1`; grey `0` throughout. buspi's poll at 08:54:40 read 16 L mid-ramp and adopted it → ramp debounce (`settle_water`, 5 s). What triggers a measurement: unknown | CAPTURE-TRACE: buspi `~/ble.jsonl` (`CALICTL_BLE_TRACE`, not committed), 2026-10-09/10 |
| 2026-10-09 | **The #265 connect watchdog fired in the field**: `session: no connect verdict after 20000 ms` during the kick/reconnect cycle — the silent-cancel case it was built for, recovered by backoff instead of wedging | ESP serial log, morning after the fix shipped |
| 2026-10-09 | An ESP `/api/command` answered `write_failed` while the write **actuated** (kitchen-ambient → 0): the unit applied the frame, dropped the link (the pre-#279 MTU drop) before the ACK/echo reached the ESP, and buspi's next poll read the changed state. "Write failed" under the kick cycle can mean "confirmation lost", not "not written" — the ESP now answers that case `200 … "unconfirmed": true` (link lost after a frame went out) and keeps `write_failed` for nothing-sent / ATT error | ESP command result vs buspi `/api/state` `brightness_zone_5 2→0` with no other writer |
| 2026-10-10 | **The real app on the real unit: every exercised non-motor control byte-identical to calictl's builders** (cooler, lighting except the wake-up switch, campingmode; airheater not exercised, wake-up not diffed) (Fairphone 6, CaliforniaOnTour, Android HCI snoop over adb from buspi; app driven by `adb input`): cooler power `fd77…`/`fc77…`, level `ff74…`/`ff73…`, quiet `ff27…`/`ff47…`/`ff07…`, timer `f777…`/`df77…` (each + the app's neutral `ff771e3e1f1f` 500 ms later); lighting all-lights `0c10…`/`0010…`, favourite A `0110…`, zones `0904…`, door contact `0810…01`/`…00`, screen-open REQUEST_CONFIG `0d0c…` (= `control.LIGHT_REQUEST_CONFIG`) (each + commit `0e00…`); wake-up switch on/off `0e146acb3d501f53`/`…52` (Mode 20, Timestamp = the next 07:40 local packed as UTC, on/off = the enabled bit only — consistent with the builder's packing, not diffed: the builder needs the unit's latch); camping master `fc`/`fd`, USB `f3`/`f7`, lights `5f`/`0f` (+ neutral `ff`). The app holds the link with the `1003` heartbeat (BE counter, +1 every ~0.79 s) for as long as it is in the foreground, and on connect reads `1002`, `1001`, `1004`, subscribes every state char and reads each. Not exercised: airheater (diesel), roof (motor) | `buspi:~/applog/att-2.txt` (decoded ATT, not committed) vs `control.build` over `overrides.apply` |
| 2026-10-10 | **Roof, real app on the real unit (owner holding the button; ignition on, stationary): full open + full close captured.** (1) The app **runs the `1003` heartbeat through the whole move** (38 heartbeats in 30 s of opening, ~0.79 s period) — answers the #230 question and confirms on-device the app-faithful arm calictl adopted 2026-10-05 (A1; calictl ticks at a fixed 0.6 s). (2) While the roof screen is open the app streams `1401` STOP frames `00 00 <counter>` (~0.45 s) even with nothing pressed; a press sends the move byte (`01` open / `04` close) with the **same** counter as the last STOP, then +1 per frame (~0.33–0.45 s); release → STOP stream again. (3) The first open press after the screen opened got `1402` `0302` = **InfoPopUp 2 → the app's pre-open safety checklist dialog** (space above / access board / window or door open), no motion; after OK a fresh press moved the roof. calictl's `_ROOF_ALERT` names 2 "in_use" — wrong on the real unit. (4) Moving: `1402` `030c`→`230c` (Position 2 = between, InfoPopUp 12 while moving), end of travel `2308` (InfoPopUp 8 at the limit) → `1300` (Position 1 open) / `0300` (Position 0 closed); a release mid-way gave `2303` → `2300`. Open took ~28 s, close ~23 s of hold. (5) Remote presses via `adb input` (one DOWN, or a 6 s swipe) did NOT keep the app streaming the move byte — only 1–3 move frames went out, the roof moved a little once; a held real finger is needed | `buspi:~/applog/att-5.txt` 13:32–13:34 UTC (not committed) |
| 2026-10-10 | **Roof capture re-analysed frame by frame** (owner decision "align to original app"): the STOP stream ticks **+1 per ~500 ms** (0.4997 s per increment over att-5), not "+1 per frame every ~0.33–0.45 s"; every direction change (26 of 26, press and release) goes out at once **repeating** the current counter; while held a second frame repeats the value about once a second — the decompiled two-timer model (`w8/a` 500 ms + `ig/c` 1000 ms). The open-press checklist `0302` came on every open press unless one had been raised ≤ 26 s before (40 s → `0302` again); never before a close (`130c`). `0302`→`0300` and `2303`→`2300` are 4.0 s; `2308`→final 1.0 s; press→`030c` 0.27 s, →`230c` 1.17 s. calictl's roof view (`R_ROOF_VIEW_STREAM`) and the mock's roof (`T_MOCK_ROOF_*`) follow it; **calictl's own move still never driven on the real unit (#157)** | `buspi:~/applog/att-5.txt` 13:20–13:34 + `att-3.txt` 13:20–13:28 (not committed) |
| 2026-10-10 | **The ESP's `unconfirmed` answer (#271) fired on the real unit**: van parked and locked, a no-op `lighting kitchen-ambient 0` posted to the ESP every 3 s — the 5th was cut by the parked unit's drop (pre-#279 firmware) after the frame left and answered `200 {"applied":null,"unconfirmed":true}` (was `502 write_failed` before #271); the ESP reconnected on its parked pace and still reported `BrightnessLFive` 0 | ESP `/api/command` responses from buspi, 11:36:45–53 local |
| 2026-10-09 | **Water stale-latch, buspi vs ESP side by side:** parked unit served FreshWaterLevel `1` (the sleep latch). buspi showed `22 l · stale` (last plausible, held by `freshness.implausible_water_drop`); the ESP showed raw `1 l · 3 %` — no guard. Fix: the guard is now ported to the ESP session, persisted in NVS (`device.water_held`), so both hold the last plausible reading | buspi `/api/state` `water.fresh {liters:22, stale:true}` vs ESP `/api/state` `fn.water.FreshWaterLevel:1`, same minute |
| 2026-10-10 | **Push model and lighting timing of the real unit** (same capture, + btmon of buspi's held link + buspi's `CALICTL_BLE_TRACE`): no notification after any CCCD write (app 8, buspi 13; the trace's per-read `notify` is BlueZ re-delivering the read); a push whenever a frame changes (`1102`, `1202`, `1502`, `1602`; `1004` only at the ignition-off edge: `017e…0090` → `047e…0000`, roll/pitch 0, `CarLevelPopUp` 1); neutral `ff771e3e1f1f` / `ff` draw none. Lighting: frames ~230 ms after a SET, before the `0e00…` flush; a rising zone `1` then its level ~100 ms later; DEFAULT = the lamp's level (3/5/10/5/5/7); L9 back to 0 with the roof closed, the rest applied; a `1502` read returns the last frame sent (wake-up / door echoes read back); REQUEST_CONFIG reply = 6 frames (Mode 12, 6, 8, 16/PN 8, 20, 24). Cooler power: `State` pushed ~2 s after the write. Phone app, buspi and ESP32 connected at once; buspi's reads saw the app's lighting frames as notifications | `buspi:~/applog/att-6.txt`, `btmon-hold.snoop` and buspi's `CALICTL_BLE_TRACE` file (not committed); the mock/fake follow it (`protocol-crosscheck-applab.md` "The simulation, aligned to this capture") |
| 2026-10-10 | App capture on the real unit (Fairphone 6, CaliforniaOnTour, HCI snoop): 1302 read `03141d010016` = 20 L fresh while buspi/ESP held 22 L since ~2026-09-18; grey reads 0 on every frame on this van, so the grey-frozen discriminator held every real drop; guard narrowed to the observed latch value (≤1 L) (`WATER_LATCH_MAX_L`) | HCI snoop of the app's 1302 read; app screen *20 / 29 l*; buspi `/api/state` `water.fresh {liters:22, stale:true}` |
