# Business logic: RoofAirCondition, LivingRoomHeater, Campingmode, Stairs

Source: decompiled `CaliforniaOnTour` app (jadx output), tree root referred to below as `SRC`.
All line numbers cite files under `SRC` as read at analysis time.

## Shared plumbing (applies to all four)

Each BLE "feature" has three cooperating classes:

- **State model** (`qf.a` impl, e.g. `lg/b.java`, `gg/b.java`, `pg/b.java`) — declares the read
  characteristic UUID and the bit-widths of the incoming fields.
- **Control model** (`m2.a` impl, e.g. `lg/a.java`, `gg/a.java`, `pg/a.java`) — declares the write
  characteristic UUID, holds one `sg.a` (bit-packed int/bool) box per outgoing field, and has a
  `--> Sending Data: ...` logging method (named `A()` or `B()` depending on class) that lists the
  fields **in the order they're packed into the outgoing BLE write**.
- **Feature ViewModel** (e.g. `kg/b.java` for RoofAirCondition, `fg/b.java` for LivingRoomHeater,
  `tf/a.java` for Campingmode, `og/b.java` for Stairs) — implements a per-feature action interface
  (`jf.a`, `ff.a`, `xe.a`, `lf.a` respectively — these play the role the task brief calls "`af.a`").
  Every action method here does exactly:
  ```
  aVar.<field>.o(<value>);   // set ONE control-model field
  aVar.B(); / aVar.A();      // log + (re)build the full outgoing packet from ALL cached fields
  aVar.y(true);              // write the characteristic now, and arm a 500ms resend timer
  ```

**Critical interop fact:** the control model is a *stateful full-packet* writer, not a delta
writer. `y()` (`m2/a.java:152-170`) always serializes **every** field currently cached in the
control object (`f()` builds the whole bit array from all `sg.a` boxes), not just the one the
action just touched. The cached values are seeded once from the constructor defaults and are
never refreshed from the vehicle's own incoming state (that lands in a separate, independent
state-model instance). Practical consequence for a from-scratch implementation: **you must locally
cache the last value you sent for every field of a feature and resend all of them together
whenever you change one**, exactly mirroring the app's `sg.a` boxes, or you risk stomping sibling
settings back to their power-up defaults.

`sg.a(initValue, widthCode)` (`sg/a.java:138-161`) packs an int into a fixed-width bitfield;
the widths actually used below are: `widthCode 4` → 2 bits (0-3), `widthCode 6` → 4 bits (0-15),
`widthCode 7` → 8 bits (0-255). Several fields are deliberately initialized **outside** their
documented valid range (e.g. a 2-bit on/off field defaulting to `3`, a 4-bit enum defaulting to
`7`) — this is the protocol's "leave unchanged" sentinel, confirmed by the fact the app's own
setters never write those values.

---

## RoofAirCondition

Service `00002000-...`. State char `00002002-...` (`kg/b.java:192`, state model `lg/b.java`
constructed with `i=0`). Control char `00002001-...` (`lg/a.java:139-147`, control model built as
`new lg.a(bVar, this /*kg.b*/, aVar2)` from `kg/b.java:90`).

### 1. Action → field map

Control-model fields (`lg/a.java:139-147`, labelled by `B()` at `lg/a.java:49-60`):

| Field (obj) | Width | Init (sentinel) | Set by |
|---|---|---|---|
| `State` (`f17092e0`) | 2 bit | `3` (out of valid 0-1 range ⇒ sentinel) | `b3(boolean on)` |
| `FanSpeed` (`f17093f0`) | 4 bit | `7` (out of valid 0-4 range ⇒ sentinel) | `y3(jf.b fanSpeed)` |
| `Mode` (`f17094g0`) | 4 bit | `7` (out of valid 0-3 range ⇒ sentinel) | `L3(jf.c mode)` |
| `Temperature` (`f17095h0`) | 8 bit | `0` | `O(int raw)` |

Feature ViewModel `kg/b.java` (implements `jf.a`):

- `b3(boolean on, ...)` (`kg/b.java:166-175`) — **momentary/persisted power toggle.** Sets
  `State = on ? 1 : 0`, then `B(); y(true)`.
- `y3(jf.b fanSpeed, ...)` (`kg/b.java:348-375`) — **persisted setpoint.** `ordinal(fanSpeed)`
  (0-4, identity mapping, verified by the `if/else` ladder at `kg/b.java:350-367`) is written to
  `FanSpeed`.
