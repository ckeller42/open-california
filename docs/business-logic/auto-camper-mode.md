# Auto camper mode — restore camping after you park

> **DESIGN NOTE (2026-08-19).** The original idea — *re-enable camper mode **during** an engine start
> so USB-C stays on **through the drive*** — was **DISPROVEN on hardware.** The unit **refuses camping
> mode while the vehicle is driven** (write returns `applied:false`, no `1202` push, and the unit's
> console shows *"nicht während der Fahrt verfügbar"* — a firmware **stationary gate**,
> `control-and-actuation.md` §4). The decompile confirms the app has **no** speed/brake/gear signal —
> only Terminal-15 — and the gate is enforced **unit-side**; the camping `enable` bit mirrors ignition
> (reads 1 while driving = blocked). So the feature now **restores camping once you park** (ignition
> **falling** edge). **Not yet live-verified:** that the unit accepts the camping-on write *immediately*
> after ignition-off (a brief post-park lock is possible) — the retry window covers it, but a
> park-then-write test is still owed. Feature stays **off by default** until that is confirmed.

A calictl-only feature (**not in the VW app**): the camper unit **drops camper mode when the engine
starts** (ignition / terminal-15 goes on) and **will not let it back on until the vehicle is
stationary again.** "Auto camper mode" restores camper mode + the rear USB ports **once you park** —
so you don't have to re-toggle it after every drive — while **respecting a manual off** and,
critically, **never fighting the unit's battery protection.**

Toggle it on the web-UI **Camping** card. The setting persists across daemon restarts. Off by default.

## How it decides (pure logic: `calictl/automation.py::auto_camper_restore_decide`)

Because the unit refuses camping-on while driving, the rule waits for you to **park** and restores
the config the engine took away:

1. **Arm on the engine shed.** On the ignition **rising** edge, if camper mode was on just before,
   remember the pre-drive config (`{master, usb, lights}`) and mark a **restore owed**. The actual
   shed may land a poll or two later (the BLE link can drop at the crank); the pre-edge snapshot is
   the truth we restore to.
2. **Restore on the ignition FALLING edge (park).** When ignition goes on→off with a restore owed,
   open a bounded window (`CALICTL_AUTO_CAMPER_WINDOW_S`, default 120 s) and write the remembered
   config — `campingmode master=on` (+ `usb` / `lights` to match) — re-asserting each poll until it
   sticks. The window exists in case the unit lingers in its driving-lock for a moment after parking.
3. **Manual off is respected.** A camper-off while the ignition is **steady off** (not an engine
   shed) clears any owed restore — switching camper mode off yourself sticks.

**Survives a restart.** The restore *debt* (`owe_restore` + the remembered `pre_drive` + a
**wall-clock** retry deadline + the ignition-edge state) is persisted to the state cache each poll,
so a daemon restart or buspi reboot **between engine-start and park** does not silently drop a
pending restore — it resumes on load (a deadline that already elapsed is correctly judged expired).

**Known limit — manual-off *while driving*.** Manual-off is only recognised while the ignition is
steady off. If you could turn camper mode off *mid-drive* meaning it to stay off, the restore would
still fire at park. In practice the unit force-holds camper mode off while driving (the stationary
gate), so this state isn't reachable — noted for completeness, not a live case.

## The two safety guards (why it can't loop or drain the battery)

