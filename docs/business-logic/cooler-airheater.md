# Cooler (Fridge) & AirHeater (Parking Heater) — Business Logic

Reverse-engineered from the decompiled VW CaliforniaOnTour app (JADX output). Goal: enough
fidelity to build own-vehicle-compatible BLE commands.

Source root (all `file:line` below are relative to this):
`the decompiled sources (local)`

## Shared architecture

Both features share **one control-model class, `sf/a.java`**, with two constructors:

- `sf.a(jn.b, rf.b, xm.a)` — **AirHeater**, write-characteristic UUID
  `00001701-6C77-4B7D-BBF6-A5E587701F3D` (`sf/a.java:44-58`)
- `sf.a(jn.b, vf.c, xm.a)` — **Cooler**, write-characteristic UUID
  `00001101-6C77-4B7D-BBF6-A5E587701F3D` (`sf/a.java:271-285`)

`sf.a` holds 10 shared field slots (`f23982e0` … `f23990n0`), each a bit-packed `sg.a` value
holder. **The same physical slot means a different thing depending on which constructor built
the instance** — confirmed by the two logging methods that dump the slots by name:

- `B()` (`sf/a.java:82-102`) — logs the **Cooler** names: `State, TimerStart, TimerCancel,
  NightTimerSet, Level, Mode, TimerHour, TimerMin, NightTimerHourOn, NightTimerHourOff`
- `A()` (`sf/a.java:60-80`) — logs the **AirHeater** names: `NormalOperationRequest,
  PermanentOperationConfirmation, PermanentOperationRequest, AirDistribution, HeatingLevel,
  OperationModeAirHeater, OperationModeCombined, RunningTime, TimerHour, TimerMin`

Cooler code (`vf/c.java`) always calls `aVar.B()`; AirHeater code (`rf/b.java`) always calls
`aVar.A()` — confirming which log/name-set applies to which device.

| Slot | Cooler name (B) | Heater name (A) | bit width (Cooler / Heater ctor) |
|---|---|---|---|
| `f23982e0` | State | NormalOperationRequest | 2-bit / 2-bit |
| `f23983f0` | TimerStart | PermanentOperationConfirmation | 2-bit / 2-bit |
| `f23984g0` | TimerCancel | PermanentOperationRequest | 2-bit / 2-bit |
| `f23985h0` | NightTimerSet | AirDistribution | 2-bit / 2-bit |
| `f23986i0` | Level | HeatingLevel | 4-bit / 4-bit |
| `f23987j0` | Mode | OperationModeAirHeater | 4-bit / 4-bit |
| `f23988k0` | TimerHour | RunningTime | 8-bit / 8-bit |
| `l0` | TimerMin | TimerHour | 8-bit / 8-bit |
| `f23989m0` | NightTimerHourOn | TimerMin | 8-bit / 8-bit |
| `f23990n0` | NightTimerHourOff | OperationModeCombined | **8-bit / 4-bit** (only slot whose width differs between devices) |

**Critical send behavior (`m2/a.java:88-99`, the common base class of `sf.a`):**
`y(boolean)` is called at the end of every action method. It:
1. Calls `f()` to rebuild the **entire** bit-packed frame from *all 10 slots' current
   in-memory values* — not just the one you changed (`m2/a.java:96`, `sf/a.java:104-232`).
