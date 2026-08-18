# Feature Availability — How CaliforniaOnTour Decides Which Functions a Camper Unit Has

Reverse-engineered from the decompiled VW CaliforniaOnTour app (JADX output). Goal: give a
Home Assistant integration a concrete, reproducible rule for "does *this* vehicle have function
X" — so the integration only creates entities for hardware that's actually fitted, instead of a
fixed list for "a California".

Source root (all `file:line` below are relative to this):
`.../scratchpad/decompile/src/sources`

This document is the cross-feature roll-up. Per-feature bit-level protocol detail (control
frames, command recipes, enums) already lives in the sibling docs in this folder
(`climate-stairs.md`, `cooler-airheater.md`, `lighting-energy-water-sat-roof.md`) and is not
repeated here except where directly relevant to the availability question.

## TL;DR

There is **no central "capabilities" blob, no `REQUEST_CONFIG` command, and no static
VIN/model → feature table** anywhere in the app. Instead:

1. Every accessory function (Cooler, Stairs, RoofAirCondition, Water, SatelliteAntenna,
   LivingRoomHeater, AirHeater, …) has its **own BLE GATT characteristic** that the vehicle's
   control units push notifications on. Buried in that same bit-packed payload — right next to
   `State`/`Mode`/`Error`/etc. — is a 1-bit **`Installed`** flag.
2. Every feature's Kotlin/Compose ViewModel implements a common interface, `te.k`, whose single
   method `g1 b4()` (`te/k.java:8`) exposes a `StateFlow<Boolean>`. For every function checked
   below **except two noted exceptions**, `b4()` is fed directly from the decoded `Installed` bit,
   and nothing else.
3. Screen/dashboard ViewModels read each function's `b4()` flow to decide what to render —
   confirmed by `tj/f.java:416` (`this.l0 = xVar.f31147j0;`, pulling Stairs' `b4()` StateFlow
   straight out of the `yg.x` wrapper) and by every per-feature action interface extending `te.k`
   (`lf/a`, `jf/a`, `cf/b`, `kf/c`, `ff/a`, `we/b`/`df/a`, `af/a` all `extends te.k`).
4. A separate, always-first characteristic (service `0x1000`, class `zf/d.java`, "General"/Vehicle
   info) reports firmware version, VIN, and a `CarVariant` field that decodes to a
   `CALIFORNIA_6_1` / `CALIFORNIA_7` / `GRAND_CALIFORNIA` enum (`te/a.java`). This is a **secondary,
   much weaker signal** — in the files reviewed it is read but the app-wide flag it could feed
   (`pf/k.java:96`, `f21190d0`) is a hardcoded default that is never observed being written from
   the live `CarVariant` decode, and the one place that reads it (AirHeater's
   Grand-California branch, `rf/b.java:141`) is marked `UNVERIFIED` for effect in the sibling doc
   `cooler-airheater.md:261-267`. **Do not use vehicle model/variant as your gating signal —
   use the per-function `Installed` bit.**

**Recommended rule for the HA integration**: for each function, subscribe to its state
characteristic (you have to anyway, to get live values) and read the `Installed` bit given in the
table below on every notification (or at least the first one after connect). Only create/enable
the corresponding HA entities while `Installed == true`. Treat characteristics with no
`Installed` bit (see "Always-on functions" below) as unconditionally present.

## Part 1 — The mechanism: `Installed` bit → `te.k.b4()`

Common shape, seen identically in every confirmed function below (example is Stairs,
`og/b.java`):

```java
// og/b.java:153-184 (abbreviated)
public final void e(String str, Boolean[] boolArr) {
    ...
    if (str.equalsIgnoreCase("00001802-6C77-4B7D-BBF6-A5E587701F3D")) {
        if (boolArr.length >= 8) {
            aVar5.p(...boolArr.subList(7, 8)...);   // <- Installed bit lands here
            ...
        }
        ...
        StringBuilder sbK = ou.a.k("<-- Incoming Data for Stairs:  Installed: ", obj, ...);
        ...
    }
    Object obj6 = this.f20517e0.f398b;   // f20517e0 aliases the same Installed sg.a box
    i1 i1Var = this.f20525n0;
    i1Var.l(obj6);                       // <- written straight into the b4() StateFlow
    ...
}

@Override // te.k
public final g1 b4() { return this.f20525n0; }
```

Every function's decode routine is named `e(String uuid, Boolean[] boolArr)` (from base class
`pf/n.java:26`); `boolArr` is the characteristic payload unpacked one `Boolean` per bit, index 0 =
first bit of byte 0. The `Installed` field's index is given as the literal Java
`subList(from, to)` range so it's directly reproducible against your own captured payload.

