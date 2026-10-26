# VW California T7 Camper Unit — BLE Protocol (reverse-engineered)

Own-vehicle interoperability research. Derived from live GATT observation on the
owner's vehicle plus static analysis of the official app's *algorithm* (no
third-party source is reproduced here). All command frames are our own
reconstruction; verify against the live `State` characteristic before trusting.

## Device

- Advertises as **`VWCAMPER`**, rotating random address (RPA). Resolve **by name**
  until bonded; after bonding the identity address is stable.
- Pairing: **LE passkey entry** — the unit displays a 6-digit passkey on its
  Bluetooth screen; the {term}`central` types it. No legacy PIN.
- Vendor UUID base: `0000XXXX-6c77-4b7d-bbf6-a5e587701f3d` (`6c77` = ASCII "lw").

## Service / characteristic layout

Uniform per-subsystem: service `0xNN00` with `0xNN01` = **Control** (write) and
`0xNN02` = **State** (read+notify); higher `0xNN0x` = extra info reads. The
unit's own `0x2901` descriptors label every pair "Control"/"State".

| Service | Function | Service | Function |
|---|---|---|---|
| 1000 | Vehicle (Info/VIN/Car_Info/Counter) | 1700 | AIR_HEATER (parking heater) |
| 1100 | COOLER / fridge | 1800 | STAIRS (electric step) |
| 1200 | CAMPING MODE (`campingmode`: master + lights/USB loads) | 1900 | SAT antenna + WLAN/system |
| 1300 | FRESH_WATER (tank) | 2000 | ROOF_AIR_CONDITION |
| 1400 | ROOF (pop-top) | 2100 | LIVING_ROOM_HEATER |
| 1500 | **INTERIOR LIGHTING** (per-zone brightness/color/profile) | 1600 | ENERGY (battery/solar/DC-DC/shore) |
| f000 | generic Read/Write | | |

> Note on 1500: command enum (`SET_BRIGHTNESS`/`SET_COLOR`/`SET_PROFILE`/`PREVIEW`/
> `SYSTEM_TIME`/`WAKEUP_TIME`) + control fields `Mode, Timestamp, LightValue,
> BrightnessL1…L16` (16 per-zone fields) = the interior LED lighting. There is **no** BLE
> control for the unit's own touchscreen/panel brightness (that's a local setting).

## Connection lifetime

How long the unit keeps a {term}`central`'s link depends on camping mode and shore power. The
sequence is {need}`S_SEQ_POWER_SAVE_HANGUP`. The measurements are in the
[evidence ledger](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html).

| Condition | What the unit does | Evidence |
|---|---|---|
| Camping master on (`1202` `State` 1) | Keeps the link for hours. | Live-verified on the real unit |
| Camping master off, no shore power | Ends every link about 30.7 s after encryption (HCI `0x13` remote user terminated, NimBLE status 19 or 531), with or without the `1003` heartbeat or reads. This is the {term}`power-save hang-up <Power-save hang-up>`. | Live-verified on the real unit |
| Camping master off, a link that starts less than 6 s after the unit ended the previous one | Cuts it about 1.2 s in. A link that starts 6 s or later holds until the hang-up. | Live-verified on the real unit |
| Shore power active or standby (`1602` `StateLandAfs` 1 or 2) | Keeps the link, by the app's reconnect rule. | Verified against the app's code only |
| Ignition on, camping master off | Unknown. | Untested |

The hang-up is the unit's own timer. Before it, the unit sends no request except the ATT Exchange
MTU Request right after connect. It ends an idle BlueZ link at the same time.

**Reconnect cooldown.** After the unit ended a link, wait at least 7 s before the next connect. A
sooner link is cut about 1.2 s in.

**How the centrals react:**

