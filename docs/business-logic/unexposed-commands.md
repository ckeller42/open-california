# Hidden / unexposed API surface (decompile audit, 2026-08-20)

A three-angle sweep of the decompiled app for **hidden or debug API commands** — every BLE
characteristic + opcode vs our catalog, every control-frame field the UI never sets, and any
debug/service/engineering gate. Summary: **there is no debug backdoor**, but a handful of commands
are *declared in the frame schema and never sent by the app*. This is the reference for that.

## What is NOT there (so we stop looking)

- **No OTA/DFU/UART/SMP/DIS/factory service.** The app talks to exactly one vendor service
  (`…-6C77-4B7D-BBF6-A5E587701F3D`); the only standard BT UUID referenced anywhere is the CCCD
  `0x2902`. No Nordic DFU (`fe59`/`1530`), no Nordic UART, no mcumgr, no Device Information Service.
- **No debug build gate / feature flags / simulator.** No `BuildConfig.DEBUG`, no Timber, no
  RemoteConfig toggles, no mock/demo BLE. The only `Debug.*` use is an anti-tamper root check
  (`up/a.java`). A `DeveloperModule` nav route exists in the KMP sealed hierarchy but is **inert** —
  no composable, no navigation caller (registrar `fb/a.java` doesn't map it).
- **No raw/passthrough BLE write.** Every write goes through a typed per-function frame builder
  (`m2.a` → `f()`); there is no `writeCharacteristic(uuid, bytes)` exposed to the UI.
- **No fault-memory / error-log / bootloader / calibrate command.** Firmware version is a normal
  state read (`general` char `1001`: `AmbSwVersion`/`CmSwVersion`/`CommunicationVersion`).
- All **42** unit characteristics (service bases `1000`–`2100` + `F000`) are already in
  `dictionary.yaml`.

## Unexposed COMMANDS (declared in a control frame, never sent by the app)

Ranked by usefulness / confidence. **None are live-verified** — they are candidates, to be tested
one at a time with a human watching the unit (writes are actuations).

1. **Lighting `SYSTEM_TIME` (Mode 24)** — char `1501`. The `dg/n` LightingMode enum declares it
   (`dg/n.java:44`, `new n(24)`) but **no code path stages Mode=24**. The lighting frame carries a
   32-bit `Timestamp` field (`eg/a` `f7184f0` @bit16-47) that the *working* `WAKEUP_TIME(20)` path
   already uses (`dg/h.java:870`), so `SYSTEM_TIME` is almost certainly "push phone clock → unit
   RTC". The `vehicle`/1004 decode exposes a unit RTC (`CarTime`), corroborating a settable clock.
   Cleanest, highest-value; same write mechanism as a proven path, no special arming.
2. ~~**Cooler `NightTimerSet` bit**~~ — **RETRACTED / do NOT test-write** (2026-08-26). Once thought
   the "activate night timer" latch, but the app **never stages this bit on the cooler** — the only
   writer of that shared `1101` @bit0-1 slot is the AIR-HEATER VM (`rf/b.H3`); on the cooler it's a
   read-only, unrendered dead-end bit (`vf/c` decodes it to a flow with zero UI consumers). The
   schedule is armed by **`Mode==4`** ("Automatischer Flüstermodus"), not this bit — verified DEVICE
   (unit screen + call-stack) and the within-window hypothesis was REFUTED live (bit read 0 with the
   unit RTC inside the armed window). The `night_set` command was retracted; the old `vf/c.X1` cite
   is actually `setCoolingLevel`. See `evidence-ledger.md` + `control-and-actuation.md` §5.
3. **Energy `DisplayRefresh` bit** — char `1601` @bit7 (`pg/a` `f21228f0`, 1-bit, default false).
   `xf/d.d4()` (set energy mode) never touches it. Effect unknown ("refresh display/telemetry?").
   The same merged slot is stairs `Movement` (used). Low-risk to try; unknown result. NB this is
   **not** a confirmed starter-battery refresh — the app has no starter-refresh action at all, and
   starter values only re-measure engine-on (see below).
4. **Air-heater permanent-heating** — char `1701`. `PermanentOperationRequest` (`f23984g0`) is only
   ever written **0** (OFF) by the app; `PermanentOperationConfirmation` (`f23983f0`) is never
   staged. Together a Request+Confirmation handshake for continuous heating with only the OFF
   direction wired. **Not testable here** (no air-heater installed).

Corrected opcode note: of the four opcodes the first pass flagged as "unused", only **`SYSTEM_TIME`
(24)** is truly never sent. `SET_DOUBLE(8)`, `WAKEUP_TIME(20)` and `PREVIEW(28, raw v(28) at
`b1/d.java:209`)` **are** exercised. Full `dg/n` ordinals: NO_MODE 0, SET_BRIGHTNESS 4, SET_COLOR 6,
SET_DOUBLE 8, REQUEST_CONFIG 12, SET_PROFILE 16, WAKEUP_TIME 20, **SYSTEM_TIME 24**, PREVIEW 28.

## Hidden READ surface — the `F000/F001` general-purpose register

The unit pushes a raw **21-cell diagnostic register** on char `F001` (`generalpurposesignals`):
8 bit-flags (`BitZeroOne…Eight`), 8 bytes (`ByteZeroOne…Eight`), 4 words (`WordZeroOne…Four`), 1
dword (`DwordZeroOne`) — all **anonymous / uninterpreted**. Read-only (no control writer touches
`F001`); the app only subscribes to it. It is the closest thing to a vendor diagnostic/telemetry
channel and its cell meanings are unknown.

**Probe wired (opt-in):** the daemon normally drops `generalpurposesignals` from InfluxDB (no
`installed` bit → treated as noise). Set **`CALICTL_STORE_GENERALPURPOSE=1`** and it logs all 21
cells to InfluxDB each poll, so the cells can be **correlated against known events over time** to
reverse-engineer what the register carries. Query e.g.::

    from(bucket:"…") |> range(start:-7d)
      |> filter(fn:(r)=> r._measurement=="camper" and r.function=="generalpurposesignals")

Watch which cells move when ignition/engine/loads change; a moving cell that tracks a known signal
is decodable. Off by default (keeps the normal dashboard clean).
