# Evidence ledger — how strongly is each protocol fact proven?

Every protocol claim in this repo sits at one of three **evidence tiers**. The goal is to drive
everything toward the top tier. The decompile tells us what the app *intends*; only a wire capture
proves what actually happens.

| Tier | Meaning | How it's checked |
|---|---|---|
| **CAPTURE** | Matched byte-for-byte against a real HCI/PacketLogger capture of the app | `tests/scenarios/<fn>/*.yaml` + `tests/test_capture_diff.py` |
| **DECOMPILE** | Grounded in the app's decompiled decode/setter + the enigma mapping, but never seen on the wire | agent cross-checks; `mapping.enigma` (54 verified classes) |
| **DEVICE** | Physically observed on the van (photons / a human at the hardware), frame not necessarily diffed | owner report, dated |

Automated ties that keep this honest: `test_signal_coverage.py` (dictionary ↔ catalog),
`test_doc_offset_consistency.py` (prose/comment `Field@offset` citations ↔ dictionary),
`test_capture_diff.py` (our frames ↔ captured app frames).

## Owed a capture (currently DECOMPILE-only — do not trust as verified)

| Fact | Tier now | Capture that would verify it |
|---|---|---|
| cooler `timer_set`, `timer_start`/`cancel` (start-at cooling timer) | DECOMPILE | app: set + arm a cooling timer → diff 1101 frames |
| cooler `mode` quiet=2(manual)/4=scheduled — DISPLAY-CONFIRMED 2026-08-26: the unit's Flüstermodus screen shows "Ein/Aus"(manual=Mode2) + "Automatisch"(scheduled=Mode4) toggles; scheduled quiet = Mode 4 (vf/c L0), decompile-cross-checked end to end (yh/e QuietModeViewModel). No physical compressor-audible confirm yet | DEVICE (display) | a human hearing the compressor quieten in the window |
| cooler `NightTimerSet` bit — meaning UNKNOWN: decoded (1102 bit3) + plumbed into a StateFlow (vf/c D3) but NEVER rendered (dead-end, zero UI consumers) and NEVER written by any cooler path (only air-heater rf/b.H3 stages that shared frame slot). NOT the schedule-arm bit (that's Mode 4); "within-window active flag" hypothesis **REFUTED** DEVICE 2026-08-26 — read 0 with the unit RTC at 22:06 INSIDE the armed 22:00–06:00 window (also 0 outside it). Vestigial on this unit, or asserts only under some unseen condition. Not surfaced | DEVICE (refuted) | — |
| airheater `runtime` (`3f7b003c1f3f`), `timer_start` (`3f3b017f1f3f` = Mode 3 + Combined 1), `timer_cancel` (`3f0b007f1f3f`) | APP-OBSERVED (tools/applab 2026-09-16; frames identical to calictl's) | unit-side: does the heater actually start at TimerHour:TimerMin, and what Mode does 1702 report after it fires (mock assumes 0) |
| airheater `timer` HH:MM (`B0`, TimerHour/TimerMin) | DECOMPILE | app: move the "Start heating at" wheel (emulator swipes didn't turn it) → diff 1701 frame |
| fault dialogs: heater ErrorCode 1–5, cooler Error 1–3, 11 energy flags, water InfoPopUps (fresh 1–5/7, waste 1–3) → the app's exact dialog texts | APP-OBSERVED (fake unit injection 2026-09-16; `alert-states.md`, `cooler-airheater.md`) | unit-side: which real conditions raise each code (only cooler door-open and heater codes have ever been seen live) |
| `Installed` bit gates a function's tile; stairs / LR-heater / satellite / roof-A/C screens + vocabulary | APP-OBSERVED (Installed flipped on the fake unit) | — (not fitted on this van) |
| airheater **permanent-ON** (NOT wired — only OFF is known) | unknown | app: enable permanent heating → learn the ON value |
| energy `mode` (EnergyModeSet 0/1/2) | DECOMPILE | app: switch eco/normal/max → diff 1601 frames |
| lighting `profile` activate + `save_profile` (favorite define) | DECOMPILE | app: activate a favorite; edit+save a favorite → diff 1501 frames |
| lighting **wake-up TIME** (`m0`, Mode 20 — LightValue bitmask packing unknown) | unknown | app: arm a wake-up alarm → learn the Timestamp + LightValue packing |
| roof drive end-to-end (frames match app; motor never driven by calictl) | DECOMPILE + partial DEVICE | calictl drives the roof with ignition on, owner-watched |
| roof: no-heartbeat arm (handshake + immediate SafetyCounter stream, no 1003 / no ARM_DELAY_S) is app-faithful — fixed #150; motor still never driven by calictl | DECOMPILE | first owner-watched roof move confirms the arm on-device |

## Already at CAPTURE / DEVICE (examples, keep as the model)

- cooler power/level frames, airheater on/off — CAPTURE (HCI 2026-07-08/14; `tests/scenarios/`).
- cooler `night_on`/`night_off` + `mode` timer_quiet(4) — DEVICE (live writes 2026-08-26, PR #112): hours
  + Mode are stored on the unit (survive reconnects) and every change is **broadcast as an unsolicited
  1102 notification**.
- cooler night-schedule bytes are **LITERAL, not optional** — DEVICE (2026-08-26): a frame carrying
  `NightTimerHourOn=0` clobbered a just-set 22 (unit pushed `quiet_from 22->0`). ⚠️ CORRECTS the earlier
  ledger line "NightTimer sentinels = 0 — CAPTURE": the captured power-on frame *did* send 0s, but that
  van had **no schedule set**, so 0 was simply the current value — the capture never showed that 0 means
  leave-unchanged (it doesn't; the leave-unchanged sentinels are `v()`'s 31/3, and the app re-sends them
  in its 500 ms post-write neutral frame). **A capture only validates the state it was taken in.**
  `_cooler_values` now carries the current schedule in every write.
- lighting per-zone SET + power — DEVICE (photon-verified 2026-08-16).
- general(1001) SW-version decode + DC-DC +2 — DEVICE (live-read `0410`, `dcdc_current` −2→0, 2026-08-17).
- roof InfoPopUp `5` = DRIVING (`_ROOF_ALERT`) + the web move-gate's block set {child_lock, error,
  driving, emergency_locked, not_possible, low_battery, Position==15} — DECOMPILE (2026-09-15,
  `ig/c.java` `j()` movable-check; sensor_error is warn-only there). Not yet seen live: the van has
  never reported 5 while calictl was polling. Owed: one drive with the roof screen open.
- roof InfoPopUp → dialog texts (1 over-use cooldown, 5 roof-open-while-driving, 6 jammed/blocked,
  7 secure manually, 10 unavailable, 11 low battery/run engine) — DECOMPILE (2026-09-16, `ig/c.java`
  switch → `ea/j`/`ea/n` string accessors → `.cvr` EN/DE tables).
- campingmode `Enable` (1202 bit 3) = terminal-15, **one poll behind** 1004 `TerminalOneFive` —
  DEVICE (2026-09-16, camping-watch: 8 paired `ignition 0→1` / `enable 0→1` / `master_on 1→0`
  transitions; owner screenshot of the lag). Decompile: `tf/a.java n0()` ignitionTerminal15Flow.
- air-heater run-time cap 120 min + ErrorCode IDs 1–5; cooler quiet-needs-ON / timer-needs-OFF
  gates — DECOMPILE + string tables (2026-09-16, `rf/b.java`, `infoPage_heating_immediate_description`,
  `coolboxPage_*` strings). Not live-provoked.
- **APP-OBSERVED tier (new, 2026-09-16):** the real CaliforniaOnTour app running in an Android
  emulator against `tools/applab/fake_unit_ble.py` (a Bumble peripheral serving `tools/mock_unit.py`).
  Not the unit — but the app's genuine frames, dialogs and gates. Rows: `1002` = `SHA-256(VIN)[16:32]`
  (mismatch → "Wrong vehicle found"); heater ON `3d7b007f1f3f` + neutral `3f7b007f1f3f` @ +500 ms,
  continuous-heating OFF `0f7b007f1f3f`, untargeted fields at their defaults; roof page streams
  `Up=0 Down=0 SafetyCounter+1` every ~500 ms and requires `SafetyCounterValid`; roof `InfoPopUp`
  1–15 → dialog/tile texts (2/3/12 in use, 9 not stationary, 8/13/14 nothing); heater sliders 1–9+HI
  and 10–120; `1003` heartbeat cadence ~750 ms while the app is connected; lighting All-lights
  frames byte-identical to calictl's, lamp taps write nibble value 11 (DEFAULT), and the app's lamp →
  nibble positions confirm calictl's DEVICE-verified `LIGHT_ZONES` for all nine app lamps (the
  cabinet light `LSix` has no app control); cooler
  OFF/quiet/timer frames and camping master OFF (`fc`, identical) as tabled in
  `control-and-actuation.md`.
- energy current scales — DECOMPILE (2026-09-07): `ITwoBattBemAfs`/`ILandAfs`/`IPvAfs` ÷10 → A
  (`xf/d.java:159,173,175`, holders bound `xf/a.java:150-157,239,307`), `IDcdcAfs` unscaled A + the
  SW-0409/0410 `+2` (`xf/d.java:171`). Plausibility from 14 d telemetry: `batt2_current` raw −49…318
  → −4.9…31.8 A with mean ≈ 0 (balanced leisure battery) — consistent, not a calibration. Owed: one
  metered shore-charging read to upgrade `shore_current` to DEVICE.
- roof `Installed=1` — DEVICE (live read 2026-08-26, #106): the pop-top IS installed (motor never driven).

Captures come from the Mac "bar" (PacketLogger/tshark) or buspi HCI. When one lands: add a
`tests/scenarios/<fn>/<case>.yaml`, assert our frame matches in `test_capture_diff.py`, flip the row
to CAPTURE, and drop the GUI "not verified" confirm for that control. See the memory
`capture-evidence-tier`.
- lighting **zone 9 = pop-top roof READING light** — DEVICE (2026-08-30, single-light isolation:
  unit screen "Dach Ein/Aus" on, live read `zone_9=2`, all other zones 0/13). Exposed an `any_on`
  bug (a zones-1..8 whitelist excluded it — fixed same day). NB conflicts with the 2026-08-27
  "pop-roof = zone 5" toggle note — likely two distinct roof fixtures; map still needs the full pass.
- lighting **full 10-lamp map** — DEVICE (2026-08-30, single-light isolation + owner-watched writes):
  L1=Lesen-R, L2=Lesen-L, L3=Umgebung-hinten, L4=Lesen-vorne, L5=Küche-Ambient, L6=Küche-Schrank,
  L7=Küche-Kochen, L8=Dach-Ambient, L9=Dach-Lesen, L12=Eingang. Fixed the roof-reading mislabel
  (was L6→ now L9; L6=cabinet) and added L12 to the real-zone set. The L6 write was calictl→unit,
  owner-confirmed the cabinet lamp lit (bonus live actuation check).
- BlueZ needs a quiet radio to make a new LE connection: **0x3e under full-duty scanning** —
  CAPTURE (btmon on buspi, 2026-09-25): `LE Connection Complete`, then BlueZ re-enabled active
  scanning at 100% duty (window == interval, 11.25 ms) because another client held discovery,
  ~300 ms later `Connection Failed to be Established (0x3e)`; with all scanners stopped the same
  connect succeeded. See `guided-pairing.md` "Environment the wizard needs".
- the camper unit **advertises from a rotating address** — CAPTURE (observed 2026-09-25):
  four different advertising addresses seen over about 45 minutes of continuous scanning; only
  the bonded identity address is stable, and only becomes known once bonded.
- the unit's own screen shows **"Passcode: ---" until a pairing request arrives** — OBSERVED
  (owner photo, 2026-09-25): the placeholder stays literal `---` until a central starts pairing
  against the unit, then is replaced by the 6-digit passcode.
- roof **Position decode + L9=roof-reading** — DEVICE (2026-08-30, roof physically opened): live read
  `roof.Position=1 -> position_name "open"` (first live confirm — roof was never driven before), and a
  `roof-reading`(L9) write lit the pop-top reading lamp only with the roof up. Gates the write: L9 is
  unpowered while the roof is closed.
- cooler **cooling-timer decode** — DEVICE (2026-08-30, owner set Startzeit 09:00): live wire
  `timer_active=True, timer_hour=9, timer_min=0` matched the unit screen (was decompile-only). The
  timer can only be armed while the fridge is off — gated.
