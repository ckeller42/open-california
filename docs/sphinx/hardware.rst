Tested hardware
===============

This page documents the *specific* vehicle and host this project has been reverse-engineered
and tested against — what's confirmed on real hardware versus decompile/static analysis only.
Facts here are hand-authored from ``CLAUDE.md`` ("Known state"),
``docs/business-logic/control-and-actuation.md``, and the generated protocol views under
``docs/protocol/reference.md``; nothing here is regenerated automatically, so if the
equipment profile or verification tier changes, update this page by hand.

.. danger::

   No real vehicle identifiers appear on this page or anywhere in this repository — no BLE
   MAC address, no VIN. Where an example is useful, a placeholder (``AA:BB:CC:DD:EE:FF``) is
   used instead.

Vehicle
-------

VW California **T7** camper control unit. It exposes its telemetry and control surface over a
single vendor **BLE GATT service**, ``0000XXXX-6c77-4b7d-bbf6-a5e587701f3d``, where ``XXXX``
varies per function (see the service map below). The unit only otherwise talks to the vendor's
iOS/Android app; there is no other Linux-facing interface.

GATT service map
-----------------

Each on-board function occupies a 4-hex-digit slot in the vendor UUID base. The **generated**
``docs/protocol/reference.md`` lists every field per function (offsets, widths, scales); the
table below is the coarse per-function characteristic map for orientation:

.. list-table::
   :header-rows: 1
   :widths: 22 14 44 20

   * - Function
     - Char base
     - State chars (read/notify)
     - Control char (write)
   * - general
     - ``1000``
     - ``1000``, ``1001``
     - —
   * - vehicle
     - ``1000``
     - ``1000``, ``1004``
     - —
   * - cooler
     - ``1100``
     - ``1100``, ``1102``
     - ``1101``
   * - campingmode
     - ``1200``
     - ``1200``, ``1202``
     - ``1201``
   * - water
     - ``1300``
     - ``1300``, ``1302``
     - —
   * - roof
     - ``1400``
     - ``1400``, ``1402``
     - ``1401``
   * - lighting
     - ``1500``
     - ``1500``, ``1502``
     - ``1501``
   * - energy
     - ``1600``
     - ``1600``, ``1602``
     - ``1601``
   * - airheater
     - ``1700``
     - ``1700``, ``1702``
     - ``1701``
   * - stairs
     - ``1800``
     - ``1800``, ``1802``
     - —
   * - satelliteantenna
     - ``1900``
     - ``1900``, ``1902``–``1905``
     - —
   * - roofaircondition
     - ``2000``
     - ``2000``, ``2002``
     - —
   * - livingroomheater
     - ``2100``
     - ``2100``, ``2102``
     - —
   * - generalpurposesignals
     - ``f000``
     - ``f000``, ``f001``
     - —

There is also the write-only liveness counter char ``00001003`` (the actuation-arming
heartbeat — see the control docs), not tied to any one function above.

Software / version fields
--------------------------

The ``general`` function (char ``1001``) reports three firmware identifiers, decoded straight
off the wire with no scale applied:

- ``AmbSwVersion`` — the camper-unit ("Ambiente") firmware version. Both ``0409`` and ``0410``
  have been observed on the reference van across firmware updates; the ``0410`` revision is
  the one that made the DC-DC-current correction take effect (see the energy signal notes in
  ``docs/business-logic/signals.md``).
- ``CmSwVersion`` — the "CM" (control-module) firmware version.
- ``CommunicationVersion`` — the BLE protocol/communication-layer version.

These are read-only, surfaced signals (no control fields on this function); they're the
right thing to check first when a capture or a field's behavior doesn't match what's
documented here — the protocol has visibly drifted across firmware revisions once already.

Equipment profile
------------------

