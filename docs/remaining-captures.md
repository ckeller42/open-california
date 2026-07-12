# At-the-van plan — closing the last ~2% (van-gated)

Everything statically recoverable from the APK is done (protocol ~98%, semantics verified). What
remains needs the **physical van** (a capture / a driven reference) — it cannot be decompiled.
This is the ordered checklist for the next session, highest-leverage first.

Do actions **one at a time with ~3 s pauses** and **distinct values** so each frame is easy to
correlate. Two tools: `idevicebtlogger` on the **bar** Mac (captures the iPhone app's BLE), and
`calictl` / the spike on **buspi**. Recording: I trigger it — you say "go", I confirm packets
flow, you act, you say "done", I decode.

## Priority 1 — the lighting-apply gap (unlocks the MOST)
Our lighting frames ACK but the zones don't visibly change (`re-gap A1`). Cracking this unlocks
**all lamp control + colour + profile authoring** at once.
- With the app: **activate a profile, then change ONE lamp's brightness** (e.g. kitchen 0→8).
- Then **change a colour** (SET_COLOR) and **edit/create a favorite profile**.
- I diff the app's real writes against `control._lighting` (SET_BRIGHTNESS / SET_COLOR=Mode 6 /
  SET_PROFILE) to find what it carries that we don't — likely a `LightValue`/SET_PROFILE preamble.
  Also confirms `SET_COLOR` (now wired) and the `WAKEUP_TIME` packing.

## Priority 2 — can buspi hold a persistent session? (continuous data)
Van awake, **phone app closed**, on buspi:
`PYTHONPATH=/home/pi/open-california python3 -m tools.ble_stability_spike --seconds 180`
Verdict tells us if a persistent-read mode is viable, or if intermittent access is inherent (→ the
offline/hold-last design is the ceiling).

## Priority 3 — the "van awake" freshness signal
Baseline read, then **open a door / ignition on**, then re-read all functions and diff. The field
that flips = the gate for marking sensor values fresh vs stale. (Cloud unlock does NOT wake the
unit — must be a physical door/ignition, phone app closed so buspi gets the slot.)

## Priority 4 — verify the frames we just fixed/decoded (decoded-but-UNVERIFIED)
- **vehicle ignition byte-0**: ignition ON → confirm `ignition_on` reads True (the bit-7 fix).
- **water ↔ ignition**: ignition on → 11 L; off → decays.
- **cooler night-timer** (corrected offsets 16/24/32/40): set a night-timer, capture, confirm.
- **SET_COLOR** (Mode 6): `set lighting color amber` with a profile active — capture + confirm it
  matches the app's colour write, and (with P1) whether it applies.

## Priority 5 — finish the smaller gaps
- **air-heater level + runtime** (only on/off captured; the physical fields were at sentinels):
  drag Heizstufe to 3 then 8; Laufzeit to 30 then 90 min.
- **WAKEUP_TIME** (Wecklicht): set a wake-alarm → capture → confirm the epoch-seconds `Timestamp`
  + the internal packing of `LightValue` (both currently INFERRED).
- **full lamp→nibble map**: ~6 of 16 lamps mapped; set each remaining lamp to a distinct level.
- **roof open/close** (SAFETY-SENSITIVE, only if the roof path is clear): open→partial→Stop→close,
  to live-verify the move frames + SafetyCounter cadence.

## Out of reach even at the van (WiFi, not BLE)
- **Exlap data-body schemas** — need the camper's WiFi AP up + an Exlap/TCP capture on
  `192.168.2.1`; a separate transport, not a BLE capture.
- **Uninstalled features** (satellite/stairs/roof-A/C/LR-heater) — no hardware on this van.
