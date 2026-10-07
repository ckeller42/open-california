# Crosscutting concepts and decisions

## 8 Crosscutting concepts

| Concept | How it works | Where |
|---|---|---|
| Single source of protocol truth | `protocol/dictionary.yaml` feeds the Python codec, the generated C codec header and the golden vectors. Both consumers (calictl and the ESP satellite) decode the same bits. | `protocol/`, `csrc/`, `tests/vectors/` |
| Signal catalog | Every dictionary field has a `surface` (with a name) or `omit` (with a reason) decision. A dropped field fails CI. | `protocol/signals.yaml`, `tests/test_signal_coverage.py` |
| Semantic polarity | Interpretation is not auto-checked. A field whose app getter is inverted or combined is verified against the app's getter and the unit's own screen, never a naive `bool(field)`. | [signals notes](../business-logic/signals.md) |
| Evidence tiers | Each claim is tagged DEVICE, CAPTURE, DECOMPILE or UNVERIFIED. A buildable command without a tier row fails CI, and so does an uncited app recording. | [evidence ledger](../business-logic/evidence-ledger.md) |
| Control safety | Full-packet frames, width and range validation in `protocol.encode`, preconditions per command, a single lock, and an applied-check that treats readback as an echo. | `control`, `protocol`, `postcheck` |
| Freshness | Reads go stale and the unit deep-sleeps. The last state is persisted with an "as of" time, and implausible drops (water) are held and flagged stale. | `freshness`, [value freshness](../business-logic/value-freshness.md) |
| Honest units | A value gets a unit only when it is verified. SoC is a coarse level, temperatures are raw levels, and currents are amps since they were checked against the app. | [signals notes](../business-logic/signals.md) |
| Internationalisation | The UI uses the unit's own vocabulary in English and German. A test guards literal translation keys. | `calictl/webui/strings.de.js`, `tests/test_i18n_de.py` |
| Logging and tracing | Standard `logging` with an env-controlled level, and a JSONL recorder of every notify, read and write for replay against the mock. | `log`, `trace`, `tools.trace_compare` |
| Requirements traceability | `req` objects in code docstrings link to `test` objects in test docstrings through sphinx-needs. A dangling link fails the docs build. | [requirement traceability](../index.rst) |
| Test layers | Unit tests, a mock unit, GUI end-to-end over the mock, real-unit trace replay, a BlueZ pairing rig and C codec parity. | [simulation and testing](../simulation-and-testing.md) |

## 9 Architecture decisions

The dated record of resolved questions and ruled-out dead ends is the
[decision log](../business-logic/DECISIONS.md). The decisions that shape the architecture most are
summarised below in ADR form, newest context first. Each links to its provenance.

| Decision | Context | Consequence |
|---|---|---|
| **One daemon owns the BLE slot** | The unit takes one connection and the Pi's adapter is shared with other readers. | A lock created inside the running loop serialises poll and command, and no other code path may open a connection. |
| **Control writes are armed by a one-shot `1003` heartbeat** (issue 2, 2026-07-07) | Writes were ignored until a liveness counter was ticking. The load latches once armed. | The heartbeat only spans the write window. A roof move ticks it through the whole move. See [control and actuation](../business-logic/control-and-actuation.md). |
| **Control frames follow the vendor app byte for byte** (ruling R1, 2026-10-06) | calictl once re-asserted current values in untargeted cooler fields, which differs from the app. | Untargeted fields carry the leave-unchanged value, and the recording replay compares whole frames. |
| **The ESP satellite is a second implementation, not a port of the runtime** (2026-10-06) | A satellite should work without buspi but must not drift from calictl. | It shares the dictionary, generated constants and golden vectors, writes through one allow-list, never drives the roof, and writes only in station mode. See [firmware](../firmware.md). |
| **Runtime modules import only the standard library** | Tests must run on a machine with no BLE or MQTT stack. | Heavy dependencies import lazily inside functions, and a guard checks it. |
| **Diagrams are text-based Mermaid** | The docs site is built without Java and diagrams live next to the prose. | A lint guards the characters that break Mermaid in the browser, because the Sphinx build cannot catch them. |

New decisions go into the decision log first. Add a row here when a decision changes a building
block or a quality goal.
