# CaliforniaOnTour BLE Business Logic: Lighting, Energy, Water, SatelliteAntenna, Roof

Reverse-engineered from decompiled `com.californiaontour` (JADX output). All BLE writes go through a shared
"control model" pattern: a class extends `m2.a` and holds one `sg.a` wrapper per protocol field. `sg.a.o(value)`
*stages* a value (no I/O). A method named `A()` or `B()` builds a `"--> Sending Data: ..."` debug-log line by
reading every field's current staged value, and `y(boolean)`/`x(...)` on the base class performs the actual BLE
characteristic write of the **entire struct** (all fields, not just the one just changed). This means every
"action" in the sections below is really: *stage field(s) → log → transmit whole struct*.

Field bit-widths are fixed by `sg.a(defaultValue, kindIndex)` (see `sg/a.java`): kindIndex 6 = 4 bits, 7 = 8 bits,
3 = 16 bits, 4 = 2 bits, 5 (no-arg ctor) = 32 bits, 0 = 1 bit (bare boolean).

The `qf.b` enum (`qf/b.java:11-20`) controls whether a setter transmits immediately: `DIRECT`(0) sends now,
`PENDING`(1) only stages (used to batch several field writes before one send), `NO_INIT`(2) sends but is treated
as a config/request write rather than a user command.

---

## Lighting (service `0x1500`, char `0x1501`)

Control model: `eg/a.java` (UUID `00001501-...`, `eg/a.java:72`). State/dispatch logic: `dg/a.java` (field
setters) + `dg/h.java` (feature class, service `00001500-...`, `dg/a.java:73`).

### Fields (control struct, `eg/a.java:69-92`)

| Field | Java member | Bits | Default | Meaning |
|---|---|---|---|---|
| ProfileNumber | `f7182d0` | 4 | 14 | selects a stored profile slot (see `dg.l`/`ef.k` below); 14 = `INIT` sentinel |
| Mode | `f7183e0` | 8 | 0 | command opcode, see `dg/n.java` enum |
| Timestamp | `f7184f0` | 32 | 0 | epoch seconds, only meaningful for `WAKEUP_TIME` |
| LightValue | `f7185g0` | 16 | 0 | opcode-dependent payload (bit-packed) |
| BrightnessLOne … BrightnessLOneSix | `f7186h0` … `f7200w0` | 4 each | 14 | per-zone brightness, 16 zones total |

Log line format confirms names/order: `eg/a.java:116-129` (`A()`).

### Command enum — `dg/n.java:20-31`

| Name | Wire value |
|---|---|
| NO_MODE | 0 |
| SET_BRIGHTNESS | 4 |
| SET_COLOR | 6 |
| SET_DOUBLE | 8 |
| REQUEST_CONFIG | 12 |
| SET_PROFILE | 16 |
| WAKEUP_TIME | 20 |
| SYSTEM_TIME | 24 |
| PREVIEW | 28 |

`SET_DOUBLE` and `SYSTEM_TIME` are not in the task's expected list but exist in the enum; `SET_DOUBLE` is used
(see `P0` below), no call site for `SYSTEM_TIME` was found (`UNVERIFIED` — likely used only during BLE pairing/clock
sync, not in the reviewed classes).

### Brightness value enum — `dg/i.java:20-43`

Each `BrightnessLxx` field (and `LightValue` when packed, see Wake-up) carries a `dg.i` wire value, not a raw
percentage:

| Wire value | Name | Meaning |
|---|---|---|
| 0 | OFF | light off |
| 1–10 | PERCENTAGE_10 … PERCENTAGE_100 | 10%-step brightness |
| 11 | DEFAULT | device default brightness |
| 12 | *(unused)* | — |
| 13 | NOT_EQUIPPED | zone not present on this vehicle |
| 14 | *(sentinel, out of enum range)* | "unset / not part of this command" — matches the field's default |

So brightness is effectively a 0–10 step scale (0%,10%,...,100%) plus two special codes, packed in a 4-bit field.

