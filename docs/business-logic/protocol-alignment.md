# Protocol alignment audit (APK ↔ our dictionary)

Decompiled `de.volkswagen.CaliforniaOnTour` (androguard) field-by-field against
`protocol/dictionary.yaml`, 2026-07-10. Method: each per-function state decode `e()` gives
`subList(a,b)` bit-slices (offset `a`, width `b−a`); each control builder `f()` gives explicit
`arr[idx]` placements. Convention verified against **water** + **cooler** (extractor output, both
live-verified) — app offsets are directly comparable to ours, no bit-reversal.

## Char ↔ function assignments — ALL CONFIRMED ✅

The `campingmode@1201` / `roofaircondition@2001` swap hypothesis is **refuted** (decisive: the same
builder `Llg/a;` holds both chars): `1201/1202` = campingmode (`State/UsbCharger/OutsideLight/
InteriorLight`); `2001/2002` = roof-A/C (`State/FanSpeed/Mode/Temperature`). All 14 assignments match.

## State — 13/14 aligned; one bug **FIXED**

- **`vehicle` (1004) byte-0 was wrong** (hand-added, not extractor-emitted). App subList slices:
  `CarVariant 0/4`, `CarLevelPopUp 4/2`, **`TerminalOneFive 7/1`** (bit 6 spare) — we had them at
  `3/4 · 1/2 · 0/1`. **Fixed** in `dictionary.yaml`. Proof from committed captures:
  - `017e0608…` (leveling non-zero → ignition ON): only `T15@7` decodes ignition = True.
  - `047e06…` (parked): CarVariant is `0` with the new layout in **both** frames (consistent),
    vs `0`/`2` with the old (the old read a `CarLevelPopUp` bit as variant).
  This is why the Vehicle screen always showed "Ignition Off" even ignition-on. Guarded by
  `test_capture_verified::test_vehicle_leveling_is_degrees` (+ ignition assertion) and
  `test_calictl::test_vehicle_decode_char_1004`.
- All other 13 functions' state layouts match exactly.
- `general` (1001) intentionally omits the opaque SW-version/VIN ASCII strings (not bitfields).

## Control — outstanding items (recorded, not yet applied)

| function | status | detail |
|---|---|---|
| campingmode, roofAC, lighting, roof, airheater, LR-heater, stairs | ✅ aligned | offsets match |
| **cooler `1101` timers** | ⚠️ CONFLICT | app subList → contiguous `TimerHour 16/8, TimerMin 24/8, NightTimerHourOn 32/8, NightTimerHourOff 40/8`; our `overrides.py` (from an earlier direct read of `sf/a.java`) has `NightTimerHourOff 20/4 · TimerHour 24 · TimerMin 32 · NightTimerHourOn 40`. **Unverified either way** (night-timer write never live-tested). Resolve by a careful re-read + live test before trusting cooler timers. |
| **satellite `1901`** | ➕ missing | app adds `DishStop 7/1, Wlan 6/1, System 2/2, Dish 0/2` (we have only `SatelliteSelection 12/4`). Uninstalled here → can't verify; add when it matters. |
| **energy `1601`** | ⚠️ likely wrong | our `OperationMode/Movement` duplicate the `stairs` model; app writes `EnergyModeSet` + `DisplayRefresh` (offsets route through `Lxf/d;` ViewModel, unresolved). Energy is read-only telemetry for us; re-derive before adding any energy control. |

## Enums / extras (add when the extractor emits them)

- **Lighting `Mode` wire values** (`Ldg/n;`): `NO_MODE=0, SET_BRIGHTNESS=4, SET_COLOR=6,
  SET_DOUBLE=8, REQUEST_CONFIG=12, SET_PROFILE=16, WAKEUP_TIME=20, SYSTEM_TIME=24, PREVIEW=28`.
  `command_enums.dg_n` currently lists names only — a consumer can't build a frame without these.
  (`SET_COLOR`/`SET_DOUBLE` have no calictl support at all.)
- **Characteristics**: no unread char — only `1000/1001/1002/1004` are literal; all per-function
  chars are runtime-built and already in the dictionary. `1002` = `SHA-256(VIN)[16:32]` opaque.
- **Exlap `VWN_Camper_*`**: separate WiFi/TCP transport (see `value-freshness.md`), doc-only.
  Objects with no BLE counterpart: `Outside_Temperature`, `CarOptions`, `Connection_Active`.
- **Alerts / fault codes**: already complete in `alert-states.md`.
- **Wake / liveness**: only the `1003` heartbeat on BLE (Exlap "Alive" tokens belong to the WiFi
  transport). No door/terminal signal is *written*; ignition/leveling are *read* from `1004`.
