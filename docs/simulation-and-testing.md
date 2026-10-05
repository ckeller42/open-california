# Simulation and testing

The camper unit is usually out of reach. It sits in a parked van, deep-sleeps for days, and has a
single BLE connection slot. It also drives real loads. So nearly all of `calictl` is tested against
**fakes of the unit**, in layers. Each layer is closer to real hardware than the one below it and
runs less often. This page lists the layers, what each one proves, what it does *not* prove, and
which CI job runs it.

The one rule that applies to every layer: **a fake encodes only behaviour that has already been
reverse-engineered.** It is a regression harness and executable documentation, not an oracle. New
protocol facts come from the real unit (a trace, a capture, the owner watching the camper's screen)
or from the vendor app (the app lab, below). See the
[evidence ledger](https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html)
for how strongly each fact is proven.

## The layers at a glance

| Layer | What it runs against | Proves | CI job |
|---|---|---|---|
| Unit tests (`tests/test_*.py`) | pure functions, stubs, golden vectors | decode/encode, semantics, control frames, state machines, web/API contracts | `test` (Python 3.11 / 3.12 / 3.13) |
| Mock unit (`tools/mock_unit.py`) | an in-process model of the unit behind a fake `bleak` | the real `device`/`cli`/`serve` paths end to end, no radio | `test` |
| GUI end-to-end (`tests/e2e/`) | the real daemon + web UI over the mock, in Chromium | what a user sees and clicks, with no uncaught JS errors | `gui-e2e` |
| Trace replay (`tools/trace_compare.py`) | a recorded trace of the **real** unit | where the mock differs from the hardware | manual (needs a trace from the van) |
| Pairing over a virtual radio (`tests/test_pairing_link.py`) | the fake unit as a Bumble peripheral, Bumble `LocalLink` | the real pairing runner + state machine with real SMP passkey pairing | `test` |
| Pairing over real BlueZ (`tests/realstack/`) | the same fake unit, real BlueZ + kernel in a VM | calictl's real `BluezTransport` (D-Bus agent, scan, connect, Pair, bond probe) | `pairing-real-stack` (not required yet) |
| App lab (`tools/applab/`) | the **vendor Android app** in an emulator against the fake unit | that the fake behaves like the unit *as the app sees it*, and app-vs-calictl frame diffs | manual (local only) |
| App recordings (`tests/vectors/app/`) | the real app's recorded GATT steps and writes, per scenario and APK version (none committed yet) | that calictl's frames equal the app's on the fields the app targets | `test` |
| C codec parity (`csrc/`) | the C port of the codec and three decision ports | Python and C produce identical results | `codec-parity` |

`tools/ci.sh` runs most of this locally. It skips `gui-e2e` without Playwright and the C tests
without a compiler, and it never runs the real-BlueZ VM job. The git hooks (`tools/ci.sh dev`
installs them from `.pre-commit-config.yaml`) run the fast guards and linters on every commit and
the full suite plus the signal audit on every push. The notes on the `tools/ci.sh` line
in `AGENTS.md` list exactly what only GitHub runs.

## Unit tests

`python3 -m pytest tests/ -q` runs everywhere with no BLE, MQTT or InfluxDB installed, because the
runtime is stdlib-only at import. Some guards in this suite protect repo-wide invariants:

- `tests/test_signal_coverage.py`: every dictionary field has a catalog decision.
- `tests/test_mermaid_syntax.py`: diagrams in the docs do not contain characters that break
  mermaid in the browser.
- `tests/test_gen_c_dict.py`, `tests/test_codec_vectors.py`: the generated C header and the golden
  vectors are fresh.
- `tests/vectors/pairing.json` (replayed by `tests/test_pairing_sm.py`): the pairing state
  machine's sequence vectors, which a future C port will also replay.
- `tests/test_mock_fidelity.py`: pins the mock to behaviour observed in the vendor app's own
  traffic.

Tests that need an optional dependency skip cleanly when it is missing: Bumble for the pairing
harnesses, Playwright for `tests/e2e`, a C compiler for the parity tests.

## The mock unit (`tools/mock_unit.py`)

`MockCamperUnit` is the "firmware". It holds per-function decoded state and models the behaviour
reverse-engineered so far. `MockBleakClient` is a drop-in for `bleak.BleakClient` that exposes only
the surface `calictl/device.py` uses. It is seeded with **every function this van has fitted**, so
every tile and screen renders.

What it models:

- **The 1003 arm gate.** A control write is honoured only while the liveness heartbeat is ticking.
  Without it the write is ACKed and ignored. A link with no beat for 15 s is dropped. Lighting is
  exempt, as on the van: an awake unit actuates a bare SET_BRIGHTNESS plus commit with no
  heartbeat (photon-verified).
- **The parse layer.** An out-of-range field value drops the link (`MockDisconnect`), armed or not.
  The 2-bit `3` and the wider fields' defaults are treated as "leave unchanged", as the app sends
  them.
- **Device-confirmed silent refusals.** A write is ACKed but not applied for: camping master while
  driving, the pop-top reading light while the roof is closed, and a cooling-timer change while the
  fridge is on.
- **Push cadence.** Each subscribed char is pushed once right after subscribe (as the real unit was
  traced doing). After that, only `CHANGE_PUSH_FNS` (camping mode and vehicle/ignition) push on
  change. On top of that, the lighting ramp pushes and water pushes on a measured change.
- **Water.** It is measured only while the van's water system is powered
  (`set_water_power(False)` freezes both tanks). It is pushed on a measured change and never
  refreshed by the heartbeat. This is the latch that `freshness.implausible_water_drop` detects.
- **Lighting.** SET_PROFILE and SET_BRIGHTNESS only stage a change, and the `0e00…` commit frame
  applies it. A brightness frame must carry a non-zero ProfileNumber. The state char is a
  write-through **echo**, while the physical lamps ramp on the clock and push 1502 Mode-4 frames.
  `light_applies = False` models "ACKed and echoed, but the lamps stay dark".
- **The roof SafetyCounter.** The counter is valid only while it is monotonic and still advancing.
  A restart invalidates it. A freshly validated counter withholds the motor for about 3 s
  (`ROOF_WITHHOLD_S`, semi-verified). A held move steps `Position`, and releasing it (no frames)
  stops the motion.
- **Clock-driven dynamics.** Heater and cooler countdowns and timers. Ignition sheds camping mode.
  The link drops for about a minute at engine crank. Deep sleep (`drop()`/`wake()`) and an opt-in
  single connection slot (`one_slot`).
- **Pairing.** `FakePairingTransport` scripts the guided-pairing wizard's happy path and
  wrong-passkey path for the web e2e. It stands in for BlueZ, so it does no radio work.

Known fidelity gaps. The mock is **wrong** or **coarser** than the van here, so don't trust a green
mock run on these points:

- **Roof motion is a model, not a measurement.** calictl has never driven the real motor. The
  withhold time, step timing and limit behaviour are parameters.
- **"Driving" is an explicit flag**, not derived: the real "vehicle is stationary" predicate is
  still unknown. **The single connection slot is opt-in** (off by default), although the real unit
  always has one.
- **No link-layer behaviour**: no encryption, bonding, MTU or advertising. That lives in the fake
  peripheral below.
- Alert codes (`InfoPopUp`, `ErrorCode` and the like) change only when a test or the app-lab console
  sets them. Their real causes are not modelled.

### Running calictl against the mock

`tools/run_against_mock.py` installs the fake `bleak`, then runs the normal CLI:

```sh
python -m tools.run_against_mock set cooler power on
python -m tools.run_against_mock get cooler
CALICTL_ENABLE_WRITES=1 python -m tools.run_against_mock serve --web 8080 --interval 1 --no-influx
```

The last command runs the real daemon and web UI at `http://localhost:8080` over the mock, with
the pairing wizard on `FakePairingTransport`. This is what the GUI e2e suite launches.

## GUI end-to-end (`tests/e2e/`)

Playwright drives Chromium against that mock daemon. It checks real data readouts,
installed-gating, command feedback, the unit's state gates, roof press-and-hold → STOP, and the
pairing wizard. **Every test fails on an uncaught JS error**, and one test opens every tile.

```sh
pip install -r requirements-e2e.txt && python -m playwright install chromium
python -m pytest tests/e2e -q
```

The same mock also feeds the screenshots: `tools/ci.sh screenshots` runs `tools.ux_gallery` to
render `docs/screenshots/`. On `main` the `screenshots.yml` workflow re-renders them and commits
the result.

## Real-unit traces (`CALICTL_BLE_TRACE` + `tools/trace_compare.py`)

This layer checks the hardware side. Run the daemon at the van with a trace file:

```sh
CALICTL_BLE_TRACE=~/ble.jsonl python3 -m calictl serve …    # + CALICTL_BLE_TRACE_HEARTBEAT=1 for 1003 beats
python3 -m tools.trace_compare ~/ble.jsonl                   # or --json
```

`calictl/trace.py` writes one JSON line per notify, read, write and link event. `trace_compare`
replays the trace through the mock and reports four things:

- state frames that do not round-trip through the dictionary (bits the unit uses that are not
  modelled);
- notification cadence per char, compared with the mock's push model;
- countdown and age rates, roof `Position` transitions, and the ignition → camping coupling;
- calictl's own frames.

A difference means the mock has a bug or you have found a new protocol fact. Don't edit the trace
to make it go away. Results are recorded in
[protocol-crosscheck-applab.md](https://ckeller42.github.io/open-california/business-logic/protocol-crosscheck-applab.html).

## Pairing harnesses (#201)

Pairing can't use the mock: it happens below GATT, in SMP and in BlueZ. So
`tools/fake_unit_peripheral.py` wraps the same `MockCamperUnit` in a **Bumble BLE peripheral**
(requirement `R_FAKE_UNIT_FIDELITY`). The peripheral:

- advertises `VWCAMPER` from a rotating private address over a fixed identity;
- pairs with LE Secure Connections passkey entry, and **displays a fresh passkey per attempt**;
- refuses new bonds while its "Gerät verbinden" screen is closed;
- holds one connection at a time.

The same fake is used in three places.

### 1. A virtual radio, on every PR (`tests/test_pairing_link.py`)

calictl's real `PairingRunner` and state machine pair with the fake over a Bumble `LocalLink`,
through a Bumble central (`BumbleTransport` in `tests/_bumble_link.py`). This runs in the normal
`test` job (Bumble is pinned in `requirements-dev.txt`). It takes no BlueZ and no radio.
`tests/test_fake_unit_peripheral.py` pins the fake's own contract. **What it does not cover:**
`BluezTransport` itself, because `BumbleTransport` stands in for it here.

### 2. Real BlueZ in a VM (`tests/realstack/`, CI job `pairing-real-stack`)

GitHub's runner kernel has no Bluetooth, so `tests/realstack/vm.sh` boots a guest kernel that does
(virtme-ng under KVM). Inside the VM, `in_vm.sh` loads `hci_vhci`, starts `dbus`, `bluetoothd` and
`btmon`, and runs `rig.py` (test `T_PAIRING_REALSTACK`).

A Bumble virtual controller appears to BlueZ as a new adapter. It is linked to a second Bumble
controller that hosts the fake unit. calictl's **real** `PairingRunner` and `BluezTransport` then
pair with it. The rig checks these paths:

- agent registration, scan, connect, Pair, verify and persist;
- link release at flow end;
- the stale-bond probe and removal;
- an asleep unit keeping its bond;
- `radio_busy`.

On failure it dumps the `bluetoothd` and `btmon` tails. The job is **not a required check** until it
has been reliably green.

To run it locally you need a Linux host with KVM and `sudo`. `vm.sh` is written for a
**throwaway** Ubuntu machine or VM like the GitHub runner. It rewrites a udev rule, `apt-get`
installs `bluez`, `qemu` and a generic kernel, and pip-installs the rig's dependencies for the
system `python3`. Don't run it on a workstation you care about, and don't run `in_vm.sh` directly
on a host: it starts its own system D-Bus and `bluetoothd` and mounts over `/var/lib/bluetooth`.

```sh
bash tests/realstack/vm.sh      # passes when the rig prints "ALL PASS"
```

What CI still can't show is the real unit's radio behaviour: RSSI jitter, its advertising policy
and deep sleep, WiFi coexistence on buspi, and other clients starving LE connects. That is the
open van session (#157). See {doc}`protocol-sequences` (`S_SEQ_PAIRING`) and
{doc}`howto-pair-your-camper`.

### 3. The app lab: the real app against the fake unit

`tools/applab/` runs the vendor's **Android app** in an emulator. Its virtual Bluetooth controller
is bridged to the fake peripheral (`tools/applab/fake_unit_ble.py`, which adds the netsim
transport, a heartbeat link watchdog and address rotation). The app then shows every screen in any
state you inject through the scenario console. Its real control frames land in the fake's log, so
they can be diffed against `control.build()`.

The lab is how the mock learned the app's leave-unchanged defaults and the roof SafetyCounter
stream. It is also how the fake's pairing behaviour was checked against the app on 2026-09-27.
It is local-only tooling: nothing from the APK or the emulator is committed. Setup and usage are in
[`tools/applab/README.md`](https://github.com/ckeller42/open-california/blob/main/tools/applab/README.md).

App **captures** from the real van are a separate path. `tools/capture_diff.py` diffs an HCI
capture against a scenario in `tools/scenarios/<fn>/<case>.yaml` (see the `capture-and-diff`
skill).

## App recordings (`tests/vectors/app/`)

The app lab can **record** what the real app does. `tools/applab/walk.py <scenario>` starts the
fake unit with `FAKE_UNIT_RECORD`, walks the app through the steps in `tools/applab/scenarios.py`,
and writes `tests/vectors/app/<scenario>.jsonl`: a header line naming the app version, then every
connect, read, subscribe, write (the `1003` heartbeat included), notification and disconnect the
unit saw, in calictl's trace schema, merged with the runner's `step` events. `1002` is stored as
`<vin-hash>`; passkeys are never stored.

```sh
~/esp-venv/bin/python tools/applab/walk.py cooler            # on thinky, lab up (tools/applab/README.md)
python3 -m tools.capture_diff tests/vectors/app/cooler.jsonl --recording
python3 -m tools.app_parity tests/vectors/app/session.jsonl  # app / calictl / ESP lifecycle report
```

**Replay** runs in CI without an emulator: `tests/test_app_recordings.py` attributes every
non-neutral write to the step that caused it and requires `control.build` to produce the same
values on the fields the app targets. Known gaps live in `capture_diff.GAPS` and fail once calictl
closes them. **Parity report:** `tools/app_parity.py` prints the app's connection lifecycle (MTU,
subscribe order, heartbeat start and period, reads, disconnects) next to calictl's and the ESP
satellite's. It is a report, not a test: nothing asserts.

**Recorded, not generated.** There is no `--check`: a new APK version means re-recording by hand
on thinky and committing the new files, with the evidence-ledger rows that name them. The header's
`recorded_by` is the only version signal.

Status: `tests/vectors/app/` is **empty** until the first recording session on thinky (it needs the
APK). Until then no evidence-ledger row is APP-RECORDED. The owed scenarios are `airheater-permanent-on`,
`energy-mode`, `lighting-profile` and `lighting-wakeup`, plus `cooler` and `airheater`.

## C codec parity (`csrc/`)

The planned ESP32 satellite (#154) reuses the protocol through a C port generated from the same
dictionary. The C side has the codec plus three decision ports: the freshness stale-latch, the
plausibility anchors and the roof SafetyCounter. Golden vectors are generated by an independent
bit slicer and proven against Python. Then `tests/test_codec_parity.py` (vectors plus a seeded
differential fuzz pass) and `tests/test_ports_parity.py` replay them through the host-compiled
`csrc/codec_cli`. The `codec-parity` job also runs `python -m tools.gen_c_dict --check` and
`python -m tools.gen_codec_vectors --check`.

See {doc}`cross-language-codec` and
[`csrc/README.md`](https://github.com/ckeller42/open-california/blob/main/csrc/README.md) for the
line protocol, the regenerate commands, and what is still planned (the pairing state machine in C,
the postcheck port, the ESP-IDF build).

## Which CI job runs what

| Workflow / job | When | Runs |
|---|---|---|
| `ci.yml` `test` | every PR and push to `main` | full pytest suite on 3.11/3.12/3.13 (unit, mock, Bumble pairing link, vector freshness, and the C parity tests since `gcc` is present; `tests/e2e` skips without Playwright), signal audit, `screens.json` freshness, import-clean |
| `ci.yml` `pre-commit` | every PR and push to `main` | `pre-commit run --all-files`: `ruff` + `ruff format`, markdownlint, gitleaks (plus a working-tree scan), whitespace/YAML checks, the vendor/MAC/VIN guard, import-clean, doc-offset, `screens.json` + codec freshness, web-UI `tsc --checkJs` + `node --check` (the calictl web UI and the ESP32 page script) |
| `ci.yml` `docs` | every PR | the `sphinx -W` site build (both builds) that `docs.yml` deploys from `main` (see {doc}`building-the-docs`) |
| `ci.yml` `gui-e2e` | every PR and push to `main` | `tests/e2e` in Chromium over the mock daemon |
| `ci.yml` `codec-parity` | every PR and push to `main` | C header and vector `--check`, the four codec/ports parity test modules with `gcc` |
| `ci.yml` `pairing-real-stack` | every PR and push to `main` | `tests/realstack/vm.sh` (real BlueZ in a VM); not a required check |
| `ci.yml` `no-vendor-material` | every PR and push to `main` | no APK/decompile/manual/vendor binaries, no real vehicle MAC, no VIN in any tracked file |
| `ci.yml` `install-script` | every PR and push to `main` | `sh -n` + `shellcheck install.sh` |
| `docs.yml` | push to `main` (docs/code paths) only | `sphinx -W` site build + GitHub Pages deploy (see {doc}`building-the-docs`) |
| `screenshots.yml` | push to `main` (web UI / mock paths) only | re-render `docs/screenshots` over the mock and commit them with `[skip ci]` |
