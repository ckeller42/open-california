# State-value freshness: the 1003 liveness heartbeat

**Status:** SOLVED + live-verified on-device 2026-07-09.

> **CORRECTION 2026-07-14 (ground-truth at the van): the heartbeat keeps the *link* fresh, but
> fresh-*water* measurement is gated on the van's WATER SYSTEM being powered — not the heartbeat.**
> Verified against the van's own panel: parked/locked, buspi read fresh-water **1 L** (stale) while
> the tank truly held **17–19 L**; after unlocking the van (water system on) + running a tap, buspi
> tracked the true level **live** (19→18→17 L, grey 0→1 L, matching the panel within the poll lag).
> So the 2026-07-09 "heartbeat → 1→11 L" was **correlation** (the van was active then), not cause.
> buspi is a faithful mirror; the unit simply stops measuring water when parked. We can't force a
> measurement, so `calictl/freshness.py::implausible_water_drop` rejects the latched drop — the
> latch signature is a fresh ↓ **to ≤ 1 L** while grey is **EXACTLY frozen** (the unit freezes both
> tanks when unpowered; narrowed 2026-10-10, below); **any** grey movement (rise *or* fall) proves a live measurement — and the daemon
> holds the last plausible reading, flagged **stale** (forecast suppressed). See
> `[[value-freshness-heartbeat]]` memory + `R_WATER_STALE_GUARD`.
>
> **FIX 2026-08-16 (the `<=` wedge):** the guard originally treated grey `<=` its previous value
> as the latch. After a real grey **dump** at a dump station, grey sat below the pre-dump baseline,
> so every genuine post-dump reading re-latched — the hold wedged for a month. Fixed to the
> exact-freeze rule (`==`): only a grey tank that has not moved at all corroborates the latch.
> Same day: when the guard holds a value, **both tanks** are now flagged stale (grey is frozen by
> the same unpowered unit, not just fresh).
>
> **FIX 2026-10-10 (the grey-always-0 wedge):** on this van grey (`WasteWaterLevel`) reads **0 on
> every frame**, so "any fresh drop with grey frozen" held **every** real drop: buspi served 22 L
> from ~2026-09-18 while the unit really reported 20 L (app capture: *20 / 29 l*, 1302
> `03141d010016`). Only refills passed. The guard is now narrowed to the observed latch value:
> **latch iff fresh dropped AND new fresh ≤ `WATER_LATCH_MAX_L` (1 L) AND grey exactly frozen**
> (grey unknown → latch, conservative, still only for the ≤ 1 L case). Any other reading is a live
> measurement — shown, and the new baseline. Known trade-off: a real drop to ≤ 1 L with grey
> frozen is held until fresh or grey moves again.

## Symptom

A bare poll of the fresh-water state char (`00001302`) returned **1 L** while the vehicle truly
held **11 L** (shown by the app). The decode was correct (capacities 29/22 and the waste level
all cross-matched the app); only the live *level* was a **stale latched value** — not a decode
bug, a drain, or a sensor fault (`FreshWaterInfoPopUp = 0` = valid).

## What it is NOT (ruled out along the way)

