# RE gap inventory — what of the CaliforniaOnTour app is NOT yet understood

> **NOTE (2026-07-12):** this is the original 2026-07-07 audit. Several gaps here have since been
> **resolved** — see **`protocol-alignment.md`** (cooler-timer offsets, satellite `1901`, vehicle
> byte-0, all lighting modes) and **`value-freshness.md`** (the stale-read / deep-sleep behavior)
> for the current state. The BLE protocol is now ~98%; the residual is van-gated (see
> `remaining-captures.md`).
>
> **UPDATE (2026-07-13):** `A1` (lighting on-device apply) is **RESOLVED** — the unit applies a
> SET only after a **commit frame** `0e00000000000000eeeeeeeeeeeeeeee` (Mode 0); our SET frames
> were already byte-identical to the app's. HCI-captured + confirmed on-device via readback. Fix:
> `device.actuate(..., follow=control.LIGHT_COMMIT)`. See `protocol-sequences.md` §2 + CLAUDE.md
> Known state. Also 2026-07-13: roof frames verified (SafetyCounter-echo gap noted); the app
> exposes **no colour control**, so SET_COLOR is unverified/possibly N/A; the cooler `Error` field
> tracks the **fridge door** (door open→true, verified live).
>
> **UPDATE (2026-08-16):** the 2026-07-13 "resolved" claim above was **readback-based and wrong**
> (the `1502` state char is a write-through echo; owner-confirmed 2026-07-18 the lamps stayed
> dark). `A1` is now **RESOLVED for real, photon-verified 2026-08-16** — with the unit **awake**,
> a bare SET_BRIGHTNESS + `0e00…` commit physically actuates; the real gate is the unit's
> wake/active state. (The same morning's "REQUEST_CONFIG preamble required" theory was falsified
> that evening — a wake-state confound.) See §A1.

Compiled 2026-07-07 from a three-way decompile audit (BLE protocol, feature/domain
logic, session/auth/infra), each diffed against the current baseline: `protocol/
dictionary.yaml` (13 functions), `protocol/signals.yaml`, `ui/screens/*.yaml` (18
screens), and the `docs/business-logic/*.md` writeups. Citations are `file:line`
under the JADX decompile (the decompiled sources (clean pass) = CLEAN,
the decompiled sources (bad-code pass) = method bodies jadx dropped from clean). VW material:
citations only.

**Codec master key** (resolves most "width UNVERIFIED" statically): every field is an
`sg.a` bit-cell whose 2nd ctor arg selects width — `sg/a.java b()`: type 2/7→8-bit,
3→16-bit signed (`u8.g`), 4→2-bit, 5→32-bit, 6→4-bit, 1→16-bit. `f()` in each builder
places those bits; `tt/u8.java:88 c()` = little-endian bytes, MSB-first within a byte.

---

## Cross-cutting priority ranking (open only)

| # | Gap | Kind | Resolvable now? |
|---|-----|------|-----------------|
| ~~1~~ | ~~Lighting SET does not actuate~~ — **RESOLVED 2026-08-16, photon-verified** (unit awake ⇒ bare SET+commit actuates, §A1) | protocol | ✅ done |
| 3 | MERGED_AMBIGUOUS control offsets — **roof/roofAC/stairs/LR-heater remain** (airheater done) | protocol | **static** |
| 4 | **1003 counter cadence / firmware disarm timeout** | protocol | needs idle HCI capture |
| 5 | Lighting **profile/color/wake-timer** notification overlay | protocol | static (enum decode) |
| 6 | **EXLAP over WiFi/TCP** — an entire second transport (99 files) | infra | reachability-blocked |
| 7 | Whole feature modules untracked: **Travel, Search, Discover/CMS, AiAssist, Camera, Developer** | feature | static |
| 8 | **Light scenes/favorites** model (7 slots × 8 zones × on+brightness) | feature | static |
| 9 | VW **OIDC/IDK** auth + backend request signing | infra | static + capture |
| 10 | UI label *text* — 1882 string KEYS recovered, VALUES in `.cvr` assets (light zone names done) | feature | needs the APK's `.cvr` |

