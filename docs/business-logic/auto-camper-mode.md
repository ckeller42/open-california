# Auto camper mode — re-enable camper + USB after an engine start

A calictl-only feature (**not in the VW app**): the camper unit **drops camper mode when the engine
starts** (ignition / terminal-15 goes on). "Auto camper mode" re-asserts camper mode + the rear USB
ports after that — so USB-C power and the camper outputs stay on through an engine start — while
**respecting a manual off** and, critically, **never fighting the unit's battery protection.**

Toggle it on the web-UI **Camping** card ("Auto re-enable after engine start"). The setting persists
across daemon restarts. It is off by default.

## How it decides (pure logic: `calictl/automation.py::auto_camper_decide`)

The rule can only tell "the engine disabled it" from "you disabled it" by **timing relative to the
ignition edge**, so it keys off the ignition **rising edge**:

1. **Arm on engine start.** When ignition goes off→on, arm a re-assert for a bounded window
   (`CALICTL_AUTO_CAMPER_WINDOW_S`, default 120 s). The unit disables camper mode a few seconds
   *after* ignition-on, so a one-shot at the edge could be undone — hence a window, not a single try.
2. **While armed and camper mode is off, re-assert** `campingmode master=on` + `usb=on` each poll —
   unless a guard fires (below). Disarm the instant camper mode is confirmed **on**.
3. **Manual off is respected.** A camper-off with **no recent ignition edge** never triggers anything
   (we're only armed right after an engine start), so switching camper mode off yourself sticks.

## The two safety guards (why it can't loop or drain the battery)

**Guard 1 — battery gate (defer to the unit's protection).**
Before any re-assert, the leisure battery must be healthy: `soc2_pct ≥ CALICTL_AUTO_CAMPER_MIN_SOC`
(default **20 %**) **AND** no `sleep_warning` / `warning_active` / `warning_level ≥ 1`
(`SleepWarning` / `WarningLevelActive` / `WarningLevelTwo` from the energy state). If the battery is
low or warning, the unit shed camper mode **to protect itself** — the feature **stands down** and
does not re-enable. Re-checked every poll, so a mid-window battery drop stops it immediately. When
the battery recovers, the next engine start re-arms.

**Guard 2 — bounded retries / back-off (catch-all).**
Even on a healthy-looking battery, re-asserts are capped at `CALICTL_AUTO_CAMPER_MAX_FAILS`
(default **3**) per arm cycle. If camper mode keeps dropping despite that — a low-power state we
can't see cleanly (firmware quirk, thermal derating, an unmapped protection) — the feature
**gives up for that ignition cycle** instead of hammering the unit and draining the battery. The
window is a further backstop: if camper mode never stays on within it, it gives up too.

**The three "camper mode went off" causes therefore resolve as:**

| Cause | Signal | Action |
|---|---|---|
| Engine start | ignition rising edge, battery healthy, within window | **re-assert** (master + USB) |
| Manual off | no ignition edge (not armed) | **ignore** — your off sticks |
| Low-battery load-shed | SoC < min / a warning flag, **or** repeated re-drops | **stand down** — never fights protection |

## When it stands down / gives up

Each stand-down or give-up is **logged** (`auto-camper: …`) and surfaced once to the web UI as a
**toast** (`_meta.auto_camper.notice = {ts, msg}`, deduped by `ts`). Examples:

- "Auto camper stood down: battery low / warning (SoC 12%) — not re-enabling."
- "Auto camper gave up: camper mode keeps dropping (likely low-power) — standing down."

## Guarantees

- **Never an endless loop** — bounded by both `MAX_FAILS` and the window (unit-tested end-to-end in
  `tests/test_automation.py::test_no_loop_full_cycle`).
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
- **The shed is firmware-side, not an app interlock.** The camping control path has no
  ignition/Terminal-15 check; but the app ships a **"camping mode only possible when stationary"**
  dialog (`dialog_info_campingMode_onlyPossibleWhenStationary_*`). So the unit itself disables
  camping while moving/engine-on. **Implication for auto-camper:** re-asserting *during* the drive
  will likely be **refused by firmware** (a source of the very flip-flop we guard against) — the
  feature probably only succeeds once **stationary / engine off**. The observation will confirm
  which, and may move the correct trigger from "after engine start" to "once stationary again."

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
| `CALICTL_AUTO_CAMPER_WINDOW_S` | 120 | re-assert window after an engine start |
| `CALICTL_AUTO_CAMPER_MAX_FAILS` | 3 | consecutive re-asserts before giving up for the cycle |
| `CALICTL_OBSERVE_BURST_INTERVAL_S` | 3 | poll interval during the post-engine-start observation burst |
| `CALICTL_OBSERVE_BURST_S` | 180 | how long the fast-poll burst lasts after an engine-start edge |

Persisted toggle: `auto_camper` in the state cache. State/route: `serve.Server.set_auto_camper` +
`ServeBackend.set_auto_camper`, web `POST /api/auto_camper {"enabled": bool}`, surfaced in
`_meta.auto_camper`. Logic: `calictl/automation.py`; wiring: `calictl/serve.py::_auto_camper_step`.