- `L3(jf.c mode, ...)` (`kg/b.java:119-143`) — **persisted setpoint.** `ordinal(mode)` (0-3,
  identity mapping) written to `Mode`.
- `O(int raw, ...)` (`kg/b.java:145-154`) — **persisted setpoint.** Raw int passed straight
  through to `Temperature` with **no scaling/clamping visible at this layer** — the caller (UI
  layer, not reached in this pass) must already have produced the wire-format value.
  `UNVERIFIED`: exact unit/encoding of the raw temperature int (not found in the reachable
  Compose code; likely a direct °C value or a fixed offset/step encoding used by the AC unit).

Every one of these 4 setters ends with `aVar.B(); aVar.y(true);` — i.e. **all four fields are
re-sent together** every time any one of them changes (see "Shared plumbing" above).

### 2. Value semantics / enums

- `Mode` (`jf/c.java`): `0=AUTOMATIC, 1=MANUAL_COOLING, 2=MANUAL_HEATING, 3=VENTING`. Read-back
  decode confirms identity mapping and clamps any out-of-range value to `AUTOMATIC`
  (`kg/b.java:323-326`).
- `FanSpeed` (`jf/b.java`): `0=LEVEL_0, 1=LEVEL_1, 2=LEVEL_2, 3=LEVEL_3, 4=AUTO`. Read-back clamps
  out-of-range to `AUTO` (`kg/b.java:318-321`).
- `State`: `0=off, 1=on` (2-bit field; `2` and `3` are unused except `3` as the "unset" sentinel).
- `Temperature`: raw passthrough int, `UNVERIFIED` unit (8-bit field, so wire range 0-255).
- Read-back-only `Error` field (`lg/b.java` `i=0` variant, bits 0-1): `0=OK, 1=NoPowerSupply,
  2=ErrorAC` (confirmed by `kg/b.java:236-284` dispatching
  `ROOF_AIR_CONDITION_NO_POWER_SUPPLY_ID` / `ROOF_AIR_CONDITION_ERROR_AC_ID` notifications on
  those exact values). This is fault telemetry from the vehicle, not settable.

### 3. UI rules / constraints

