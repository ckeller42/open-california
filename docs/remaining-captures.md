# At-the-van plan — closing the last ~2% (van-gated)

## 🚐 YOUR CHECKLIST FOR THE NEXT VAN VISIT (as of 2026-07-14)

Only these need you physically at the van. Everything else is done or is a desk task.

### 1. Water — settle "is 1 L stale or real?" (PRIORITY, unresolved)
buspi reads **fresh water = 1 L** while polling fine every ~45 s with the heartbeat running.
We do **not** know if that's stale or the tank is genuinely near-empty (the "true 11 L" was a single
2026-07-09 reading that isn't reproducing). Do this to settle it:

- [ ] **Before touching anything**, note the **actual** fresh-tank level (gauge / how full you know it is).
- [ ] **Close the phone app** so buspi owns the single BLE slot.
- [ ] Read buspi now: `ssh buspi 'curl -s localhost:8088/api/state | python3 -c "import sys,json;print(json.load(sys.stdin)[\"water\"][\"fresh\"])"'`
- [ ] **Run a tap for ~20–30 s** (pump active), wait ~1 min, read buspi again.
- [ ] Tell me three numbers: **actual level**, **buspi before**, **buspi after the pump ran**.
  - buspi jumps toward the real level → the sensor needs *pump flow* to re-measure (real finding).
  - buspi already matches the tank → **no bug, 1 L was correct**, and the "fix" was for a non-issue.
  - buspi stays wrong even after the pump → real stale bug to reopen.

### 2. Finish the lamp map (~1 min)
Sweep 2026-07-14 confirmed **L7=Kochen, L8=Ambientelicht, L3=Umgebung hinten**, and **L5 is a real
lamp**. Two gaps:

