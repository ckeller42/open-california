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
> latch signature is a fresh ↓ while grey is **EXACTLY frozen** (the unit freezes both tanks when
> unpowered); **any** grey movement (rise *or* fall) proves a live measurement — and the daemon
> holds the last plausible reading, flagged **stale** (forecast suppressed). See
> `[[value-freshness-heartbeat]]` memory + `R_WATER_STALE_GUARD`.
>
> **FIX 2026-08-16 (the `<=` wedge):** the guard originally treated grey `<=` its previous value
> as the latch. After a real grey **dump** at a dump station, grey sat below the pre-dump baseline,
> so every genuine post-dump reading re-latched — the hold wedged for a month. Fixed to the
> exact-freeze rule (`==`): only a grey tank that has not moved at all corroborates the latch.
> Same day: when the guard holds a value, **both tanks** are now flagged stale (grey is frozen by
> the same unpowered unit, not just fresh).

## Symptom

A bare poll of the fresh-water state char (`00001302`) returned **1 L** while the vehicle truly
held **11 L** (shown by the app). The decode was correct (capacities 29/22 and the waste level
all cross-matched the app); only the live *level* was a **stale latched value** — not a decode
bug, a drain, or a sensor fault (`FreshWaterInfoPopUp = 0` = valid).

## What it is NOT (ruled out along the way)

- **Not Exlap / WiFi.** The app *does* ship an Exlap client (`de.exlap.*`, XML over a
  `socket://192.168.2.1` TCP transport to the camper's WiFi AP), but that is a **separate path**,
  code-disjoint from the camper BLE stack, and is **not** how the app reads the vehicle tab here.
  The van's WiFi AP wasn't even broadcasting. (Earlier notes that guessed an Exlap/WiFi fix were
  wrong.)
- **Not BLE GATT notifications.** Enabling notifications (CCCD `01 00`) yields no fresh push on
  its own; a persistent bare subscription stayed stale and the van dropped the link every ~15 s.

## Root cause (live-verified)

The **`00001003` liveness heartbeat** — a +1 4-byte-BE counter — is what drives the unit's
sensor-measurement loop **and** keeps the link alive. It is not only for actuation: the app ticks
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
app reads the same char and sees the same value; a physical control-panel "0" is that panel's own
continuously-sampled sensor, which the BLE char lags. The honest surface is the **stale flag** +
"last measured" — never a fabricated floor-correction.
