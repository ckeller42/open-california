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
  - `017e0608…` (leveling non-zero → ignition ON): only `TerminalOneFive@7` decodes ignition = True.
  - `047e06…` (parked): CarVariant is `0` with the new layout in **both** frames (consistent),
    vs `0`/`2` with the old (the old read a `CarLevelPopUp` bit as variant).
  This is why the Vehicle screen always showed "Ignition Off" even ignition-on. Guarded by
  `test_capture_verified::test_vehicle_leveling_is_degrees` (+ ignition assertion) and
  `test_calictl::test_vehicle_decode_char_1004`.
- All other 13 functions' state layouts match exactly.
- `general` (1001) surfaces the 3 SW-version fields (`AmbSwVersion`/`CmSwVersion` = 4 ASCII bytes,
  `CommunicationVersion` = u8); only char 1002 (opaque VIN hash) is omitted.

## Control — outstanding items (recorded, not yet applied)

| function | status | detail |
|---|---|---|
| campingmode, roofAC, lighting, roof, airheater, LR-heater, stairs | ✅ aligned | offsets match |
| **cooler `1101` timers** | ✅ aligned | see **RESOLVED 2026-07-12** below — `overrides.py` now has the contiguous `TimerHour 16/8, TimerMin 24/8, NightTimerHourOn 32/8, NightTimerHourOff 40/8` matching the app's `f()` default branch byte-for-byte. (The old `20/4 · 24 · 32 · 40` values no longer exist in the code.) |
| **satellite `1901`** | ✅ aligned | see **RESOLVED 2026-07-12** below — offsets `Dish 0/2, System 2/2, Wlan 6/1, DishStop 7/1` (+ `SatelliteSelection 12/4`) now live in `overrides.py`, matching the app. Uninstalled here → not live-verifiable. |
| **energy `1601`** | ✅ aligned (decompile-derived, wire-capture pending) | fields renamed + placed + wired: dictionary has `EnergyModeSet 2/2` + `DisplayRefresh 7/1`, `control.py` `_energy` builds the frame and `set energy mode normal\|max_charge\|eco` is registered in `BUILDERS`. Decompile-verified vs the app (`pg/a` default: `EnergyModeSet@2/2`, `DisplayRefresh@7`, 1 byte); not yet live-captured. |

## Control gaps — RESOLVED 2026-07-12 (decompile push)

Proven directly from the `f()` frame builders:

- **cooler `1101` timers — FIXED (was a real bug).** The old `overrides.py` had the *airheater*
  layout (which has a 4-bit hole at 16–19) pasted onto cooler. Cooler's default branch is
  contiguous: **`TimerHour 16/8, TimerMin 24/8, NightTimerHourOn 32/8, NightTimerHourOff 40/8`**.
  Corrected in `overrides.py`; `test_encode_cooler_frames_match_hand_derived` updated.
- **satellite `1901` — RESOLVED** (the `MERGED_AMBIGUOUS` offsets): `Dish 0/2, System 2/2,
  Wlan 6/1, DishStop 7/1` (+ existing `SatelliteSelection 12/4`). Added to `overrides.py`.
  Uninstalled here → not live-verifiable.
- **energy `1601` — applied (decompile-derived, wire-capture pending).** The app's real energy write is
  **`EnergyModeSet 2/2` + `DisplayRefresh 7/1`** (`Lpg/a;` default / `Lxf/d;` d4). These fields are now
  **placed** in the dictionary (no longer `MERGED_AMBIGUOUS`) — the old `OperationMode/Movement` stairs
  mis-map is gone — and `set energy mode` **is wired**: `control.py` `_energy` builds the frame and it is
  registered in `control.BUILDERS`. Decompile-verified vs the app; not yet confirmed by a live wire capture.

## Lighting modes — FULLY DECODED 2026-07-12

