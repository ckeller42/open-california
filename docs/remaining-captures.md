# In-car capture plan — the remaining ~10%

A field checklist for the next capture session, to close the gaps that need the app + the
vehicle. Everything else (cooler, camping, lighting, air-heater on/off, roof frames, leveling)
is already cracked/verified. Do actions **one at a time with ~3 s pauses** and **distinct
values** so each frame is easy to correlate. See `.claude/skills/capture-and-diff` for the tool.

## Setup (I trigger the recording)
1. Wake the bar (Mac) so `ssh bar` works; keep the phone plugged in / the iOS Bluetooth-logging
   profile active. Tell me "go".
2. I start `idevicebtlogger` on the bar and confirm packets are flowing before you tap anything.
3. You run the actions below; reply "done"; I stop + decode against our model.

## Priority 1 — Air-heater level + runtime (finishes air-heater)
Only on/off was captured (the physical fields were at "leave-unchanged" sentinels). On the
Heizung screen:
- Drag **Heizstufe** (level) to two distinct values (e.g. 3, then 8) — pause between.
- Drag **Laufzeit** (runtime) to two distinct values (e.g. 30, then 90 min).
- Toggle the **Timer** on with a set time; toggle it off.
→ gives the HeatingLevel / RunningTime / timer frame layout + scale.

## Priority 2 — Full lamp → nibble map (finishes the lighting zone map)
~6 of 16 lamps are mapped. Set **every** lamp to a **distinct** level, one at a time:
- Leselichter: **Links**, **Rechts**, **Beifahrer**
- **Küche** (each sub-lamp it exposes)
- Aufstelldach: **Ambientelicht** — and **Leselicht** (only shows with the roof open, so open
  the roof a little first if you want this one)
- Außenlicht: **Umgebung hinten** — and **Eingang** (only in Camping mode, so enable camping
  first if you want this one)
→ maps each remaining `BrightnessL` nibble to its physical lamp.

## Priority 3 — Lighting: Alle-Lichter + profiles
- Toggle **Alle Lichter** ON, then OFF (captures the master all-on / all-off frame).
- Switch/create profiles **A → B → C → D** (tap each; "+" creates one). Captures the
  SET_PROFILE (Mode 16) payload per profile → the profile-number semantics (we know A=9).

## Priority 4 — Lighting SET_COLOR + Wecklicht (extras)
- If a lamp supports **color**, change one zone's color (captures the SET_COLOR frame + Mode).
- **Wecklicht** (wake-light, under the lighting screen): set a wake time + lead time +
  target brightness, toggle it **on** (captures its config frame).

## Priority 5 — Cooler timer / quiet-mode (pins the last cooler enum)
On the Coolbox screen: switch **Quiet mode** manual → timer → off (captures the Mode enum
0/2/4), and set the **timer** (start/cancel + hours/minutes) — confirms the timer field layout.

## Priority 6 — Roof open/close (LIVE-VERIFY — safety-sensitive)
**Only if parked with the roof path fully clear.** We have the frames but never confirmed a
real move end-to-end. Do it slowly:
- **Open** and hold to a partial position (~a few seconds) → **Stop**.
- **Close** fully.
→ confirms which byte0 is open vs close, the STOP frame, and the SafetyCounter cadence during
an actual move. This is the one item that also needs the physical roof to move.

## Not capturable here (for reference)
- **EXLAP over WiFi** — needs the infotainment WiFi up (ignition) + buspi joined to that SSID;
  a different transport, not a BLE capture.
- **Uninstalled features** (roof-A/C, stairs, LR-heater, satellite) — no hardware on this van.
