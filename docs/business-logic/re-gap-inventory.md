# RE gap inventory — what of the CaliforniaOnTour app is NOT yet understood

Compiled 2026-07-07 from a three-way decompile audit (BLE protocol, feature/domain
logic, session/auth/infra), each diffed against the current baseline: `protocol/
dictionary.yaml` (13 functions), `protocol/signals.yaml`, `ui/screens/*.yaml` (18
screens), and the 10 `docs/business-logic/*.md` writeups. Citations are `file:line`
under the JADX decompile (`…/scratchpad/decompile/src/sources` = CLEAN,
`…/decompile/bad/sources` = method bodies jadx dropped from clean). VW material:
citations only.

**Codec master key** (resolves most "width UNVERIFIED" statically): every field is an
`sg.a` bit-cell whose 2nd ctor arg selects width — `sg/a.java b()`: type 2/7→8-bit,
3→16-bit signed (`u8.g`), 4→2-bit, 5→32-bit, 6→4-bit, 1→16-bit. `f()` in each builder
places those bits; `tt/u8.java:88 c()` = little-endian bytes, MSB-first within a byte.

---

## Cross-cutting priority ranking

| # | Gap | Kind | Resolvable now? |
|---|-----|------|-----------------|
| 1 | ~~Lighting SET frame needs the active ProfileNumber~~ — **FIXED** (echo 1502's ProfileNumber); one live confirm pending | protocol | done (static) |
| 2 | ~~Char 1004 / 1002 / sat 1903–1905 unmodeled~~ — **DONE** (1004 modeled; 1002 = SHA-256(VIN)[16:32]; sat chars mapped) | protocol | done |
| 3 | MERGED_AMBIGUOUS control offsets from the builders — **airheater DONE**; roof/roofAC/stairs/LR-heater remain | protocol | **static** |
| 4 | **1003 counter cadence / firmware disarm timeout** | protocol | needs idle HCI capture |
| 5 | Lighting **profile/color/wake-timer** notification overlay | protocol | static (enum decode) |
| 6 | **EXLAP over WiFi/TCP** — an entire second transport (99 files) | infra | static + capture |
| 7 | Whole feature modules untracked: **Travel, Search, Discover/CMS, AiAssist, Camera, Developer** | feature | static |
| 8 | **Light scenes/favorites** model (7 slots × 8 zones × on+brightness) | feature | static |
| 9 | VW **OIDC/IDK** auth + backend request signing | infra | static + capture |
| 10 | UI label *text* (dex-only: 1882 string KEYS recovered, but VALUES are in `.cvr` assets) — light zone names DONE | feature | needs the APK's `.cvr` |

---

## Update 2026-07-08 — 6-agent decompile batch + VIN + APK strings

Resolved/applied this pass (details in the sections below):

- **§A1 Lighting SET — FIXED (no capture needed).** The active ProfileNumber is an identity
  round-trip of the ProfileNumber the unit reports in 1502 (`dg/l.java` f5472x==ordinal,
  `w10/d.java` identity). `control._lighting` now echoes `last["ProfileNumber"]` instead of 0.
  Also fixed the `ui/screens/lighting.yaml`/`prototype.html` "offset 14, width 6" misread
  (→ offset 4, width 4). Pending one live confirm on buspi.
- **§A2 char 1002 "Vin" — RESOLVED via the owner's VIN: it is `SHA-256(VIN)[16:32]`** (the low
  16 bytes of the plaintext VIN's SHA-256 — matched live). A privacy-preserving one-way vehicle
  id; no plaintext VIN over BLE. Vehicle decodes to a **2026 T7 California** (WMI WV2, model
  code ST, MY `T`=2026, Hannover). New sat chars: **1904=`SystemInfoWlanSsid`** (15 B UTF-8),
  **1905=`SystemInfoWlanKey`** (10 B), **1903**=`SatDish/CapConverter/CibusInterface/TpList` —
  all satellite-option-gated (not installed here).
- **§A3 control models — DONE for all four.** roof/roofAC/stairs/LR-heater MERGED offsets
  resolved from their `f()` builders and added to `overrides.py` (+ frame bytes). Setter values
  + roof's ~1 Hz move-heartbeat documented. `set` not wired (not installed; roof needs the
  heartbeat loop). Enum semantics still UNVERIFIED.
