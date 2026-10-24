# Tested hardware

This page describes the hardware this project runs on and is tested against: the camper unit and
the reference van's equipment, the Raspberry Pi host, the ESP32 satellite board and the sinks.
It is hand-authored from the code and the
[evidence ledger](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html);
the generated protocol views under `docs/protocol/` list every field. Update this page by hand
when the equipment profile changes.

What `calictl` can read and control today, function by function and with how far each feature
is verified, is on the generated page
{doc}`What calictl does today <features>`.

```{danger}
No real vehicle identifiers appear on this page or anywhere in this repository — no BLE
MAC address, no VIN. Where an example is useful, a placeholder (`AA:BB:CC:DD:EE:FF`) is
used instead.
```

## Vehicle

VW California **T7** camper control unit. It exposes its telemetry and control surface over a
single vendor **BLE GATT service**, `0000XXXX-6c77-4b7d-bbf6-a5e587701f3d`, where `XXXX`
varies per function (see the service map below). Besides this project, the unit talks only to
the vendor's iOS/Android app; there is no other Linux-facing interface. The unit serves several
BLE centrals at once (the phone app, the Pi and the ESP32 satellite).

## GATT service map

Each on-board function occupies a 4-hex-digit slot in the vendor UUID base. The **generated**
{doc}`protocol/frame-layouts` places every field of each function in a per-byte bit grid; the
table below is the coarse per-function characteristic map for orientation:

```{list-table}
:header-rows: 1
:widths: 22 14 44 20

* - Function
  - Char base
  - State chars (read/notify)
  - Control char (write)
* - general
  - `1000`
  - `1000`, `1001`
  - —
* - vehicle
  - `1000`
  - `1000`, `1004`
  - —
* - cooler
  - `1100`
  - `1100`, `1102`
  - `1101`
* - campingmode
  - `1200`
  - `1200`, `1202`
  - `1201`
* - water
  - `1300`
  - `1300`, `1302`
  - —
* - roof
  - `1400`
  - `1400`, `1402`
  - `1401`
* - lighting
  - `1500`
  - `1500`, `1502`
  - `1501`
* - energy
  - `1600`
  - `1600`, `1602`
  - `1601`
* - airheater
  - `1700`
  - `1700`, `1702`
  - `1701`
* - stairs
  - `1800`
  - `1800`, `1802`
  - —
* - satelliteantenna
  - `1900`
  - `1900`, `1902`–`1905`
  - —
* - roofaircondition
  - `2000`
  - `2000`, `2002`
  - —
* - livingroomheater
  - `2100`
  - `2100`, `2102`
  - —
* - generalpurposesignals
  - `f000`
  - `f000`, `f001`
  - —
```

There is also the write-only liveness counter char `00001003` (the actuation-arming
{term}`heartbeat` — see the control docs), not tied to any one function above.

## Software / version fields

The `general` function (char `1001`) reports three firmware identifiers, decoded straight
off the wire with no scale applied:

- `AmbSwVersion` — the camper-unit ("Ambiente") firmware version. The reference van has run
  `0409` and `0410`. On both, `calictl` adds 2 to the decoded DC-DC current (the energy signal
  notes in `docs/business-logic/signals.md` explain the correction).
- `CmSwVersion` — the "CM" (control-module) firmware version.
- `CommunicationVersion` — the BLE protocol/communication-layer version. The reference van
  reports 2.

These are read-only signals (no control fields on this function). Check them first when a
capture or a field's behaviour does not match what is documented here: the protocol has
changed between firmware revisions before.

The daemon **monitors the firmware/protocol version**: it flags `firmware_untested` (in
`_meta.firmware` and as a Vehicle-page warning) when the unit runs anything outside the tested
set (`AmbSwVersion` 0409/0410, `CommunicationVersion` 2), captures a raw-frame snapshot on the
first-seen firmware and again on any change (so a correction for a new firmware can be derived
from the wire evidence), and runs plausibility anchors that alarm when a decoded value falls out
of physical range (a likely sign of an offset shift after an update). See `calictl/firmware.py`,
`calictl/anchors.py` and `calictl/semantics.py`.

## Equipment profile

Installed equipment is a **per-van configuration**, not a protocol constant — each function
carries its own {term}`Installed bit`, and `calictl` gates a function off when that bit is unset.
The table below is the profile of the reference van this project is developed and tested
against:

