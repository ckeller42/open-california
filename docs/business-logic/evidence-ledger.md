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
| airheater `runtime`, `timer` | DECOMPILE | app: set run-time + a start timer → diff 1701 frames |
| airheater **permanent-ON** (NOT wired — only OFF is known) | unknown | app: enable permanent heating → learn the ON value |
| energy `mode` (EnergyModeSet 0/1/2) | DECOMPILE | app: switch eco/normal/max → diff 1601 frames |
| lighting `profile` activate + `save_profile` (favorite define) | DECOMPILE | app: activate a favorite; edit+save a favorite → diff 1501 frames |
| lighting **wake-up TIME** (`m0`, Mode 20 — LightValue bitmask packing unknown) | unknown | app: arm a wake-up alarm → learn the Timestamp + LightValue packing |
| roof drive end-to-end (frames match app; motor never driven by calictl) | DECOMPILE + partial DEVICE | calictl drives the roof with ignition on, owner-watched |
| roof: whether the 1003 heartbeat + 3 s ARM_DELAY_S pre-arm is needed (app doesn't do it) | unknown | trial with/without on an awake unit |

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
- roof `Installed=1` — DEVICE (live read 2026-08-26, #106): the pop-top IS installed (motor never driven).

Captures come from the Mac "bar" (PacketLogger/tshark) or buspi HCI. When one lands: add a
`tests/scenarios/<fn>/<case>.yaml`, assert our frame matches in `test_capture_diff.py`, flip the row
to CAPTURE, and drop the GUI "not verified" confirm for that control. See the memory
`capture-evidence-tier`.
- lighting **zone 9 = pop-top roof READING light** — DEVICE (2026-08-30, single-light isolation:
  unit screen "Dach Ein/Aus" on, live read `zone_9=2`, all other zones 0/13). Exposed an `any_on`
  bug (a zones-1..8 whitelist excluded it — fixed same day). NB conflicts with the 2026-08-27
  "pop-roof = zone 5" toggle note — likely two distinct roof fixtures; map still needs the full pass.