**Live-confirmed 2026-08-16** (HCI capture of the app + photon-verified actuation): the wire value
really is this `dg.i` enum, not a raw 0-13 scale — the app's 50 % slider produced wire `5`, and
`13` is a **read-only NOT_EQUIPPED marker** for absent zones (calictl previously wrote 13 as
"on"/max — garbage; now `LIGHT_ON_BRIGHTNESS=10`, settable range 0-11, GUI slider max 10).

### Zone → wire-field map (confirmed via `dg/a.java` setters `f,g,h,i,j,k,l,m,n,o,p,q,r`, each `(int, qf.b)`)

| Setter (`dg/a.java`) | Control field | Zone name (BrightnessL…) |
|---|---|---|
| `j` (:203) | `f7186h0` | One |
| `r` (:291) | `f7187i0` | Two |
| `q` | `f7188j0` | Three |
| `h` | `f7189k0` | Four |
| `g` | `l0`      | Five |
| `p` | `f7190m0` | Six |
| `o` | `f7191n0` | Seven |
| `f` | `f7192o0` | Eight |
| `i` | `f7193p0` | Nine |
| `n` | `f7194q0` | OneZero (10) |
| `k` | `f7195r0` | OneOne (11) |
| `m` | `f7196s0` | OneTwo (12) |
| `l` | `f7197t0` | OneThree (13) |
| `s` (:302) | `f7185g0` (LightValue) | n/a |
| `v` (:313) | `f7183e0` (Mode) | n/a |
| `w` (:324) | `f7182d0` (ProfileNumber) | n/a |

**No setter was found for zones 14–16** (`BrightnessLOneFour/Five/Six`, fields `f7198u0/f7199v0/f7200w0`) —
`UNVERIFIED`, likely reserved for a vehicle variant not exercised by the reviewed UI code path (`dg/h.java`'s
zone dispatcher only covers 13 physical positions).

Named light positions (`ef/i.java:27-114`, 29 values total; UI dispatch in `dg/h.java:174` `E()` and
`:689-717` `p0()`, `switch(iVar.ordinal())`) map ordinals onto the 13 wired zones above — the switch has
duplicate `case` labels (e.g. `0` and `11` both drive zone One) because two different vehicle-generation light
layouts (e.g. reading-light-only layouts vs. roof/kitchen-equipped ones) reuse the same physical BLE channel:

- zone One ← `REAR_READING_LEFT`(0) or `READING_RIGHT`(11)
- zone Two ← `REAR_READING_RIGHT`(1) or `READING_LEFT`(12)
- zone Three ← `EXTERIOR_LOAD_COMPARTMENT`(2) or `EXTERIOR_REAR_SURROUNDINGS`(13)
- zone Four ← `REAR_READING_FRONT_AREA`(3) or `READING_FRONT_PASSENGER`(14)
- zone Five ← `KITCHEN_BACKGROUND_LIGHTING`(15)
- zone Six ← `KITCHEN_CABINETS`(16)
- zone Seven ← `INTERIOR_KITCHEN`(4) or `KITCHEN_COOKING`(17)
- zone Eight ← `INTERIOR_BACKGROUND_LIGHTING`(5) or `ROOF_BACKGROUND_LIGHTING`(18)
- zone Nine ← `FRONT_READING_ALCOVE`(6) or `ROOF_READING`(19)
- zone Ten ← `INTERIOR_LIVING_AREA`(7)
- zone Eleven ← `INTERIOR_DINING_AREA`(8)
- zone Twelve ← `EXTERIOR_ENTRANCE`(10)
- zone Thirteen ← `INTERIOR_BATHROOM`(9)

**Zone→lamp map, capture-confirmed 2026-08-16 (this van, T7):** dragging the app's *Küche
Ambientelicht* slider moved the **L5** nibble — `Küche Ambientelicht = BrightnessLFive` is now
CONFIRMED (was only inferred from the `KITCHEN_BACKGROUND_LIGHTING`(15) ordinal above); *Küche
Kochen = L7* re-confirmed in the same capture; **L6 = Aufstelldach Leselicht** is now solid by
elimination.

### Action → field map