- **Not Exlap / WiFi.** The app *does* ship an Exlap client (`de.exlap.*`, XML over a
  `socket://`<camper-AP-ip>` TCP transport to the camper's WiFi AP), but that is a **separate path**,
  code-disjoint from the camper BLE stack, and is **not** how the app reads the vehicle tab here.
  The van's WiFi AP wasn't even broadcasting. (Earlier notes that guessed an Exlap/WiFi fix were
  wrong.)
- **Not BLE GATT notifications.** Enabling notifications (CCCD `01 00`) yields no fresh push on
  its own; a persistent bare subscription stayed stale and the van dropped the link every ~15 s.

## Root cause (live-verified)

The **`00001003` liveness heartbeat** — a +1 4-byte-BE counter — **keeps the link alive** so a
read returns a fresh value instead of a stale latch. (It does NOT itself drive every subsystem's
measurement — see the water note below, where the heartbeat was DISPROVEN as a water refresh; a
subsystem re-measures only while it's powered/awake.) It is not only for actuation: the app ticks
it **continuously (~0.7 s) the entire time it is connected**. A characteristic read returns the
last latched value; without the heartbeat running, that value decays to a stale reading and, after
~15 s with no heartbeat, the van drops the connection.

**Capture evidence** (PacketLogger via `idevicebtlogger`, app opening the vehicle screen): the app
writes a monotonic +1 counter to handle `0x001a` (= char `1003`) every ~0.76 s; the water value
came back fresh (`030b1d…` = 11 L) from a **plain read** — no notification, no request/refresh
command (`DisplayRefresh` is an unrelated *energy* actuation bit).

**On-device confirmation** (buspi): with a continuous, landing heartbeat the fresh-water read went
**1 L → 11 L immediately and held for 64 s**, `hb_fail=0`, and **the link never dropped**.

## The fix

`device.read_all` / `device.read` run the existing `_heartbeat(client, stop)` for the duration of
the read session (a short `HEARTBEAT_WARMUP_S` lets the measurement refresh first), then stop it
and disconnect — all within the one session under the `serve` BLE lock. This both **freshens the
values** and **fixes the mid-poll link drops**. Stdlib-only, no new transport. See
`R_READ_HEARTBEAT_REFRESH` in `calictl/device.py` ← `T_READ_HEARTBEAT_REFRESH`
(`tests/test_mock_integration.py`); the mock models "reads are stale until a heartbeat arms the
session."

## The stale-latch guard on the ESP satellite (2026-10-09)

The ESP32 satellite runs the SAME guard as `freshness.implausible_water_drop`, ported to
`firmware/components/cali_core/session.c`: when a water (`1302`) frame shows a FreshWaterLevel
**drop to ≤ `CALI_SESSION_WATER_LATCH_MAX_L`** (1 L, the observed latch; mirrors
`freshness.WATER_LATCH_MAX_L`) while WasteWaterLevel is **exactly frozen**, the session keeps
serving the last plausible frame instead of the latch (any other reading — a drop that stays above
1 L, any grey movement, or a rising fresh level — is adopted and persisted as the new baseline). Comparing the
raw `Level` fields is equivalent to `freshness.py`'s liters comparison — the unit is stable and the
guard only asks "did fresh drop / did grey move". The plausible frame is **persisted to NVS**
(`water_good`, wrapped like every `cali_platform` KV record), so a reboot while parked shows the real
level rather than re-accepting the `1` latch — the firmware twin of `serve.py`'s persisted
`_water_good`. `/api/state` reports `device.water_held` when the held frame is being served, and the
shared UI (`semantics.js::adaptSatellite`) flags both tanks stale (`🕒 last measured`, no
`stale_since` — the satellite has no wall clock). **Same cold-start limit as buspi:** a fresh ESP
with no NVS baseline that first reads while parked accepts the latch as its baseline until the van is
next active. Why this matters: buspi (poll, then release) and the ESP (persistent, re-read every 30 s)
would otherwise disagree — buspi holding the real ~17 L while the ESP showed a confident `1 L` / 3 %
(field 2026-10-09: buspi 22 L held/stale vs ESP 1 L raw, the discrepancy that prompted this).
After a reflash the ESP baseline can be seeded from buspi's last plausible reading with the
console command `water seed <hex>` (the raw 6-byte `1302` frame; anything else is rejected and the
baseline is untouched); otherwise it self-establishes on the next active-van read.

## Connection failure modes (why buspi shows offline)

A `BleakDeviceNotFoundError` / "no BLE session after retries" means the unit isn't reachable — and
buspi's own stack is usually fine (check `hciconfig hci0` = UP RUNNING). The distinct causes, now
recorded per-poll in `poll_outcomes.jsonl` and classified by `tools.analyze_battery`:

- **Van deep-sleep** — parked/idle, the unit stops advertising; **self-resolves** on the next wake
  (door/ignition). Expected. `outcome: asleep` once the supervisor backoff hits the cap.
- **Phone holds the single BLE slot** — the CaliforniaOnTour app is connected, so the unit stops
  advertising; **resolves when the app disconnects**. `outcome: ble_error`, short run.
- **Unit Bluetooth DISABLED** (in the unit's own settings) — persistent `DeviceNotFound` that
  **will NOT self-resolve** until re-enabled; then buspi reconnects on the next poll. Shows as a
  long unbroken `ble_error` run (observed 2026-08-18). NOT a buspi fault.
- **Daemon down / Pi reboot** — no `poll_outcomes` rows at all for the window.
- **Influx-write failure** — polls succeed (`outcome: ok`) but nothing is stored: a *false* gap.

## Read order: subscribe, then read, last frame wins (2026-10-07)

**Decompile (APK via jadx, enigma `38a0d6b` in the private RE repo):** at every (re)connect the app
writes the CCCD of each notifiable char and **then reads it** (`jb/b`, for each descriptor:
subscribe, then an unconditional READ). The read result and every notification go into **one
decoder** (`qd/b` read → `i2` tag 26 re-emits it into the same flow as notifies → `qg/b.e`), which
sets the water StateFlows unconditionally. So the **last frame to arrive wins**, normally the read.
The app has no water cache, no validity gate, no "last measured" UI, no wake write and no periodic
1302 re-read.

This **contradicts the 2026-07-14 "water is push-only for freshness" claim**, which calictl had
built into two places:

- `PersistentSession.read_all` (`PUSH_ONLY_FUNCS = {"water"}`) served water from the
  **subscribe-time push for the whole session** and never re-read 1302. Its own comment said this
  was UNVERIFIED on-device.
- the per-op `read_all`/`read` preferred any push over the read.

**calictl now follows the app** (`R_READ_LAST_FRAME_WINS`, `device._later_pushes`): every poll
reads every state char, water included, after the subscribe. The read replaces the subscribe-time
push, and a notification that arrives after the read replaces the read. Lighting is excluded from
the notify-override, because its 1502 pushes include config/ack frames that `serve` latches itself.
The mock models the case: a stale subscribe-time push (`notify_push`) plus a correct read
(`tests/test_mock_integration.py`, `T_READ_ALL_LAST_FRAME_WINS`).

**Hypothesis (to confirm with the #230 trace): the long-running parked "1 L" may have been calictl
preferring the push, not (only) the unit's latch.** The owner states (2026-10-07) that the
original app shows the right value. That was not a side-by-side comparison with calictl at the
same moment. Two calictl paths served a push instead of the read:

- **The per-op path (the likely cause).** Before this change, the per-op `read_all` preferred the
  subscribe-time push over the read for **every** char. buspi's background polls mostly run on
  this path: the persistent session is released when the web UI goes idle. Suppose the unit's
  subscribe-time 1302 push carries the parked latch while its read is right. Then every per-op poll
  showed the latch, for days, whereas the app lets the later read win.
- **The persistent session (a minor cause).** Its pinning only lasted one UI-active session
  (minutes), so it cannot explain days of 1 L.

**Settled 2026-10-10 (CAPTURE): the unit sends no subscribe-time push at all** — no notification
follows a CCCD write in the phone's HCI snoop or in btmon on buspi's link. The "push" calictl's
trace logged before each read is BlueZ re-delivering that read, so it always carried the read's
value. The 1 L was the unit's own latch: buspi's reads flip FreshWaterLevel 20 ↔ 1 across
wake/sleep with grey 0 throughout (`ble.jsonl` 2026-10-09). The mock serves that latch
(`WATER_LATCH_L`) while the water system is unpowered; `notify_push` stays an opt-in test scenario.

The 2026-07-14 comparison against the van's panel (above) **predates `PersistentSession`**
(2026-07-17). It ran on the per-op path, so it fits this hypothesis as well as the latch
explanation.

To confirm or refute: on the next van wake, diff the push bytes against the read bytes for each
char in `CALICTL_BLE_TRACE`, which records both on every link. If the 1302 push and read are
identical while parked, the latch explanation stands. In that case the claim in "the settled
conclusion" below, that the app "sees the same value", also stays true.

**Open question (owner, after a van trace):** is `freshness.implausible_water_drop` still needed?
It stays unchanged for now (with `water_stale_since`). If a parked trace shows the unit's 1302 read
is always right, the guard may be redundant. If the unit really latches while parked, the guard
remains the only defence; the app has none. The van check is in `remaining-captures.md` §1 (#230).

## Water freshness — the settled conclusion (2026-08-19)

Traced end-to-end. Water is READ-ONLY on char `1302` (no `1301` control char, `qg/b` never writes),
and the app interprets `FreshWaterLevel` LITERALLY — `qg/b.h()/i()` = liters/percent with **no floor,
clamp, or 1→0 mapping** (so the app displaying 0 means it received `Level=0`; a read of 1 is a stale
value, not a floor). Our decode matches the app bit-for-bit (`FreshWaterLevel@8/w8`, `FreshWaterVolume@16/w8`…).

**The `1003` heartbeat does NOT refresh water** — DISPROVEN 2026-08-19: subscribed to `1302` with the
heartbeat running for 60 s → **0 notifications**; reads always return the same value. The unit pushes
`1302` only on an actual measured CHANGE, and it re-measures when its **own water system is active**,
NOT in response to any BLE activity we can send. (The heartbeat's real, still-valid role is keeping
the LINK alive during a read — not driving measurement.)

**So there is no BLE-side fix**: buspi reads the char faithfully; the value it holds is the unit's
last measurement and self-corrects only when the unit next re-measures (water system on). The phone
app reads the same char and sees the same value (2026-10-07: unproven — the app takes the READ, while
calictl's per-op path took the subscribe-time PUSH; see "Read order" above and #230); a physical control-panel "0" is that panel's own
continuously-sampled sensor, which the BLE char lags. The honest surface is the **stale flag** +
"last measured" — never a fabricated floor-correction.