Resolved gaps (chars 1004/1002/sat, airheater offsets, energy scales, EXLAP URLs) and the dated
changelog are in **[DECISIONS.md](DECISIONS.md)**. (Lighting SET: twice thought fixed on readback
evidence, finally photon-verified 2026-08-16 — §A1.)

---

## A. BLE protocol / control gaps

### A1 — Lighting SET — RESOLVED 2026-08-16 (unit awake ⇒ bare SET actuates; photon-verified)
**The physical-actuation gap is closed.** With the unit **awake/active**, a **bare
SET_BRIGHTNESS + `0e00…` commit physically drives the lamps, both directions** — no
REQUEST_CONFIG preamble, no 1003 heartbeat, no arm delay (settled 2026-08-16 evening: 6×
photon-confirmed, incl. three controlled fresh-connection trials varying only the arming;
Trial 1 = immediate cold-connection SET+commit, lamp switched, owner-watched). The morning's
"REQUEST_CONFIG preamble is the required arming step" theory (from an iOS HCI capture of the
app lighting a lamp, diffed via `tools/capture_diff.py`) was falsified that evening — a
wake-state confound: the preamble path added ~3.3 s + extra frames while the unit was still
sleepy. Decompile agrees: the app's set-one-zone `dg/h.java:174 E()` writes DIRECT and never
calls the config request `d0()` (`dg/h.java:471`). **REQUEST_CONFIG's real role is a
screen-open config pull**: `0d0c000000000000eeeeeeeeeeeeeeee` (Mode=12, ProfileNumber=13, all
zones=14) triggers the unit's Mode-tagged config dump + Mode-4 brightness-ramp
**notifications on `1502`** — the truthful feedback channel (a Mode-4 notification is a
normal state frame, `protocol.decode` reads it; the state-char readback remains a
write-through echo). Brightness is the `dg/i.java` enum (0=OFF, 1-10=10–100 %, 11=DEFAULT,
13=NOT_EQUIPPED, 14=unchanged), not a raw 0-13 scale. calictl still sends the preamble —
app-faithful, harmless, optional (`control.preamble_for`, `device.actuate(..., pre=…)`,
`R_LIGHT_PREAMBLE`). Full story: **`control-and-actuation.md` §4**.

Earlier layers of this gap (kept for history): the 2026-07-08 capture nailed the 14-sentinel
(unchanged zones = `14`, NOT 0) and Mode-16 profile switch; the "profile must be ACTIVE" rule
later proved to be the state-char echo bug (the real frame rule is hardcoded PN=9,
`dg/h.java:170`); the 2026-07-13 `0e00…` commit frame is necessary but was NOT sufficient —
both "solved" declarations before 2026-08-16 rested on readback, which this unit's echo makes
worthless as proof.
**Still open (need more captures/trials):** whether a truly *deep-asleep* unit needs any
arming or just waking (the 1003 heartbeat WAS needed for cooler/camping, issue #2 — not
retested for those); SET_COLOR (app exposes no colour control — possibly N/A), Wecklicht
wake-light, the Alle-Lichter master frame, the rest of the 16-zone→lamp map, and the
profile-number semantics (A=9).

### A2 — chars 1004 / 1002 / satellite 1903–1905 (RESOLVED; open leads)
1004 modeled; **1002 = `SHA-256(VIN)[16:32]`**; sat chars 1903–1905 mapped — detail + citations
in `DECISIONS.md`. **Open leads:**
- **`generalpurposesignals` (service `F000`, char `F001`)** — a read-only 168-bit/21-byte block
  of semantically-unassigned signals (`bg/a.java` service, `cg/a.java` char; 8×1-bit `BitZero*`,
  8×8-bit `ByteZero*`, 4×16-bit `WordZero*`, 1×32-bit `DwordZero*`; `b()`=false so not even
  subscribed), not yet captured by `calictl`. Field meanings unknown (generic names). Confirms
  **OTA is absent** — F000 is a diagnostic register set, not a bootloader (see §C4, §A6).