2. Writes that full frame to the characteristic (`m2/a.java:96-98`).
3. CORRECTION (2026-07-08): this is a **~500 ms one-shot** `jn.a`, **not** a repeating timer.
   `jn.a`'s 3rd ctor arg is `false` for cooler/camping (`repeat=false`); only the roof uses
   `true`/repeating (its move-heartbeat). See `remote-control-write-mechanics.md`. The actual
   on-device precondition for actuation is the **1003 liveness heartbeat** (issue #2, SOLVED),
   not this timer. So: send the full frame once under a running 1003 heartbeat; no per-command
   resend is needed for cooler/camping.

**Implication for own-vehicle commands:** you must maintain shadow state for *all* fields of a
device's control object, not just the one you're changing, and send the full frame each time —
exactly mirroring what the app does. Sending only the "changed" field with everything else
zeroed will likely stomp Level/Mode/timers.

Momentary "trigger" fields (TimerStart, TimerCancel, PermanentOperationRequest=0) are set to a
value and **never explicitly reset to 0/idle** by the action methods themselves. The app relies
on `v()` (`sf/a.java:242-269`) — called elsewhere (presumably device init/reconnect) — to restore
sentinel defaults. Don't worry about explicitly clearing them after firing; the real app doesn't
either.

---

## Cooler

Feature class: `vf/c.java` (implements `af/a.java`). State/read-back characteristic
`00001102-...`; log message `<-- Incoming Data for Cooler:` (`vf/c.java:377-388`).
Service UUID `00001100-...` (`vf/c.java:134`).

### 1. Action → field map

| Method | Signature | Field set | Value | Cite |
|---|---|---|---|---|
| `U0` | `(boolean, dz.c)` | State | `z ? 1 : 0` | `vf/c.java:240-248` |
| `X1` | `(int, yh.b)` | Level | raw int, **no clamp in setter** | `vf/c.java:263-271` |
| `f` (static helper) | `(c, int)` | Mode | raw int | `vf/c.java:182-190` |
| `T1` | `(fz.i)` | Mode | `0` (via `f(this,0)`) | `vf/c.java:234-237` |
| `x0` | `(yh.c)` | Mode | `2` (via `f(this,2)`) | `vf/c.java:621-624` |
| `k0` | `(yh.c)` | Mode | `4` (via `f(this,4)`) | `vf/c.java:590-593` |
| `D` | `(fz.i)` | TimerStart | `1` | `vf/c.java:193-201` |
| `X0` | `(dz.c)` | TimerCancel | `1` | `vf/c.java:251-260` |
| `c0` | `(int, yh.b)` | NightTimerHourOn | raw int | `vf/c.java:305-313` |
| `Y2` | `(int, yh.b)` | NightTimerHourOff | raw int | `vf/c.java:279-287` |
| `y0` | `(m, si.e)` | TimerHour = `m.f24999a`, TimerMin = `m.f25000b` | raw ints (hour, min) | `vf/c.java:632-642` |

`m` is a simple `(hour, minute)` pair class (`te.m`) used both here and for the heater's timer.

Two `af.a` interface methods (`w3`, `x4`) are **no-ops** — declared but do nothing
(`vf/c.java:615-618`, `626-629`). Not wired to any hardware effect in this build.

TimerStart/TimerCancel/NightTimerHourOn/NightTimerHourOff, State and Level/Mode all get sent
together in one frame per the shared-send behavior above — e.g. calling `U0(true)` also
transmits whatever Level/Mode/timer values are currently held in the model.

### 2. Value semantics / enums

- **Level**: raw int, GUI slider 1-5 confirmed via the read-back clamp: `X3()` getter
  (`vf/c.java:273-276`) exposes `H0`, computed as
  `(1 > level || level >= 6) ? 1 : level` (`vf/c.java:162-164, 397-398`) — i.e. the app treats
  1-5 as valid and falls back display to 1 outside that range. **The write path (`X1`) does
  NOT clamp** — sending an out-of-range Level is possible but unsupported/undefined; stick to
  1-5.
- **Mode**: no dedicated enum class found; raw int with exactly three values used by the app's
  own UI: `0` (via `T1`), `2` (via `x0`), `4` (via `k0`). Two derived booleans:
  - `J0()` → `Mode == 2` (`vf/c.java:167, 218-221`)
  - `P1()` → `Mode == 4` (`vf/c.java:168, 223-226`)
  **DISPLAY-CONFIRMED 2026-08-26** (the unit's Flüstermodus screen: two toggles "Ein/Aus" +
  "Automatisch") + call-stack (`vf/c` K0/L0 → QuietModeViewModel `yh/e`):
  **Mode 0 = off, Mode 2 = manual Quiet ("Ein/Aus"), Mode 4 = scheduled Quiet ("Automatischer
  Flüstermodus").** `quiet_scheduled` is derived from Mode 4, NOT `NightTimerSet` (a dead-end bit).
- **Error** (read-back field, not directly settable): `0` = none, `1` = Error, `2` = Emergency
  operation, `3` = Door open (`vf/c.java:408, 420-437` dispatches to
  `COOLER_ERROR_NOTIFICATION_ID` / `COOLER_EMERGENCY_OPERATION_NOTIFICATION_ID` /
  `COOLER_DOOR_OPEN_NOTIFICATION_ID` respectively). Exposed to UI as:
  `H()` → `Error == 1` (`vf/c.java:208-211`), `h0()` → `Error == 2 || Error == 3`
  (`vf/c.java:584-587`).
- **NightTimerHourOn/Off**: raw hour ints, 8-bit field (0-255 range in the wire format, but
  semantically an hour-of-day 0-23) — used only to configure the schedule for Mode 4
  (timer-based Quiet Mode); matches `coolboxPage_quietModeDrawer_quietModeStart_text` /
  `..._quietModeEnd_text`.
- **TimerHour/TimerMin**: raw ints for the separate "cooling start" timer (distinct system from
  the Quiet-Mode night-timer above, despite similar naming) — matches
  `coolboxPage_timerSetupDrawer_*` / `coolboxPage_timerWidget_*`.
- **Timer widget state** (read-only, not user-set): `af/b.java` enum `INIT(0), SET(1),
  RUNNING(2), PAUSED(3), ELAPSED(4)`, derived in `g()` (`vf/c.java:575-577`) from the
  `TimerState`/`Installed`/`State` read-back fields; drives which of
  `coolboxPage_timerWidget_startTimer_button` / `continueTimer_button` / `pauseTimer_button` /
  `cancelTimer_button` is shown. UNVERIFIED exact mapping of RUNNING vs ELAPSED to those buttons.

### 3. UI rules / constraints

From string-resource keys (`rg -o -i "coolboxPage_[a-zA-Z0-9_]+"`); literal English copy was not
recoverable (lives in compiled `.cvr` resource blobs, not source) — key names below are strong
signals of intent, marked UNVERIFIED where the exact literal rule wording matters:

- `coolboxPage_quietModeWidget_quietModeCanOnlyBeActivated_text` — **resolved 2026-09-16** from
  the string tables: "To activate quiet mode, the refrigerator box must be switched on." /
  "Um den Flüstermodus zu aktivieren, muss die Kühlbox eingeschaltet sein." A UI-layer gate
  (no `enabled=` guard around `x0()`/`k0()` in `vf/c.java`); the web UI mirrors it by greying the
  quiet-mode controls while `State != 1`. Its sibling
  `..._quietModeCanOnlyBeSwitchedOn_text` = "Quiet mode can be switched on for up to 23 hours."
  (the manual mode's duration hint, not a gate).
- `coolboxPage_timerWidget_toSetTimer_text` — "To set the timer, please switch off the
  refrigerator box above." The inverse gate: the cooling timer is only settable while the box is
  OFF. calictl enforces it server-side (`control.command_precondition`) and greys the timer
  controls in the web UI.
- `coolboxPage_timerWidget_afterTheTimerIsActivated_text`,
  `..._afterContinuingTheBoxWillbeCooled_text`, `..._theBoxWillBeCooled_text`,
  `..._pleaseSetTheTimespan_text` — standard timer lifecycle copy (set → confirm → running →
  pause/continue → cancel), consistent with the `af.b` state machine above.
- Door-open / emergency / error handling is real business logic (not just UI text): on
  Error==0 the app **clears** all three cooler notification channels
  (`COOLER_ERROR_ID`, `COOLER_EMERGENCY_OPERATION_ID`, `COOLER_DOOR_OPEN_ID`) and disables a
  corresponding feature-flag bit; on Error 1/2/3 it raises the matching notification
  (`vf/c.java:428-567`). This is read-back-only — it does not gate what commands you may send,
  it only reflects device-reported state.

### 4. Correct command recipe

Send the full 10-field frame every time (see shared-send behavior); only the field you intend
to change should differ from the previously-known state.

- **Turn Cooler on/off**: set `State = 1/0` (via `U0`), leave Level/Mode/timer fields at their
  last-known values. `vf/c.java:240-248`.
- **Set cooling level (1-5)**: set `Level` (via `X1`) to 1-5; keep State/Mode unchanged.
  `vf/c.java:263-271`.
- **Turn Quiet Mode off**: `Mode = 0` (via `T1`). `vf/c.java:234-237`.
- **Turn manual Quiet Mode on**: `Mode = 2` (via `x0`). `vf/c.java:621-624`.
- **Turn timer-based Quiet Mode on**: `Mode = 4` (via `k0`); should be paired with previously
  configured `NightTimerHourOn`/`NightTimerHourOff` (via `c0`/`Y2`) so the schedule is
  meaningful. `vf/c.java:590-593, 279-313`.
- **Configure Quiet-Mode schedule**: set `NightTimerHourOn` and `NightTimerHourOff` (hour 0-23)
  independently via `c0`/`Y2` — these do not themselves activate anything; you still need
  `Mode = 4` to arm the schedule. `vf/c.java:279-313`.
- **Start the cooling-start timer**: set `TimerHour`/`TimerMin` together via `y0`, **then**
  set `TimerStart = 1` (via `D`) to arm it — both are sent in the same/subsequent full frame.
  `vf/c.java:632-642, 193-201`.
- **Cancel the cooling-start timer**: `TimerCancel = 1` (via `X0`). `vf/c.java:251-260`.
- Do **not** attempt to set Level/Mode outside the documented ranges — the setters don't
  validate, but behavior outside 1-5 (Level) or {0,2,4} (Mode) is unproven and may confuse the
  read-back UI logic (which assumes those ranges).

---

## AirHeater

Feature class: `rf/b.java` (implements `we/b.java` and `df/a.java` — two separate interfaces,
unlike Cooler's single `af.a`). State/read-back characteristic `00001702-...`; log message
`<-- Incoming Data for AirHeater:` (`rf/b.java:393-402`). Service UUID `00001700-...`
(`rf/b.java:121`).

### 1. Action → field map

| Method | Interface | Signature | Field set | Value | Cite |
|---|---|---|---|---|---|
| `C2` | `we.b` | `(boolean, fz.c)` | NormalOperationRequest | `z ? 1 : 0` | `rf/b.java:182-191` |
| `D4` | `we.b` | `(int, uh.c)` | RunningTime | raw int, **no clamp** | `rf/b.java:198-207` |
| `E3` | `we.b` | `(fz.i)` | PermanentOperationRequest | `0` (only "off" is exposed here) | `rf/b.java:209-218` |
| `H3` | `we.b` | `(we.a, uh.b)` | AirDistribution | `1/2/3` from `we.a` enum ordinal+1 | `rf/b.java:220-242` |
| `q4` | `we.b` | `(int, uh.c)` | HeatingLevel | raw int, **guarded 1 ≤ i ≤ 10, else silently dropped (no send)** | `rf/b.java:771-783` |
| `B0` | `df.a` | `(m, fz.i)` | TimerHour = `m.f24999a`, TimerMin = `m.f25000b` | raw ints (hour, min) | `rf/b.java:164-175` |
| `a2` | `df.a` | `(df.b, fz.i)` | **"Start timer"**: OperationModeCombined = ordinal+1 (AIR_HEATER → 1) **and** OperationModeAirHeater(Mode) = `3` → `3f3b017f1f3f` (APP-OBSERVED 2026-09-16; caller `uh/d.java`) | `rf/b.java:274-308` |
| `j4` | `df.a` | `(fz.i)` | **timer "Stop"**: OperationModeAirHeater(Mode) = `0` (via `f(this,0)`) → `3f0b007f1f3f` (APP-OBSERVED) | `rf/b.java:745-749, 154-162` |

Note the field-name overlap trap: the wire slot the app calls `TimerHour` for the heater is
physical slot `l0`, and `TimerMin` is `f23989m0` — **the opposite of the Cooler's slot↔name
pairing** for those two positions (see the shared table above). Do not reuse Cooler field
offsets for the Heater.

**No direct "turn Permanent Heating ON" write was found** in `rf/b.java` — `E3()` only ever
writes `PermanentOperationRequest = 0` (off). The only method that raises `OperationModeAirHeater`
above 0 is `a2()` — and running the app showed what it is for: **`a2(AIR_HEATER)` is the heater's
"Start timer"** (Mode=3 + OperationModeCombined=1, observed 2026-09-16, see the Mode bullet
above); it has nothing to do with turning Permanent Heating on. The string-resource keys
(`..._permanentHeatingTimerDialog_permanentHeatingWillTurnOff_text`) say arming the timer
turns permanent heating off, i.e. the two are mutually exclusive. Permanent Heating ON stays
in-vehicle-only (no write site in the app).

The heater's "timer start" trigger is therefore NOT a bit like Cooler's `TimerStart` but the
`OperationModeAirHeater` value itself — physical slot `f23983f0` (Cooler's `TimerStart`) is named `PermanentOperationConfirmation`
for the heater and is never written by `rf/b.java`. Inference: writing non-zero
`TimerHour`/`TimerMin` via `B0()` alone is what arms the heater's departure timer (UNVERIFIED).

### 2. Value semantics / enums

- **HeatingLevel**: hard app-enforced range **1-10** (`rf/b.java:774`: `if (i > 0 && i <= 10)`) —
  unlike the Cooler, out-of-range values are **silently dropped, no BLE write occurs at all**.
  Matches `airHeaterPage_heatingSlider_heatingTemperature_text`. Field is 4-bit wide (fits 0-15),
  consistent with the 1-10 range.
- **AirDistribution**: enum `we/a.java` — `FRONT_AND_REAR(0)`, `FRONT(1)`, `REAR(2)`
  (`we/a.java`). `H3()` maps ordinal→wire value by `+1`: `FRONT_AND_REAR→1, FRONT→2, REAR→3`
  (`rf/b.java:220-242`). Wire value `0` is the pre-connection sentinel, not a valid selection.
- **OperationModeCombined**: enum `df/b.java` — 7 values describing *which auxiliary
  climate devices run together*: `AIR_HEATER(0)→1, TRUMA_HEATER(1)→2,
  ROOF_AIR_CONDITION(2)→3, TRUMA_HEATER_AND_AIR_HEATER(3)→4,
  TRUMA_HEATER_AND_ROOF_AIR_CONDITION(4)→5, AIR_HEATER_AND_ROOF_AIR_CONDITION(5)→6,
  AIR_HEATER_AND_TRUMA_HEATER_AND_ROOF_AIR_CONDITION(6)→7` (`df/b.java`, `rf/b.java:274-308`).
  This strongly implies these "combined operation" features (Truma heater, roof air-con) are
  **only relevant on vehicles equipped with them** (see model-gating note below) — plain
  AirHeater-only vehicles should never need this method.
- **OperationModeAirHeater (Mode)** — **the departure-timer arm** (APP-OBSERVED 2026-09-16,
  `tools/applab`): the heater page's "Start timer" runs `uh/d.java` → `a2(df.b.AIR_HEATER)` →
  Mode `3` + `OperationModeCombined` `1`, wire `3f3b017f1f3f`; its "Stop" runs `j4` → Mode `0`,
  wire `3f0b007f1f3f`. Readback Mode 3 drives the page's "Timer: On" row and the status bar
  "Inactive • Timer: HH:MM" (gone after Stop even though TimerHour/TimerMin keep the time). Every
  other heater frame carries the sentinel `7`. calictl: `timer_start`/`timer_cancel`,
  `semantics.airheater().timer_armed`. Values `1`/`2` are referenced in the field's bit layout
  but no setter writes them — likely firmware-reported running states. UNVERIFIED. What the
  unit reports after the timer fires is also UNVERIFIED (the mock assumes Mode back to 0).
- **ErrorCode dialogs — APP-OBSERVED 2026-09-16** (fake unit `set airheater ErrorCode=N`, the
  app pops a toast the moment the readback changes; text verbatim):
  `1` **Battery voltage too low** — "Your second battery looks low. Please connect to an
  external power source or charge your second battery." · `2` **Low fuel** — "The auxiliary air
  heater was switched off because the fuel level is low. It can only be activated again when
  there is sufficient fuel. Please refuel." · `3` **Auxiliary air heater** — "There seems to be a
  fault with the auxiliary air heater. Please contact your authorised workshop." · `4` **Emission
  limit exceeded** — "The vehicle automatically switched off the auxiliary air heater. The
  auxiliary air heater can be switched on again when the vehicle is moving at a speed of 5 km/h
  or more." · `5` **Auxiliary air heater deactivated** — "The auxiliary air heater cannot be
  activated when the engine is running or when the auxiliary water heater is activated. If the
  auxiliary air heater is active, it will be switched off when the engine is running or when the
  auxiliary water heater is activated." `FaultTriggerBit=1` alone shows nothing. The web UI's
  `AIRHEATER_ERROR_MSG` mirrors these titles (so `heating_time_exceeded` reads "Emission limit
  exceeded", `not_possible` "deactivated — engine / water heater running").
- **ErrorCode** (read-back only): `0` = none/cleared, `1` = low battery, `2` = low fuel,
  `3` = system error, `4` = heating time exceeded, `5` = operation not possible
  (`rf/b.java:461-711`, dispatches to `AIR_HEATER_LOW_BATTERY_ID` / `AIR_HEATER_FUEL_LOW_ID` /
  `AIR_HEATER_SYSTEM_ERROR_ID` / `AIR_HEATER_HEATING_TIME_EXCEEDED_ID` /
  `AIR_HEATER_OPERATION_NOT_POSSIBLE_ID` respectively). `ErrorCode == 0` additionally sets a
  sticky "deactivated" flag (`bVar.I0 = true`, `rf/b.java:446-460`) matching
  `airHeater_deactivated_headline/text`. `h()` (`rf/b.java:718-722`) derives a general
  "problem" boolean from `ErrorCode == 5 || (everSeenDeactivated && 1 ≤ ErrorCode < 5)`.
  Heating-time-exceeded (ErrorCode 4) plausibly corresponds to
  `airHeater_emmissionExceedance_headline/text` (EU parking-heater runtime/emissions limits) —
  UNVERIFIED, inferred by elimination since the two other found string keys (lowBattery,
  lowFuel, systemError) already map 1:1 to their own ErrorCode values.
- **Vehicle-model gate**: a boolean readback (`rf/b.java:141`,
  `kVar2.f21190d0.getValue() == te.a.X`) checks the current vehicle model against
  `te/a.java` enum `CALIFORNIA_6_1(0), CALIFORNIA_7(1), GRAND_CALIFORNIA(2)` — `X` = ordinal 2 =
  `GRAND_CALIFORNIA`. This flag (`q1()` getter) is UNVERIFIED in exact effect from these two
  files alone, but strongly suggests Permanent Heating / combined-device operation
  (Truma heater + roof air-con) is a **Grand California-specific feature set** — plain
  California 6.1/7 owners likely only have the basic on/off + level + timer + air-distribution
  controls (`C2`, `D4`, `q4`, `H3`, `B0`).

### 3. UI rules / constraints

From string-resource keys (`rg -o -i "airHeaterPage_[a-zA-Z0-9_]+|airHeater_[a-zA-Z0-9_]+"`);
literal copy again not recoverable from source, keys used as strong signals:

- `airHeaterPage_imediateHeatingWidget_imediateHeating_text` +
  `airHeaterPage_runTimeSlider_runTime_text` — "Immediate Heating": `C2(true)` (turn on
  NormalOperationRequest) paired with `D4(minutes)` (RunningTime), for a one-shot bounded
  heating session.
- `airHeaterPage_permanentHeatingWidget_permanentHeating_text` /
  `..._canOnlyBeActivated_text`, `airHeaterPage_permanentHeatingOnDialog_*` — Permanent Heating
  has an activation guard/confirmation dialog (likely device/model preconditions); see
  Mode/OperationModeCombined discussion above.
- `airHeaterPage_permanentHeatingOffDialog_turnOffPermanentHeating_text` /
  `..._youCanOnlyTurnItBackOn_text` — turning Permanent Heating off (`E3()`, sets
  `PermanentOperationRequest = 0`) apparently has a one-way-ish quality ("you can only turn it
  back on [via a specific path]") — consistent with there being no direct "on" setter for that
  field.
- `airHeaterPage_permanentHeatingTimerDialog_doYouWantToProceed_text` /
  `..._permanentHeatingWillTurnOff_text` / `..._yesStartTimer_button` — confirms starting the
  departure timer (`B0()`) is presented as **mutually exclusive with Permanent Heating** (starting
  the timer turns permanent heating off).
- `airHeaterPage_applyDialog_applyNewSettingsNow_text` / `..._runTimeWillBeReset_text` — changing
  the combined-device selection (`a2()`) resets RunningTime; treat `a2()` as a disruptive/config
  change, not a lightweight toggle.
- `airHeaterPage_timerWidget_startHeatingAt_text` / `..._heatingWillStart_text` /
  `..._startTimer_button` / `..._afterTheTimerIsActivated_text` — standard timer lifecycle copy,
  same shape as Cooler's timer widget.
- `airHeaterPage_statusBar_activePermHeating_text` / `..._activePermHeatingTimer_text` /
  `..._activeRemainingTimerStartTimer_text` / `..._activeRunTime_text` / `..._inactive_text` /
  `..._inactiveStartTimer_text` — six distinct status-bar states derived from combinations of
  NormalOperation/PermanentOperation/RunningTime/Timer read-back fields; no single enum backs
  this in the two source files (composed in UI layer, not directly readable here). UNVERIFIED
  exact boolean combination per state.

### 4. Correct command recipe

Send the full 10-field frame every time (shared-send behavior above).

- **Turn on Immediate/Normal Heating for N minutes**: set `RunningTime = N` (via `D4`, which
  writes the raw int unclamped — the bound lives in the UI: the app's heating info page states
  immediate heating "is limited to 120 minutes" (`infoPage_heating_immediate_description`), and
  the unit reports `ErrorCode 4` HEATING_TIME_EXCEEDED past it. calictl enforces **0–120** in
  `control._airheater` (`AIRHEATER_MAX_RUNTIME_MIN`); the 8-bit width is not the valid range),
  then/also set `NormalOperationRequest = 1` (via `C2(true)`). `rf/b.java:198-218`.
- **ErrorCode (1702) → app fault IDs** (`rf/b.java:461-671`): 1 `AIR_HEATER_LOW_BATTERY`,
  2 `FUEL_LOW`, 3 `SYSTEM_ERROR`, 4 `HEATING_TIME_EXCEEDED`, 5 `OPERATION_NOT_POSSIBLE`;
  0 clears. Each drives a dialog AFTER a refused start (the app never greys the heater switch
  pre-emptively). Surfaced by `semantics.airheater` as `error` (`low_battery` / `low_fuel` /
  `system_error` / `heating_time_exceeded` / `not_possible`) next to the raw `error_code`; the
  web UI shows it as a banner. Code→dialog-string pairing beyond the IDs is unresolved.

**Observed on the running app (2026-09-16, `tools/applab` — the real app against a fake unit):**

- Heater page ("Heating"): temperature slider **1–9 + "HI"** (HI = level 10), run-time slider
  **10–120 min**, *Immediate heating* switch, *Timer: Off* expander, *Permanent Heating* switch
  **always present** — greyed with "This function can be activated only in the vehicle." while
  off, live while on. Status line: "Inactive" / "Active • N min remaining" (from
  `RunningTimeinAction`) / "Active • Continuous heating".
- *Immediate heating* ON: **no confirmation dialog**; the app writes `3d7b007f1f3f` and, 500 ms
  later, the neutral `3f7b007f1f3f` (every request field back at sentinel 3). OFF: `3c7b007f1f3f`.
- *Permanent Heating* OFF (only after "Turn off continuous heating? You can only turn continuous
  heating on again using the controls in your California." → *Turn off*): `0f7b007f1f3f` +
  neutral. There is no ON write; the switch is inert when off.
- The untargeted bytes `7b 00 7f 1f 3f` are the model defaults = leave-unchanged sentinels
  (HeatingLevel 11, OperationMode 7, RunningTime 127, TimerHour 31, TimerMin 63). calictl's
  `_airheater_values` re-sends the CURRENT values instead — the unit accepts both; the mock now
  treats those defaults as "leave unchanged" (`tests/test_mock_fidelity.py`).
- **Turn off Immediate Heating**: `NormalOperationRequest = 0` (via `C2(false)`).
  `rf/b.java:182-191`.
- **Turn off Continuous Heating ("Dauerbetrieb")**: `PermanentOperationRequest = 0` (via `E3`,
  `rf/b.java:209-218`) — the ONLY remote write for that mode; there is no ON write site anywhere
  in the app (it is started from the in-vehicle controls; the app's ON tap only raises the
  "can only be activated in the vehicle" dialog). calictl: `set airheater permanent off`
  (`on` is refused), web UI: the Continuous-heating switch is live only while it is on.
- **Set heating level (1-10)**: `HeatingLevel` via `q4`; values outside 1-10 are dropped
  client-side (no send at all) — mirror that guard in your own implementation.
  `rf/b.java:771-783`.
- **Set air distribution**: `AirDistribution` via `H3`, using `we.a` enum
  (`FRONT_AND_REAR/FRONT/REAR`) mapped to wire values 1/2/3. `rf/b.java:220-242`.
- **Arm the departure timer**: set `TimerHour`/`TimerMin` via `B0` (note the swapped slot
  mapping vs Cooler above). This appears to be the sole trigger — no separate "start" bit
  found. Expect this to end any active Permanent Heating per the dialog text.
  `rf/b.java:164-175`.
- **Turn off Permanent Heating**: `PermanentOperationRequest = 0` via `E3`. `rf/b.java:209-218`.
- **Arm the departure timer ("Start timer")**: `a2(df.b.AIR_HEATER)` →
  `OperationModeAirHeater(Mode) = 3`, `OperationModeCombined = 1`, everything else at the
  sentinels: **`3f3b017f1f3f`** (APP-OBSERVED 2026-09-16 on a plain AirHeater-only California;
  `uh/d.java` → `rf/b.java:274-308`). The other `df.b` combos (2-7) name Truma / roof-A/C
  equipment and are for vehicles that have it. calictl: `set airheater timer_start`.
- **Stop the departure timer ("Stop")**: `OperationModeAirHeater(Mode) = 0` via `j4` →
  **`3f0b007f1f3f`** (APP-OBSERVED). calictl: `set airheater timer_cancel`.
  `rf/b.java:745-749`.

---

## Open questions (UNVERIFIED, out of scope of these two files)

- Exact retry/cancel semantics of the 500 ms `jn.a` resend timer.
- Literal UI copy for all `coolboxPage_*`/`airHeaterPage_*` string keys (binary `.cvr` resources,
  not present in decompiled Java source).
- Precise Compose-layer `enabled=`/guard conditions gating buttons (e.g. exactly when Quiet Mode
  or Permanent Heating buttons are greyed out) — this logic lives in Compose UI files that are
  too obfuscated (single-letter classes/methods shared across the whole app) to reliably
  attribute to Cooler/AirHeater specifically.
- Whether "Permanent Heating ON" has a direct control-field write anywhere in the app — none
  found; `a2()` turned out to be the departure-timer arm, not a permanent-heating path (2026-09-16).
- What `1702` reports once the armed timer fires (Mode stays 3? drops to 0? `NormalOperation`
  rises?) — needs a real-unit trace across a timer start.
