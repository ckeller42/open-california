# Decisions & RE changelog

Dated record of resolved reverse-engineering questions and the dead ends ruled out along the
way. Open gaps live in [`re-gap-inventory.md`](re-gap-inventory.md); the current write/frame
model is in [`control-and-actuation.md`](control-and-actuation.md). Citations are `file:line`
under the JADX decompile (`…/scratchpad/decompile/src/sources` = CLEAN,
`…/decompile/bad/sources` = bad-code pass). Newest first.

---

## 2026-07-08 — HCI capture (owner's phone ↔ van): lighting CRACKED + live-verified

Owner captured the CaliforniaOnTour app driving the real van (`idevicebtlogger` on the bar
Mac; the phone had the iOS Bluetooth-logging profile). This resolved three gaps at once.

- **Lighting SET — SOLVED and live-verified** (supersedes §A1). SET_BRIGHTNESS (Mode 4) changes
  ONE zone; every OTHER zone carries **`14` (0xe) = leave-unchanged**, NOT 0 — our old builder
  zeroed all zones, and the earlier ProfileNumber sweep failed only because it *also* zeroed
  them. ProfileNumber echoes the active profile; the profile switch is **Mode 16**. calictl
  reproduces the app byte-for-byte (Kitchen=5 @profile 9 → `0904000000000000eeeeeee5eeeeeeee`;
  SET_PROFILE → `0010…`). **RECIPE, verified on buspi with the app gone:** a profile must be
  ACTIVE first — `set lighting profile 9` (→ profile 9 = "A") then `set lighting <zone> <0-13>`
  applies; with no active profile (state reads 0) bare SET_BRIGHTNESS is ACKed but ignored.
  Partial zone→lamp map from the drags: L1/L2/L4 = Leselichter (Links/Rechts/Beifahrer), L3 =
  Außenlicht (Umgebung hinten), L7 = Küche, L8 = Aufstelldach (Ambientelicht).
- **Air-heater — VERIFIED.** calictl's on/off frames match the app byte-for-byte (on
  `3d7b007f1f3f`, off `3c7b007f1f3f`; `NormalOperationRequest` 1/0, other fields at their
  leave-unchanged sentinels). Dropped the "not live-verified" caveat.
- **Roof — SafetyCounter is a LIVE +1 counter.** Direction bytes were already right (open
  `0x01`, close `0x04`, stop `0x00`); the app writes an incrementing 32-bit SafetyCounter
  (frame bytes 1-4) on every roof frame, idle and moving. `device.actuate_roof` now increments
  it instead of sending a fixed 0. Still not live-verified on hardware (safety).
- **Still open:** lighting SET_COLOR / Wecklicht (wake-light) / Alle-Lichter master / full
  16-zone map / profile-number semantics; air-heater level+runtime frames; leveling (char 1004
  read decode still wrong — needs the app's parser). See `re-gap-inventory.md`.

## 2026-07-08 — 6-agent decompile batch + VIN + APK strings

- **Lighting SET — theory PROPOSED then DISPROVEN live** (`re-gap` §A1). The decompile said the
  SET_BRIGHTNESS frame must carry the active ProfileNumber (identity round-trip of `1502`'s
  ProfileNumber; `dg/l.java` `f5472x`==ordinal, `w10/d.java` identity), so `control._lighting`
  was changed to echo `last["ProfileNumber"]` instead of 0. **A live sweep on 2026-07-08
  refuted it**: under a 1003 heartbeat, SET_BRIGHTNESS with *every* ProfileNumber 0–14 was ACKed
  but changed nothing (`scratchpad/profile_sweep.py`). Lighting SET is **still unsolved** — the
  gate is not ProfileNumber (open again in §A1; suspects `LightValue`/SET_PROFILE, needs a
  capture). The echo is kept (harmless, more correct than 0). Genuinely fixed: the
  `ui/screens/lighting.yaml`/`prototype.html` "offset 14, width 6" misread (→ offset 4, width 4).
- **Char 1002 "Vin" — RESOLVED as `SHA-256(VIN)[16:32]`** (`re-gap` §A2): the low 16 bytes of
  the plaintext VIN's SHA-256, matched live. A privacy-preserving one-way vehicle id; no
  plaintext VIN over BLE. Vehicle decodes to a **2026 T7 California** (WMI WV2, model code ST,
  MY `T`=2026, Hannover). New satellite chars: **1904=`SystemInfoWlanSsid`** (15 B UTF-8),
  **1905=`SystemInfoWlanKey`** (10 B), **1903**=`SatDish/CapConverter/CibusInterface/TpList` —
  all satellite-option-gated (not installed here).
- **Control models — DONE for all four** (`re-gap` §A3). roof/roofAC/stairs/LR-heater MERGED
  offsets resolved from their `f()` builders and added to `overrides.py` (+ frame bytes). Setter
  values + roof's ~1 Hz move-heartbeat documented. `set` not wired (not installed; roof needs
  the heartbeat loop). Enum semantics still UNVERIFIED.