```{list-table}
:header-rows: 1
:widths: 30 15 55

* - Function
  - Installed
  - Notes
* - cooler
  - yes
  - compressor fridge with on/off, level 1–5, a quiet mode and quiet hours, and a cooling timer
* - campingmode
  - yes
  - master switch, interior + outside lights, rear USB; lights and USB need the master on (see
    the note below for driving)
* - water
  - yes
  - fresh + waste tank levels; measured only while the van's water system is powered
* - energy
  - yes
  - leisure/starter battery, DC-DC, shore and solar telemetry; charging-mode selector
* - lighting
  - yes
  - 16 addressable zones
* - airheater
  - yes
  - diesel air heater; power, level, run time and departure timer; continuous heating can be
    switched on only in the vehicle (the app never sends that ON)
* - roof
  - yes
  - pop-top roof; the unit reports `roof.Installed = 1`
* - stairs
  - no
  - not installed on this van
* - livingroomheater
  - no
  - not installed on this van
* - roofaircondition
  - no
  - not installed on this van
* - satelliteantenna
  - no
  - not installed on this van
* - solar
  - no
  - not a function of its own: the `energy` function's `PvInstalled` bit, not set on this van
```

A different van's equipment profile surfaces a different subset — the `Installed` bits make
this self-describing at runtime (`calictl status` shows only what is on board).

```{note}
**Roof.** The pop-top roof is installed (`roof.Installed = 1`) and `calictl` reads its position
live; only the roof *A/C* is absent. The roof motor has never been driven by `calictl`: its
frames match the app's byte for byte, but a first drive needs ignition ON and the owner watching.

**Camping mode while driving.** The unit itself refuses camping master ON while the van is
driven: it ACKs the write, does not apply it, and its own screen says the function is not
available while driving. An engine start also switches camping mode off. The web UI greys the
master switch while the ignition is on ("Only possible when stationary"). `calictl` itself does
not block the write: from the CLI, the API or Home Assistant it is sent and reported as not
applied.

**Energy mode.** `calictl` builds Normal, Max and ECO. The app's Normal and Max frames are
recorded and match `calictl`'s. The app offers ECO only when the unit reports `PvInstalled`
(solar fitted), which this van does not, so the ECO frame is not app-recorded.

**Cooler quiet mode.** The quiet mode and its hours are live-verified on the unit's own
screen; the unit takes the quiet-hour bytes literally.
```

```{caution}
The lighting **state-char readback is a write-through echo**, not proof of actuation — a
write can appear to "take" on readback while the lamp never physically changes. Trust
either a human at the lamp or the unit's genuine `1502` Mode-4 ramp notifications, never
the bare readback. See the lighting section of
`docs/business-logic/control-and-actuation.md`.
```

## Host

- **Hardware:** Raspberry Pi 4, referred to throughout this project's docs as {term}`buspi`.
- **OS:** Debian 13 (aarch64). The kernel is pinned to 6.18.34 because a later kernel cannot
  reconnect to the unit's rotating address (see {doc}`raspberry-pi-setup`).
- **Python:** 3.13 (the project floor is 3.11+; `tools/ci.sh` prefers 3.13 to match buspi).
- **BLE:** the Pi's `hci0` through BlueZ via the [bleak](https://github.com/hbldh/bleak)
  library, imported lazily — the runtime package is stdlib-only at import time (a hard rule in
  [AGENTS.md](https://github.com/ckeller42/open-california/blob/main/AGENTS.md)). `hci0` is
  shared with other BLE readers on the Pi, so one daemon owns the camper link.

## ESP32 satellite

- **Board:** M5Stack {term}`CoreS3 <Satellite>` (ESP32-S3) with its touch screen and a microSD
  slot for the reading log (FAT32, up to 32 GB).
- **Power:** its own 5 V USB-C supply in the van; it needs no cable to the Pi.
- **Radio:** its own NimBLE stack and bond to the unit, next to the Pi's; WiFi for its web UI.
- **Details:** {doc}`firmware`, {doc}`howto-esp-standalone`.

## Sinks

Home Assistant over MQTT (Mosquitto), and Grafana over InfluxDB, both fed by the daemon on the
Pi that owns its BLE link to the unit. The phone app and the ESP32 satellite stay connected to
the unit alongside it.

No real vehicle MAC address or VIN is recorded anywhere in this repository; if you need to
reference a device address in your own notes, use a placeholder such as
`AA:BB:CC:DD:EE:FF`.
