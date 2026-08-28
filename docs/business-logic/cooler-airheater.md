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
  Cross-referenced against string-resource keys `coolboxPage_quietModeWidget_manualQuietMode_text`
  and `..._timerBasedQuietMode_text`: strong inference (not 100% proven textually) —
  **Mode 0 = normal cooling (quiet mode off), Mode 2 = manual Quiet Mode active, Mode 4 =
  timer-based Quiet Mode active.** UNVERIFIED: exact label-to-value binding (2 vs 4 could be
  swapped relative to the true UI labels), but the on/off boundary (Mode 0 = off) is solid.
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

- `coolboxPage_quietModeWidget_quietModeCanOnlyBeSwitchedOn_text` and
  `..._quietModeCanOnlyBeActivated_text` — a guard exists around turning Quiet Mode on; likely
  "Quiet Mode can only be switched on [not off] via this control" or an activation
  precondition (e.g. cooler must already be running). UNVERIFIED exact condition — no runtime
  `enabled=` guard was found gating `x0()`/`k0()` in `vf/c.java` itself, so this is probably
  enforced purely in the Compose UI layer (not reachable from these two source files).
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
| `a2` | `df.a` | `(df.b, fz.i)` | OperationModeCombined = ordinal+1 (1-7); **also** sets OperationModeAirHeater(Mode) = `3` as a side effect | `rf/b.java:274-308` |
| `j4` | `df.a` | `(fz.i)` | OperationModeAirHeater(Mode) = `0` (via `f(this,0)`) | `rf/b.java:745-749, 154-162` |

Note the field-name overlap trap: the wire slot the app calls `TimerHour` for the heater is
physical slot `l0`, and `TimerMin` is `f23989m0` — **the opposite of the Cooler's slot↔name
pairing** for those two positions (see the shared table above). Do not reuse Cooler field
offsets for the Heater.

**No direct "turn Permanent Heating ON" write was found** in `rf/b.java` — `E3()` only ever
writes `PermanentOperationRequest = 0` (off). The only method that raises `OperationModeAirHeater`
above 0 is `a2()`, which sets it to `3` while also picking an `OperationModeCombined` device
combo. UNVERIFIED, but the string-resource keys (`airHeaterPage_permanentHeatingOnDialog_*`,
`..._permanentHeatingTimerDialog_permanentHeatingWillTurnOff_text`) suggest Permanent Heating is
entered either via the combined-device selection (`a2`) or is mutually exclusive with arming the
departure timer (`B0`) — i.e. **starting the timer turns permanent heating off**, per the dialog
text key. Building an own-vehicle "turn permanent heating on" command from these two files alone
is not fully verified; treat `a2()`'s effect (Mode=3 + OperationModeCombined) as the best lead.

No explicit "timer start" trigger bit (equivalent to Cooler's `TimerStart`) was found for the
heater — physical slot `f23983f0` (Cooler's `TimerStart`) is named `PermanentOperationConfirmation`
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
- **OperationModeAirHeater (Mode)**: raw int; confirmed app-driven values are `0` (idle/reset,
  via `j4`) and `3` (combined-operation active, via `a2`). Values `1`/`2` are referenced in the
  field's bit layout but no setter in this file writes them directly — likely firmware-reported
  states for "normal operation running" / "permanent operation running" that mirror
  `NormalOperationRequest`/`PermanentOperationRequest`. UNVERIFIED.
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

- **Turn on Immediate/Normal Heating for N minutes**: set `RunningTime = N` (via `D4`, only
  accepted if you also send within its own valid magnitude — no explicit upper bound found
  besides the natural 8-bit field size), then/also set `NormalOperationRequest = 1` (via
  `C2(true)`). `rf/b.java:198-218`.
- **Turn off Immediate Heating**: `NormalOperationRequest = 0` (via `C2(false)`).
  `rf/b.java:182-191`.
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
- **Enable combined operation (Truma/roof-AC equipped vehicles only)**: pick a `df.b` device
  combo and call `a2()`, which sets `OperationModeCombined` and forces
  `OperationModeAirHeater(Mode) = 3`. Only meaningful if your vehicle is factory-equipped with
  the corresponding devices (see Grand California gating note) — sending this on a plain
  California 6.1/7 is UNVERIFIED/likely meaningless or rejected by firmware.
  `rf/b.java:274-308`.
- **Reset heater mode to idle**: `OperationModeAirHeater(Mode) = 0` via `j4`.
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
- Whether "Permanent Heating ON" has a direct control-field write anywhere in the app, or is
  only reachable through `a2()`'s combined-device selection.