No temperature-range clamp or fan-speed-availability gating was found reachable from this feature's
files (Compose UI that renders the slider is fully obfuscated/inlined and wasn't traced further).
The only conditional behavior found is fault-driven: `Error==1` and `Error==2` each surface a
one-shot device notification (`kg/b.java:236-314`) and are otherwise inert w.r.t. commands (i.e.
nothing stops you from still sending Mode/FanSpeed/Temperature changes while an error is present —
`UNVERIFIED` whether the vehicle itself rejects them).

### 4. Correct command recipe

To change **one** control (matching app behavior), send a full 24-bit packet with:
- the target field set to its new value,
- the other 3 fields set to **whatever you last sent for them** (or the sentinel defaults
  `State=3/FanSpeed=7/Mode=7/Temperature=0` if this is the first command of the session — mirrors
  `lg/a.java:139-147`).

Recipes:
- **Power on/off**: `State = 1|0`, keep last FanSpeed/Mode/Temperature.
- **Set fan speed**: `FanSpeed = 0..4` (`LEVEL_0..AUTO`), keep others.
- **Set mode**: `Mode = 0..3` (`AUTOMATIC/MANUAL_COOLING/MANUAL_HEATING/VENTING`), keep others.
- **Set temperature**: `Temperature = raw` (unit `UNVERIFIED`), keep others.

---

## LivingRoomHeater

Service `00002100-...` (Truma-style). State char `00002102-...` (`fg/b.java:234`, state model
`gg/b.java`). Control char `00002101-...` (`gg/a.java:31-40`, built as
`new gg.a(bVar, this /*fg.b*/, aVar2)` from `fg/b.java:105`).

### 1. Action → field map

Control-model fields (`gg/a.java:31-39`, labelled by `A()` at `gg/a.java:42-53`):

| Field (obj) | Width | Init (sentinel) | Set by |
|---|---|---|---|
| `StateAir` (`f9325e0`) | 2 bit | `3` (sentinel, valid 0-1) | `Z1(boolean on)` |
| `StateWater` (`f9326f0`) | 2 bit | `3` (sentinel, valid 0-1) | `p1(boolean on)` |
| `TemperatureWater` (`f9328h0`) | 2 bit | `3` (sentinel, valid range unclear) | `U2(boolean on)` |
| `Mode` (`f9327g0`) | 4 bit | `5` = `ff.b.INIT` (named sentinel) | `W0(ff.b mode)` |
| `TemperatureAir` (`f9329i0`) | 8 bit | `0` | `u2(int raw)` |

Feature ViewModel `fg/b.java` (implements `ff.a`):

- `Z1(boolean on, ...)` (`fg/b.java:185-194`) — **power toggle, air/space heating.**
  `StateAir = on?1:0`, `A(); y(true)`.
- `p1(boolean on, ...)` (`fg/b.java:512-521`) — **power toggle, water heating.**
  `StateWater = on?1:0`, `A(); y(true)`.
- `U2(boolean on, ...)` (`fg/b.java:157-166`) — sets `TemperatureWater = on?1:0`. Despite the field
  name and its 2-bit width (which could hold 4 states), **the only wired setter sends just 0 or
  1** — `UNVERIFIED` whether values `2`/`3` have meaning; the app's hot-water widget has UI
  strings for `eco` / `hotWater` / `max` presets (`ea/w.java`:
  `livingRoomHeaterPage_hotWaterWidget_eco_text`, `..._hotWater_text`, `..._max_text`) but no
  reachable code path in this class writes anything other than 0/1 to this field.
- `W0(ff.b mode, ...)` (`fg/b.java:173-183`) — **persisted setpoint, energy/power source.**
  `Mode = ordinal(mode)` (0-4; `5`/`INIT` is a sentinel, not a real user-selectable mode).
- `u2(int raw, ...)` (`fg/b.java:528-537`) — **persisted setpoint, air temperature.** Raw
  passthrough, same caveat as RoofAirCondition's `Temperature`. `UNVERIFIED` unit.

All setters end `A(); y(true)` — same full-packet-resend behavior as RoofAirCondition.

### 2. Value semantics / enums

- `Mode` / power source (`ff/b.java`): `0=DIESEL_GAS, 1=MIX_WITH_SIX_AMPERE,
  2=MIX_WITH_TEN_AMPERE, 3=ELECTRIC_WITH_SIX_AMPERE, 4=ELECTRIC_WITH_TEN_AMPERE, 5=INIT`
  (`5` should never be sent by a well-behaved client — it's the "not yet chosen" default).
  Matches UI preset strings `livingRoomHeaterPage_livingRoomHeatingWidget_{diesel,gas,mix6A,
  mix10A,6A,10A}_text` in `ea/w.java`.
- `StateAir` / `StateWater`: `0=off, 1=on`.
- `TemperatureWater`: only `0`/`1` observed as sent; semantics beyond on/off `UNVERIFIED`.
- `TemperatureAir`: raw passthrough int, unit `UNVERIFIED` (8-bit, wire range 0-255).
- Read-only `Variant` field (`ff/c.java`, via `vc.a.s(...)`): `DIESEL_ELECTRIC` / `GAS_ELECTRIC` —
  describes which fuel hardware is actually installed in the vehicle; not settable. Likely gates
  which `Mode` values make sense to offer (a diesel-only variant shouldn't be sent
  `ELECTRIC_WITH_*`), but the exact UI gating logic wasn't traced (`UNVERIFIED`).
- Read-only `Error` field drives four distinct fault notifications (`fg/b.java:299-459`):
  `1=heater error, 2=gas empty, 3=fuel empty, 4=window open` (`LIVING_ROOM_HEATER_ERROR_HEATER_ID`
  / `_GAS_EMPTY_ID` / `_FUEL_EMPTY_ID` / `_WINDOW_OPEN_ID`).

### 3. UI rules / constraints

`ea/w.java` / `x9/i1.java` define an "energy source unavailable" dialog
(`livingRoomHeaterPage_energySourceUnavailableDialog_energySourceUnavailable_text`,
`..._externalPowerSourceIsNeeded_text`) implying the app blocks selecting electric-powered `Mode`
values when no external power is connected — exact trigger condition not traced (`UNVERIFIED`).
Fault fields (`window open`, `gas/fuel empty`, `heater error`) surface warning banners but, as with
RoofAirCondition, nothing in this file stops further commands from being sent while a fault is
active.

### 4. Correct command recipe

Full-packet resend rule applies (5 fields). Recipes:
- **Toggle air heating**: `StateAir = 1|0`, keep others.
- **Toggle water heating**: `StateWater = 1|0`, keep others.
- **Toggle hot water (only 0/1 supported)**: `TemperatureWater = 1|0`, keep others.
- **Set power source**: `Mode = 0..4` (never send `5`/`INIT`), keep others.
- **Set air temperature**: `TemperatureAir = raw` (unit `UNVERIFIED`), keep others.

---

## Campingmode

Service `00001200-...`. Control model and state model are the **same shared classes** used by
RoofAirCondition (`lg.a` / `lg.b`), differentiated only by constructor path — confirmed by two
separate constructors/log methods in each (`lg/a.java` has `A()` for Campingmode vs `B()` for
RoofAirCondition; `lg/b.java` has a `switch(i)` with a `1200`-style branch vs the RoofAC branch).
Campingmode's field names (`State, UsbCharger, OutsideLight, InteriorLight`) are declared by the
`i=2` variant of `lg/b.java` and match the `A()` labels in `lg/a.java:41-46`.

Control char `00001201-...`, state char `00001202-...` (`tf/a.java:78`, `tf/a.java:159`; control
model built as `new lg.a(bVar, this /*tf.a*/, aVar2)` from `tf/a.java:74`, using the
`(jn.b, tf.a, xm.a)` constructor of `lg/a.java:26-34`, all four fields `sg.a(3, 4)` — 2-bit,
sentinel `3`).

### 1. Action → field map

| Field (obj) | Width | Init (sentinel) | Set by |
|---|---|---|---|
| `State` (`f17092e0`) | 2 bit | `3` | `z2(boolean on)` |
| `UsbCharger` (`f17093f0`) | 2 bit | `3` | `B2(boolean on)` |
| `OutsideLight` (`f17094g0`) | 2 bit | `3` | `K0(boolean on)` — combined, see below |
| `InteriorLight` (`f17095h0`) | 2 bit | `3` | `K0(boolean on)` — combined, see below |

Feature ViewModel `tf/a.java` (implements `xe.a`):

- `z2(boolean on, ...)` (`tf/a.java:211-220`) — **overall Campingmode on/off.** `State = on?1:0`,
  `A(); y(true)`.
- `B2(boolean on, ...)` (`tf/a.java:94-103`) — **USB charger toggle.** `UsbCharger = on?1:0`,
  `A(); y(true)`.
- `K0(boolean on, ...)` (`tf/a.java:110-121`) — **both lights, single control, no independent
  outside/interior switches exist in this interface.** Sets `OutsideLight = (!on)?1:0` **and**
  `InteriorLight = (!on)?1:0` to the same value in one call, then `A(); y(true)`. Note the
  inversion: `on=true` writes `0` to both light fields, `on=false` writes `1`.
  `UNVERIFIED` which raw value (`0` or `1`) the vehicle actually interprets as "lit" — presented
  exactly as coded so the polarity can be tested empirically.
- `q0(boolean, ...)` (`tf/a.java:207-209`) — **dead stub.** Returns immediately, touches no field.
  The read-only state struct exposes an `Enable` bit (see below) that this method apparently used
  to set in an earlier version but no longer does in this build.

All real setters end `A(); y(true)` — same full-packet-resend rule (4 fields).

### 2. Value semantics / enums

- `State`, `UsbCharger`, `OutsideLight`, `InteriorLight`: all 2-bit, `0`/`1` are the only values
  ever written by this class; `3` is the "unset" sentinel (consistent with every other 2-bit field
  in this app).
- Read-back-only extra fields not present on the control side: `Enable` (bool, not wired to any
  setter — see `q0` above) and `Installed` (bool, hardware-presence flag).

### 3. UI rules / constraints

`tf/a.java:185-188` derives a `m2()` flag = `true` iff both `OutsideLight` and `InteriorLight`
read-back state are currently `false` — almost certainly drives a single "lights" switch shown in
the UI (on when both are off, i.e. the switch represents "turn all lights on"), consistent with
`K0` only ever toggling both together. No separate per-light control is exposed anywhere in this
interface.

### 4. Correct command recipe

Full-packet resend rule applies (4 fields, all 2-bit, sentinel `3`). Recipes:
- **Toggle Campingmode on/off**: `State = 1|0`, keep others.
- **Toggle USB charger**: `UsbCharger = 1|0`, keep others.
- **Toggle all lights**: `OutsideLight = InteriorLight = (!on)?1:0` (both fields set together, see
  polarity caveat above), keep `State`/`UsbCharger`.
- There is **no** app-level command to control outside/interior lights independently, and no
  working command for whatever `Enable` originally meant.

---

## Stairs

Service `00001800-...` (electric step). Control model / state model shared package `pg.a` / `pg.b`
is also reused by a second, unrelated feature (`00001600-...`, via `xf.a`) — the Stairs-specific
constructor is the `(jn.b, og.b, xm.a)` overload of `pg/a.java:20-26`. State char `00001802-...`
(`og/b.java:162`, `pg/b.java`). Control char `00001801-...` (`pg/a.java:20-26`, built as
`new pg.a(bVar, this /*og.b*/, aVar2)` from `og/b.java:85`).

### 1. Action → field map

Control-model fields (`pg/a.java:20-26`, labelled by `A()` at `pg/a.java:28-30`):

| Field (obj) | Width | Init | Set by |
|---|---|---|---|
| `OperationMode` (`f21227e0`) | 2 bit | `3` (sentinel, valid 0-1) | `N0(boolean auto)` |
| `Movement` (`f21228f0`) | 2 bit | `0` | `h1(boolean extend)` |

Feature ViewModel `og/b.java` (implements `lf.a`):

- `N0(boolean auto, ...)` (`og/b.java:121-131`) — **mode toggle.** `OperationMode = (!auto)?1:0`
  i.e. `auto=true → 0`, `auto=false → 1`. By analogy with every other enum in this app where `0`
  is the "automatic"/default member (`jf.c.AUTOMATIC=0`, etc.), `0` is most likely `AUTOMATIC` and
  `1` is `MANUAL`, but no dedicated enum class was found for this field, so this mapping is
  `UNVERIFIED`.
- `h1(boolean extend, ...)` (`og/b.java:288-297`) — **momentary movement command.**
  `Movement = extend ? 2 : 1`. Only `1` and `2` are ever sent by the app; `0` (the field's power-up
  default) is never explicitly written by a user action, so **there is no explicit "stop" command
  in this interface** — a real electric-step controller either auto-stops at the end of travel, or
  stopping is done by re-issuing the opposite `Movement` value, or is out of scope of this
  characteristic. Which of `1`/`2` is "extend/down" vs "retract/up" is `UNVERIFIED` (presented
  exactly as coded).

Both setters end `A(); y(true)` — full-packet resend of both fields.

### 2. Value semantics / enums

- `OperationMode`: `0`/`1` only (2-bit field, `2`/`3` unused, `3` doubles as the "unset" sentinel).
  Semantic labels (`AUTOMATIC`/`MANUAL` or similar) `UNVERIFIED` — no `lf`-package enum class
  exists for it (unlike RoofAirCondition/LivingRoomHeater, which have `jf.c`/`ff.b`).
- `Movement`: `1` and `2` are the two drive directions; direction-to-value mapping `UNVERIFIED`.
- Read-back-only `InfoPopUp` field (`og/b.java:187-201`, 4-bit but only 3 values meaningful):
  `0=none, 1=STAIRS_STEPS_OR_SENSOR_JAMMED, 2=STAIRS_CONTROL_DENIED`. Both trigger user-facing
  warning notifications/dialogs; this is vehicle-reported fault/refusal state, not settable.
- Read-back-only `Installed` and `Sensor` booleans (UI display only), and read-back `State`
  (boolean, likely "currently extended" — not confirmed).

### 3. UI rules / constraints

`og/b.java:100-108` derives a `C3()` flag = `true` iff read-back `InfoPopUp == 1 || InfoPopUp == 2`
— almost certainly used to show a warning banner and/or disable the movement control in the UI
while the vehicle has refused control or reports a jam. `og/b.java:101` also derives `f3()` =
`!OperationMode(readback)`, plausibly gating whether the manual movement buttons (`h1`) are shown
enabled (i.e., manual drive only available when the readback operation-mode indicates "manual", as
opposed to "automatic"). Neither gating condition is enforced by the write path itself in this
file — `UNVERIFIED` whether the vehicle ECU silently ignores `h1` while `OperationMode` readback is
"automatic" or while `InfoPopUp` is non-zero; the app-level notifications (`STAIRS_CONTROL_DENIED`)
suggest the vehicle can and does reject movement commands out-of-band.

### 4. Correct command recipe

Full-packet resend rule applies (2 fields). Recipes:
- **Switch automatic/manual mode**: `OperationMode = (!auto)?1:0`, keep `Movement`.
- **Drive the step**: `Movement = extend?2:1`, keep `OperationMode`. No explicit stop value is
  sent by the app; assume the vehicle handles end-of-travel stop internally, and treat repeated/
  opposite `Movement` writes as the only way to interrupt a move from this protocol layer.
- Before driving, check the last known `InfoPopUp`/`OperationMode` readback state for your own
  vehicle — if it mirrors the CaliforniaOnTour app's `C3()`/`f3()` gating, an automatic-mode or
  jammed/denied state may cause the vehicle to ignore `h1` regardless of what you send.