- [ ] **Screenshot the TOP of the Beleuchtung list** (or just name the top 4 lamps) — pins L2, L1, L4, **L5**.
- [ ] **Capture L6** (the one zone never seen — it's a conditional lamp): **open the pop-roof** so
  *Leselicht* unlocks and set it, **and/or activate camping mode** so *Eingang* unlocks and set it.
  Ping me to start the logger first.

### 3. Roof drive from calictl (OPTIONAL — higher risk)
The roof **protocol is now live-verified** (capture 2026-07-14: dir bytes 0x01/0x04/0x00, free-running
+1/500 ms counter, press-and-hold, Position closed→middle→closed all confirmed against real motion).
The only thing left is proving **calictl itself** can drive it:
- [ ] Ignition **ON**, roof path **clear**, writes enabled → I run a bounded `open → stop → close` from
  calictl and read `1402` back. Only do this if you want it; the protocol is already proven.

**Not van tasks:** `car_variant` (buspi read, anytime); the air-heater 1–10 fix + roof-verified status
(desk changes, in progress).

---

> **STATUS 2026-07-13 (captures done, lighting claim later corrected):** ~~P1 lighting-apply
> CRACKED + FIXED (commit frame `0e00…`; live-verified via readback)~~ — **that "verification"
> was readback-only and WRONG** (the `1502` state char is a write-through echo; the lamps stayed
> dark, owner-confirmed 2026-07-18). **P1 was cracked for real 2026-08-16**: a capture of the app
> *physically* lighting a lamp showed the missing **REQUEST_CONFIG preamble** (`0d0c…ee`,
> Mode=12, PN=13) + `0e00…` commit on `1501` before the SET — photon-verified on Kochen L7
> (0→dark, 8→80 %, 0→dark). See `control-and-actuation.md` §4.
> **Roof sequence SETTLED from the decompiled roof class**
> (open `0x01`/stop `0x00`/close `0x04`; **press-and-hold** move stream, **app-generated monotonic
> SafetyCounter ~+1/500 ms**, unit self-gates the first **~3 s** via `SafetyCounterValid` (`1402`
> bit 7) — NO 4 s countdown, NO STOP-hold phase — see `protocol-sequences.md` §3). SET_COLOR is
> **not exposed** in the app (skip). Cooler `Error` = **fridge door** (now surfaced in the GUI).
>
> **UPDATE 2026-07-14 (at-the-van captures):** **roof protocol LIVE-VERIFIED** (dir bytes + free-running
> +1/500 ms counter + press-and-hold + Position closed→middle→closed confirmed against real ~30 cm
> motion); **air-heater level range = 1–10** (HI=10; `HeatingLevel=11` is a post-set commit frame);
> **lamp map** mostly done (L3/L5/L7/L8 confirmed; L6 = a conditional lamp, still to capture). New open
> item: **water reads 1 L, stale-vs-real unresolved** (see the checklist above). The old "Remaining"
> list is superseded by the **YOUR CHECKLIST** section at the top.
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

## Which unclear items a capture can actually resolve (2026-07-14)

The test is **"is the unknown on the BLE wire?"** A passive HCI capture of the app driving the van
answers wire facts; it adds nothing for app-side logic or unexposed features.

| Unclear item | Capture resolves it? | What to do |
|---|---|---|
| **Air-heater level range** (is it 1–10 or the raw 0–15?) | ✅ **yes, fully** | In the app, set the heater to its **lowest** then **highest** level. The writes to char `1701` carry the exact min/max `HeatingLevel` raw values → pins the real range. Until then `semantics.airheater` keeps the honest raw 0–15 (`HeatingLevel` is 4-bit, dict-marked `UNVERIFIED`). |
| **`car_variant` → model name** | 🟡 **partial** | A single **read** of char `1004` shows this van's raw `CarVariant`; the app screen shows the model. Pairs raw→label for *this* van (confirms whether `1 = California 7`, per `te/a.java`). Can't prove the other variants — only one vehicle. Kept as raw int meanwhile. |
| **Vehicle clock-out-of-sync** | ❌ **no — not on the wire** | The RTC decode is already correct. "Out of sync" is the app comparing the unit clock to the **phone** clock (>5 min, `zf/d.java:235`). Nothing to capture — implement daemon-side (compare `vehicle.car_clock` to the Pi system clock) if wanted. |
| **Lighting colour (`SET_COLOR`)** | ❌ **no — not exposed** | The app has **no colour UI** (decompile-confirmed), so it never emits a colour frame. A capture only confirms the negative. `set lighting color` stays inferred/N-A. |

**Highest-leverage capturable targets** (all wire facts, grab them in one at-the-van session):
1. **Roof actuation** — the last control path still NOT-LIVE-VERIFIED, and the roof **is** fitted here.
   A *passive* capture (you tap, we watch — zero risk) confirms SafetyCounter cadence, the ~3 s
   self-gate, and direction bytes vs `device.actuate_roof`. See §5 + Priority 5.
2. **Air-heater level range** (above) + a live read while it runs.
3. **Full 16-zone lamp map** — only L1–L8 identified; drag each remaining lamp to a distinct level.

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
| c | **Kitchen lamp → 5** | `lighting/kitchen-50` | zero diff (P1 crack photon-verified 2026-08-16) |
| d | ~~Set light colour → red~~ | ~~`lighting/color-red`~~ | **N/A — app exposes no colour control** |
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

## Priority 1 — the lighting-apply gap (RESOLVED 2026-08-16, photon-verified)
~~Our lighting frames ACK but the zones don't visibly change (`re-gap A1`).~~ **Cracked exactly
this way**: a 2026-08-16 capture of the app *physically* lighting a lamp, diffed with
`tools/capture_diff.py`, showed the app opens its Lighting screen with a **REQUEST_CONFIG
preamble** (`0d0c000000000000eeeeeeeeeeeeeeee`, Mode=12, PN=13) + the `0e00…` commit on `1501`;
only in a session where that landed does a SET_BRIGHTNESS drive the real lamps. Photon-verified
on Kochen L7 (0→dark, 8→80 %, 0→dark, owner-watched) — the moral stands: **only a human seeing
the lamp counts; readback of `1502` is a write-through echo.** See `control-and-actuation.md` §4
+ `re-gap-inventory.md` §A1.

**Remaining lighting capture targets:**
- The app's **"Alle Lichter" master frame** — presumably SET_PROFILE with profile 12=LIGHTS_ON /
  0=LIGHTS_OFF (`dg/l.java`), but **uncaptured**.
- **SET_COLOR apply** — the app exposes no colour control (possibly N/A); a colour frame has
  never been captured.
- **The `1502` Mode-4 ramp-notification layout** — the truthful actuation-feedback channel found
  2026-08-16 (real brightness stepping to the target); its layout is not yet decoded.

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
- ~~**SET_COLOR** (Mode 6)~~: **dropped** — the app exposes no colour control (decompile-confirmed
  2026-07-13), so there is no colour write to capture.

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