### Table: per-function `Installed` bit (confirmed data-driven)

| Function | Nav screen | Service/char UUID | `Installed` bit (boolArr index) | Frame field order (for context) | `b4()` wiring citation |
|---|---|---|---|---|---|
| **Cooler** (fridge) | `CoolerScreen` | `00001100`/`00001102` | index `[4,5)` (byte0) | State,TimerState,TimerElapsed,**Installed**,NightTimerSet,Error(2b),Level(4b),Mode(4b),... | `vf/c.java:138` (`l0`), `vf/c.java:392-393` (`cVar.l0.f398b` → `cVar.B0`), returned at `vf/c.java:300` |
| **Stairs** (electric steps) | `StairsScreen` | `00001800`/`00001802` | index `[7,8)` (byte0) | **Installed**,OperationMode,State,Sensor,InfoPopUp(2b) | `og/b.java:164` (decode), `og/b.java:181-183` (wiring), returned at `og/b.java:149` |
| **RoofAirCondition** | `RoofAirConditionScreen` | `00002000`/`00002002` | index `[6,7)` (byte0) | State,**Installed**,Mode(2b),Error(2b),Fanspeed(4b),Temperature(8b) | `kg/b.java:96,105` (`f15679f0`→`f15687o0`), `kg/b.java:215-217` (wiring), returned at `kg/b.java:178` |
| **Water** (fresh+waste, one unit) | `VehicleData`/water widget | `00001300`/`00001302` | index `[6,7)` (byte0) | FreshWaterUnit,**Installed**,FreshWaterInfoPopUp(4b),FreshWaterLevel(8b),FreshWaterVolume(8b),WasteWater... | `qg/b.java:117,129` (`f22310e0`→`f22321q0`), `qg/b.java:230` (wiring), returned at `qg/b.java:184` |
| **SatelliteAntenna** | `SatelliteAntennaScreen` | `00001900`/`00001902` | index `[7,8)` (byte0) | **Installed**,System(2b),Error(4b),Dish(4b),SatelliteSelection(4b),SignalLevel(8b) | `mg/f.java:142,161` (`l0`→`E0`), `mg/f.java:384-386` (wiring), returned at `mg/f.java:303` |
| **LivingRoomHeater** | `LivingRoomHeaterScreen` | `00002100`/`00002102` | index `[4,5)` (byte0) | StateAir,StateWater,TemperatureWater,**Installed**,Variant(2b),Mode(4b),Error(4b),TemperatureAir(8b) | `fg/b.java:122` (`f8229h0`→`f8237q0`), `fg/b.java:262-263` (wiring), returned at `fg/b.java:213` |
| **AirHeater** (parking heater) | `AirHeaterScreen` | `00001700`/`00001702` | index `[3,4)` (byte0) | AirDistribution(2b),**Installed**,FaultTriggerBit,...,HeatingLevel(4b),ErrorCode(4b),... | `rf/b.java:124,138` (`f23029g0`→`f23042u0`), `rf/b.java:408-409` (wiring), returned at `rf/b.java:322` |

Notes on the table:

- **Bit index convention**: the index is the literal argument to `.subList(from, to)` on the
  bit-unpacked payload, taken verbatim from source — reproduce the same unpacking (MSB-first
  within each byte, byte 0 first) and read the same index.
- **`Variant`** field seen on LivingRoomHeater (2-bit, `fg/b.java:254`) is a sub-model selector
  for that one heater (which physical heater unit is fitted), not a feature-presence flag — it
  doesn't gate `b4()`. Treat it as informational/cosmetic (icon/label selection), same status as
  `UNVERIFIED` in `climate-stairs.md`.
- Water's single `Installed` bit gates **both** the fresh-water and waste-water sub-panels —
  `qg/b.java` is instantiated once in `pf/k.java` and exposed through two different interfaces
  (`cf.b` for fresh, `nf.a` for waste) that share the same underlying `Installed`/`b4()` flow
  (`pf/k.java`: `this.f21208w0 = bVar4; this.f21210x0 = bVar4;`).

## Part 2 — Exceptions and edge cases (read before you build the HA rule)

### 2a. Campingmode — `Installed` is decoded but silently ignored

`tf/a.java`, UUID `00001200`/`00001202`. The frame **does** contain an `Installed` bit
(`tf/a.java:177`, index `[2,3)` — see field order `State,UsbCharger,OutsideLight,InteriorLight,
Enable,Installed`), and it's logged just like every other function
(`tf/a.java:174-178`). But the `b4()`-backing field is initialized once, hardcoded, and **never
updated** from the decode:

```java
// tf/a.java:91
this.f25013p0 = p.b(Boolean.TRUE);
// tf/a.java:139-141
@Override public final g1 b4() { return this.f25013p0; }
// e() body (tf/a.java:147-181) never assigns f25013p0 — only logs `obj6` (the Installed bit)
```

So the app shows the Campingmode screen/tile **unconditionally**, even if the reported
`Installed` bit is `false`. **UNVERIFIED** whether this is a deliberate choice (Campingmode may be
a baseline feature on every unit that supports BLE at all) or a bug — but for interop purposes,
don't rely on Campingmode's `Installed` bit for gating; either always expose it, or gate it on
something else if you observe it staying `false` on units that clearly don't have it.

### 2b. Elevating (pop-top) roof — data-driven, but exact bit UNVERIFIED

`ig/c.java`, UUID `00001400` (`ig/c.java:113`), nav screen `RoofScreen` (distinct from
`RoofAirConditionScreen` — this is the physical lift-roof motor: `e1()` at `ig/c.java:239-264`
logs `"Blocked roof usage (close) since last usage was only ... ms ago"`). `b4()` is backed by
`f12437x0`, seeded from `f12420f0` (`ig/c.java:127`, `= bVar2.f14589c`), which is a genuine
decoded field (not a `Boolean.TRUE` literal like Campingmode) — but JADX failed to decompile the
`e()` method body for this class (`ig/c.java:230-236`, "Method not decompiled"), so the exact bit
index inside the `00001400`-derived characteristic is **UNVERIFIED**. Treat as "likely
`Installed`-gated, confirm bit position by live capture" — do not assume unconditional
availability the way you would for Campingmode.

### 2c. Energy/battery system — multiple sub-component `Installed` flags, no single top-level one

`xf/a.java` (abstract) / `xf/d.java` (concrete), UUID `00001600`/`00001602`. Unlike every other
function, there is no single `Installed` bit for the whole tile. Instead the one state frame
carries **four independent equipment-presence flags** for sub-widgets:

| Flag | boolArr index | Meaning |
|---|---|---|
| `EmpInstalled` | `[11,12)` | Energy Management Processor fitted |
| `PvInstalled` | `[10,11)` | Solar/PV panel fitted |
| `LadInstalled` | `[9,10)` | Shore/land power charger fitted |
| `DcdcInstalled` | `[8,9)` | DC-DC (battery-to-battery) converter fitted |

(`xf/a.java:182-198` field pickup, `xf/a.java:207-247` bit unpacking and log string; cross-checked
against `lighting-energy-water-sat-roof.md:216-225`.) The top-level Energy tile's own `b4()`
(`xf/d.java:140,352-353`, backed by `X0`) is hardcoded `Boolean.TRUE` at `xf/d.java:138-140` and
was not observed being updated from the decode in the code reviewed — i.e. **Energy/battery
monitoring itself is treated as a baseline always-present function**; only its four sub-widgets
(2nd battery/solar/shore-power/DC-DC) should be conditionally shown, keyed on the four flags
above.

### 2d. Always-on functions — no `Installed` bit at all

Two functions carry **no** `Installed` field whatsoever and should be treated as
unconditionally available on any unit that answers BLE at all:

- **General/Vehicle handshake** — `zf/d.java`, service `0x1000` (`zf/d.java:134`). Characteristics
  `1001` (firmware: `AmbSwVersion`/`CmSwVersion`/`CommunicationVersion`, `zf/d.java:232-243`),
  `1002` (opaque 128-bit = SHA-256(VIN)[16:32], NOT plaintext, `zf/d.java:247-252`), `1004` (`TerminalOneFive`, `CarLevelPopUp`,
  **`CarVariant`**, car clock, `CarLevelRoll`/`CarLevelPitch`, `zf/d.java:266-303`). `b4()` is
  hardcoded `Boolean.TRUE` (`zf/d.java:155,222-224`). This is the very first thing the app reads
  after connecting (constructed first in `pf/k.java`'s constructor, before any accessory
  function). **This is the closest thing to a "handshake"** the prompt hypothesized, but it
  carries version/VIN/clock/level data, not a feature bitmap.
- **LevelIndicator** (spirit-level / tilt display) is **not** a separate BLE characteristic — it's
  derived from the same `0x1004` frame above: `CarLevelRoll`/`CarLevelPitch` (exposed via
  `ye.a.R1()`/`ye.a.F3()`, `zf/d.java:196-204`) and `CarLevelPopUp` (`ye.a.t3()`/`ye.a.B4()`,
  `zf/d.java:160-161,363-366`). Since it rides on the always-true General handshake, treat
  LevelIndicator as always available too.

### 2e. `CarVariant` / vehicle model — parsed, weakly wired, not a gating signal