- **Energy semantics — CORRECTED + live-verified** (`semantics.energy`): batt2 current /10,
  SoC→%, source powers ×10 (0 when not installed — the PPv=254 sentinel), shore/solar current
  unsigned /10, source-state enum, `energy_mode` un-inverted, warning level, expanded faults.
- **§C2 EXLAP — URLs are HARDCODED literals, not runtime** (correction). The 12 handlers return
  literal `VWN_Camper_<Fn>_State`/`_Control` URLs; 7 actuate. `<Call>` wire format fully
  recovered: `<Req id="N"><Call url="VWN_Camper_<Fn>_Control"><Abs|Enm name=".." val=".."/>…
  </Call></Req>`, unauthenticated, no heartbeat/sequence. Only blocker remains reachability.
- **§A5 lighting overlay + §E alerts** decoded — see the new subsections below.

---

## A. BLE protocol / control gaps

### A1 (TOP) — Lighting SET_BRIGHTNESS must carry the *active* ProfileNumber
The live test that showed a `Mode=4, ProfileNumber=0` frame ACKed-but-ignored is now
explained. The app's brightness flow (`dg/h.java:615–642`) stages every zone nibble then
sends `w(w10.d.b(kVar).f5472x, DIRECT)` — ProfileNumber = **the currently-active profile's
wire value, never 0**; on/off is encoded purely in the 4-bit brightness nibbles
(`dg/i.java:26–41`: OFF=0, 10–100%→1..10, DEFAULT=11, NOT_EQUIPPED=13). `ProfileNumber=0`
addresses an inactive/invalid profile → silently dropped. `LightValue@48/16b` is **not part
of the SET_BRIGHTNESS frame** — it is a per-zone on/off bitmask read back in REQUEST_CONFIG
(`dg/a.java e()`, bits 9–15 = 7 zones) and the on/off lever in `SET_PROFILE` (`dg/h.java:808`
`n4()`: `v(16); w(8); s(z?1:0)`). Command enum `dg/n.java:34–50`: SET_BRIGHTNESS=4,
SET_COLOR=6, SET_DOUBLE=8, REQUEST_CONFIG=12, SET_PROFILE=16, WAKEUP_TIME=20, SYSTEM_TIME=24,
PREVIEW=28. Frame builder `eg/a.java f()` (nibble order swapped: L1@68, L2@64, L3@76…).
- **Impact:** this is the fix for `calictl set lighting` (currently ACKs, no-op). `calictl`'s
  `overrides.CONTROL_RANGES` note ("ProfileNumber must be 0") is **wrong** — it must be the
  active profile's wire value.
- **To resolve:** HCI-capture one in-app brightness change + one profile on/off; decode the
  profile→wire table `w10/l.java` and `w10/d.java:55–89 b()`; replay `Mode=4` with the
  captured active profile number.

### A2 — Chars 1004 / 1002 / satellite 1903–1905 are unmodeled (static decode)
`dictionary.yaml general` models only the three SW-version fields from char **1001**
(`ag/c.java`, parsed `zf/d.java:289–296`). Missing:
- **1004** (`ag/a.java`, parsed `zf/d.java:323–357`): CarVariant, CarLevelPopUp,
  TerminalOneFive, CarTime Year/Month/Day/Hour/Minute/Second (8-bit @8–56), **CarLevelRoll
  16b@56**, **CarLevelPitch 16b@72** — the vehicle-leveling readout + the RTC that drives
  CLOCK_OUT_OF_SYNC. Widths from `ag/a.java:8–35`.
- **1002** (`ag/d.java`, guard `zf/d.java:304`) — parsed but undocumented; `ag/d.java` is
  shared with 1904/1905.
- **satellite 1903/1904/1905** (`mg/f.java`, `jg/b.java`, `ag/d.java`) — extended
  signal/config chars; dictionary models only 1900/1902.
- No OTA/DFU service exists (only the `…-6C77-4B7D-BBF6-A5E587701F3D` family, services
  1000–2100 + F000). **Add these as new dictionary functions — pure static work.**