- **The app** (verified against the app's code only) keeps reconnecting after an unexpected
  disconnect only on a connection timeout, with shore power active or standby, or in the
  foreground when the unit did not end the link itself. Otherwise it stops and shows
  "Disconnected to save energy" with the hint to turn on camping mode to stay connected. The user
  reconnects by hand.
- **The ESP32 satellite** waits the 7 s cooldown before any reconnect. After the hang-up it enters
  the {term}`power-save rest <Power-save rest>`: no redial, one short reading every
  `rest_poll_min` minutes, and a *Reconnect* button. While a page is visibly in use, it redials
  after the cooldown instead. See {doc}`firmware`.
- **buspi's `calictl serve`** has no camping or shore-power rule and no cooldown. It holds a
  session only while its web UI is active, checks it every `--interval` (default 30 s) and
  reconnects a dropped one at that check. With the web UI idle it reads with short
  connect-read-release polls every `--interval`.

## Machine-readable dictionary

The full per-function field map is auto-extracted from the app by
**`tools/extract_protocol.py`** into **`protocol/dictionary.yaml`** (14 {term}`functions <Function>`).
Extraction is **object-keyed** (`f23982e0`…) so name/width/default/offset stay
aligned even for the shared fridge/heater control model; the right name-set is
chosen by state-field overlap.

Per field it emits: **name, bit offset, width, default, raw_range** (`0..2^w-1`).
Control offsets come from each `f()`; state offsets from each `e()` (`subList`).
A field placed differently across a shared model's two branches gets an {term}`ambiguous offset`
rather than a guess; every such offset calictl uses
is placed in `calictl/overrides.py` from the app's frame builder (the generated views show it as
"(override)").
A `command_enums:` section lists the app's command vocabularies (e.g. lighting's
`SET_BRIGHTNESS`/`SET_COLOR`/`SET_PROFILE`) for `Mode`-style value semantics;
elsewhere the extractor leaves `value_semantics` at its unverified default, which is not a
verification verdict: what is verified, and how, is the [signal catalog](protocol/signal-matrix.md).

**Reproduce**: the decompile pipeline (apkeep → dex → jadx) + the enigma name-map are kept in a
private own-vehicle-RE repo (they analyse the VW-copyrighted app locally); this repo ships the
RESULT (`protocol/dictionary.yaml`). Regenerate the {term}`dictionary` from local decompiled sources with
`python3 tools/extract_protocol.py <sources> protocol/dictionary.yaml`. The
dictionary-driven frame builder is `calictl.protocol.encode` + `calictl/control.py`
(`encode` raises for a control field with no placed offset whose name is absent from `values`;
it packs only placed fields).

## Frame format

State and Control values are **bit-packed structures**: a value is a bit array
sliced/packed into fields at hardcoded bit offsets, per subsystem.

**Encoding** (see `calictl/protocol.py`):

- field value → bits: low `n` bits, **MSB-first**
- bits → bytes: **MSB-first** (`bit 0 → byte0 0x80`)
- round-trip verified against the app's LSB-first decoder

**No rolling code / no per-command auth** — confirmed in the app's write path
(no counter/nonce/timestamp/CRC/HMAC) and by the unit accepting an arbitrary
bonded write. Security finding: the control plane is **bonded-but-unauthenticated**;
any bonded central has full, replayable control.

## Control frame rules

The per-feature recipes live in
[`control-and-actuation.md`](https://ckeller42.github.io/open-california/business-logic/control-and-actuation.html) (§3 frame model, §4
per-feature status). Two rules hold for every control frame:

- **Never write a whole frame of model defaults** (e.g. `fd770f1e3e1f`). It carries out-of-range
  and conflicting action values: the unit ignores the power request and stores the stray bytes. A control frame
  carries the app's leave-unchanged values in every untargeted field (2-bit fields `3`, cooler
  `Level`/`Mode` 7, timer 30/62, night hours 31), exactly as the app sends them. The real app's
  frames, captured on the real unit, are byte-identical. The one exception is the
  cooler's night hours, which carry the live schedule (the unit takes them literally).
  `calictl/control.py` builds it that way.
- Cooler power and level are **live-actuation-verified**, armed by the `1003` heartbeat. For the
  wire sequence, see the
  [protocol sequence diagrams](https://ckeller42.github.io/open-california/protocol-sequences.html#heartbeat-armed-control-write).
  The evidence tiers are in
  [`evidence-ledger.md`](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html).
