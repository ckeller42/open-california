# VW California T7 Camper Unit — BLE Protocol (reverse-engineered)

Own-vehicle interoperability research. Derived from live GATT observation on the
owner's vehicle plus static analysis of the official app's *algorithm* (no
third-party source is reproduced here). All command frames are our own
reconstruction; verify against the live `State` characteristic before trusting.

## Device

- Advertises as **`VWCAMPER`**, rotating random address (RPA). Resolve **by name**
  until bonded; after bonding the identity address is stable.
- Pairing: **LE passkey entry** — the unit displays a 6-digit passkey on its
  Bluetooth screen; the central types it. No legacy PIN.
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
| f000 | generic Read/Write (OTA?) | | |

> Note on 1500: command enum (`SET_BRIGHTNESS`/`SET_COLOR`/`SET_PROFILE`/`PREVIEW`/
> `SYSTEM_TIME`/`WAKEUP_TIME`) + control fields `Mode, Timestamp, LightValue,
> BrightnessL1…L16` (16 per-zone fields) = the interior LED lighting. There is **no** BLE
> control for the unit's own touchscreen/panel brightness (that's a local setting).

## Machine-readable dictionary

The full per-function field map is auto-extracted from the app by
**`tools/extract_protocol.py`** into **`protocol/dictionary.yaml`** (14 functions).
Extraction is **object-keyed** (`f23982e0`…) so name/width/default/offset stay
aligned even for the shared fridge/heater control model; the right name-set is
chosen by state-field overlap.

Per field it emits: **name, bit offset, width, default, raw_range** (`0..2^w-1`).
Control offsets come from each `f()`; state offsets from each `e()` (`subList`).
A field placed differently across a shared model's two branches is flagged
`offset: MERGED_AMBIGUOUS` (needs a live pass to disambiguate) rather than guessed.
A `command_enums:` section lists the app's command vocabularies (e.g. lighting's
`SET_BRIGHTNESS`/`SET_COLOR`/`SET_PROFILE`) for `Mode`-style value semantics;
everything else stays `value_semantics: UNVERIFIED`.

**Reproduce**: the decompile pipeline (apkeep → dex → jadx) + the enigma name-map are kept in a
private own-vehicle-RE repo (they analyse the VW-copyrighted app locally); this repo ships the
RESULT (`protocol/dictionary.yaml`). Regenerate the dictionary from local decompiled sources with
`python3 tools/extract_protocol.py <sources> protocol/dictionary.yaml`. The
dictionary-driven frame builder is `calictl.protocol.encode` + `calictl/control.py`
(they refuse to build when a required field is `MERGED_AMBIGUOUS`/`UNKNOWN`).

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

## Worked example (fridge) — superseded

This page used to walk through the fridge (`1101` control / `1102` state) as its worked example.
That walkthrough has been superseded by live on-device results, and the current per-feature
recipes live in
[`control-and-actuation.md`](https://ckeller42.github.io/open-california/business-logic/control-and-actuation.html) (§3 frame model, §4
per-feature status). Two points from it still hold:

- **Never write a whole frame of model defaults** (e.g. `fd770f1e3e1f`). It carries out-of-range
  and conflicting action values; the unit ignored power and stored stray bytes. A control frame must
  carry the *current* state in untargeted fields and the leave-unchanged sentinel (`3` for 2-bit)
  in action fields. `calictl/control.py` builds it that way.
- Cooler power and level are **live-actuation-verified**, armed by the `1003` heartbeat. For the
  wire sequence, see the
  [protocol sequence diagrams](https://ckeller42.github.io/open-california/protocol-sequences.html#heartbeat-armed-control-write).
  The evidence tiers are in
  [`evidence-ledger.md`](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html).
