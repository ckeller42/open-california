# VW CaliforniaOnTour — Vehicle Status/State Extraction: Completeness Audit

Scope: own-vehicle interoperability, i.e. every BLE characteristic the CaliforniaOnTour
app *reads/subscribes to* as vehicle status, compared against
`protocol/dictionary.yaml`. Sources: decompiled app at
`/private/tmp/claude-501/-Users-ckeller-src-open-california/b137c4a5-b11b-4b8b-b6d6-16b437dd2993/scratchpad/decompile/src/sources`
(re-decompiled two classes with `--show-bad-code` where JADX's normal pass failed —
noted inline). All camper-protocol characteristics share the UUID suffix
`-6C77-4B7D-BBF6-A5E587701F3D` ("Exlap" protocol).

## RESOLVED 2026-07-06 (commits e74e176, 7ac5480)

This audit's gaps are now **fixed**. `state_offsets` in the extractor was joining
on debug-log variable names (missed struct sub-fields) instead of the `subList`
slices; reworking it to a subList spine recovered Energy (17→36) and the rest.
Roof/Lighting were genuinely absent because jadx's normal pass skipped their
state-decode methods ("Method dump skipped") — a `--show-bad-code` re-decompile
of `ig/c` and `dg/a` recovered them; `decompile.sh` now passes that flag.
**Dictionary is now 13 functions, 0 unresolved, all with state fields.** Energy
telemetry (SoC/V/A/W) and Water (`Level`=current, `Volume`=capacity) are
additionally **live-verified** against the vehicle. The original verdict below
is kept as the historical record of what was found.

## Verdict (short) — HISTORICAL (now resolved, see above)

**INCOMPLETE.** The dictionary is missing two entire status functions (Roof,
Lighting — never even referenced in the 11-function list), has a completely
empty field list for `general` despite 15 real fields, undercounts
`satelliteantenna` by 6 fields, and undercounts `energy` by 19 fields — the
19 missing Energy fields are exactly the analog telemetry (battery SoC%,
voltages, currents, power, remaining time), i.e. the most useful status data
in that function is currently uncaptured. 7 of the 11 originally-catalogued
functions match the decompiled source exactly.

---

## 1. Confirming the 11 "Incoming Data" logs are exhaustive

```
rg -o -N '"<-- Incoming Data for [A-Za-z0-9 ]+' <root> | sort -u
```
returns exactly 11 hits, one per function, matching the dictionary's 11:
AirHeater (`rf/b.java:393`), Campingmode (`tf/a.java:174`), Cooler (`vf/c.java:377`),
Energy (`xf/a.java:286`), General (`zf/d.java:239`), GeneralPurposeSignals
(`bg/a.java:162`), LivingRoomHeater (`fg/b.java:253`), RoofAirCondition
(`kg/b.java:207`), SatelliteAntenna (`mg/f.java:334`), Stairs (`og/b.java:175`),
Water (`qg/b.java:222`).

A broader sweep for `<--`/`-->`/`Incoming`/`Received`/`State` variants turned up
no additional "Incoming Data for X" style channel — but it did surface two other
message shapes that matter:

- `"<-- Received Exlap data for "` (`ah/h.java:171`) — this is the **generic**
  transport-layer log emitted for every characteristic notification, one level
  below the per-function "Incoming Data for X" logs. Not a 12th channel.
- Several functions log **more than one** "Incoming Data for X" line, once per
  characteristic they own (see §2 — SatelliteAntenna and General each have
  multiple such lines that only partially made it into the dictionary).

**Conclusion for task 1: the 11 are exhaustive as *log-line prefixes*, but that
undercounts the true number of state characteristics, because several of the
11 functions read from more than one characteristic and only their first/main
characteristic's fields were captured into `state_fields`.**

---

## 2. Per-function field-count audit (dictionary vs. decompiled source)

Verified by reading each `e(String, Boolean[])` decode method in full and
counting the `Incoming Data for X: ... ` interpolations.