1. **Set one zone's brightness (`E(ef.i zone, dg.i level, boolean stage)`, `dg/h.java:174`)**
   - `w(9, PENDING)` → ProfileNumber = 9 (staged only, not sent)
   - `v(4, PENDING)` → Mode = `SET_BRIGHTNESS`(4) (staged only)
   - dispatch on `zone.ordinal()` → call the matching setter above with `(level.wireValue, DIRECT-or-PENDING)`.
     If `stage==false` the call uses `DIRECT`, which sends the *whole* struct (ProfileNumber=9, Mode=4,
     Timestamp=stale, LightValue=stale, all Brightness fields at last-known value except the one just changed).
     If `stage==true` (`PENDING`) nothing is transmitted yet — used for live slider drag preview.
   - **Recipe: "set light zone N brightness = X%"** → `E(zone_N, dg.i.PERCENTAGE_X0, false)`. Wire effect:
     Mode=4 (SET_BRIGHTNESS), ProfileNumber=9, `BrightnessL<N>` = wire code for X% (1–10), sent immediately.

2. **Live-preview a zone's brightness without transmitting (`p0(ef.i zone, dg.i level)`, `dg/h.java:689-717`)**
   Same dispatch table, always called with `PENDING` — stages the value locally only; nothing goes over BLE
   until a later `E(...)` call commits it.

3. **`P0(int i, ...)` (`dg/h.java:315-321`)**: Mode = `SET_DOUBLE`(8), then `LightValue = i` sent `DIRECT`.
   Exact meaning of `i` `UNVERIFIED` (likely a 2-way quick toggle for a paired light, e.g. both reading lights).

4. **`Q(boolean allOn, ...)` (`dg/h.java:323-337`)**: Mode = `SET_PROFILE`(16) staged, then
   `ProfileNumber = allOn ? 12 : 0` sent `DIRECT`. ProfileNumber 12 = `LIGHTS_ON` and 0 = `LIGHTS_OFF` per the
   `dg.l`/`ef.k` profile enum (below) — i.e. a master "all lights on/off" toggle implemented as a profile select.

5. **`Q3()` (`dg/h.java:339-354`)**: Mode = `PREVIEW`(28) staged, `ProfileNumber = 10` (`WAKEUP_LIGHT`) and
   `LightValue = 1` sent `DIRECT`; schedules a 5000 ms one-shot (`jn.a`) after which local UI state reverts.
   This is the "preview a light configuration for 5 seconds" action.

6. **Request current config (`d0()`, `dg/h.java:465-522`)**: `ProfileNumber = 13` staged (`DEFAULT`... actually
   13 = `DEFAULT` per enum below), then Mode = `REQUEST_CONFIG`(12) sent with `NO_INIT` (still transmits — only
   `PENDING` suppresses sending), and awaits the device's async response via a coroutine helper `A(...)`.

7. **Activate a stored profile (`u0(ef.k profile, ...)`, `dg/h.java:751-829`)**: Mode = `SET_PROFILE`(16) staged;
   `dg.a.x(this, dg.l-value-for(profile))` (`dg/h.java:798`, exact wiring of per-zone brightness `UNVERIFIED` —
   not fully decompiled) then transmits via the `A(SET_PROFILE, ...)` awaiting-response path.

8. **`l3(ef.k profile, ...)` (`dg/h.java:572-578`)**: same signature shape as `u0` but body not decompiled
   (`UNVERIFIED`) — likely "save current zone brightness state into profile slot" (only meaningful for the
   `FAVORITE_1..7` user slots).

9. **Toggle profile 8 (`n4(boolean on)`, `dg/h.java:679-688`)**: Mode=`SET_PROFILE`(16) staged, `ProfileNumber=8`
   (`DOOR_CONTACT`) staged, `LightValue = on?1:0` sent `DIRECT` — enables/disables the "door-contact" auto-light
   profile.

10. **Set wake-up light (`m0(ef.m area, ef.j wakeupConfig, ...)`, `dg/h.java:581-677`)**: computes the next
    epoch timestamp ≥ now matching the requested hour/minute (`i5`=hour, `i`=minute), packs `LightValue` (16 bits)
    from: 4-bit `dg.k` wake mode + 4-bit color (`dg.j`, via `u.i(jVar)`) + 4 area-membership bits
    (`dg.m.AREA_1..4`) — sent via `s(..., PENDING)` then finalized when `Timestamp` is set and
    `aVar.y(true)` fires; Mode = `WAKEUP_TIME`(20). Recipe: pick a wake time, a `dg.k` mode
    (`WAKE_UP_ON`/`WAKE_UP_ON_10/20/30` = on with N-minute gentle-wake ramp, or the `_OFF_*` variants to
    disable), a `dg.j` color preset, and which of the 4 `AREA_n` zone-groups light up.

