# Quality, risks and glossary

## 10 Quality requirements

### Quality scenarios

| Goal | Scenario | How it is checked |
|---|---|---|
| Safe actuation | A client sends a value outside a control's range. The frame is refused before any byte is written. | `protocol.encode` range tests, the range-rejection sequence |
| Safe actuation | A write is sent while the readback echoes the new value. The UI must not claim the unit acted. | `postcheck` and the "applied" semantics, [control and actuation](../business-logic/control-and-actuation.md) |
| Truthful values | A new dictionary field appears with no catalog decision. CI fails. | `tests/test_signal_coverage.py`, `tools.audit_signals` |
| Truthful values | A new buildable command has no evidence tier. CI fails. | `tests/test_command_coverage.py` |
| One BLE owner | A poll and a command arrive together. They run one after the other. | the lock in `serve`, the e2e tests over the mock |
| Absent unit | The unit is asleep for days. The UI shows the last state and an offline banner, not an error. | persisted last state, web e2e tests |
| Drift is caught | The C codec and the Python codec disagree. CI fails. | `codec-parity`, `gen_codec_vectors --check` |
| Maintainability | An undeclared identifier reaches a rarely used screen. The type check rejects it. | `tsc --checkJs` baseline of zero errors |

### Quality gates in CI

The required checks are `test` on Python 3.11, 3.12 and 3.13, `pre-commit`, `no-vendor-material`,
`install-script`, `docs`, `codec-parity`, `gui-e2e`, `firmware-host-e2e`, `firmware-build` and
`firmware-qemu`. Coverage on `calictl/` is a ratchet that is only ever raised. The full list and
what each job proves is in [simulation and testing](../simulation-and-testing.md).

## 11 Risks and technical debt

| Risk or debt | Impact | Status |
|---|---|---|
| The roof motor has never been driven by calictl | A protocol-correct path that has not moved real hardware. The unit withholds the motor for about 3 seconds until its safety counter validates. | Open. First owner-watched drive is tracked as issues 157 and 230. |
| Several app-faithful frames are not verified on the real unit | The cooler level and the guided pairing wizard are CI-verified but not yet confirmed at the van. | Open, issues 157 and 230. |
| The satellite has not touched the real unit | The read side and the control path ran against a fake unit and a mock unit on a bench only. | Open, work in progress. |
| Some scales are unverified | Temperatures and the state-of-charge level carry no unit. | Tracked in the signal notes. |
| The unit deep-sleeps and the vendor firmware can change | Access is intermittent, and a firmware update could move a field. | Mitigated by firmware-drift capture and plausibility anchors, but not removable. |
| Semantic correctness is not auto-checked | A getter that is inverted or combined can be shipped as a wrong label. | Mitigated by the polarity procedure and the on-screen ground truth. |
| Water readings can be a stale latch | A parked read may return an old value. | A guard holds the last plausible reading. Whether it is still needed is open until a van trace. |
| Grafana dashboards do not update on their own | A new signal can be missing from the dashboards. | A reminder hook and the push script. Still a manual step. |

## 12 Glossary

| Term | Meaning |
|---|---|
| Unit | The VW California T7 camper control unit that speaks BLE. |
| buspi | The Raspberry Pi that runs calictl and the other readers in the van. |
| Dictionary | `protocol/dictionary.yaml`, the extracted map of frame fields. |
| Catalog | `protocol/signals.yaml`, the surface or omit decision per field. |
| Function | One of the unit's 14 feature areas such as cooler, camping mode, lighting, air heater, water, energy or roof. Each has a state and a control characteristic. |
| `1003` heartbeat | The liveness counter incremented about every 0.6 seconds that arms control writes. |
| Full-packet | A control frame that resends every field, with unchanged fields set to the leave-unchanged sentinel. |
| Armed, latched | A write is accepted only while the heartbeat ticks, and the load then holds after it stops. |
| Readback echo | The state characteristic returning the value just written. It is not evidence that the load acted. |
| Mode-4 notification | The `1502` frame that carries the real ramping lighting brightness, the truthful feedback channel. |
| SafetyCounter | The roof's app-generated monotonic counter that the unit must validate before moving the motor. |
| Evidence tier | DEVICE (watched on hardware), CAPTURE (seen on the wire), DECOMPILE (read from the app), UNVERIFIED. |
| Satellite | The optional ESP32 firmware that talks to the unit without buspi. |
| Mock unit | The Bumble-based fake BLE peripheral used by tests and the app lab. |
| Sofortheizen, Dauerbetrieb, Flüstermodus | The unit's own German labels (immediate heating, continuous operation, whisper mode). The UI uses them as shown. |