All modes share one builder (`eg/a`, full 128-bit packet); a mode = which slots `dg/h` writes.
Frame: `ProfileNumber@4/4, Mode@8/8, Timestamp@16/32 (control-side MERGED_AMBIGUOUS → 16/32,
confirmed), LightValue@48/16, Brightness×16 @64 (4-bit each, sentinel 14 = leave-unchanged)`.

| Mode | value | what it writes |
|---|---|---|
| SET_BRIGHTNESS | 4 | per-zone Brightness (supported) |
| **SET_COLOR** | 6 | `LightValue` = **colour palette INDEX 1–10** (`dg/j`: WARM_WHITE=1…SALMON=10 — *not RGB*), `ProfileNumber` = target profile |
| **SET_DOUBLE** | 8 | `LightValue` = an int (dual/split config; value meaning INFERRED) |
| REQUEST_CONFIG | 12 | Mode only + `ProfileNumber=13`; a config-pull trigger, no payload |
| SET_PROFILE | 16 | `ProfileNumber` = `dg/l` profile (supported); variants set PN 0/8/12 |
| **WAKEUP_TIME** | 20 | `Timestamp@16/32` = **epoch seconds** (next hh:mm), + packed `LightValue` (colour+areas+wake-profile) |
| SYSTEM_TIME | 24 | **defined but the app NEVER sends it** (unit likely self-syncs its RTC) |
| PREVIEW | 28 | `ProfileNumber=10, LightValue=1` (+ 5 s UI timeout) |