### Profile-number enum (`dg/l.java` mirrors `ef/k.java`)

| Wire value | Name |
|---|---|
| 0 | LIGHTS_OFF |
| 1–7 | FAVORITE1 … FAVORITE7 (user-savable presets) |
| 8 | DOOR_CONTACT |
| 9 | LIVE_VIEW |
| 10 | WAKEUP_LIGHT |
| 11 | INTERIOR_LIGHT |
| 12 | LIGHTS_ON |
| 13 | DEFAULT |
| 14 | INIT (matches the field's power-on sentinel default) |

### Color enum (`dg/j.java`, wire values 1–10)

`WARM_WHITE(1), BLOOD_ORANGE(2), AMBER(3), PISTACHIO(4), PEPPERMINT(5), MINT(6), AZURE(7), DARK_BLUE(8),
RED(9), SALMON(10)` — matches `control.LIGHT_COLORS` byte-for-byte. **CORRECTED 2026-08-17:** there IS a direct
`SET_COLOR`(Mode 6) call site — `dg/h.java:644` (`n.SET_COLOR` + `v(6, …)`), a **profile-recolour** method:
`Mode=6`, `LightValue`=colour index, `ProfileNumber`=the *target* profile, and the zones carry that profile's
brightness (`w10.d.b(kVar2)`), transmitted via the await-response path `A(SET_COLOR, …)`, gated so two profiles
(likely LIGHTS_OFF/ON) can't be coloured. So SET_COLOR is a real capability, not N/A — but it recolours a
stored profile. `control._lighting`'s `color` builds `ProfileNumber=9` + sentinel zones, which is NOT what the
app sends (target profile + its brightness), so our `set lighting color` frame is mis-shaped and likely won't
actuate as built. The other `dg.j` use is the wake-up-light `LightValue` packing (`dg/h.java:656`).

### Wake mode enum (`dg/k.java:39-53`)

`WAKE_UP_OFF`(0), `WAKE_UP_ON`(1), `WAKE_UP_OFF_10`(2), `WAKE_UP_ON_10`(3), `WAKE_UP_OFF_20`(4),
`WAKE_UP_ON_20`(5), `WAKE_UP_OFF_30`(6), `WAKE_UP_ON_30`(7) — the `_10/_20/_30` variants add a gentle
pre-wake ramp of that many minutes.

### VW "EXLAP" light-profile keys (local app storage, not BLE) — `xp/g.java:179-198`, method `a(String suffix, wp.i)`

Per named profile suffix ("One".."Seven", `xp/g.java:280`), the app locally persists (SharedPreferences, prefix
`EXLAP_LIGHT_PROFILE_`): `_MODIFIED`, `_AMBIENT_BRIGHTNESS`/`_AMBIENT_ENABLED`,
`_READING_RIGHT_BRIGHTNESS`/`_ENABLED`, `_READING_LEFT_BRIGHTNESS`/`_ENABLED`, `_READING_REAR_BRIGHTNESS`/`_ENABLED`,
`_SPOT_RIGHT_BRIGHTNESS`/`_ENABLED`, `_SPOT_LEFT_BRIGHTNESS`/`_ENABLED`, `_KITCHEN_BRIGHTNESS`/`_ENABLED`,
`_D_PILLAR_BRIGHTNESS`/`_ENABLED`. This is a client-side cache of profile contents (AMBIENT/READING_LEFT/RIGHT/
REAR/SPOT_LEFT/RIGHT/KITCHEN/D_PILLAR), used for the app's own profile editor UI, separate from the live BLE
zone names above (`READING_LEFT`/`READING_RIGHT` line up with `ef.i` ordinals 11/12).

Page-seen / onboarding key: `INFO_PAGE_LIGHTING_WAS_SEEN` (`xp/g.java:146`).

---

## Energy (service `0x1600`)

Feature/telemetry class: `xf/a.java` (UUID `00001600-...`, `xf/a.java:123`; 36-field struct populated in
`e(String,Boolean[])`, `xf/a.java:173-309`, log `"<-- Incoming Data for Energy"` at `:286`). Control model
`pg/a.java` is **shared** between service `0x1601` (Energy — second constructor, `pg/a.java:81-87`) and service
`0x1801` (Stairs — first constructor, `pg/a.java:20-26`); both variants have the same 2-field shape but different
names in the `A()` log (`pg/a.java:28-30`): `OperationMode` (2-bit, default 3) and `Movement`/`DisplayRefresh`
(2-bit/bool, default 0).

### Telemetry (read-only) — 36 fields, log format `xf/a.java:286-306`

`TwoBattNotCharged, TwoBattSwitchAtCharging, TwoBattSwitchAtWorkshop, WarningLevelActive, DcdcDefect,
EnergyModeNotSelectable, LandDefect, LandNotAvailable, PvDefect, SleepWarning, CurrentDeratingTemperature,
SystemError, EmpInstalled, PvInstalled, LadInstalled, DcdcInstalled, WarningLevelTwo, EnergyMode,
SocOneBattAfs, SocTwoBattAfs, StateDcdcAfs, StateLandAfs, StatePvAfs, AgeOneBattValuesMinutes, IOneBattBemAfs,
PDcdcAfs, PLandAfs, PPvAfs, tTwoBattRemainingh, tTwoBattRemainingmin, UOneBattBemAfs, UTwoBattBemAfs,
ITwoBattBemAfs, IDcdcAfs, ILandAfs, IPvAfs`.

("Land" = shore/landline power, "Pv" = photovoltaic/solar, "Dcdc" = DC-DC converter, "Afs" = raw AFS-scaled
value, "Two Batt" = second/auxiliary battery, "Emp"/"Lad" = installed-equipment flags.)

Post-processing (`xf/a.java:328-390`, `xf/d.java`) derives UI values from the raw fields:
- SOC (state of charge) percentages: raw value × 10 → percent (`xf/a.java:333-337`, fields A0/B0 → ×10).
- Voltage fields (`U*BemAfs`): raw ÷ 10 → volts (`:351-362`).
- Power fields (`P*Afs`): raw × 10 → watts (`:363-374`).
- Battery/DC-DC/Land/PV state fields (`StateDcdcAfs` etc.) decode through `d.k(int)` (`xf/d.java:203-217`)
  by **wire value**: raw `0`→`INACTIVE`, `1`→`ACTIVE`, `2`→`STANDBY`, `6`→`INIT`, and **every other raw
  value** (incl. `3`/`4`/`5`/`7`)→`ERROR`. The `INACTIVE`(0)/`ACTIVE`(1)/`STANDBY`(2)/`INIT`(3)/`ERROR`(4)
  numbers in `bf/a.java` are the enum's Java **ordinals**, not the raw field values `k()` switches on (on the
  wire `INIT` is `6`, and raw `3`/`4` decode to `ERROR`).