| Function | dict `state_fields` count | actual field count in source | verdict |
|---|---|---|---|
| AirHeater | 14 | 14 (`rf/b.java:393`) | **MATCH** |
| Campingmode | 6 | 6 (`tf/a.java:174`) | **MATCH** |
| Cooler | 14 | 14 (`vf/c.java:377-386`) | **MATCH** (spot-check requested in task) |
| Energy | 17 | **36** (`xf/a.java:286-304`) | **MISMATCH — 19 missing** |
| General | 0 | **15** (`zf/d.java:239,251,296-303`) | **MISMATCH — 15 missing (100%)** |
| GeneralPurposeSignals | 21 | 21 (`bg/a.java:162-172`) | **MATCH** |
| LivingRoomHeater | 8 | 8 (`fg/b.java:253`) | **MATCH** |
| RoofAirCondition | 6 | 6 (`kg/b.java:207`) | **MATCH** |
| SatelliteAntenna | 6 | **12** (`mg/f.java:334,359,373,382`) | **MISMATCH — 6 missing** |
| Stairs | 5 | 5 (`og/b.java:175`) | **MATCH** |
| Water | 9 | 9 (`qg/b.java:222-227`, spot-check requested) | **MATCH** |

### Energy — the 19 missing fields (`xf/a.java:286-304`)

Dictionary only captured the 17 single-bit flags. Missing entirely, all
numeric telemetry (offsets/widths readable at `xf/a.java:227-284`, boolArr
length 176):

`EnergyMode, SocOneBattAfs, SocTwoBattAfs, StateDcdcAfs, StateLandAfs,
StatePvAfs, AgeOneBattValuesMinutes, IOneBattBemAfs, PDcdcAfs, PLandAfs,
PPvAfs, tTwoBattRemainingh, tTwoBattRemainingmin, UOneBattBemAfs,
UTwoBattBemAfs, ITwoBattBemAfs, IDcdcAfs, ILandAfs, IPvAfs`

These are battery state-of-charge (%), DC/DC + land-power + PV fault states,
battery age, currents (I*), voltages (U*), powers (P*), and remaining runtime
— i.e. essentially all of the quantitative "how much energy do I have left"
status. This is the single biggest functional gap in the audit.

### General — 0 vs 15 fields (`zf/d.java`)

`dictionary.yaml` lines 104-108 list `general` with `state_fields:` **empty**.
The function actually decodes 3 separate characteristics:

- **char `1001` ("Info")** — `zf/d.java:232-242`, boolArr ≥72 bits (`ag/c.java:44-51`):
  `AmbSwVersion` (offset 0, width 32), `CmSwVersion` (offset 32, width 32),
  `CommunicationVersion` (offset 64, width 7)
