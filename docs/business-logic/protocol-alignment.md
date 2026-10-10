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
| SET_BRIGHTNESS | 4 | per-zone Brightness (supported). CAPTURE 2026-10-10: a group switch = one frame, the group's zones at 11 on / 0 off |
| **SET_COLOR** | 6 | `LightValue` = **colour palette INDEX 1–10** (`dg/j`: WARM_WHITE=1…SALMON=10 — *not RGB*), `ProfileNumber` = target profile |
| **SET_DOUBLE** | 8 | `LightValue` = an int (dual/split config; value meaning INFERRED) |
| REQUEST_CONFIG | 12 | Mode only + `ProfileNumber=13`; a config-pull trigger, no payload. CAPTURE 2026-10-10: sent on Lighting-screen open (`0d0c…` + commit) |
| SET_PROFILE | 16 | `ProfileNumber` = `dg/l` profile (supported); variants set PN 0/8/12 |
| **WAKEUP_TIME** | 20 | `Timestamp@16/32` = **epoch seconds** (next hh:mm), + packed `LightValue` (colour+areas+wake-profile) |
| SYSTEM_TIME | 24 | **defined but the app NEVER sends it** (unit likely self-syncs its RTC) |
| PREVIEW | 28 | `ProfileNumber=10, LightValue=1` (+ 5 s UI timeout) |