- `S()`/`T()`/`I3()`/`p4()` (`xf/d.java:247-620`) are **local-only** "acknowledge warning" toggles (dismiss a
  banner in the UI); they do not transmit over BLE.

### Controllable: Energy/charging mode — `xf/d.java:362-380`, `d4(bf.c mode, ...)`

```
int i = mode.ordinal() (clamped: NORMAL_MODE→0, MAX_CHARGE→1, ECO_MODE→2, ERROR not selectable)
pg.a.f21227e0.o(i)      // "EnergyModeSet" field (shared struct's OperationMode slot)
send DIRECT
```

`bf/c.java:16-25` enum: `NORMAL_MODE`(0), `MAX_CHARGE`(1), `ECO_MODE`(2), `ERROR`(3, read-only state, not
user-selectable). **Recipe: "set energy mode = ECO"** → write `EnergyModeSet = 2` on char `0x1601`
(`DisplayRefresh` field left at its last value, default 0). This is the only confirmed write-capable Energy
control; everything else in the 36-field struct is telemetry.

---

## Water (service `0x1300`) — READ-ONLY

Feature class `qg/b.java` (UUID `00001300-...`, `qg/b.java:106`; struct populated in `e(String,Boolean[])`,
`qg/b.java:189-230`, log `"<-- Incoming Data for Water"` at `:222`). **No control model / setter methods exist**
for this service — confirmed no `sg.a.o(...)` + `.y(true)` send pattern anywhere in `qg/b.java`.

