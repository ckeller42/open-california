# Decisions & RE changelog

Dated record of resolved reverse-engineering questions and the dead ends ruled out along the
way. Open gaps live in [`re-gap-inventory.md`](re-gap-inventory.md); the current write/frame
model is in [`control-and-actuation.md`](control-and-actuation.md). Citations are `file:line`
under the JADX decompile (the decompiled sources (clean pass) = CLEAN,
the decompiled sources (bad-code pass) = bad-code pass). Newest first.

---

## 2026-08-29 — lighting REQUEST_CONFIG preamble retired from calictl

The app-faithful screen-open config pull (proven NOT an actuation gate, 2026-08-16 evening
entry) was still sent on the CLI path, costing ~3.3 s per `set lighting`; the daemon path
already shipped without it. Removed everywhere: `control.preamble_for`, `LIGHT_REQUEST_CONFIG`,
the `device.actuate(pre=…)` plumbing and `PRE_SETTLE_S` (`R_LIGHT_PREAMBLE` retired with it).
The frame stays documented in `protocol-sequences` as the RE record.

## 2026-08-17 — roof SafetyCounter model re-verified (and an intraday mis-edit reverted)

- **`actuate_roof`'s 500 ms / +1-per-frame stream confirmed protocol-correct** against fresh
  source (`w8/a` + `b1/d` + `ig/c`): the app pumps move frames from a ~500 ms SafetyCounter
  timer (the primary transmitter, +1/frame) plus a **secondary 1000 ms timer that only
  re-affirms direction** (~3 frames/s net, consecutive counter deltas 0/+1, never +2). Our
  single 500 ms/+1 stream reproduces the counter-timer sub-stream with the same wall-clock
  counter trajectory the unit validates.
- **Intraday mis-edit, reverted the same day:** an edit wrongly rewrote the model as
  ~1 Hz / +2-per-frame by mistaking the 1000 ms direction-resend for the pump. That reading
  was itself wrong and was reverted; **the implementation never needed a change.** Recorded
  here so the current model in `protocol-alignment.md` stays clean prose with this changelog
  holding the history.

## 2026-08-16 (evening) — CORRECTION: the REQUEST_CONFIG preamble is NOT the actuation gate; the unit's wake-state is

Supersedes the *mechanism* claimed in the morning entry below (the "preamble arms actuation"
conclusion). The photon verification, brightness enum, lamp map and feedback-channel findings
stand.