- **Energy semantics — CORRECTED + live-verified** (`semantics.energy`): batt2 current /10,
  SoC→%, source powers ×10 (0 when not installed — the PPv=254 sentinel), shore/solar current
  unsigned /10, source-state enum, `energy_mode` un-inverted, warning level, expanded faults.
- **EXLAP — URLs are HARDCODED literals, not runtime** (`re-gap` §C2, correction). The 12
  handlers return literal `VWN_Camper_<Fn>_State`/`_Control` URLs; 7 actuate. `<Call>` wire
  format fully recovered.
- **Lighting profile/color/wake-timer overlay + the alert taxonomy** decoded (`re-gap` §A5;
  alert detail folded into `alert-states.md`).

## 2026-07-08 — alert-states addendum (folded into `alert-states.md`)

- **Roof pop-top fault alerts** — char `1402`, `ig/c.java` (a second roof alert surface,
  distinct from the `ij/c.java` interlocks). `InfoPopUp` bits 12-15 dispatch 7 IDs incl. the new
  `ROOF_ERROR`, `ROOF_NOT_POSSIBLE_TEMPORARILY`, `ROOF_OP_DRIVING` (CRITICAL). Two ack keys lack
  the `_CONFIRMED` suffix (`..._NOT_POSSIBLE_TEMP`, `..._ERROR_WORKSHOP`).
- **`CLOCK_OUT_OF_SYNC_ID` threshold closed** — fires when |phone − camper clock| > **5 min**
  (UTC), or unconditionally if the car-time object is null (`bad/sources/zf/d.java:235,242-248`;
  unit `n20/d.java:42` = MINUTES). Severity LOW.

## 2026-07-07 — write gate SOLVED (1003 heartbeat)

The gate is a **1003 liveness heartbeat**, not a handshake or interlock. Full current writeup:
[`control-and-actuation.md`](control-and-actuation.md) §1. Superseded the "safety interlock"
hypothesis below. `vehicle` char 1004 modeled; `general` repointed 1002→1001.

### Dead ends ruled out (superseded, kept for the record)

- **1004 is NOT a session token.** Traced the auth-read response through the write path
  (`pf/g.java` default case): the `1004` data (`boolArr2`) is used only for `.length` (empty vs
  non-empty) then discarded — never stored, never fed into any write. No nonce / signed-write /
  challenge exists in the GATT layer (`qd`/`s`/`m2`/`jb`). The auth read is purely an
  encryption/bonding trigger + a "did it return data" gate.
- **Incomplete-subscription theory RULED OUT** (live lighting write test —
  `scratchpad/full_light.py`, `echo_light.py`, `diag_light.py`). Only **12** notifiable chars
  exist (`1004, 1102, 1202, 1302, 1402, 1502, 1602, 1702, 1802, 1902, 2002, 2102`) and we
  subscribe all 12 — no 13th hidden char. Established the firmware has two write-processing
  layers: *parse/validate* runs (a `ProfileNumber=14`+`Mode=4` frame is rejected at ATT with
  `0x0E`; `ProfileNumber` must be `0` when `Mode=4`), while *apply/act* was gated (a
  firmware-valid frame — even an echo of the unit's own `1502` bytes — was ACKed then ignored).
  The block was **not** subscription count, **not** the 1004 token, **not** frame validity — it
  sat at the apply layer (now known: the 1003 heartbeat).
- **"Selective gate / safety interlock" hypothesis — SUPERSEDED.** Cooler probes
  (`scratchpad/gate_class2.py`, `gate_decisive.py`, `config_surface.py`) showed schedule/metadata
  writes (`TimerHour`, `TimerMin`) **landed + persisted** across sessions while physical-load
  writes (`State`, `Level`, light on/off+brightness) were ACKed-then-ignored — which *looked
  like* a load-energizing vehicle-permissive interlock (ignition/terminal-15). It was not: the
  real missing precondition was the 1003 heartbeat. **Still-true sub-findings:** config writes do
  persist to durable storage; control→state field names are **offset-based, not name-aligned**
  (control `TimerHour` lands in state `TimerMinSet`, `TimerMin` in `NightTimerHourOn`); the
  firmware **range-validates** and drops the ATT link on out-of-range values (cooler `State=3`,
  lighting `ProfileNumber=14`+`Mode=4` → `0x0E`), which motivated `overrides.CONTROL_RANGES` +
  `protocol.encode` validation (`tools.app_ranges` reports coverage).

## 2026-07-06 — status/state extraction completeness (commits e74e176, 7ac5480)

`state_offsets` in the extractor was joining on debug-log variable names (missing struct
sub-fields) instead of the `subList` slices; a subList-spine rework recovered Energy (17→36
fields) and the rest. Roof/Lighting were absent because jadx's normal pass skipped their
state-decode methods ("Method dump skipped"); a `--show-bad-code` re-decompile of `ig/c` and
`dg/a` recovered them, and `decompile.sh` now passes that flag. **Dictionary is 13 functions, 0
unresolved, all with state fields.** (Full audit: `status-states-audit.md`.)