Enums: profiles `dg/l` 0=OFF,1–7=FAVORITE,8=DOOR,9=LIVE_VIEW,10=WAKEUP,11=INTERIOR,12=ON,13=DEFAULT;
brightness `dg/i` OFF=0,10–100%=1–10,DEFAULT=11,NOT_EQUIPPED=13,14=leave-unchanged; colour `dg/j`
1–10; wake `dg/k` 0–7; areas `dg/m` 0–3. calictl builds WAKEUP_TIME (`wakeup`, byte-exact vs the app
recording `lighting-wakeup.jsonl`; packing settled by call stack, #154) and SET_COLOR only as the
`save_profile N <colour>` preface (DECOMPILE-only); the standalone `color` is retired (A2).

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
  (`SET_DOUBLE` has no calictl support; `SET_COLOR` only as the `save_profile` preface.)
- **Characteristics**: no unread char — only `1000/1001/1002/1004` are literal; all per-function
  chars are runtime-built and already in the dictionary. `1002` = `SHA-256(VIN)[16:32]` — the app's
  **vehicle-identity check**: right after service discovery + MTU it reads `1002` and compares it with
  the hash of the VIN the user entered (`ny/c.java` case 8); a mismatch disconnects with "Wrong
  vehicle found" before any other read (APP-OBSERVED 2026-09-16, `tools/applab`). calictl never reads
  it (the bond is our identity), but a faithful fake unit must serve it.
- **Exlap `VWN_Camper_*`**: separate WiFi/TCP transport (see `value-freshness.md`), doc-only.
  Objects with no BLE counterpart: `Outside_Temperature`, `CarOptions`, `Connection_Active`.
- **Alerts / fault codes**: already complete in `alert-states.md`.
- **Wake / liveness**: only the `1003` heartbeat on BLE (Exlap "Alive" tokens belong to the WiFi
  transport). No door/terminal signal is *written*; ignition/leveling are *read* from `1004`.
  **Who ticks `1003` in the app (#154, 2026-10-05, call stack):** the ticker is session-global. `zf/d.java:183`
  wires it, `d2/s.java:795-802` runs it while the link is Connected and the control state is ACTIVE
  (`mj/d.java:247`, `pf/k.java:335-337`), and `c/i.java:349-367` sets a random seed of 1–1 000 000 and a random
  period of 750–850 ms. Each tick writes counter+1 (`t0/c.java:262-266`). No function starts or stops it, so it
  also runs during roof moves. The roof's own frames never touch 1003. **calictl follows the app** (owner
  decision 2026-10-05, A1): a roof move runs with the 1003 heartbeat ticking — inside the live persistent
  session, or a fresh connection that starts it — and still streams the counter with no `ARM_DELAY_S`
  pre-arm. The app's `roof-hold` recording agrees: 11 beats during a 9.1 s hold. **CAPTURE 2026-10-10**
  (real app on the real unit, evidence-ledger 2026-10-10): the app's heartbeat ran through a full roof open
  and close, every 0.76–0.79 s, with large counter values (`0x00049363…`, later `0x00061b62`), not 0 —
  CONSISTENT with the random seed and the 750–850 ms period. calictl's own roof path is still not
  device-verified (#157/#230). calictl keeps its own fixed 0.6 s period and fixed seed (device-verified)
  rather than the app's random 750–850 ms.

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
  **clobbered** a set `quiet_from=22`. So `_cooler_values` carried the current schedule in
  every write — since 2026-10-06 (ruling R1) only `night_on`/`night_off` do; every other cooler
  write sends the app's own 31 (APP-RECORDED `fd771e3e1f1f`, leave unchanged).
  Lesson: a capture only validates the state it was taken in.

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
  `control.py`/`overrides.py` "unit echo / send 0" comments were corrected to app-generated. (The
  roof-counter decision history is in `DECISIONS.md`.)
  **CAPTURE 2026-10-10** (real app, real unit, full open + close; re-analysed frame by frame): with the
  roof screen open and nothing pressed the app streams STOP `00 <counter>`, one frame per ~500 ms tick,
  +1 each (0.4997 s per increment over the capture); a press switches to `01`/`04` **at once** with the
  **same** counter (the first move frame repeats the last STOP value; the first STOP after a release
  repeats the last move value — 26 of 26 changes); while held a second frame repeats the current value
  about once a second. That is exactly the two-timer model above (deltas 0/+1, never +2). **calictl
  follows it** (owner decision 2026-10-10, `R_ROOF_VIEW_STREAM`): while the web UI's roof page is open
  (`POST /api/roof` `view`, refreshed; lapses after 15 s) the persistent session runs a
  `device.RoofStream` of STOP frames; a press switches that same stream to the move byte with the
  current counter, a release back to STOP. The counter is therefore validated before the press and the
  unit has no ~3 s withhold left (the app's motor started ~1.2 s after the press). A press without a
  view (API/CLI/HA) still starts its own stream (fresh counter, ~3 s withhold). calictl omits the 1000 ms
  re-send, and auto-stops at end of travel on `InfoPopUp` 8 (`2308`) as the app does. Its own move is
  still never driven on the real unit (#230).

- **`lighting.Timestamp` de-flagged** to `@16/w32` (offset read from the `dg/h.java` builder).

## App 5.4.0 — re-decompile against 5.0.8 (2026-10-10)

CaliforniaOnTour **5.4.0.3036** (the owner's phone, installed 2026-10-09) re-decompiled and diffed
against **5.0.8.3028** (the build every earlier citation in this file refers to). R8 renamed every
class; the full diff + rename table is in the private RE repo
(`notes/2026-10-10-diff-5.0.8-to-5.4.0.md`, `mapping.enigma`). Citations below are 5.4.0 classes.
Tier: **DECOMPILE 5.4.0** (`evidence-ledger.md`).

### The T7 / CommunicationVersion 2 protocol is unchanged

For this van (California 7, CommunicationVersion 2) no frame, field, sentinel, scale or timing
changed. Every 5.0.8 control write site has a 1:1 counterpart:

| Item | 5.4.0 |
|---|---|
| Handshake | read 1001 CommunicationVersion, then the authenticated 1004 read, then subscribe (`gg/h`), same order |
| `1003` heartbeat | +1 BE uint32, random **750–850 ms** period, seed **1..1e6**, session-global (`cj/e.java:313-326` → `rg/a`) |
| Neutral re-write | **500 ms** after a direct control write (`f40/v`, `c1/c` case 5) |
| Roof SafetyCounter | seed 1..1e6, **450–550 ms** tick (`a9/b`); roof dialogs/gates identical (`jk/c.java:86-123`) |
| Cooler / air heater / camping / lighting / energy 1601 / roof frames | same fields, enums and leave-unchanged sentinels (cooler 30/62/31/31, energy `EnergyModeSet` 3, lighting 14) |
| Energy 1602 on V2 | 176-bit layout identical (`og/e.java:1396-1441`); same scales (I, U /10, P ×10, `IDcdc` raw +2 on AmbSw 0409/0410, `og/e.java:844`; SoC level ×10) |
| Water 1302 | same 9 fields, same unconditional decode (see `value-freshness.md` "App 5.4.0") |
| 1004 vehicle | same layout; roll/pitch /100 |

New on this van's code path (no wire change): heater **ErrorCode 6/7** (`ig/b.java:606-700`,
`alert-states.md` §3), the rear-USB rule (`signals.md`), the "Disconnected to save energy" card
(`gg/k.java:44-95`; StateLand STANDBY now also counts as shore power for the stay-connected rule),
and persisted last-known values (`value-freshness.md`).

### Not on this van / not used for V2 (documented, not implemented)

calictl decodes none of these and must not write any of them to this unit. None is in
`protocol/dictionary.yaml` (no catalog decision needed).

| Item | What 5.4.0 does | Applies to |
|---|---|---|
| **Protocol-version layer** | 1001 CommunicationVersion → `UNKNOWN / VERSION_2 / VERSION_3 / VERSION_4` (`mh/a`), handed to every function (`gg/l.java:99-127`). Max accepted version **2 → 3** (`gf/a.java:39`; 5.0.8 hard `<= 2`). An unknown value → UNKNOWN → energy and general-purpose signals are not decoded | every unit; only energy, F001/F002 and VirtualBattery branch on it |
| Char **`1603`** energy measurements | 128-bit: `PLand 0-8, PPv 8-16, UOne 16-24, UTwo 24-32, IOneBattBem 32-48, ITwo 48-64, IDcdc 64-80, ILand 80-96, IPv 96-112, PDcdc 112-128`, same scales; polled every 4.8 s (`og/e.java:1150-1215`, `pg/a.java:37`) | V3+ |
| **1602 on V3** | 64-bit: flags + SoC bits 0-32 unchanged, then `StateDcdc 24-28, StatePv 32-36, StateLand 36-40, Age 40-48`, remaining hours 48-56 / minutes 56-64 (`og/e.java:986-1018`); the measurements move to 1603 | V3+ — calictl's 176-bit decode would be **wrong** here |
| **F001 on V3** | named fields: b7 `TimerForNoSleepDuringCampingModeActive`, b6 `IsRearUsbInTSevenAlwaysOn`, b5 `NightReductionActive`, b4 `NightReductionInstalled`, 8-40 night-reduction start/end h:m, 40-48 `HeatingLevel`, 48-56 `NightReductionActivated` (2 = on) (`sg/e.java:404-526`). On V2 the app keeps the generic placeholders (`sg/e.java:342-399`), as calictl does | V3+ |
| Char **`F002`** general-purpose write | 168-bit: b7 `NightReductionChangeTrigger`, 8-40 times, 40-48 `HeatingLevel` (sentinel 255), 48-56 `Activated` (2/1, sentinel 0) (`tg/b.java:408-583`) — the **heater night reduction** (UI requires continuous heating on). **calictl must never write F002 to a V2 unit** (behaviour unknown) | V3+ |
| Chars **`2200` / `2201` / `2202`** VirtualBattery (function 22) | 2202 state (`Installed`, SoC/current/voltage of the high-voltage battery, `VirtualBatteryMode` 0 AUTOMATIC / 1 PERMANENT / 6 DISABLED, warnings), 2201 control (24-bit, sentinels 3 / 127) (`hh/g`, `ih/a`, `ih/b`) | California Next + VERSION_4 only — **unreachable in this build** (max accepted is 3) |
| **CarVariant 2 = CALIFORNIA_NEXT** | 1004 CarVariant 0 T7, 1 Grand California, **2 California Next** (ID. Buzz based), else T7 (`qg/e.java:357-363`, `lf/a.java:25-31`) | other vehicles; this van reports 0 |
| **Lighting zones 29–36** | Next-only zone IDs mapped onto existing 1501 brightness fields: 29→L7, 30→L4, 31→L1, 32→L2, 33→L9, 34→L8, 35→L3, 36→L5 (`ug/h` `:467-519`); light groups per variant (`ug/h` `:565-590`). No new wire field. L6 and L14–L16 stay unaddressed | California Next |
| **Per-variant subscribe gate** | `isSupportedForVariant` (`gg/o`): roof T7 only; satellite, stairs, living-room heater, roof A/C **Grand California only**; VirtualBattery Next only; energy, water, heater, cooler T7 + GC; the rest all three. So a T7 app **no longer subscribes** 18xx / 19xx / 20xx / 21xx — matching the 8 subscriptions of the 2026-10-10 capture (`protocol-sequences` "Session foundation"). calictl still subscribes all 12; their `Installed` bit is 0 here | all |

**calictl's guard.** `semantics._firmware_untested` already flags any `comm_version != 2`
(`firmware_untested`, the web UI's "tested: amb 0409/0410 · comm 2" banner, and the raw-frame drift
capture). A unit that ever reports 3 would get that warning — and per the table its energy decode
(1602 layout, missing 1603) would be wrong, not just unvalidated. Check CommunicationVersion
(1001 byte 8) after any unit firmware update.