- **Necessity falsified, 6× photon-confirmed the same evening.** With the unit awake (phone
  off, daemon stopped), a **bare SET_BRIGHTNESS + `0e00…` commit physically actuates the
  lamps, both directions** — no preamble, no 1003 heartbeat, no arm/settle delay. The decisive
  run: three fresh-connection trials varying ONLY the arming, owner-watched ("off, then on,
  then off") — Trial 1 was an immediate SET+commit on a cold connection with zero arming and
  it switched the lamp.
- **Decompile-confirmed (fresh jadx source):** the app's set-one-zone method `dg/h.java:174
  E()` stages ProfileNumber=9 + Mode=4 and writes DIRECT (immediately); it does **not** call
  the config request `d0()` (`dg/h.java:471`), no heartbeat, no delay. `ag/b.java` is a
  generic connection-liveness counter (char `00001003`) separate from the write path.
- **The morning result was a confound:** the unit was freshly woken/sleepy; the preamble path
  added ~3.3 s + extra frames that gave it time to become ready — correlation, not cause.
  **The real gate is the unit's wake/active state.** REQUEST_CONFIG's real role is a
  **screen-open config pull** (it triggers the Mode-tagged config-dump notifications on
  `1502`); only that dump is gated on it. The preamble stays in the code — app-faithful,
  harmless, ~3.3 s slower (`control.preamble_for`, `R_LIGHT_PREAMBLE`) — but is optional.
- **1502 Mode-4 notifications are decodable standard state frames** (`protocol.decode` reads
  them; Mode + real per-zone brightness ramping to target); they fired in armed and un-armed
  sessions alike and tracked real actuation in every observed case — the genuine feedback
  channel. Not yet certified as an actuation *gate* (no observed negative). The state-char
  readback remains a write-through echo — never proof.
- **Kept open:** does a truly deep-asleep unit need any arming, or just waking? And the 1003
  heartbeat WAS needed for cooler/camping (issue #2) — those loads may differ; not retested.

## 2026-08-16 — lighting PHYSICAL actuation CRACKED (REQUEST_CONFIG preamble, photon-verified) + water guard unwedged

> **SUPERSEDED (mechanism only) by the 2026-08-16 evening entry above.** The photon-verified
> actuation, brightness enum, lamp map and `1502` feedback findings stand; the "only in a
> session where that preamble landed…" causal claim was a wake-state confound.

- **Lighting SET — RESOLVED FOR REAL, photon-verified** (finally closes `re-gap` §A1; supersedes
  every earlier readback-based "verified" claim — the `1502` state char is a **write-through
  echo**, owner-confirmed 2026-07-18 that the lamps had stayed dark). An iOS HCI capture of the
  app *physically* lighting a lamp, diffed via `tools/capture_diff.py`, found the one frame the
  app sends that we never did: a **REQUEST_CONFIG preamble** `0d0c000000000000eeeeeeeeeeeeeeee`
  (Mode=12, ProfileNumber=13, all zones=14) + the `0e00…` commit, written to `1501` on opening
  the Lighting screen. Only in a session where that preamble landed does a later SET_BRIGHTNESS
  drive the lamps. **Photon-verified on Kochen L7 from buspi: 0→dark, 8→80 %, 0→dark
  (owner-watched).** Implementation: `control.LIGHT_REQUEST_CONFIG` + `control.preamble_for` →
  `device.actuate(..., pre=…)`; requirement `R_LIGHT_PREAMBLE` ← `T_LIGHT_PREAMBLE`. See
  `control-and-actuation.md` §4.
- **Genuine feedback channel found:** after REQUEST_CONFIG the unit streams a Mode-tagged config
  dump + **Mode-4 brightness-ramp notifications on `1502`** (real brightness stepping to the
  target) — the first truthful actuation feedback. Notification layout not yet decoded (open RE
  task). The state-char *readback* remains an echo — never proof.
- **Brightness enum fix (same capture):** values are the `dg/i.java` enum, not a raw 0-13 scale —
  0=OFF, 1-10 = 10–100 % in 10 % steps, 11=DEFAULT, 12 unused, 13=NOT_EQUIPPED (read-only),
  14=leave-unchanged sentinel. Settable range 0-11; "power on" = 10. Our old code wrote 13 as max
  — NOT_EQUIPPED garbage.
- **Lamp map:** L5 = Küche Ambientelicht (capture-confirmed), L7 = Kochen, L6 = Aufstelldach
  Leselicht (by elimination).
- **Water stale-guard unwedged** (`freshness.implausible_water_drop`): the latch signature is a
  fresh drop while grey is **EXACTLY frozen**; any grey movement (rise or fall) is a live
  measurement. The old `<=` comparison wedged the hold for a month after a real grey dump (every
  post-dump reading re-latched because grey sat below the pre-dump baseline) — fixed to `==`,
  and a held value now flags **both** tanks stale. See `value-freshness.md`.

## 2026-07-08 — HCI capture (owner's phone ↔ van): lighting CRACKED + live-verified

> **SUPERSEDED 2026-08-16 (lighting bullets only).** The "live-verified" and the
> profile-activation RECIPE below were **readback-based** — the `1502` state char echoes what was
> written, so readback always "confirms" while the physical lamps stay dark (owner-confirmed
> 2026-07-18). The "profile must be ACTIVE first" rule was that echo bug, not a unit rule; the
> real frame rule is ProfileNumber hardcoded 9 (`dg/h.java:170`), and physical actuation needs
> only an **awake unit** — bare SET + commit suffices (2026-08-16 evening entry above; the
> interim "REQUEST_CONFIG preamble required" theory was a wake-state confound). The wire facts
> (14-sentinel, Mode 16 profile switch,
> byte-identical frames) and the air-heater/roof findings stand.

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
  values + roof's ~1 Hz move-heartbeat documented. `set` not wired (roof IS installed — live
  `Installed=1`, #106 — but the motor has never been driven; roofAC/stairs/LR-heater not
  installed here). Enum semantics still UNVERIFIED.
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
`dg/a` recovered them, and the (private) decompile pipeline now passes that flag. **Dictionary is 13 functions, 0
unresolved, all with state fields.** (Full audit: `status-states-audit.md`.)