- **Service `F000` decoded 2026-07-07 — it is NOT a bootloader/DFU.** `bg/a.java` (service) +
  `cg/a.java` (char `F001`) = a **read-only "GeneralPurposeSignals"** block: one char, `b()`=
  false (not even subscribed), only an incoming-data parser `e()` (no write/command). It
  decodes a fixed **168-bit / 21-byte** frame of *semantically-unassigned* signals — 8×1-bit
  (`BitZeroOne..Eight`), 8×8-bit (`ByteZeroOne..Eight`), 4×16-bit (`WordZeroOne..Four`),
  1×32-bit (`DwordZeroOne`) — logged as `"<-- Incoming Data for GeneralPurposeSignals"`. So
  **OTA is definitively absent** (see §C4); F000 is a firmware diagnostic/expansion register
  set, readable telemetry `calictl` doesn't yet capture (meaning unknown — generic names).

### A3 — Statically-resolvable MERGED_AMBIGUOUS control offsets — airheater DONE
Builders are per-control-char, so the "shared model" ambiguity is an extractor artifact, not
in the bytecode:
- **airheater (1701, `sf/a.java f()`) — RESOLVED + WIRED 2026-07-08.** The four merged fields
  are `OperationModeCombined@20(4)`, `RunningTime@24(8)`, `TimerHour@32(8)`, `TimerMin@40(8)`
  (read directly from `f()`; the old `overrides` entry was cooler's layout — a latent bug that
  made airheater unencodable). `overrides.CONTROL_OFFSETS["airheater"]` fixed; `set airheater
  power|level` wired (`control._airheater`): **power = `NormalOperationRequest` 1/0, verified
  from `rf/b.java C2()`:187**; level = `HeatingLevel` 0-15. **Not yet live-verified** (buspi was
  offline; airheater IS installed, so testable when it returns).
- **satelliteantenna (1901, `gg/a.java:157–159`) — the earlier "wrong width" claim was FALSE.**
  `System`/`Dish` = `sg.a(_,4)` = **type 4 = 2-bit**, which matches the dictionary (width 2). The
  audit conflated the sg.a type-arg with bit-width. Only real gap: `DishStop`/`Wlan` =
  `sg.a(false)` = 1-bit booleans with MERGED offsets — low value (sat not installed).
- **Remaining (not installed, unresolved-but-derivable):** roof (1401 `jg/a.java`), roofAC (2001
  `lg/a.java`), stairs (1801 `pg/a.java`), LR-heater (2101 `gg/a.java`). Transcribe from each
  `f()` when needed; roof also needs a 1-Hz move-heartbeat (`ig/c.java`) so its `set` is more
  than a frame. Field *value semantics* (enum meanings) still need a live pass.

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
:111). This is the storage behind A1's active-profile requirement — documents how scenes are
defined/saved/recalled.

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
  travel router (`192.168.8.0/24`, gw `.8.1` nginx). WiFi scan shows no camper AP (only the
  home SSID); a full `192.168.8.0/24` sweep found no host with **tcp/28500**; a 12 s passive
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

## Files to decode next (bodies in `…/decompile/bad/sources/`)
- `w10/l.java`, `w10/d.java` — lighting profile→wire-value table (**unblocks A1 / `set lighting`**).
- `ag/a.java`, `ag/d.java`, `mg/f.java`, `jg/b.java` — chars 1004/1002/1903–1905 (A2, static).
- `sf/a.java`, `gg/a.java`, `lg/a.java`, `pg/a.java`, `jg/a.java` — resolve merged offsets (A3).
- `oc/g.java`, `od/k.java` — OIDC token flow + backend signing (C1).
- `de/exlap/ExlapClientDelegateImpl.java`, `AbstractInterface.java`, `command/*` — EXLAP (C2).
- producer feeding `t0/c.java:264` — 1003 counter source/cadence (A4).

## Items that REQUIRE a live capture (not static)
- Lighting active-ProfileNumber wire value (A1); 1003 disarm timeout + app cadence (A4);
  OIDC redirect/token endpoint/secret (C1); EXLAP discovery+handshake on 28500 (C2);
  control-field enum *meanings* across functions (A3).
