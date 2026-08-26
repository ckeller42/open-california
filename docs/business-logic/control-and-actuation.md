# Control & actuation — the write gate, the frame model, per-feature status

How `calictl` turns a `set` into an on-device actuation: the arm gate that was blocking
writes (**SOLVED**), the full-packet frame the app puts on the wire, and which features
actuate for real today. Dead-end hypotheses ruled out along the way are in
[`DECISIONS.md`](DECISIONS.md).

All `file:line` citations are under the JADX decompile
(`…/scratchpad/decompile/src/sources` = CLEAN, `…/decompile/bad/sources` = method bodies
jadx dropped from the clean pass). VW material: citations only.

---

## 1. The gate — a 1003 liveness heartbeat arms actuation (SOLVED 2026-07-07)

Control writes were ACKed at ATT yet silently ignored. The missing precondition is a
**liveness heartbeat on characteristic `00001003`** — the write-only "Counter" char
(`ag/b.java`, 32-bit; `status-states-audit.md:123`). While a monotonic **+1 four-byte
big-endian counter ticks on `1003`** (~0.6 s cadence), the unit **honours actuation
writes** to the normal control chars. Static analysis missed it because `1003` carries no
debug log and the write path never references it — it is a generic liveness signal, not a
per-command repeat.

**Confirmed on-device**, phone app **closed**, buspi the sole controller:
- With the heartbeat running, `cooler.on` goes **False → True** on a `State=1` write to
  `1101` (True → False on `State=0`). Proof: `scratchpad/heartbeat_actuate.py`, then the
  productionized `calictl set cooler power on|off` (frames `3d4300000000` / `3c4300000000`).

**Arm behaviour** (`scratchpad/sustain_test.py`):
- **One-shot arm, NOT a deadman.** After actuation the load **latches**: cooler stayed ON
  for a full 120 s window with the heartbeat **stopped and the client disconnected**.
  Actuation is a bounded operation — the heartbeat only needs to span the write window, not
  run continuously. (This is why `serve` did not need a persistent-connection redesign.)
- **No release frame needed.** A single `State` write latches both directions; the app's
  assert-then-`State=3`-release pattern is unnecessary (and would collide with the curated
  `CONTROL_RANGES` `State∈{0,1}`, so it is deliberately not used). See §3 for why the app's
  500 ms follow-up is a one-shot release, not a keep-alive.
- **Cadence:** 0.6 s proven (~10 beats span the ~3 s arm delay + write). Max-gap tolerance
  was not pinned down (immaterial for a short one-shot window); the firmware disarm timeout
  is still an open capture item (`re-gap-inventory.md` §A4).

**Where it lives in `calictl`:** `device.HEARTBEAT_CHAR` + `device.actuate()` — one BLE
session under the `serve` lock: arm handshake (§2) → subscribe-all → heartbeat task →
control write → readback. `calictl set` and `serve.on_command` both route through it.

---

## 2. The connect handshake (still required before any write)

The firmware only treats a session as `CONNECTED` after a fixed handshake; control writes
are effective only from that state. There is **no** magic enable-write, challenge/response,
or token — the `setUpRemoteControl_*` strings are UI wizard resource text
(`v9/e.java:37`, `xp/g.java`), never a BLE payload.

```
connectGatt(LE) → discoverServices → requestMtu
   → READ  00001001 (version)                          [service 00001000]
   → READ  00001004 (AUTHENTICATED read, 60 s timeout) [service 00001000]  ← forces bonding
   → subscribe (CCCD/2902 enable) + read EVERY status char
   → state = CONNECTED  → only now are control writes effective
```