### Fields (`rg.a` struct, bit ranges from `qg/b.java:207-215`)

| Field | Bits | Meaning |
|---|---|---|
| FreshWaterUnit | 1 | display-unit flag: `false`→`cf.c.PERCENT`, `true`→`cf.c.ABSOLUTE` (liters) |
| Installed | 1 | fresh-water sensor installed |
| FreshWaterInfoPopUp | 4 | `cf.a` enum: `EMPTY`(0), `ONE_THIRD_FULL`(1), `TWO_THIRD_FULL`(2), `FULL`(3), `OTHER`(4) |
| FreshWaterLevel | 8 | **primary value**: if unit=ABSOLUTE → current liters; if unit=PERCENT → current 0–100% |
| FreshWaterVolume | 8 | **tank capacity** (liters), used as the percent denominator (⚠️ name is misleading — this is NOT the current volume) |
| WasteWaterUnit | 1 | display-unit flag: `false`→`nf.b.PERCENT`, `true`→`nf.b.ABSOLUTE` (`nf.b` enum is `ONLY_EMPTY_FULL`/`ABSOLUTE`/`PERCENT`; only the latter two are selected by this 1-bit field) |
| WasteWaterInfoPopUp | 2 | discrete waste-tank status code |
| WasteWaterLevel | 8 | primary value (current liters or %), mirrors FreshWaterLevel |
| WasteWaterVolume | 8 | tank capacity (liters), mirrors FreshWaterVolume |

### Unit conversion helpers (`qg/b.java` `h()/i()` 414–429, `j()/k()` 441–466) — VERIFIED LIVE 2026-07-05

⚠️ **The field NAMES are backwards from their meaning.** `FreshWaterLevel` is the current
reading (liters in ABSOLUTE mode); `FreshWaterVolume` is the tank *capacity*. Confirmed by the
getters (`c.f3752y` = ABSOLUTE = enum ordinal 1; `c.X` = PERCENT = 2) **and** by a live read.

```
h()  // displayed fresh-water liters
  = unit==ABSOLUTE ? FreshWaterLevel : FreshWaterVolume * FreshWaterLevel / 100

i()  // displayed fresh-water percentage
  = unit==ABSOLUTE ? FreshWaterLevel * 100 / FreshWaterVolume : FreshWaterLevel   // Level is already % in PERCENT mode

j()/k()  // same pair for waste water, using WasteWaterUnit / WasteWaterLevel / WasteWaterVolume
```

A consumer on another vehicle must read the Unit bit first, then treat **Level = current value,
Volume = capacity**. Do not infer roles from the field names.

