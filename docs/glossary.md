# Glossary

The terms the documentation uses, with the camper unit's own vocabulary. This page is the only
glossary in the docs; every term is also listed in the {ref}`genindex`. Elsewhere in the docs a
term links back here (`{term}` in Markdown, `:term:` in reStructuredText). Pages under
`business-logic/` are the separately published RE lab notes.

```{glossary}
:sorted:

Unit
Camper unit
  The VW California T7 camper control unit that speaks BLE.

buspi
  The Raspberry Pi that runs calictl and the other readers in the van.

Dictionary
  `protocol/dictionary.yaml`, the extracted map of frame fields.

Catalog
Signal catalog
  `protocol/signals.yaml`, the surface or omit decision per field.

Function
  One of the unit's 14 feature areas such as cooler, camping mode, lighting, air heater, water,
  energy or roof. Each has a state and a control characteristic.

GATT characteristic
GATT char
State char
Control char
  A BLE attribute, named by its short id (`1102`, `1003` …). Each function has a state char
  (telemetry, read or notify) and a control char (write). See {doc}`protocol`.

1003 heartbeat
Heartbeat
Arm
Armed
  The +1 liveness counter (4-byte big-endian) on char `1003`, about every 0.6 s. A control write
  is accepted only while it ticks. See {doc}`protocol-sequences`.

Latch
Latched
  The load holds after the heartbeat stops. Arming is one-shot, not a dead-man switch.

Full-packet
  A control frame that resends every field, with unchanged fields set to the leave-unchanged
  sentinel.

Leave-unchanged sentinel
Sentinel
  The value a full-packet frame writes for a field it does not change, usually `3` for a 2-bit
  field. Per field in {doc}`protocol/frame-layouts`.

Neutral re-write
Neutral frame
  The frame the app sends 500 ms after a command, with every field at its sentinel (cooler
  `ff771e3e1f1f`). The unit accepts it; calictl sends none, except the lighting flush.
  See {doc}`protocol-sequences` ("Cooler command — the app's frame").

Readback echo
  The state characteristic returning the value just written. It is not evidence that the load
  acted.

Mode-4 notification
  The `1502` frame that carries the real ramping lighting brightness, the truthful feedback
  channel.

REQUEST_CONFIG
  Lighting Mode 12 frame (`0d0c…`) that asks the unit to report its lighting config (wake-up,
  door contact, favourites) on `1502`. See {doc}`protocol-sequences`.

Door contact
  The lighting option "light on when the sliding door opens" (Mode 16, profile number 8,
  LightValue 1/0). `door_contact on|off`; see {doc}`protocol-sequences`.

Favourite
Profile
  A stored lighting scene (tiles A–D = profiles 1/5/6/7). `save_profile N` stores the current
  levels, `profile N` activates it. See {doc}`protocol-sequences`.

SafetyCounter
SafetyCounterValid
  The roof's app-generated monotonic counter (big-endian uint32, +1 per frame). The unit holds
  the motor about 3 s until it validates it (`1402` bit 7, `SafetyCounterValid`).

Roof view
STOP stream
  While the web UI's roof page is open, calictl streams STOP frames with a running SafetyCounter,
  like the app; a press reuses that counter, so the motor starts without the 3 s hold
  (`R_ROOF_VIEW_STREAM`, {doc}`protocol-sequences`).

InfoPopUp
  The roof's prompt and alert code (`1402` byte 1, low nibble). 2 = the app's pre-open safety
  checklist; 3, 8 and 12 are travel progress codes, not alerts. See {doc}`protocol-sequences`.

Terminal-15
Ignition
  Ignition on, read from `1004` bit 7. The roof moves only with it on.

Water latch
Not measuring
FreshWaterLevel 1
  The unit measures water only while its water system is powered; otherwise it reports
  FreshWaterLevel `1`, a stale latched value. See
  [value-freshness](https://ckeller42.github.io/open-california/business-logic/value-freshness.html).

Measurement ramp
Settle window
  When a measurement starts the level ramps `1→2→…→real` in about 4 s. calictl adopts a new
  level only after the same reading held for the settle window (`WATER_SETTLE_S`, 5 s).

Unconfirmed
  The ESP's command answer `200 {"applied": null, "unconfirmed": true}`: the link dropped after
  the frame went out, so the unit may have applied it (#271). See {doc}`firmware`.

CommunicationVersion
  The unit's BLE protocol version in `1001`. This van reports 2 (V2); app 5.4.0 accepts up to 3
  (V3) and branches some decodes on it. See {doc}`protocol-sequences`.

Installed bit
  The per-function flag that says the equipment is fitted. Only installed functions publish.

MERGED_AMBIGUOUS
  A dictionary field whose offset the extractor could not place; resolved in `overrides.py`.

EXLAP
  VW's XML publish/subscribe protocol, an alternative WiFi/TCP transport.

Central
Peripheral
  The BLE roles: the central (buspi, the satellite, the phone) connects; the peripheral (the
  unit) advertises and accepts. The unit serves several centrals at once.

GATT server
  The side that answers ATT requests. The unit is also a GATT client, so every central needs a
  GATT server; the satellite's NimBLE had none built in. See {doc}`firmware`.

ATT Exchange MTU Request
MTU exchange
  The unit sends it to every central right after connect (Client RX MTU 247). Left unanswered,
  the unit's ATT transaction timeout (30 s) ends the link with `0x13`. See {doc}`protocol-sequences`.

HCI reason code
0x13
0x16
0x3e
0x08
  Why a BLE link ended or failed: `0x13` remote user terminated, `0x16` terminated by the local
  host, `0x3e` connection failed to be established (often a busy scanner), `0x08` supervision
  timeout (out of range, or the peer went silent).

Persistent session
Held link
  A BLE connection kept open between commands instead of connect-read-release; buspi's daemon
  holds one while a viewer is active ({doc}`protocol-sequences`).

Satellite
ESP32 satellite
CoreS3
  The optional ESP32 firmware (on an M5Stack CoreS3) that talks to the unit without buspi, with
  its own WiFi and web UI. See {doc}`firmware`.

Mock unit
Fake unit
  The simulated camper unit: `tools/mock_unit.py` (the state model, used by tests), served over
  real BLE by the Bumble fake peripheral for the app lab. See {doc}`mock-unit-guide`.

App lab
  The real vendor app in an Android emulator against the fake unit (`tools/applab/`): screens
  in any state, and the app's frames diffed against calictl's.

Phone app lab
  The real app on the owner's phone against the real unit, driven over wireless adb; its HCI
  snoop log is decoded with `tools/applab/phone/` (evidence tier CAPTURE).

HCI snoop
btsnoop
  Android's Bluetooth HCI log (`btsnoop_hci.log`): every packet the phone sent or received,
  the source of the real-app captures.

Evidence tier
CAPTURE
DEVICE
APP-RECORDED
APP-OBSERVED
APP-DISPLAY
DECOMPILE
DV
  How a protocol claim was verified, from the wire (CAPTURE) and the van (DEVICE) down to the
  app's code (DECOMPILE, DV = decompile-verified frame). Definitions in
  [evidence-ledger](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html).

Requirement ID
Need ID
  A sphinx-needs object id in a docstring: `R_*` requirement, `S_*` specification, `T_*` test
  that `:links:` its requirement. See {doc}`building-the-docs`.

Sofortheizen
Immediate heating
  The air heater's immediate-heating mode, the unit's own label. The UI uses it as shown.

Dauerbetrieb
Continuous heating
  The air heater's continuous operation, the unit's own label. The UI uses it as shown.

Flüstermodus
Quiet mode
  The cooler's whisper mode, the unit's own label. The UI uses it as shown.

Zweitbatterie
Second battery
  The camper's house battery, as the unit labels it (vs the vehicle battery).
```