- **Authenticated read of `00001004`** (`pf/g.java:52`, 60000 ms timeout): an
  encryption-gated char whose read forces BLE bonding/encryption. On first-ever run it
  triggers the OS pairing dialog, the link drops, and the app reconnects to find the bond
  established (`pf/g.java:63-91`, logs *"Try to reconnect in the hope that the pairing was
  already successful"*). Its **value is not a token** — only its length (empty vs non-empty)
  is checked, then discarded (see `DECISIONS.md`, "1004 not a session token").
- **Subscribe-all**: `CONNECTED` requires a CCCD (descriptor `00002902`) enable on **every
  notifiable status char**. There are exactly **12** on the device —
  `1004, 1102, 1202, 1302, 1402, 1502, 1602, 1702, 1802, 1902, 2002, 2102` — and `calictl`
  subscribes all 12 (`bad/sources/h1/n3.java:994-1058` case 20; success-count == group-count
  → logs *"All control units subscribed: CONNECTED"*). Each subscribed char is a control
  unit's **status** char, distinct from its control/write char.
- **Write type = `WRITE_TYPE_DEFAULT` (2) = write WITH response** (`bad/sources/s/a1.java:455,458`;
  `qd/q.java:52`). A char whose only write property is Write (0x08, not WriteNoResponse 0x04)
  silently drops a no-response write while the local stack still returns success.
- **Bonded/encrypted link** — implied by the auth read; Android auto-initiates bonding at
  the encryption-required char (no explicit `createBond`). **MTU** requested = 26
  (`qd/g.java:55`); payloads are tiny, not a blocker. **Transport** forced to LE. All GATT
  ops run through one **serialized queue** (`s.a1`) — one outstanding op at a time.

**Lifecycle citations:** callback `qd/f.java`; connect op `jb/b.java` case 24 `t()`
(`:283-378`, enqueues `discoverServices`+`requestMtu` at `:352-370`); GATT executor
`bad/sources/s/a1.java:409-548`; version check `defpackage/u1.java:549-553`; handshake
`pf/g.java`, `pf/k.java:143`; per-group subscribe+read `jb/b.java:100-158` case 18; op-param
classes `qd/{q,l,m,p}.java`.

---

## 3. The frame model — full-packet, no wrapper

Every action setter does `field.o(value); A()/B(); y(true)`. `m2.a.y(boolean z11)`
(`m2/a.java:152-170`, base of every control model) cancels any pending timer, reads the
**whole** bit array `f()` from all slots, enqueues one write to the control char `n()`, and
— if `z11` — arms a **one-shot** 500 ms timer. The queued write flows
`qd.g.b` (`qd/g.java:86-95`) → `h1.n3` case 25 (`h1/n3.java:90-109`, `byte[]` via
`tt.u8.e`) → `qd.q` (writeType 2, 5 s response timeout via `td/d.java:30`).

**Packing / byte order (state this once):**
- `tt.u8.e(Boolean[])` (`tt/u8.java:115-126`) packs **MSB-first within each byte**, into
  `ceil(nbits/8)` bytes. Multi-byte fields are **little-endian** across bytes
  (`tt/u8.java:88 c()`). Inverse for incoming state = `u8.d` (`tt/u8.java:102-113`).
- **No wrapper:** no header, length prefix, sequence/counter, CRC, or trailer. On-wire bytes
  == exactly the field bits of `f()`.
- **Full packet:** resend **every** field. Untouched fields carry a **sentinel
  "leave-unchanged" default**, not 0 — fridge ctor `sf/a.java:275-284`:
  `e0..h0=3, i0=7, j0=7, k0=30, l0=62, m0=31, n0=31`. `v()` (`sf/a.java:242-269`) resets all
  slots to those sentinels. Zeroing sibling fields yields a different frame that can be
  rejected or that stomps `State→0` ("nothing happens").
- **Frame lengths:** fridge/heater 48 bits → **6 bytes**; camping 8 bits → **1 byte**
  (`tf/a.java`; `lg/a.java` is the R8-merged roof-AC branch, not camping); lighting 128 bits →
  **16 bytes**. Target char is the per-feature **control** UUID `n()` (fridge `00001101`
  `sf/a.java:274`, camping `00001201`), distinct from its status/read char.
  (`overrides.CONTROL_FRAME_BYTES`: cooler/airheater 6, campingmode 1, lighting 16, roof 5.)

**Cadence — one-shot release, only roof repeats.** The `jn.a` timer's 3rd ctor arg is the
**repeat flag**:
- Control release: `jn.a(500L, …, false, false)` → **one-shot** (the general case). Its
  runnable (`b1/d.java:234-245`, case 8) does **not** resend the command — it calls `v()`
  (reset all slots to sentinel) then writes **one** idle/release frame. It is a plain delay,
  not gated on any read-back, and **not needed** for cooler/camping (sentinels mean
  "don't change", so it never undoes a persisted setpoint).
- Roof movement keep-alive: the PRIMARY frame pump is the **~500 ms SafetyCounter timer**
  (`w8/a`, +1/frame, value `seed + floor(elapsed_ms/500)`, `b1/d.java:352`), NOT the 1000 ms
  timer. The `ig/c.java:674` / `:764` `jn.a(1000L, …, true, false)` timer also repeats but only
  **re-affirms direction** — it doesn't touch the counter. Net ~3 frames/s, counter deltas 0/+1,
  never +2. See §3 (Roof) + `protocol-alignment.md`. Other repeating timers: `dg/d.java:146`
  `jn.a(100L,…,true,true)`, `dg/d.java:164` `jn.a(1000L,…,true,true)`, `pf/k.java:457`
  `jn.a(30000L,…,true,false)`.

**Acknowledgement.** The app relies on the **ATT write response** (`onCharacteristicWrite`,
5 s), not an app-level ack. State updates arrive independently as **notifications** on the
status char (`u8.d`). `calictl` additionally reads the status char back after a write to
report OK / NOT APPLIED.

**Firmware range-validation.** The unit parses and content-validates writes and **drops the
ATT link** on out-of-range values (cooler `State=3` and lighting `ProfileNumber=14`+`Mode=4`
both → `0x0E`/disconnect). The app does no client-side clamp; validation is firmware-side.
Mitigation: `protocol.encode` validates every value against its bit-width **and** a curated
semantic range (`overrides.CONTROL_RANGES`); `python3 -m tools.app_ranges` reports coverage.

---

## 4. Per-feature actuation status (heartbeat-armed, live 2026-07-07)

| Feature | `set` wired | On-device | Notes |
|---|---|---|---|
| **cooler** | `power` on/off, `level` 1-5, `mode`, `night_on`/`night_off` 0-23, `night_set`, timers | ✅ actuates (schedule live 2026-08-26) | State 0↔1, Level applied. Night schedule + Mode=4 stored on the unit (survive reconnects) and **broadcast on 1102** per change. ⚠️ Hour bytes are LITERAL — every write must carry the current schedule (`_cooler_values` does; hard-coded 0s used to clobber it). `night_set` write accepted but the `NightTimerSet` state bit never flipped (suspected edge-triggered at window entry — observer watching). |
| **campingmode** | `master`/`lights`/`usb` on/off | ✅ actuates **when stationary** | usb_charger toggled live; 1-byte inverted/combined model (see `signals.md`). **REFUSED while driving** — see the stationary gate below. |
| **lighting** | `power`, per-zone `brightness` 0-11, `profile` | ✅ actuates (2026-08-16) | Bare SET + commit is enough once the unit is awake — see below. |
| **airheater** | `power`, `level` 1-10 | untested | Installed; frame fixed (`re-gap-inventory.md` §A3); buspi was offline. |
| **roof** | not wired | — | Needs the ~500 ms SafetyCounter move loop (§3); not installed here. |
| roofAC / stairs / LR-heater | not wired | — | Not installed; offsets derivable, enum semantics UNVERIFIED. |

**Camping mode — a firmware STATIONARY GATE (live-verified 2026-08-19).** Turning camping mode
**on is refused by the unit while the vehicle is being driven** — a write of `campingmode master=on`
with the engine running does NOT take: the daemon's actuation returns `applied:false` (its verify
reads the state unchanged), no genuine `1202` push reports a change, and the **unit's own console
display pops _"Diese Funktion ist während der Fahrt nicht verfügbar"_** ("not available while
driving"). This is the decompile's `dialog_info_campingMode_onlyPossibleWhenStationary` gate,
enforced **unit-side** (not just an app UI block). Conversely, an engine start **sheds** camping —
`master_on`→0 and `lights_on`→0 (and the rear USB with them, since USB is master-gated — see
`signals.md`) — **cleanly, once, no flip-flop** (captured on both the 30 s poll and the genuine
`1202` push channel, which is hereby **confirmed on real hardware**: the unit pushes camping state
on `1202` and ignition on `1004`, exactly as the app subscribes). Implication for the `auto-camper`
feature: re-enabling camping *during* a drive is impossible; the only viable moment is once the
vehicle is **stationary again**. The exact "stationary" predicate (Terminal-15 vs road-speed vs
parking-brake) is under decompile review; see `auto-camper-mode.md`. NB the BLE link also **drops
for ~1 min at the engine crank** — buspi cannot reach the unit right at the start edge.

**Lighting — SOLVED 2026-08-16: physical actuation works; the real gate is the unit's
WAKE/active state, not any arming frame (photon-verified).** The gap's long history, kept
because it is instructive RE:
- **2026-07-07:** SET_BRIGHTNESS (`Mode=4`) ACKed at ATT (no `0x0E`) but nothing changed under
  the identical heartbeat that actuates cooler/camping — a lighting-specific gap. The
  ProfileNumber-echo theory (`dg/h.java:615`) was **disproved live 2026-07-08**
  (`scratchpad/profile_sweep.py`: every ProfileNumber 0–14 ACKed, no change).
- **2026-07-08/13 captures:** our frames were **byte-identical to the app's** (14 = per-zone
  leave-unchanged sentinel; `ProfileNumber` hardcoded 9 like `dg/h.java:170` `w(9)`); the
  `0e00…` neutral follow (`control.LIGHT_COMMIT`) flushes a single write. Readback then
  "confirmed" the change — but **the `1502` state char is a write-through echo** (owner-checked
  2026-07-18: lamps stayed dark). Every readback-based "verified" claim was worthless; the
  physical load never switched. Ruled out: persistent session, camping-mode/ignition/battery
  gates, "profile must be active first" (that was the echo bug too).
