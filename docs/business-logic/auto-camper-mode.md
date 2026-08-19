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

## Config

| Env | Default | Meaning |
|---|---|---|
| `CALICTL_AUTO_CAMPER_MIN_SOC` | 20 | leisure-battery % floor below which it stands down |
| `CALICTL_AUTO_CAMPER_WINDOW_S` | 120 | re-assert window after an engine start |
| `CALICTL_AUTO_CAMPER_MAX_FAILS` | 3 | consecutive re-asserts before giving up for the cycle |

Persisted toggle: `auto_camper` in the state cache. State/route: `serve.Server.set_auto_camper` +
`ServeBackend.set_auto_camper`, web `POST /api/auto_camper {"enabled": bool}`, surfaced in
`_meta.auto_camper`. Logic: `calictl/automation.py`; wiring: `calictl/serve.py::_auto_camper_step`.