**Guard 1 — battery gate (defer to the unit's protection).**
Before any restore write, the leisure battery must be healthy: `soc2_pct ≥ CALICTL_AUTO_CAMPER_MIN_SOC`
(default **20 %**) **AND** no `sleep_warning` / `warning_active` / `warning_level ≥ 1`
(`SleepWarning` / `WarningLevelActive` / `WarningLevelTwo`). If the battery is low or warning, the
feature **stands down** and does not restore — it never fights the unit's power-saving. Re-checked
every poll.

**Guard 2 — bounded retries / back-off (catch-all).**
Restore writes are capped at `CALICTL_AUTO_CAMPER_MAX_FAILS` (default **3**) per park cycle. If the
unit keeps refusing after park (a lingering driving-lock, or an unseen low-power state), the feature
**gives up for that park** instead of hammering the unit. The window is a further backstop: if
camping never comes back within it, it gives up too.

**The "camper mode went off" causes resolve as:**

| Cause | Signal | Action |
|---|---|---|
| Engine start | camper-on → off on the ignition **rising** edge | **remember** it (owe a restore) |
| You parked (restore owed) | ignition **falling** edge, battery healthy, within window | **restore** master (+usb/lights) |
| Manual off | camper-on → off with ignition **steady off** | **clear the debt** — your off sticks |
| Low-battery load-shed | SoC < min / a warning flag, **or** repeated refusals after park | **stand down** — never fights protection |

## When it stands down / gives up / succeeds

Each notable step is **logged** (`auto-camper[<event>]: …`, events `armed_engine_shed` / `restoring`
/ `restored` / `stood_down` / `gave_up`) and the notice is surfaced once to the web UI as a **toast**
(`_meta.auto_camper.notice = {ts, msg}`, deduped by `ts`). Examples:

- "Auto camper: camping restored after park."
- "Auto camper stood down: battery low / warning (SoC 12%) — not restoring."
- "Auto camper gave up: camping would not re-enable after park."

`_meta.auto_camper.armed` is true while a restore is **owed** (engine shed camping, not yet back).

## Guarantees

- **Never an endless loop** — bounded by both `MAX_FAILS` and the window (unit-tested end-to-end in
  `tests/test_automation.py::test_no_loop_full_cycle_engine_shed_then_park_then_refused`).
- **Never fights battery protection** — Guard 1 stands down on any known low-battery signal.
- **Respects read-only mode** — if writes are disabled, it logs intent and does not actuate.
- **Actuation is app-faithful** — the same `campingmode` master/USB frames the manual controls use
  (verified on-device), one-shot latch, under the single `serve` BLE lock (after the poll read).

## Observing before enabling (the camping/ignition watcher)

Before auto-camper is switched on, the daemon **passively observes** what the unit does to camping
mode on an engine start — to confirm it sheds cleanly and does **not flip-flop** (which would make an
auto-re-enable fight the unit). This runs **always, regardless of the toggle, and never actuates**
(`serve.Server._observe_transitions`).

**Two "engine on" signals, because they differ:**

- `vehicle.ignition_on` — **Terminal-15** (the ignition switch). Can be on with the engine *not*
  running (accessory position).
- `energy.dcdc_charging` — the **DC-DC converter active** = the alternator feeding the leisure
  battery = the engine actually **running**.

An "engine start" is a rising edge of **either**. Recording both lets the analysis see which one the
camping shed actually follows.

**What it does each poll:** compares ignition + camping substates (`master_on`, `usb_charger`,
`lights_on`, `enable`=Terminal-15-as-camping-sees-it) to the previous poll and logs a one-line
`camping-watch:` journal entry on any change, tagged with Δ-since-engine-edge, SoC, DC-DC current and
leisure-battery current. On an engine-start edge it starts a **fast-poll burst** (drop to
`CALICTL_OBSERVE_BURST_INTERVAL_S`≈3 s for `CALICTL_OBSERVE_BURST_S`≈180 s, then back to 30 s) — the
30 s cadence otherwise **aliases** the engine-on and the shed into one sample, so a single clean shed
can't be told from a flip-flop. The burst also densifies the InfluxDB series right at the edge.

**Reading it back:** `python3 -m tools.analyze_camping [--days 3] [--every 10s]` (on buspi, with
`/etc/buspi/*.env` sourced). For each engine start it prints the camping transitions in a window
around the edge and **flags a flip-flop** (≥2 `master_on` transitions within `--flip-window`).

**What the app source says (decompile, 2026-08-19):**

- **The unit PUSHES camping state.** Camping state decodes on char **`00001202`** (`tf/a.java:149`,
  fields `State`/`UsbCharger`/`Outside`+`InteriorLight`/`Enable`/`Installed`), and the app
  **subscribes to notifications** on it (`jb/b.java:142` `td.c` subscribe; router `pf/j.java:105`) —
  exactly like lighting's `1502`. So the engine-start shed arrives as an **unsolicited `1202`
  notification**; ignition (`1004`) is pushed too. calictl already reads `1202`. **This is now
  wired:** `serve.Server._on_ble_push` (via `device._subscribe_all(..., on_push=)` on the persistent
  session) logs `camping-push[...]` the instant a `1202`/`1004` notification changes camping or
  ignition — full resolution, no poll wait. **Caveat:** the persistent session is only held while the
  **web UI is active** (otherwise the BLE slot is released for the phone), so the push overlay fires
  only then; the 30 s poll observer + fast-poll burst remain the always-on baseline. The push lines
  also **confirm the unit's push channel on this specific unit** (decompile-asserted, not yet observed
  here).