- **`F002` (WRITE) is referenced NOWHERE in the app** (`grep 0000F002` = 0 hits; `bg/a.java`
  parses only F001) — an unused firmware write endpoint; a possible debug/production hook. Blind
  writes risk brick — treat as a lead, not an action. Firmware-acquisition recon in §A6.

### A3 — MERGED_AMBIGUOUS control offsets — roof/roofAC/stairs/LR-heater remain
airheater (1701) is resolved + wired and the satelliteantenna "wrong width" claim was a false
alarm (both in `DECISIONS.md`). **Open:** roof (1401 `jg/a.java`), roofAC (2001 `lg/a.java`),
stairs (1801 `pg/a.java`), LR-heater (2101 `gg/a.java`) — transcribe offsets from each `f()`
when needed; roof also needs a 1-Hz move-heartbeat loop (`ig/c.java`) so its `set` is more than
a frame. Field *value semantics* (enum meanings) still need a live pass.

### A4 — 1003 counter cadence / firmware disarm timeout (needs idle capture)
Char 1003 (`ag/b.java`, 32-bit, `v()` resets to 0) is written app-side via `t0/c.java:264`
(`o(num); y(false)` = write-with-response, no-init). Unknown statically: the **source/
increment rule** of `num` (trace the producer feeding `t0/c.java:264`) and the **unit-side
disarm timeout** if writes stop (our test proved actuation *latches* one-shot, but the
liveness window before the unit refuses a *new* write is unmeasured). The clock-sync flow is
**separate** (`zf/d.java:230–246`: reads car RTC from 1004, `abs(diff) > 5` → correction +
`CLOCK_OUT_OF_SYNC` alert) — not tied to 1003. **To resolve:** idle HCI capture to measure
the app's 1003 interval + a stop-and-probe for the timeout.

### A5 — Lighting profile/color/wake-timer notification overlay (static enum decode)
`dg/a.java e()` reinterprets the same 1502 frame by Mode: REQUEST_CONFIG(12)→7 zone booleans
from LightValue; SET_COLOR(6)→profile enum `dg/k.java`→color `ef.b` + color-temp `dg/j.java`
+ 4 flags `dg/m.java` (LightValue bits 4–7) + Timestamp→UTC wake time; WAKEUP_TIME(20)/
SYSTEM_TIME(24) alarm/clock round-trips. Our dictionary treats 1500/1502 as a flat
brightness array and misses this profile/color/timer overlay. Decode the `dg/*` + `ef/*`
enums (static; one capture confirms bit assignments).

---

### A6 — Live device fingerprint (2026-07-07) — firmware-acquisition recon
Full GATT + identity read from buspi (app closed). Bearing on "can we get the firmware":
- **No Device Information Service (0x180A)** and **no DFU service** — the unit exposes only
  the custom `1000–2100`+`F000` family plus standard GAP `1800` (`2a00` name "VWCAMPER",
  `2a01` appearance) and GATT `1801`. So **no chip/vendor/firmware-rev strings, no OTA** — the
  silicon is not identifiable over the air.
- **The unit's BLE address OUI is NOT IEEE-registered** (macvendors: not found) → private/
  unregistered MAC, reveals no vendor. (The address itself is owner PII — set `CALICTL_ADDR`;
  the repo carries only a placeholder.)