Installed equipment is a **per-van configuration**, not a protocol constant — each function
carries its own ``Installed`` bit, and ``calictl`` gates a function off when that bit is unset.
The table below is the profile of the specific reference van this project was developed and
tested against:

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Function
     - Installed
     - Notes
   * - cooler
     - yes
     - compressor fridge
   * - campingmode
     - yes
     - master / interior+outside lights / USB — gated stationary-only (see below)
   * - water
     - yes
     - fresh + waste tank levels
   * - energy
     - yes
     - leisure/starter battery, DC-DC, shore, solar telemetry
   * - lighting
     - yes
     - 16 addressable zones
   * - airheater
     - yes
     - installed; ``power`` setter capture-observed, level/timer/runtime setters
       decompile-verified — untested end-to-end because buspi was offline during the capture
       window, not because the unit lacks one
   * - roof
     - yes
     - pop-top roof installed; frame is decompile-verified byte-for-byte against the app, but
       the motor has never been driven end-to-end by this project (not-live-verified)
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
     - (folded into the ``energy`` function's ``PvInstalled`` bit; not populated on this van)

A different van's equipment profile will surface a different subset — the ``Installed`` bits
make this self-describing at runtime (``calictl status`` only shows what's actually on board).

Per-function verification tier
-------------------------------

"Decompile-verified" means the frame/offsets/polarity were checked against the vendor app's
own decompiled setter/getter code, but the physical effect has not been confirmed on a real
unit. "Live-actuation-verified" means a human watched (or otherwise confirmed) the physical
device change state in response to a ``calictl`` write. See
``docs/business-logic/control-and-actuation.md`` §4–5 for the full history, including the
lighting readback-echo pitfall (a decoded value that "confirmed" a change that never
physically happened).

.. list-table::
   :header-rows: 1
   :widths: 20 25 55

   * - Function
     - Tier
     - Notes
   * - cooler
     - live-actuation-verified
     - power on/off, level 1-5 confirmed on-device
   * - campingmode
     - live-actuation-verified
     - master/lights/USB confirmed; refused by the unit while driving (stationary gate)
   * - lighting
     - live-actuation-verified
     - per-zone brightness confirmed by human observation (photon-verified); state-char
       readback is a write-through echo and is *not* itself proof — see the caveat below
   * - roof
     - decompile-verified only
     - installed (pop-top) on the reference van; frame matches the app byte-for-byte; motor
       has never been driven end-to-end by this project
   * - airheater
     - decompile-verified / partially live
     - installed on the reference van; power capture observed; level/timer/runtime setters
       are decompile-verified but untested end-to-end — buspi was offline during the capture
       window, not an equipment gap
   * - energy
     - decompile-verified only
     - mode setter (normal/max-charge/eco) matches the app; not live-actuated
   * - stairs, roof-A/C, satellite, living-room-heater
     - decompile-verified (static) only
     - not installed on the reference van; offsets and enum semantics checked statically
       against the app's getters, no physical unit available to confirm

.. caution::

   The lighting **state-char readback is a write-through echo**, not proof of actuation — a
   write can appear to "take" on readback while the lamp never physically changes. Trust
   either a human at the lamp or the unit's genuine ``1502`` Mode-4 ramp notifications, never
   the bare readback. See the lighting section of
   ``docs/business-logic/control-and-actuation.md`` for the full history of how this was
   found.

Host
----

- **Hardware:** Raspberry Pi, referred to throughout this project's docs as ``buspi``.
- **OS:** Debian 13 (aarch64).
- **Python:** 3.13 (the project floor is 3.11+; ``tools/ci.sh`` prefers 3.13 to match buspi).
- **BLE:** BlueZ via the `bleak <https://github.com/hbldh/bleak>`_ library, imported lazily —
  the runtime package is stdlib-only at import time (see ``CLAUDE.md``).
- **Sinks:** Home Assistant over MQTT, and Grafana over InfluxDB, both fed by the same daemon
  that owns the single BLE connection slot.

No real vehicle MAC address or VIN is recorded anywhere in this repository; if you need to
reference a device address in your own notes, use a placeholder such as
``AA:BB:CC:DD:EE:FF``.
