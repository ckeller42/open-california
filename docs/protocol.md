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
| 1500 | (awning/secondary) | 1600 | ENERGY (battery/solar/DC-DC/shore) |
| f000 | generic Read/Write (OTA?) | | |

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
- **Control `0x1101`** (6 bytes): field `POWER` at bits 6-7; neighbors + setpoint
  fields elsewhere (see `tools/encode.py` for the field map + defaults).
- **Computed frames** (other fields = app defaults):
  - **ON  = `3d 7b 00 7f 1f 3f`**
  - **OFF = `3c 7b 00 7f 1f 3f`**
  - Bytes 1-5 are defaults; the app sends current setpoint/mode there — pass
    live values to avoid resetting them.

## Status / open items

- Fridge State power bit + Control frame structure: **confirmed / derived**.
- Fridge ON/OFF frames: **derived, not yet live-verified** (write `3d7b007f1f3f`,
  expect `1102` byte0 `08→09`).
- Other services: same extraction method applies (per-service decode `e()` +
  control model `f()`), not yet run.
- Setpoint/mode field semantics for non-power bytes: not yet fully mapped.