- **Identity chars:** `1001` SW versions = `303431303032303702` → ASCII "0410"/"0207" + `0x02`
  ⇒ **AmbSwVersion "0410", CmSwVersion "0207", CommunicationVersion 2**. `1004` car-variant =
  `047e060717142a…`. **`1002` "VIN" returns 16 opaque high-entropy bytes**
  (`071dbddf…dfc6`), **NOT a plaintext VIN** — encrypted/hashed vehicle id; no readable VIN or
  part number is exposed over BLE (so official flash-file lookup can't be seeded from BLE alone).
- **New: `F000` has `F001`[read] + `F002`[WRITE], and the app references `F002` NOWHERE**
  (`grep 0000F002` = 0 hits; `bg/a.java` parses only F001). An **unused firmware write endpoint**
  on GeneralPurposeSignals — a possible debug/production/bootloader hook. Untested; blind writes
  to an unknown control-unit char are risky (reset/brick), so treat as a lead, not an action.
- **Net for firmware:** BLE is exhausted — no chip ID, no part number, no memory read, no DFU,
  no plaintext VIN. Acquisition requires physical: (1) PCB teardown → identify the SoC → SWD/
  JTAG or chip-off dump (feasibility = whether readout protection is set); or (2) VCDS/ODIS
  diagnostic to read the ECU part number + coding → erWin/ODIS flash-file lookup. `F002` is the
  only remaining BLE curiosity.

## B. Feature / domain-logic gaps

The app is far larger than the vehicle-control surface `ui/screens` covers — 11 top-level
`NavigationModule` entries (`com/californiaontour/multiplatformcore/graphs/NavigationModule$*`).

**Entire modules with zero yaml/doc:**
- **Travel / Trips / MyPlaces** (`travel/graphs/NavigationTravel$*`; `backend/api/trips`,
  `backend/api/myPlaces`) — saved places, itineraries, "mark as visited", server-synced.
- **Search / POI** (`search/graphs/…$SearchScreen`; `backend/api/search/model/*`) — reviews,
  ratings, opening hours, seasons, availability.
- **Discover / CMS** (`discover/graphs/…$Magazine/Marketplace/ProductDetail/Themes*`; ~55
  `cms/model/Cms*`) — magazine, marketplace, "World of California", dealer-map + booking.
- **AiAssist** (`aiassist/graphs/…$AiAssistScreen`; `backend/api/aiassist/AiEvent`;
  `topicpilot` content targeting).
- **Camera** (`camera/graphs/…$CameraLaunchScreen`) — purpose unknown (POI photo? scan?).
- **Developer** (`NavigationModule$DeveloperModule`) — hidden debug/diagnostics; likely
  exposes raw BLE signals + feature flags — **high RE value, decode first.**

**Signal-covered but UI/logic untracked:** heating-and-cooling **timer** screen +
**living-room heater** program; **level indicator** (roll/pitch, ties to A2's 1004);
lighting sub-screens **door-settings / double-tap / wake-up-light / factory-reset / roof-
console-switch**; how-to/info videos; VehicleData category/menu decomposition (note: no
standalone `energy` route — energy is surfaced via VehicleData categories; confirm routing).

**Light scenes/favorites model (B-priority, static):** `WifiExlapLightingProfiles` =
`profileOne..profileSeven` (`wp/j.java:87`), each `wp/i.java:10–56` = **8 zones × (bool on/off
+ int brightness)**; enums `FAVORITE1..7` (`dg/l.java:64–76`, `currentActiveLightProfile`
:111). Documents how scenes are defined/saved/recalled. (Earlier framed as "the storage behind
A1's active-profile requirement" — that requirement never existed; it was the `1502` echo bug,
and A1 was resolved 2026-08-16 with no profile precondition at all.)

**Persistence (untracked):** `database/CaliforniaAppDatabase.java` (Room) + remote KV store
(`backend/api/keyvaluestore`) hold the 7 favorites, settings, trips — no table/key map yet.

**Structural caveat (refined 2026-07-08):** the decompile is **dex-only** — jadx processed
`classes.dex`, not the APK assets. Consequence for strings:
- **String KEYS are fully recoverable** (1882 distinct `"string:area_component_meaning_text"`
  refs in the dex) — descriptive enough to read menu structure/feature semantics without the text.
- **Localized VALUES are NOT here** — Compose Multiplatform packs UI text into binary
  `composeResources/**/strings.commonMain.cvr` blobs (zero `.cvr` in the decompile); the code
  references them by `(path, offset, length)` (e.g. offset 5218 len 241). This is a *resource
  extraction* gap, **not** code obfuscation (obfuscation renamed symbols but preserved string
  constants — which is why pref keys / zone names / log strings all read fine).
- To recover the text: `unzip <app>.apk` → read the `.cvr` at the code-given offsets, OR re-run
  jadx/apktool WITH resources. **The APK is on buspi at `~/apks/de.volkswagen.CaliforniaOnTour.apk`
  (105 MB, single APK with assets; gitignored — never commit).**
- **App identity:** Play Store **applicationId = `de.volkswagen.CaliforniaOnTour`** (the code
  *package* root is `com.californiaontour` — they differ, which is why `com.californiaontour`
  APK-mirror lookups 404'd). Confirm via `play.google.com/store/apps/details?id=de.volkswagen.CaliforniaOnTour`.

**Developer-module dive (2026-07-08) — findings from the prefs surface (`xp/g.java`):**
- **Interior-light zone names — two complementary sources (correction).** Zone names were
  already documented in `ui/screens/lighting.yaml` (per-model section labels: ambientLighting,
  kitchen, readingLights left/right/frontArea/loftBed, popupRoof, bootLid, entrance, … across the
  base/`_t6`/`_gc` layouts) — as `label_key`s (text still in `.cvr`). The EXLAP prefs add a
  SECOND naming: the 8 profile-editor zones `AMBIENT, D_PILLAR, KITCHEN, READING_{LEFT,REAR,RIGHT},
  SPOT_{LEFT,RIGHT}` = the 8-zone `wp/i.java` profile. **Neither resolves the actual gap:** which
  BLE `BrightnessL[1..16]` index maps to which physical lamp. `ui/screens/lighting.yaml` states this
  explicitly ("order of the 16 zones to vehicle areas is UNVERIFIED — needs live-traffic capture").
  So the zone→BLE-index map still needs a live toggle-and-watch capture, not more static reading.
- **Complete alert/error taxonomy** (~50 `BLUETOOTH_*_CONFIRMED` + `EXLAP_*_INTERACTED` keys) per
  function — authoritative reference for `alert-states.md`; also reconfirms the BLE/EXLAP dual
  transport (parallel alert-ack flags).
- **Corroborations:** `LEVELING_ROLL`/`LEVELING_PITCH` prefs ↔ the char-1004 vehicle roll/pitch
  just decoded; `BACKEND_STAGE`+`CUSTOM_STAGE_NAMESPACE` = dev environment switch; the
  `DeveloperModule` nav entry is a stub (labels stripped into `.cvr`).

---

## C. Session / auth / backend / infra gaps

**Headline (answers "does remote control need the cloud?"): NO.** Actuation needs only local
BLE bond + a local flag. `xp/g.java:153–154`: `enrolledVehicle`→`"ENROLLED_VEHICLE"`,
`remoteControlSetupCompleted`→`"cali_connectivity_setup_finished"` (plain local boolean). No
vehicle-to-account registration, no server pairing credential, no token in the BLE path. The
entire cloud stack serves the *travel companion* side, not the vehicle.

**C1 — VW OIDC / IDK auth stack** (`wp/a.java:38–49` env config: backend
`california-on-tour.io`, authorize `identity.vwgroup.io/oidc/v1/authorize`, client_id
`5ab6ba3e-…@apps_vw-dilab_com`; session mgr `oc/g.java` refresh-token lifecycle; API client
`od/k.java` Basic+Bearer signing; token crypto `nu/a.java` AES-GCM). Not RE'd: PKCE/redirect
round-trip, token endpoint, Basic client secret. Decode `oc/g.java` + `od/k.java` (bad/);
capture a login.

**C2 — EXLAP over TCP/WiFi — an entire undocumented SECOND transport** (`de/exlap/`, **99
files**). `ExlapClientDelegateImpl.java:79` connects `socket://<ip>:<port>`;
`transport/TransportConnector.java:9–11` UDP discovery on **port 28500**; transports include
J2SE-socket, **Android-Bluetooth RFCOMM/SPP**, WebSocket. Wired via prefs `EXLAP_IP_ADDRESS/
PORT/USE_WIFI/WIFI_SSID` + `EXLAP_LIGHT_PROFILE_*` + `EXLAP_*_INTERACTED` (`xp/g.java:178–276`).
Shares the light-profile/alert prefs with the BLE side → **likely a parallel actuation
channel** (VW "Exchange of Live Automotive Parameters", MIB head-unit path) with its own
login/subscribe handshake — entirely outside our BLE model. Decode `AbstractInterface.java`,
`command/*`, `ContentStream.java`; capture discovery on 28500.
- **Live probe 2026-07-07 (buspi, van parked/off): NOT reachable.** buspi is on a GL.iNet
  travel router (`<router-LAN>/24`, gw `<router-gw>` nginx). WiFi scan shows no camper AP (only the
  home SSID); a full `<router-LAN>/24` sweep found no host with **tcp/28500**; a 12 s passive
  **udp/28500** listen got 0 beacons. So EXLAP is the MIB **infotainment** subsystem (distinct
  from the BLE camper unit) and its WiFi is down while the vehicle is off. Pursuing it needs
  (a) vehicle infotainment WiFi up (ignition on) + buspi joined to *that* SSID, and (b) the UDP
  discovery-probe payload decoded (the app *sends* the probe; passive listen won't do).
- **Protocol decoded 2026-07-07 (full read of `de/exlap/`).** EXLAP is a standard **XML
  request/response + publish-subscribe** protocol over a stream (TCP `socket://ip:port`,
  BT-RFCOMM, or WebSocket). Verbs (`de/exlap/command/*`, each a `writeXML`): `<Protocol
  version=.. returnCapabilities=..>` (negotiate) → optional `<Authenticate>` **challenge-
  response** (server `<Challenge nonce usingHash>`, client digest = SHA256/MD5 of
  username+password+nonce+16-byte cnonce; `AuthCommand`) → `<Dir>` (enumerate URLs) /
  `<Get url>` (read a DataObject) / `<Subscribe url ival content timestamp>` (stream on change,
  ival 0–60000 ms) / `<Call url><DataObject/></Call>` (**invoke/actuate**) / `<Unsubscribe>` /
  `<Alive>`+`<Heartbeat>` keepalive / `<Bye>`. Status codes in `ExlapConstants` (OK=0,
  AUTHENTICATIONFAILED=6, KEEPALIVE_TIMEOUT=13). Data model: typed `DataElement`s (Absolute,
  Relative, Enumeration, Text, Time, Binary) addressed by URL. Discovery = UDP `<ServiceInquiry
  service=.. id=..>` on 28500.
- **How THIS app wires it (dual-transport).** `ExlapClientDelegateImpl` exposes
  `connectToExlapClient(ip,port)` / `subscribeToURL(url,ival)` / `sendDataToExlapClient(url,obj)`
  (= `<Call>`, the actuation path) / a data flow. The connect (`ah/f.java:190` — ip/port from the
  `EXLAP_IP_ADDRESS`/`PORT` prefs) builds `socket://ip:port`, `setBlockingConnect(true)`, and
  **NEVER calls `authenticate()`** (grep: zero callers) — so the app uses EXLAP **unauthenticated**;
  auth is a library capability the vehicle side apparently doesn't require here. `ah/m.java:162`
  builds a **fixed list of 12 DataElement handlers** (`f1134w0`, each a `ch.a` with url `.c()` +
  interval `.f()` + parser `.g()`) — i.e. **the SAME ~12 vehicle functions as BLE, over EXLAP**.
  So EXLAP is a selectable *alternative transport for the identical function set*, not a
  different feature. Actuation goes through `<Call>` and (being a different transport) is **not**
  subject to the BLE 1003-heartbeat arm.
- **No EXLAP-over-Bluetooth path (checked 2026-07-07).** `ah/f.java` is the unified connectivity
  manager with exactly TWO camper transports: EXLAP over **TCP** (`:190` `connectToExlapClient(ip,
  port)`→`socket://`) and **BLE GATT** (`:404` `BluetoothDevice`→`jb.b`, the GATT processor). The
  BluetoothDevice at :404 is the BLE link, NOT RFCOMM — the SDK's `TransportConnectorAndroidBluetooth`
  /SPP is registered in `TransportManager` but never used for the camper. So there is **no parked
  Bluetooth-RFCOMM control shortcut**; EXLAP for the camper is WiFi/TCP-only (needs infotainment up).
- **Net:** feasible in principle (unauthenticated, actuation via `<Call>`, same functions), but
  **gated purely on reachability** over WiFi — the vehicle must expose the EXLAP server on a WiFi we
  can join (infotainment/ignition on). Not present while parked (probe above). The concrete
  DataElement **URLs are runtime** (from the vehicle's `<Dir>`/interface), so a live `<Dir>` against a
  reachable server is the only way to enumerate them.

**C3 — Other backends:** CARIAD AI voice (`tc/f.java`, SSE, `nvt-au-eu…cariad.digital`);
Adobe AEM GraphQL CMS (`me/j.java`); VW online-manual (`userguide.volkswagen.de`); consent
texts (`consent.vwgroup.io`). **C4 — no OTA for the unit, CONFIRMED** (only 1001 version read;
app-level force-update kill-switch via `MINUMUM_REQUIRED_APP_*_VERSION` prefs; the last suspect
service `F000` was decoded = read-only "GeneralPurposeSignals" telemetry, not a bootloader — §A2). **C5 — analytics/
consent:** Firebase + obfuscated tracker (`zw/a.java`, `hw/a.java`) + AES-GCM consent store;
check whether VIN (`cali_vin`) / vehicleId is uploaded (search `od/` request bodies).

---

## Files to decode next (bodies in the decompiled sources (bad-code pass))
- `w10/l.java`, `w10/d.java` — lighting profile→wire-value table (~~unblocks A1 / `set lighting`~~
  A1 resolved 2026-08-16 without it; still useful for profile-number semantics, e.g. A=9).
- `ag/a.java`, `ag/d.java`, `mg/f.java`, `jg/b.java` — chars 1004/1002/1903–1905 (A2, static).
- `sf/a.java`, `gg/a.java`, `lg/a.java`, `pg/a.java`, `jg/a.java` — resolve merged offsets (A3).
- `oc/g.java`, `od/k.java` — OIDC token flow + backend signing (C1).
- `de/exlap/ExlapClientDelegateImpl.java`, `AbstractInterface.java`, `command/*` — EXLAP (C2).
- producer feeding `t0/c.java:264` — 1003 counter source/cadence (A4).

## Open topics needing the vehicle (nothing else is static-blocked)

**Needs a phone HCI capture** (app behavior buspi can't derive):
- **Zone → physical-lamp map** — toggle each named light zone in the app, watch which
  `BrightnessL[1..16]` moves. The only real lighting-capture item (names + `ef.i`→channel map
  are known; the fix works without it — it's just for labeling which slider = which lamp).
- **Control-field enum meanings** for roof/roofAC/stairs/LR-heater (Mode/FanSpeed/position) —
  best captured from the app operating those functions (roof is installed → capturable here).
- *(optional/low)* confirm lighting SET_COLOR / wake-timer bit-packing; the app's exact 1003
  cadence (both already decoded statically).

**Needs a buspi live test — no phone:**
- ~~`set lighting` confirm~~ **DONE 2026-08-16** (photon-verified — the ProfileNumber-echo theory
  was moot; PN is hardcoded 9, and a bare SET+commit actuates once the unit is awake — the
  interim "REQUEST_CONFIG preamble is the gate" theory was a wake-state confound). `set
  airheater` confirm still open.
- **1003 disarm timeout** — heartbeat, stop, actuate after N s (settles arm-window sizing).
- `set roof` (once the ~1 Hz move-heartbeat loop is implemented).
- Grafana dashboard push (needs the buspi Grafana creds).

**Needs a special vehicle state (not a BLE capture):**
- **EXLAP** — infotainment WiFi up (ignition on), buspi joined to that SSID, speak `<Protocol>`
  then `<Dir>`/`<Get>`/`<Call>`. Wire format fully known; only reachability blocks it.
- **OIDC** redirect/token endpoint/client-secret (C1) — capture a login (out of scope for BLE).