- **2026-08-16 morning — the (over-stated) crack.** An iOS HCI capture (idevicebtlogger on
  the bar Mac) of the app *physically* lighting a lamp, decoded with calictl's own
  `control.decode_control` and diffed against `control.build` (`tools/capture_diff.py`, the
  capture-and-diff flow), showed one thing the app sends that we never did: **on opening its
  Lighting screen it writes a REQUEST_CONFIG frame** — `0d0c000000000000eeeeeeeeeeeeeeee`
  (Mode=12, ProfileNumber=13, all zones=14) — followed by the usual `0e00…` commit, to
  control char `1501`. That morning, adding the preamble made SETs actuate where the same
  un-armed path had failed hours earlier, so we concluded the preamble was the required
  arming step. **That necessity claim was falsified the same evening (below).**
- **Photon verification (2026-08-16, buspi, owner at the van):** Kochen lamp (zone L7) driven
  0 → physically dark, 8 → physically 80 %, 0 → dark again. Human-observed each transition —
  the only success criterion that counts for lighting.
- **2026-08-16 evening — the CORRECTION: the preamble is NOT the gate; wake-state is.** Six
  further photon-confirmed actuations, ending in three controlled fresh-connection trials
  varying ONLY the arming (unit awake, phone off, daemon stopped; owner watched "off, on,
  off"): **Trial 1 — NO 1003 heartbeat, NO preamble, ZERO settle, immediate SET + `0e00…`
  commit on a cold connection → the lamp switched.** So for an awake unit, NONE of
  {REQUEST_CONFIG preamble, 1003 heartbeat, 3 s arm delay} is required — a **bare
  SET_BRIGHTNESS + commit is sufficient, both directions**. The decompile agrees: the app's
  set-one-zone method `dg/h.java:174 E()` stages ProfileNumber=9 + Mode=4 and writes DIRECT
  (immediately) — it does not call the config request `d0()` (`dg/h.java:471`), no heartbeat,
  no delay (`ag/b.java` is a generic connection-liveness counter separate from the write
  path). The morning result was a confound: the unit was freshly woken/sleepy, and the
  preamble path added ~3.3 s of delay + extra frames that gave it time to become ready — the
  preamble *correlated* with success, it didn't cause it. **The real determinant is the
  unit's wake/active state.** Still open: whether a truly deep-asleep unit needs any arming
  at all, or just needs waking. NB: the 1003 heartbeat WAS needed for cooler/camping
  actuation (issue #2) — either those loads differ or the unit was in a lower-readiness
  state then; not retested. **REQUEST_CONFIG's real role is a screen-open config pull** — it
  triggers the unit's config-dump notifications on `1502`; only that dump is gated on it,
  not actuation.
- **Truthful feedback channel:** after REQUEST_CONFIG the unit streams a **config dump
  as notifications on `1502`**, frames tagged by Mode: `0x0c` config echo, `0x06` color state,
  `0x08` SET_DOUBLE, `0x10` profile state (profile 8), `0x14` wake time, `0x18` system time
  (ticking unix seconds). After an *applied* SET_BRIGHTNESS it notifies **Mode-4 "ramp" frames
  showing the REAL current brightness stepping to the target** (e.g. 01→03→04→05). A Mode-4
  notification is a **normal `1502` STATE frame — `protocol.decode` reads it** — and it
  tracked real actuation in every observed case (armed and un-armed sessions alike): the
  app's genuine actuation-feedback channel. The state-char **readback remains a
  write-through echo and is still not proof of actuation.**
- **Brightness enum fix (same capture):** values are the `dg/i.java` enum, NOT a raw 0-13
  scale — 0=OFF, 1-10 = 10 %…100 % in 10 % steps, 11=DEFAULT, 12 unused, 13=NOT_EQUIPPED
  (read-only marker for absent zones), 14=leave-unchanged sentinel. Our old code wrote 13 as
  "on"/max — NOT_EQUIPPED garbage. Fixed: `LIGHT_ON_BRIGHTNESS=10`, settable range 0-11, GUI
  slider max 10. (Capture-confirmed: 50 % app slider = wire `5`.)
- **Where it lives:** `control.LIGHT_REQUEST_CONFIG` (the frame);
  `control.preamble_for(function)` returns `[LIGHT_REQUEST_CONFIG, LIGHT_COMMIT]` for
  lighting; `device.actuate(..., pre=preamble_for(fn))` writes them before the SET with
  `FOLLOW_DELAY_S` (0.3 s) gaps, then a `PRE_SETTLE_S` (3.0 s, env `CALICTL_PRE_SETTLE_S`) arm
  wait. All three call sites (serve persistent + cold, cli) pass it. Requirement
  `R_LIGHT_PREAMBLE` (`control.preamble_for` docstring) ← `T_LIGHT_PREAMBLE`
  (`tests/test_mock_integration.py`). **Since 2026-08-16 evening this is known to be
  app-faithful-but-optional** (the app sends it on screen open via `d0()`, not per write): it
  costs ~3.3 s and is harmless, so the shipped code keeps it — but it is not what makes the
  lamps switch.
- **Still open:** SET_COLOR on-device apply — the app DOES have colour control (`dg/h.java:644`,
  a profile-recolour: Mode 6, LightValue=colour, ProfileNumber=target profile + its brightness),
  but our `set lighting color` frame is mis-shaped vs the app's (PN=9 + sentinel zones) so it
  likely won't actuate as built; whether a deep-asleep unit needs any arming at all; pinning the
  exact wake-state determinant with controlled trials (awake duration, parked vs active).

**Lighting frame layout** (16 bytes / 128 bits): `ProfileNumber@4/w4`, `Mode@8/w8`
(4=SET_BRIGHTNESS, 16=SET_PROFILE), `Timestamp@16/w32` (`sg.a()` no-arg, default 0 — part of
the packet, not a validated clock), `LightValue@48/w16`, 16×`BrightnessL*@64-127/w4`. Command
enum `dg/n.java:34-50` (SET_BRIGHTNESS=4, SET_COLOR=6, SET_DOUBLE=8, REQUEST_CONFIG=12,
SET_PROFILE=16, WAKEUP_TIME=20, SYSTEM_TIME=24, PREVIEW=28).

Extend actuation via `control.BUILDERS`.

---

## 5. Control command surface (`control.BUILDERS`, updated 2026-08-17)

Every command below is a full-packet write on the function's control char. Those marked
**DV** are decompile-verified (frame byte-checked against the app's own setter) but **not yet
live-verified on the van** — the web UI guards each with a "not verified" confirm.

| Function | `what` | Effect / field | Source | Status |
|---|---|---|---|---|
| cooler | `power` / `level` | State / Level 1-5 | `vf/c` U0/X1 | live-verified |
| cooler | `mode` | quiet Mode 0=normal/2=quiet/4=timer | `vf/c` T1/x0/k0 | 4=timer_quiet **live (08-26)**; 0/2 DV |
| cooler | `night_on` / `night_off` | NightTimerHourOn/Off (0-23) | `vf/c` c0/Y2 | **live (08-26)** — stored + 1102-broadcast; bytes LITERAL (carry current) |
| cooler | `night_set` | NightTimerSet 1/0 (arm/disarm schedule) | `vf/c` X1 | write accepted, state bit unmoved — arm semantics OPEN |
| cooler | `timer_set` | TimerHour:TimerMin (HH:MM) | `vf/c` y0 | DV |
| cooler | `timer_start` / `timer_cancel` | TimerStart / TimerCancel = 1 | `vf/c` D/X0 | DV |
| airheater | `power` / `level` | NormalOperationRequest 1/0 / HeatingLevel | `rf/b` C2/q4 | live (power capture 07-08) |
| airheater | `runtime` | RunningTime (min) | `rf/b` D4 | DV |
| airheater | `timer` | TimerHour:TimerMin (HH:MM) | `rf/b` B0 | DV |
| energy | `mode` | EnergyModeSet 0=normal/1=max_charge/2=eco | `xf/d`:389 | DV |
| lighting | `power` / zone / `all` | SET_PROFILE 12/0 · per-zone SET_BRIGHTNESS | `dg/h` Q/E | live (photon 08-16) |
| lighting | `profile` | SET_PROFILE, ProfileNumber (Fav 1-7, 10 wake, 11 interior) | `dg/h` u0 | DV (activate) |
| lighting | `save_profile` | SET_BRIGHTNESS w/ ProfileNumber=N + all zones (define a favorite) | `dg/h` l3 | DV |
| campingmode | `master`/`lights`/`usb` | State / lights (inverted) / UsbCharger | `tf/a` | live-verified (on **only when stationary** — §4 gate) |
| roof | `open`/`close`/`stop` | Up/Down + app-gen SafetyCounter | `ig/c` | not-live-verified |

**Not wired (deliberately):** airheater `permanent` (the app's `E3()` only writes OFF; ON value
unknown — won't arm a fuel burner on a guess), lighting wake-up **time** (`m0`, Mode 20 — the
LightValue bitmask packing is unverified; needs a capture), lighting `color` (frame mis-shaped +
not on this variant).

**State readback surfaced** (decoded straight from the state chars, cross-checked against the
app decoders): cooler `quiet_from`/`quiet_to`/`timer_hour`/`timer_min` (`vf/c.java:321 e()`,
bit-exact `NightTimerHourOn@48`/`NightTimerHourOff@56`); energy `energy_mode` (`bf/c.java`).

---

## Key files

- Callback / lifecycle: `qd/f.java`, `jb/b.java` (cases 18, 24)
- Connection manager: `qd/g.java`
- Op queue + executor: `s/a1.java` + `bad/sources/s/a1.java:409-548`
- Op→GATT translation: `h1/n3.java` case 25 (`:50-139`, frame build `:90-109`) and `bad/sources/h1/n3.java:994-1058` case 20
- Handshake: `defpackage/u1.java:549-553`, `pf/g.java`, `pf/k.java`
- Frame model: `m2/a.java`, `tt/u8.java`, `sf/a.java`, `sf/b.java`, `tf/a.java`, `lg/a.java`
- Release timer: `b1/d.java:234-245`; roof heartbeat `ig/c.java:674,764`
- Op params: `qd/{q,l,m,p}.java`, `td/{a,b,c,d}.java`
- 1003 counter: `ag/b.java`, written app-side `t0/c.java:264`