`zf/d.java:152-153,307-308` decodes the 4-bit `CarVariant` field and maps it to `te.a` enum
`CALIFORNIA_6_1(0)` / `CALIFORNIA_7(1)` / `GRAND_CALIFORNIA(2)` (`te/a.java:15-23`), stored in
`zf.d`'s own private `B0` field — which has **no public getter** on any of the three interfaces
`zf.d` implements (`ye/a.java`, `ze/a.java`, `mf/a.java` — checked, none expose it). Separately,
`pf/k.java:96` holds an app-wide `f21190d0` flow of the same `te.a` type, defaulted to
`CALIFORNIA_7` and never observed being written anywhere in the files reviewed. The one consumer,
`rf/b.java:141,414` (AirHeater), branches on `f21190d0.getValue() == te.a.X /* GRAND_CALIFORNIA */`
for a "combined operation" capability flag — already flagged `UNVERIFIED` for actual effect in
`cooler-airheater.md:261-267`. Separately, static bundled content (the Self-Check checklist JSON,
`bd/b.java:12`) keys pre-trip checklists by a user-facing vehicle string
(`GRAND_CALIFORNIA`/`CALIFORNIA_6_1`/`CALIFORNIA_7`/`CADDY_CALIFORNIA`) that most plausibly comes
from a **manually-selected vehicle profile at onboarding** (`NavigationOnboarding$MyVehicle`), not
the live BLE `CarVariant` byte — a different, static/account-side signal, again not a live
feature-presence gate.

**Conclusion for interop: model/variant is not a reliable or fully-wired feature-gating signal in
this codebase. Don't build your HA integration's capability detection around it.**

## Part 3 — Recommended detection rule for the HA integration

1. **Connect and always read the General/Vehicle service (`0x1000`) first** — it's unconditional,
   gives you firmware version + VIN for logging/diagnostics, and its `CarVariant` byte is worth
   surfacing as a diagnostic sensor even though it isn't a capability gate.
2. **Subscribe to every function's state characteristic unconditionally** — the vehicle's BLE
   gateway appears to expose a fixed superset of GATT services regardless of which accessories are
   actually fitted (this mirrors how the app itself operates: it doesn't skip service discovery
   for anything, it reads `Installed` per-function instead). You need the subscription anyway to
   get live values once you decide the feature is present.
3. **On (at least) the first notification per characteristic, read the `Installed` bit** at the
   index given in the Part 1 table. Gate entity creation/enablement on that bit for: Cooler,
   Stairs, RoofAirCondition, Water (fresh+waste together), SatelliteAntenna, LivingRoomHeater,
   AirHeater.
4. **For the elevating roof (`0x1400`)**, capture a live payload from a known-equipped and (if
   possible) known-unequipped unit to nail down the bit index empirically before trusting it as a
   gate; until then treat it as present-by-default with a note to re-verify.
5. **For Energy (`0x1600`/`0x1602`)**, always expose the base battery entities; gate the four
   sub-widgets (2nd battery presence is implied by `TwoBatt*` fields being non-trivial /
   `EmpInstalled`, solar/`PvInstalled`, shore-power/`LadInstalled`, DC-DC/`DcdcInstalled`)
   independently using the four bit positions in §2c.
6. **For Campingmode (`0x1200`)**, do not gate on its `Installed` bit — the app itself doesn't;
   expose it unconditionally (or flag for empirical re-check if you find units where it's clearly
   not applicable).
7. **Do not implement any VIN- or CarVariant-based static capability table** — there isn't one in
   the app to mirror, and the one live consumer of `CarVariant` is unverified/likely dead in the
   build analyzed.

## Open questions / further verification

- `ig/c.java` (elevating roof) `Installed` bit index — **UNVERIFIED**, JADX failed to decompile
  the decode method body; needs a live BLE capture to confirm.
- Whether `pf/k.java:96`'s `f21190d0` (app-wide `CarVariant` flow) is ever actually written at
  runtime (e.g. from a DI wiring not visible in these files, or from the account-side vehicle
  profile rather than the live BLE byte) — **UNVERIFIED**, no writer found in the reviewed source.
- Whether Campingmode's `Installed`-ignoring behavior (§2a) is intentional — **UNVERIFIED**.
- GATT-level service discovery (whether the vehicle's peripheral ever *omits* a service entirely
  for a truly-absent function, vs. always advertising every service and only flipping the
  in-payload `Installed` bit) was not traced in this pass — the low-level `BluetoothGatt` plumbing
  lives under `qd/*.java`, which is standard `BluetoothGattCallback` boilerplate; no explicit
  per-service conditional subscribe logic was found, consistent with "always subscribe, check
  `Installed` in-band" being the actual mechanism, but this is **UNVERIFIED** as an exhaustive
  claim.