Enums: profiles `dg/l` 0=OFF,1–7=FAVORITE,8=DOOR,9=LIVE_VIEW,10=WAKEUP,11=INTERIOR,12=ON,13=DEFAULT;
brightness `dg/i` OFF=0,10–100%=1–10,DEFAULT=11,NOT_EQUIPPED=13,14=leave-unchanged; colour `dg/j`
1–10; wake `dg/k` 0–7; areas `dg/m` 0–3. So calictl could add SET_COLOR (index) and WAKEUP_TIME
(epoch + packed field) — the frame layouts are now known (a live capture would confirm the two
INFERRED bits: the Timestamp unit and WAKEUP_TIME's internal packing).

## Semantics verified against the app's getters (2026-07-12) — SOUND

Read every `semantics.py` transform against the app's Kotlin state-parsers + view-model getters.
**Result: no interpretation bugs — the camping-lights-inversion class does not recur.** All
transforms MATCH the app (proven): water math + names-reversed; camping combined-inverted lights
vs un-inverted usb/master; every energy scale (÷10 V & currents, ×10 powers, IDcdc no-÷10 +2, SoC
×10, sentinels); vehicle +1900/+1-month + roll/pitch ÷100; cooler/roof/airheater.

Review flags resolved by the app's intent:
- **LR-heater `air_temp`/`water_temp` (4-bit) and roof-AC `target_temp` (8-bit) carry NO code
  scale — they are coarse *levels*, not °C.** The app never converts. `UNVERIFIED`→resolved: don't
  label °C (matches the existing `signals.md` caution).
- **stairs review NARROWED:** the app's `^1` inversion is on `OperationMode` + the movement
  *setter* only — the `extended`(State) and `obstacle_sensor`(Sensor) *getters* are non-inverted,
  so our surfaced booleans have correct polarity.
- **satellite `System` is a 2-bit enum**, not a boolean — our `system_on=bool(System)` is a lossy
  simplification (should be an enum) but not a polarity bug.
- **energy `WarnLevelTwo`:** the app *derives* it (suppresses when a source is active); our
  `faults[]` lists the raw bit — a minor over-report, not a bug.
- `energy_mode` ordinal order is **source-confirmed** (0=normal / 1=max_charge / 2=eco / 3=error, from
  `bf/c.java` + the read getter `l()` + the setter `d4()`) — no longer inferred. Only the source-state/warning
  display *labels* stay INFERRED (bound in undecompiled Compose UI); all are surfaced as raw ints, so no runtime risk.

Genuinely still need a **live measurement** (not code): power magnitudes' absolute unit,
`target_temp`'s real-world unit, and satellite/stairs end-to-end (uninstalled here).

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

## Full call-stack cross-check (2026-08-17)

A 5-agent audit re-derived every field + command from the app's decompiled decode/send
methods and view-model scales, then reconciled against our dictionary/semantics/control and
the wire captures. Corrections applied:

- **`general` (char 1001) decode was dead.** `state_fields` had no offsets, so `general()`
  returned `{}` and `amb_sw_version` was always `None` — which silently disabled the DC-DC
  **+2** correction (gated on `AmbSwVersion ∈ {0409,0410}`) in live operation; only the unit
  test kept it "passing" by injecting the string directly. Fixed: offsets added
  (`AmbSwVersion@0/32`, `CmSwVersion@32/32`, `CommunicationVersion@64/8`) **and** an ASCII
  decode — the SW versions are 4 ASCII bytes (`0x30343130 → "0410"`), not numeric. **Live-verified
  on-device 2026-08-17:** `amb_sw_version: 0410`, and `energy.dcdc_current` now reads `0` (raw
  `-2` **+2**) instead of the stale `-2`.

- **`satelliteantenna.system_on`** used `bool(System)`, but `System` is a 2-bit **enum** and the
  app getter is `System == 1`; values 2/3 wrongly read "on". Fixed to `== 1`; raw `system` surfaced.

- **Cooler night-schedule bytes are LITERAL, not leave-unchanged sentinels** (corrected
  2026-08-26). The audit proposed `NightTimerSet=3`/hours=`31` from the app builder defaults, and
  the captured power-on frame sends `0` — but that capture van simply had **no schedule set**, so
  `0` was the *current* value, not a sentinel. Live proof: a write carrying `NightTimerHourOn=0`
  **clobbered** a set `quiet_from=22`. So `_cooler_values` now carries the current schedule in
  every write. Lesson: a capture only validates the state it was taken in.

- **`energy` control fields renamed** `OperationMode→EnergyModeSet`(@2/w2/def3),
  `Movement→DisplayRefresh`(@7/w1). The old names were the **stairs (1801)** layout mis-copied into
  energy; both share the merged `pg/a` R8 class (case0=stairs, default=energy 1601). The fields are now
  **placed** and `set energy mode` **is wired** (`control.py` `_energy` in `control.BUILDERS`); the frame is
  **decompile-derived, not yet live-verified** — flagged as such until a wire capture confirms it.

- **`roof.SafetyCounter` de-flagged** to `@8/w32`: app-**generated** monotonic BE-uint32, **not**
  unit-echoed. Full drive-loop re-verified from `w8/a` + `b1/d` + `ig/c` (2026-08-17): the app pumps
  1401 frames from **TWO timers** — the PRIMARY transmitter is a **~500 ms SafetyCounter timer**
  (`w8/a`, ctor `500`=tick-ms / `450-550`=fire-jitter; each fire writes a frame carrying counter +1,
  value = `seed + floor(elapsed_ms/500)`, `b1/d.java:352`), plus a **secondary 1000 ms timer** that
  only re-affirms direction (`ig/c` `jn.a(1000L)`). Net ~3 frames/s, consecutive counter deltas 0/+1,
  **never +2**. **Our `device.actuate_roof` (500 ms, +1/frame) is protocol-correct** — it reproduces
  the app's counter-timer sub-stream exactly (we simply omit the 1000 ms duplicate re-sends), with the
  same wall-clock counter trajectory the unit validates (`SafetyCounterValid`, 1402 bit 7). The old
  `control.py`/`overrides.py` "unit echo / send 0" comments were corrected to app-generated. (An earlier
  same-day edit wrongly rewrote the model as ~1 Hz/+2 by mistaking the 1000 ms direction-resend for the
  pump — that was itself wrong and has been reverted; the implementation never needed a change.)

- **`lighting.Timestamp` de-flagged** to `@16/w32` (offset read from the `dg/h.java` builder).
