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
| 1200 | LIGHT | 1900 | SAT antenna + WLAN/system |
| 1300 | FRESH_WATER (tank) | 2000 | ROOF_AIR_CONDITION |
| 1400 | ROOF (pop-top) | 2100 | LIVING_ROOM_HEATER |
| 1500 | **INTERIOR LIGHTING** (per-zone brightness/color/profile) | 1600 | ENERGY (battery/solar/DC-DC/shore) |
| f000 | generic Read/Write (OTA?) | | |

> Note on 1500: command enum (`SET_BRIGHTNESS`/`SET_COLOR`/`SET_PROFILE`/`PREVIEW`/
> `SYSTEM_TIME`/`WAKEUP_TIME`) + control fields `Mode, Timestamp, LightValue,
> BrightnessL{One,Two,Three}` = the interior LED lighting. There is **no** BLE
> control for the unit's own touchscreen/panel brightness (that's a local setting).

## Machine-readable dictionary

The full per-function field map is auto-extracted from the app by
**`tools/extract_protocol.py`** into **`protocol/dictionary.yaml`** (11 functions).
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
`python3 tools/extract_protocol.py <sources> protocol/dictionary.yaml`. A
dictionary-driven frame builder lives in `tools/build_frame.py` (refuses to build
when required fields are `MERGED_AMBIGUOUS`/`UNKNOWN`).

## Frame format

State and Control values are **bit-packed structures**: a value is a bit array
sliced/packed into fields at hardcoded bit offsets, per subsystem.

**Encoding** (see `tools/encode.py`):
- field value → bits: low `n` bits, **MSB-first**
- bits → bytes: **MSB-first** (`bit 0 → byte0 0x80`)
- round-trip verified against the app's LSB-first decoder

**No rolling code / no per-command auth** — confirmed in the app's write path
(no counter/nonce/timestamp/CRC/HMAC) and by the unit accepting an arbitrary
bonded write. Security finding: the control plane is **bonded-but-unauthenticated**;
any bonded central has full, replayable control.

## Fridge (service 0x1100) — worked example

- **State `0x1102`** (8 bytes): byte0 bit0 = power (`09`=on/`08`=off), byte1 =
  fluctuating temperature reading; remaining bytes bit-packed flags/setpoint.
  Full bit slicing is in the app's decode; the power bit is live-confirmed.
- **Control `0x1101`** (6 bytes, device-confirmed length). The 6-byte frame is
  **10 named fields** (`sf/a.B()` debug log): `State` (power, bits 6-7),
  `TimerStart`/`TimerCancel`/`NightTimerSet` (timer *actions*, bits 0-5),
  `Level` (bits 12-15), `Mode` (bits 8-11), and `TimerHour/Min`,
  `NightTimerHourOn/Off`. Offsets bits 0-15 are extracted; the timer-value
  fields are `MERGED_AMBIGUOUS` (fridge vs heater share the model, placed
  differently). See `protocol/dictionary.yaml`.
- **LIVE RESULT (do NOT reuse `fd77…`/`3d7b…`):** writing a whole-frame with the
  model's *defaults* was tested against the unit and **did not toggle power** —
  it was a garbage command (`Level=7` is out of the 1-5 range; `TimerStart/Cancel/
  NightTimerSet=3` = three conflicting timer actions). The unit ignored power and
  stored stray bytes. The `fd770f1e3e1f` frame in `tools/encode.py` is therefore
  a **structural example only, not a working command.**
- **Correct power toggle:** set `State`, set all timer-**action** fields
  (`TimerStart`,`TimerCancel`,`NightTimerSet`) to `0` (no-op), and carry the
  *current* `Level`/`Mode` (read from State) rather than defaults. Verify against
  State `1102` byte0 `08↔09`.

## Status / open items

- Fridge State power bit + Control frame structure: **confirmed / derived**.
- Fridge ON/OFF frames: **derived, not yet live-verified** (write `3d7b007f1f3f`,
  expect `1102` byte0 `08→09`).
- Other services: same extraction method applies (per-service decode `e()` +
  control model `f()`), not yet run.
- Setpoint/mode field semantics for non-power bytes: not yet fully mapped.