**Live check (owner's vehicle, char `1302` = `03 0b 1d 01 00 16`, both tanks ABSOLUTE):**
fresh `Level=0x0b=11 L` of `Volume=0x1d=29 L` capacity → **37%**; waste `Level=0 L` of `Volume=22 L` → empty.

`T3()`/`nf.a` interface method (`qg/b.java:161-163`) is a **local-only** "acknowledge warning" toggle, not a BLE
write — consistent with Water being fully read-only.

---

## SatelliteAntenna (service `0x1900`, char `0x1901`) — shares control model with LivingRoomHeater

Control model `gg/a.java` (UUID `00001901-...` via second constructor at `gg/a.java:151-160`; first constructor
at `:31-40` is the LivingRoomHeater variant on UUID `00002101-...`). Feature class `mg/f.java` (state struct
populated in `e(...)`, log `"<-- Incoming Data for SatelliteAntenna"` at `mg/f.java:334,359,373,382`).

### Fields (`gg/a.java:151-160`, log format `:55-65` method `B()`)

| Field | Java member | Bits | Default | Meaning |
|---|---|---|---|---|
| DishStop | `f9328h0` | 1 (bool) | false | request the dish stop moving |
| Wlan | `f9329i0` | 1 (bool) | false | enable/disable the antenna's WLAN AP |
| System | `f9325e0` | 2 | 3 | receiver system on/off (0/1 observed) |
| Dish | `f9326f0` | 2 | 0 | dish movement command (1/2 observed) |
| SatelliteSelection | `f9327g0` | 4 | 0 | which satellite to search for/lock onto (1–11) |

### Actions (all in `mg/f.java`)

1. **Enable WLAN (`J1(mj.b)`, `:221-236`)**: `Wlan = true`, send `DIRECT`; schedules two follow-up timers
   (5000 ms and 10000 ms, `jn.a`) to re-check connection status — WLAN AP bring-up is async on the antenna unit.
2. **Stop dish (`e3(mj.b)`, `:866-875`)**: `DishStop = true`, send `DIRECT`. This is the "halt movement now"
   command, distinct from selecting a satellite.
3. **Dish move — direction/state A (`g0(mj.b)`, `:929-931` → static `f(fVar, 1)` at `:186-193`)**:
   `Dish = 1`, send `DIRECT`.
4. **Dish move — direction/state B (`O2(mj.b)`, `:239-241` → static `f(fVar, 2)`)**: `Dish = 2`, send `DIRECT`.
   Exact meaning of raw values 1 vs 2 (e.g. fold-in vs. search/move-out) is `UNVERIFIED` — interface method names
   were stripped by obfuscation; inferred from the readback dish-state enum below.
5. **Select satellite (`f1(kf.d sat, jb.b)`, `:877-916`)**: `SatelliteSelection = sat.ordinal() + 1` (wire
   range 1–11), send `DIRECT`.
6. **System on/off (`l1(boolean on, bn.c)`, `:1021-1031`)**: `System = on ? 1 : 0`, send `DIRECT`.

**Guard**: `e3` (Stop) exists as a dedicated field separate from `Dish`, implying the recommended sequence for
changing target is *stop first, then reposition* — matches the task hint "dish stop-before-move"
(`UNVERIFIED` exact enforcement — no explicit ordering check was found in `mg/f.java`, but the presence of a
distinct stop command alongside two "move" values, plus the async 5s/10s reconciliation timers on WLAN, suggests
the antenna firmware itself requires idling before accepting a new target).

### Enums

- Satellite list (`kf/d.java`, wire = ordinal+1): `ASTRA_19_EAST, HOTBIRD_13_EAST, ASTRA_23_5_EAST,
  ASTRA_28_2_EAST, SIRIUS_4_5_EAST, EUTELSAT_9_EAST, EUTELSAT_16_EAST, HISPASAT_30_WEST, EUTELSAT_5_WEST,
  INTELSAT_10_2_T1_WEST, TURKSAT_4_2_EAST`.
- Error/status readback (`kf/a.java`): `NO_ERROR, NO_CONNECTION, SAT_NOT_FOUND, IGNITION_ON, MECHANICAL_ERROR,
  VOLTAGE_LOW, TEMPERATURE_HIGH, NO_TRANSPONDER, ANTENNA_RESERVED`.
- Dish physical state readback (`kf/b.java`): `FOLDED, MOVED_OUT, SEARCHING, FOLDING, STOPPED, OPTIMIZING,
  STANDBY, BOOTING`.

Page-seen key: `INFO_PAGE_SATELLITE_ANTENNA_WAS_SEEN` (`xp/g.java:150`).

---

## Roof / pop-top (service `0x1400`, char `0x1401`)

Control model `jg/a.java` (UUID `00001401-...`, `jg/a.java:25`). Feature class `ig/c.java` (implements `hf.a`,
the action-dispatch interface).

### Fields (`jg/a.java:14-29`, log `:31-39`)

| Field | Java member | Bits | Default | Meaning |
|---|---|---|---|---|
| Up | `f14584d0` | 2 | 3 (idle sentinel) | commanded lift direction: up |
| Down | `f14585e0` | 2 | 3 (idle sentinel) | commanded lift direction: down |
| SafetyCounter | `f14586f0` | 32 | 0 | app-generated monotonic BE-uint32, `seed + floor(elapsed_ms/500)`, streamed by the `w8/a` ~500 ms timer (`w8/a.java:49-60` + `b1/d.java:353`) — the **primary 1401 transmitter**, NOT unit-echoed. The unit acks the stream via the state-side `SafetyCounterValid` (1402 bit 7), a complement, not a replacement (see below) |

### Movement setters (`ig/c.java:266-283`)

```
f(int i)   // stage Down = i   (no send by itself)
g(int i)   // stage Up = i, THEN send: aVar.A(); aVar.y(false)
```
Because only `g()` transmits, the caller always calls `f(down)` first, then `g(up)` — sending both fields
together in one write, `y(false)` (write-without-response semantics, inferred).

### Open / Close actions

- **Open (`n2()`, `ig/c.java:329-351`)**: no-op if already fully open (state == `OPEN`); no-op (with a log
  line "Blocked roof usage (open) since last usage was only N ms ago") if **less than 1000 ms** since the last
  open/close command (debounce, `in.a.a()` timestamp check). Otherwise: `f(0)` (Down=0), `g(1)` (Up=1, sent),
  set local flags `opening=true/closing=false`, and start a **1000 ms repeating timer** (`ig/b.java` case 0)
  that keeps re-sending `Down=0, Up=1` every second for as long as `opening` stays true — the roof requires a
  continuous "move" heartbeat, not a single command.
- **Close (`e1()`, `ig/c.java:239-266`)**: mirror of Open — no-op if already `CLOSED`, same 1000 ms debounce
  ("Blocked roof usage (close) ..."), otherwise `f(1)` (Down=1), `g(0)` (Up=0, sent), flags
  `closing=true/opening=false`, same 1-second repeating heartbeat resending `Down=1, Up=0`.
- **Stop (`W()`, `ig/c.java:191-206`)**: cancels the repeat timer, resets any pending re-home logic, then
  `f(0)`; `g(0)` (sent) — Up=Down=0 halts the motor; clears both movement flags.

### SafetyCounter guard (`ig/c.java:181-190` `R3()` start / `:156-162` `L()` stop; heartbeat logic `ig/b.java` case 1)

`R3()` ("Starting SafetyCounter") is called when a movement begins; it schedules a one-shot 3000 ms check
(`ig/b.java` default case): if after 3 seconds the *state model's* SafetyCounter/ack flag is still falsy, the app
logs `"SafetyCounter is invalid after 3 seconds."` and raises a UI warning flag. The app **generates** the
outgoing `SafetyCounter` itself; the value it waits up to 3000 ms for is the vehicle's state bit
`SafetyCounterValid` (1402 bit 7) confirming the motor accepted the live counter stream — a client
re-implementing roof control on another vehicle should generate that counter and check for `SafetyCounterValid`.
The move-frame cadence is **~500 ms**, not 1 Hz: the primary transmitter is the `w8/a` SafetyCounter timer
(~500 ms, +1/frame; `w8/a.java:49-60` + `b1/d.java:353`); a **secondary** 1000 ms `ig/c` timer
(`ig/c.java:776`) only re-affirms direction (net ~3 frames/s, consecutive counter deltas 0/+1). Cross-reference
`protocol-sequences.md` §3.

### Roof-state enum (readback, `hf/b.java`)

`CLOSED`(0), `OPEN`(1), `MIDDLE_POSITION`(2), `OTHER`(3), `INIT`(4), `ERROR`(5).

### Correct command recipes

- **"roof up" / open**: if state != OPEN and ≥1000 ms since last command → send `{Down:0, Up:1}` once, then
  repeat `{Down:0, Up:1}` every ~500 ms (primary `w8/a` SafetyCounter-timer pump; a secondary 1000 ms timer only
  re-affirms direction) until stopped or fully open; the app generates the outgoing `SafetyCounter` and waits up
  to 3000 ms for the unit's `SafetyCounterValid` (1402 bit 7).
- **"roof down" / close**: same shape with `{Down:1, Up:0}`.
- **"roof stop"**: send `{Down:0, Up:0}` once; cancel the local repeat timer.

No page-seen/onboarding page-specific string key beyond the generic set was found for Roof in the reviewed
files (`UNVERIFIED` — roof info-popups reference `BLUETOOTH_ROOF_*`/`EXLAP_ROOF_*` local-ack keys only, see
`xp/g.java:232,290`, not a dedicated "seen" page key like the other features).