- **char `1002` ("Vin")** — `zf/d.java:247-251`: 16 opaque bytes (128-bit), **NOT ASCII** —
  it is `SHA-256(VIN)[16:32]` (confirmed against the owner's VIN); a one-way hashed vehicle id
- **char `1004` ("Car_Info")** — `zf/d.java:266-303`, boolArr ≥88 bits:
  `TerminalOneFive` (offset 7, w1), `CarLevelPopUp` (offset 4, w2),
  `CarVariant` (offset 0, w4), `CarTimeYear` (offset 8, w8),
  `CarTimeMonth` (offset 16, w8), `CarTimeDay` (offset 24, w8),
  `CarTimeHour` (offset 32, w8), `CarTimeMinute` (offset 40, w8),
  `CarTimeSecond` (offset 48, w8), `CarLevelRoll` (offset 56, w16),
  `CarLevelPitch` (offset 72, w16)

`CarLevelRoll`/`CarLevelPitch` (vehicle tilt/leveling) and `CarTime*` (onboard
clock) are genuine status signals with no equivalent elsewhere in the
dictionary.

- **char `1003` ("Counter")** — `ag/b.java`, invoked from `t0/c.java:264` — this
  is **write-only** (the app writes a counter value back; `v()` resets it to 0,
  no bit-field decode of an incoming read). Not a missing status field, but
  worth noting since `dictionary.yaml`'s `general.state_services` list omits
  it entirely (it lists `[1000, 1001, 1002, 1004]`). UNVERIFIED: exact purpose
  of the counter (looks related to the `CLOCK_OUT_OF_SYNC` flow at `zf/d.java:352-361`).

### SatelliteAntenna — 6 vs 12 fields (`mg/f.java`)

Dictionary's 6 fields (`Installed, System, Error, Dish, SatelliteSelection,
SignalLevel`) match **only** char `1902` (`mg/f.java:317-341`). Three more
characteristics are read and logged but never made it into `state_fields`,
despite being listed in `state_services: [1900, 1902, 1903, 1904, 1905]`:

- **char `1903`** (`mg/f.java:348-364`, boolArr ≥144 bits): `SatDish` (offset 0,
  w32), `CapConverter` (offset 32, w40), `CibusInterface` (offset 72, w32),
  `TpList` (offset 104, w40) — this is the task's "SW_Versions" characteristic;
  field names suggest per-subcomponent firmware/hardware identifiers, not
  plain version strings. Semantics UNVERIFIED.
- **char `1904`** (`mg/f.java:369-373`, boolArr ≥120 bits): `SystemInfoWlanSsid`
  (offset 0, w120 — 15-byte ASCII) — the roof WLAN's SSID.
- **char `1905`** (`mg/f.java:378-382`, boolArr ≥80 bits): `SystemInfoWlankey`
  (offset 0, w80 — 10-byte) — the roof WLAN's passphrase/key.

---

## 3. Status-bearing chars without a dedicated "Incoming Data" log — resolved

Per task 3, checked service `0x1000` (1001/1002/1003/1004) and `0x1900`
(1903/1904/1905): both **do** have "Incoming Data for X" logs — they're just
folded under the General and SatelliteAntenna function names respectively
(see §2 above for exact fields/offsets). `f000`/`f001`
(GeneralPurposeSignals) is already one of the original 11 and its 21 fields
match the dictionary exactly — it was a red herring in the task's framing, not
a gap.

---

## 4. NEW gaps found — two entire functions missing from the dictionary

Enumerated **every** camper-protocol characteristic UUID in the app
(`rg -o -N '0000[0-9a-fA-F]{4}-6C77-4B7D-BBF6-A5E587701F3D'`, 42 distinct
UUIDs from `1000`-`2102` plus `f000`/`f001`) and mapped each to a function.
Two service blocks — `0x1400` and `0x1500` — are **not represented anywhere**
in `dictionary.yaml`, not even as unresolved. The dictionary's
`control_models_unresolved` section lists their control characteristics
(`jg/a.java` char `1401`, `eg/a.java` char `1501`) with the comment *"Control
models with no matching state function"* — **this comment is factually wrong**;
both have a matching state characteristic, found by re-decompiling
`ig/c.java` and `dg/a.java` with `--show-bad-code` (JADX's default pass failed
to decompile these two `e()` methods, which is very likely why the original
extraction script missed them — the field data exists only behind
"Method dump skipped" bytecode that requires the bad-code flag to see).

### Roof (service `0x1400`) — MISSING ENTIRELY

- Control char `1401` (`jg/a.java`): `Up`, `Down`, `SafetyCounter` (already
  known-unresolved in dictionary).
- **State char `1402`** (`ig/c.java:241-257`, boolArr ≥16 bits), log line
  `"<-- Incoming Data for Roof: ..."`:
  - `SafetyCounterValid` (offset 7, w1)
  - `Installed` (offset 6, w1)
  - `Position` (offset 0, w4)
  - `InfoPopUp` (offset 12, w4)

  This is almost certainly the pop-top roof lift/hatch position — distinct
  from `RoofAirCondition` (service `0x2000`, the roof-mounted A/C vent fan).
  `Position`/`hf.b` is mapped to an enum with values driving open/closed/error
  UI state (`ig/c.java:284-318` — `h()`, `i()`, `j()`, `k()`, `l()` helpers).

### Lighting (service `0x1500`) — MISSING ENTIRELY

- Control char `1501` (`eg/a.java`): `ProfileNumber, Mode, Timestamp,
  LightValue, BrightnessLOne..BrightnessLOneSix` (already known-unresolved
  in dictionary, 20 fields).
- **State char `1502`** (`dg/a.java:160-216`, boolArr ≥128 bits), log line
  `"<-- Incoming Data for Lighting: ..."` — the same 20 fields echoed back as
  state: `ProfileNumber` (offset 4, w4), `Mode` (offset 8, w8), `Timestamp`
  (offset 16, w32), `LightValue` (offset 48, w16), and 16×
  `BrightnessL{One..OneSix}` (4 bits each, packed offsets 64-128). This is the
  interior ambient-lighting profile/brightness status per light zone.

---

## Definitive list of ALL status/state channels found

13 distinct status **functions**, 21 state-bearing **characteristics** total:

| # | Function | Service | State char(s) | In dictionary? |
|---|---|---|---|---|
| 1 | AirHeater | 1700 | 1702 | yes, complete |
| 2 | Campingmode | 1200 | 1202 | yes, complete |
| 3 | Cooler | 1100 | 1102 | yes, complete |
| 4 | Energy | 1600 | 1602 | yes, **19/36 fields missing** |
| 5 | General | 1000 | 1001, 1002, 1004 (1003 write-only) | listed but **0/15 fields captured** |
| 6 | GeneralPurposeSignals | f000 | f001 | yes, complete |
| 7 | LivingRoomHeater | 2100 | 2102 | yes, complete |
| 8 | **Roof** | 1400 | 1402 | **NOT PRESENT AT ALL** |
| 9 | RoofAirCondition | 2000 | 2002 | yes, complete |
| 10 | SatelliteAntenna | 1900 | 1902, 1903, 1904, 1905 | listed but **6/12 fields captured** |
| 11 | Stairs | 1800 | 1802 | yes, complete |
| 12 | Water | 1300 | 1302 | yes, complete |
| 13 | **Lighting** | 1500 | 1502 | **NOT PRESENT AT ALL** |

Confirmed via full char-UUID enumeration that no 22nd characteristic exists
(every `0000XXXX-6C77-...` UUID from `1000` through `2102`, plus `f000/f001`,
maps to one of the 13 functions above; none left unaccounted for). Also
confirmed (task 4, `rg -l "subList\("` / `equalsIgnoreCase("0000` across the
whole source tree) that the bit-field-decode dispatch pattern used by these 13
functions appears in exactly the files already inspected — no additional,
differently-named decode class does this kind of characteristic parsing.

---

## Explicit gap list (action items)

1. **Add `roof` function** — service 1400, state char 1402, 4 fields
   (`SafetyCounterValid, Installed, Position, InfoPopUp`). Currently 100% absent.
2. **Add `lighting` function** — service 1500, state char 1502, 20 fields
   (`ProfileNumber, Mode, Timestamp, LightValue`, + 16 `BrightnessL*`).
   Currently 100% absent.
3. **Populate `general.state_fields`** — currently empty; 15 real fields
   across chars 1001/1002/1004 (see §2). Also note char 1003 as a write-only
   counter, not a state field.
4. **Add 19 missing `energy` fields** — all the analog telemetry
   (SoC%, voltages, currents, power, remaining time); currently only boolean
   flags are captured.
5. **Add 6 missing `satelliteantenna` fields** — from chars 1903
   (`SatDish, CapConverter, CibusInterface, TpList`), 1904
   (`SystemInfoWlanSsid`), 1905 (`SystemInfoWlankey`).
6. Fix the `control_models_unresolved` comment for `jg/a.java` (char 1401) and
   `eg/a.java` (char 1501) — both **do** have matching state characteristics
   (1402, 1502); the "no matching state function" note is incorrect and
   should be replaced by proper `roof`/`lighting` function entries.

## UNVERIFIED / needs a live pass

- Exact semantics of `SatDish/CapConverter/CibusInterface/TpList` (char 1903)
  — field names decompiled correctly but their real-world meaning (version
  numbers vs. component IDs) is a guess.
- Semantics of `Position`/mode-enum values for Roof (char 1402) — enum
  mapping (`hf.b`) exists in code but its symbolic names weren't resolved in
  this pass.
- Purpose of General's char `1003` "Counter" (write-only) — plausibly tied to
  the `CLOCK_OUT_OF_SYNC` notification flow (`zf/d.java:352-361`) but not
  traced end-to-end.
- All `value_semantics` for newly found fields are UNVERIFIED, consistent with
  the rest of `dictionary.yaml`.
