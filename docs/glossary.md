# Glossary

The terms the documentation uses, with the camper unit's own vocabulary. Elsewhere in the docs a
term links back here.

```{glossary}
:sorted:

Unit
  The VW California T7 camper control unit that speaks BLE.

buspi
  The Raspberry Pi that runs calictl and the other readers in the van.

Dictionary
  `protocol/dictionary.yaml`, the extracted map of frame fields.

Catalog
  `protocol/signals.yaml`, the surface or omit decision per field.

Function
  One of the unit's 14 feature areas such as cooler, camping mode, lighting, air heater, water,
  energy or roof. Each has a state and a control characteristic.

1003 heartbeat
  The liveness counter, incremented about every 0.6 seconds, that arms control writes.

Full-packet
  A control frame that resends every field, with unchanged fields set to the leave-unchanged
  sentinel.

Armed
  A write is accepted only while the heartbeat ticks.

Latched
  The load holds after the heartbeat stops. Arming is one-shot, not a dead-man switch.

Readback echo
  The state characteristic returning the value just written. It is not evidence that the load
  acted.

Mode-4 notification
  The `1502` frame that carries the real ramping lighting brightness, the truthful feedback
  channel.

SafetyCounter
  The roof's app-generated monotonic counter that the unit must validate before moving the motor.

Evidence tier
  How a protocol claim was verified: DEVICE (watched on hardware), CAPTURE (seen on the wire),
  DECOMPILE (read from the app) or UNVERIFIED.

Satellite
  The optional ESP32 firmware that talks to the unit without buspi.

Mock unit
  The Bumble-based fake BLE peripheral used by tests and the app lab.

Sofortheizen
  The unit's own label for immediate heating. The UI uses it as shown.

Dauerbetrieb
  The unit's own label for continuous operation. The UI uses it as shown.

Flüstermodus
  The unit's own label for whisper mode. The UI uses it as shown.
```
