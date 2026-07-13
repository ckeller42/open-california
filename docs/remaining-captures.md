# At-the-van plan — closing the last ~2% (van-gated)

> **STATUS 2026-07-13 (captures done):** **P1 lighting-apply CRACKED + FIXED** (commit frame
> `0e00…`; live-verified via readback). **Roof sequence SETTLED from the decompiled roof class**
> (open `0x01`/stop `0x00`/close `0x04`; **press-and-hold** move stream, **app-generated monotonic
> SafetyCounter ~+1/500 ms**, unit self-gates the first **~3 s** via `SafetyCounterValid` (`1402`
> bit 7) — NO 4 s countdown, NO STOP-hold phase — see `protocol-sequences.md` §3). SET_COLOR is
> **not exposed** in the app (skip). Cooler `Error` = **fridge door** (now surfaced in the GUI).
> Remaining: roof-drive fixes (P5), air-heater level/runtime, WAKEUP_TIME, full lamp map.
>
> **Required fixes before enabling roof actuation from calictl (roof stays NOT-LIVE-VERIFIED):**
> (a) **generate a live monotonic BE-uint32 SafetyCounter (~+1/500 ms) from a seed** — do NOT echo
> the unit's counter (that capture reading was wrong), and not `actuate_roof`'s fixed-start `+1`;
> (b) **account for the ~3 s unit self-gate** — the motor is withheld until the counter validates;
> optionally honour `SafetyCounterValid` (`1402` bit 7) and mirror the app's **3000 ms** dead-man
> timeout. Press-and-hold: just keep streaming move frames, **no** move→STOP "hold" phase;
> (c) **cadence ≈ 500 ms**, not 1 Hz. `device.actuate_roof` does none yet.

Everything statically recoverable from the APK is done (protocol ~98%, semantics verified). What
remains needs the **physical van** (a capture / a driven reference) — it cannot be decompiled.
This is the ordered checklist for the next session, highest-leverage first.

Do actions **one at a time with ~3 s pauses** and **distinct values** so each frame is easy to
correlate. Two tools: `idevicebtlogger` on the **bar** Mac (captures the iPhone app's BLE), and
`calictl` / the spike on **buspi**. Recording: I trigger it — you say "go", I confirm packets
flow, you act, you say "done", I decode.

## Runbook — copy-paste (staged 2026-07-13, ready to run)

Scenarios for the differential oracle are **pre-staged** under `tools/scenarios/` (all build a valid
calictl frame — verified offline). The capture is the only manual seam; everything after is one command.

**0. Confirm the van woke (buspi).** After a door/ignition, within one poll cycle (~30 s):
```
ssh buspi 'curl -s http://localhost:8088/api/state' | python3 -c 'import sys,json;m=json.load(sys.stdin)["_meta"];print("online:",m["online"],"age_s:",m["age_s"])'
```
`online: True` → buspi has the slot. If the phone app is open it may hold the single slot — close it.

**1. Capture (bar Mac) → diff (repo host).** Per action: start the logger, do ONE thing in the app,
stop, then diff. Capture SOP + iPhone UDID: `.claude/skills/capture-and-diff/SKILL.md`.
```
# on the bar: idevicebtlogger -u <UDID> -f pcap /tmp/<label>.pcap   (Ctrl-C to stop)
# then, where the repo + tshark live:
python3 -m tools.capture_diff /tmp/<label>.pcap <scenario>
```
Run the **known-good validators FIRST** (must diff to zero — proves the pipeline):
| Order | App action | Scenario | Expect |
|---|---|---|---|
| a | Camping Mode ON | `campingmode/master-on` | zero diff |
| b | Cooler power ON | `cooler/power-on` | zero diff |
| c | **Kitchen lamp → 5** (profile active) | `lighting/kitchen-50` | **LEADS = the P1 crack** |
| d | Set light colour → red | `lighting/color-red` | zero = SET_COLOR layout verified |
| e | Switch to profile 9 | `lighting/profile-9` | zero diff |

**2. Stability spike (buspi, phone app closed):**
```
ssh buspi 'cd /home/pi/open-california && python3 -m tools.ble_stability_spike --seconds 180'
```

**3. Wake-signal diff (buspi):** baseline read → physical door/ignition → re-read → diff the field
that flips (marks fresh-vs-stale). Cloud unlock does NOT wake it.

**4. Read-side verifications (buspi) — NOT capture_diff (no control write):**
```
ssh buspi 'cd /home/pi/open-california && python3 -m calictl get vehicle'   # ignition ON => ignition_on: true  (bit-7 fix)
ssh buspi 'cd /home/pi/open-california && python3 -m calictl get water'     # ignition ON => fresh ~11 L; OFF later => decays
# cooler night-timer: set one in the app, capture, then decode-only:
#   python3 -c 'from calictl import protocol,overrides,control as c;f=protocol.load();overrides.apply(f);print(c.decode_control(f["cooler"], bytes.fromhex("<app-frame>")))'
#   -> confirm TimerHour/TimerMin land at the corrected offsets (16/24/32/40)
```

**5. Roof control (SAFETY-SENSITIVE — REQUIRES IGNITION ON; only with the roof physically clear).**
⚠️ **Ignition (terminal-15) must be ON** — the pop-top actuator is dead with ignition off, so the
app's roof buttons do nothing and there's nothing to capture. Do this in the **same ignition-on
window** as the P4 ignition/leveling checks (step 4). Ensure the roof path is clear before moving it.

Not a `capture_diff` scenario: the app drives the roof with a *repeated* 1 Hz move frame + a final
STOP, so a single committed frame doesn't represent it. Capture the app doing
**open → partial → Stop → close** and inspect the sequence directly (the move-frame direction byte
+ the +1 `SafetyCounter` cadence, and that a STOP frame follows):
```
# ignition ON, roof path clear, phone app open
# on the bar: idevicebtlogger -u <UDID> -f pcap /tmp/roof.pcap   (Ctrl-C after the STOP)
# then dump every write to the roof control char (1401) in order:
tshark -r /tmp/roof.pcap -Y 'btatt.opcode.method==0x12 || btatt.opcode.method==0x52' \
  -T fields -e btatt.handle -e btatt.value | grep -i '<roof-handle>'
```
This live-verifies `device.actuate_roof`'s move-heartbeat + STOP against the real app before we
ever drive the roof from calictl. Roof writes are `CONFIRM_REQUIRED` and NOT-LIVE-VERIFIED.
(Live data 2026-07-13 confirmed the van reports `roof` **installed**, so this is a real, capturable
feature on this vehicle — unlike the uninstalled gear below.)

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
- **roof drive from calictl** (SAFETY-SENSITIVE — **needs ignition ON**, roof path clear): the
  sequence is now settled (decompiled roof class — press-and-hold, app-generated monotonic
  SafetyCounter ~+1/500 ms, ~3 s unit self-gate; see `protocol-sequences.md` §3 + the required
  fixes above). What remains is **implementing + live-verifying** the three fixes in
  `device.actuate_roof`, then a controlled open→Stop→close drive from calictl.

## Out of reach even at the van (WiFi, not BLE)
- **Exlap data-body schemas** — need the camper's WiFi AP up + an Exlap/TCP capture on
  `192.168.2.1`; a separate transport, not a BLE capture.
- **Uninstalled features** (satellite/stairs/roof-A/C/LR-heater) — no hardware on this van.