- **The shed is firmware-side, not an app interlock** — CONFIRMED by a live write test (2026-08-19).
  The app ships a **"camping mode only possible when stationary"** dialog
  (`dialog_info_campingMode_onlyPossibleWhenStationary_*`); writing `campingmode master=on` with the
  engine running was **refused by the unit** (`applied:false`, no `1202` push, the unit's console
  popped *"nicht während der Fahrt verfügbar"*). So re-asserting *during* the drive is **impossible**,
  not merely flip-flop-prone. This is what moves the trigger from "after engine start" to **"once
  stationary again"** (redesign — see the banner at the top).

## Logging & analysis

Every notable decision is logged to **stdout → the systemd journal** (no bespoke log file — the
journal is the system log, and on buspi it is persistent). One line per notable event, with the
decision inputs on the line so you can see *why* it acted or stood down:

```
auto-camper[<event>]: <msg> (ignition=.. camping=.. soc=.. warn=.. fails=..)
```

Events: `engine_start_armed`, `reasserting`, `confirmed_on`, `stood_down`, `gave_up`. Idle polls
log nothing. Review a van session with:

```
journalctl -u calictl.service --grep auto-camper          # human-readable
journalctl -u calictl.service --grep auto-camper -o json   # structured (JSON per record)
```

The raw **values** (`ignition_on`, `soc2_pct`, the battery-warning flags, camper `master_on`) are
already written to **InfluxDB every poll** via `influx.points_for` — so the picture is InfluxDB for
the continuous values + the journal for what the feature decided with them.

**Retention:** journald on buspi is persistent (`/var/log/journal`) but ships with a small default
cap (~a day of calictl history). A drop-in (`/etc/systemd/journald.conf.d/calictl.conf`, deployed via
`calictl/deploy/`) raises `SystemMaxUse` + `MaxRetentionSec` so a session stays analysable for weeks.

## Config

| Env | Default | Meaning |
|---|---|---|
| `CALICTL_AUTO_CAMPER_MIN_SOC` | 20 | leisure-battery % floor below which it stands down |
| `CALICTL_AUTO_CAMPER_WINDOW_S` | 120 | restore retry window after you park |
| `CALICTL_AUTO_CAMPER_MAX_FAILS` | 3 | consecutive re-asserts before giving up for the cycle |
| `CALICTL_OBSERVE_BURST_INTERVAL_S` | 3 | poll interval during the post-engine-start observation burst |
| `CALICTL_OBSERVE_BURST_S` | 180 | how long the fast-poll burst lasts after an engine-start edge |

Persisted toggle: `auto_camper` in the state cache. State/route: `serve.Server.set_auto_camper` +
`ServeBackend.set_auto_camper`, web `POST /api/auto_camper {"enabled": bool}`, surfaced in
`_meta.auto_camper`. Logic: `calictl/automation.py`; wiring: `calictl/serve.py::_auto_camper_step`.
