# Guided BLE pairing — the state-machine contract (#154, #157)

**Status:** SM + BlueZ transport + web wizard shipped; unit-tested against a fake transport and
CI-verified against real BlueZ (`pairing-real-stack`, a VM + the Bumble fake unit — see
[NOT-LIVE-VERIFIED](#not-live-verified)). **NOT-LIVE-VERIFIED against a real camper unit** — the
deliberate live unbond→re-pair at the van is tracked as a checkbox on
[#157](https://github.com/ckeller42/open-california/issues/157), not done yet.

## Purpose

The buspi web GUI needed a CaliforniaOnTour-style guided Bluetooth setup: first-run pairing
*and* a `bluetoothReset`-style re-pair, driven by the user typing the 6-digit passkey the
camper unit displays on its own "Gerät verbinden" screen. Rather than wire that straight into
`pairing_bluez.py`, the flow is factored as a **platform-free state machine**
(`calictl/pairing.py`, `R_PAIRING_SM`) — no strings, no clock, no BLE addresses — so it is
simultaneously: (a) the executable spec for the web wizard today, and (b) the explicit model
for the ESP32 satellite's touchscreen pairing flow (#154), which will port `step()` to C almost
mechanically. The sequence vectors in `tests/vectors/pairing.json` are written so `#154` can
replay them against the C port as a golden-vector differential test, the same pattern already
used for the frame codec (issue #156).

Full design rationale: `docs/superpowers/specs/2026-08-31-guided-pairing-design.md`.

## The state machine

9 states, 14 events, 9 actions — all pinned integer enums (never renumber; the C port and the
vectors depend on the values). `step(state, event, arg) -> (new_state, [(action, arg), ...])` is
pure: unknown `(state, event)` pairs are no-ops, so a late or duplicate transport event can never
derail the flow.

```mermaid
stateDiagram-v2
  [*] --> IDLE
  IDLE --> SCANNING: EV_START
  ERROR --> SCANNING: EV_START (retry)
  SCANNING --> CONNECTING: EV_DEVICE_FOUND
  CONNECTING --> PAIRING: EV_CONNECTED
  CONNECTING --> SCANNING: EV_CONNECT_FAIL (attempts remain)
  CONNECTING --> ERROR: EV_CONNECT_FAIL (attempts exhausted)
  PAIRING --> WAITING_PASSKEY: EV_PASSKEY_REQUESTED
  WAITING_PASSKEY --> PAIRING: EV_PASSKEY_ENTERED
  PAIRING --> VERIFYING: EV_PAIR_OK
  PAIRING --> SCANNING: EV_PAIR_FAIL (attempts remain)
  PAIRING --> ERROR: EV_PAIR_FAIL (attempts exhausted)
  VERIFYING --> BONDED: EV_VERIFY_OK
  VERIFYING --> ERROR: EV_VERIFY_FAIL
  BONDED --> RESETTING: EV_RESET
  ERROR --> RESETTING: EV_RESET
  IDLE --> RESETTING: EV_RESET
  RESETTING --> IDLE: EV_RESET_DONE
  note right of PAIRING
    EV_PAIR_FAIL retries up to 3 attempts, then moves to ERROR
  end note
  note right of CONNECTING
    EV_CONNECT_FAIL retries up to 3 attempts, then moves to ERROR
  end note
```

Not drawn (to keep the diagram legible — see `calictl/pairing.py` for the exact guards):
`EV_TIMEOUT` fires from any state with an armed timer straight to `ERROR(ERR_TIMEOUT)`, and
`EV_CANCEL` fires from any active state straight to `IDLE` — both run the same connection
cleanup (`ACT_STOP_SCAN` from `SCANNING`, `ACT_DISCONNECT` from `CONNECTING`/`WAITING_PASSKEY`/
`PAIRING`/`VERIFYING`, nothing from terminal states).

### Timeout table

One source of truth for both platforms; the transport arms **one timer per state change** and
injects `EV_TIMEOUT` — absent from the table means no timer for that state.

| State | Timeout |
|---|---|
| `SCANNING` | 30 s |
| `CONNECTING` | 20 s — the BlueZ transport splits it: probe an existing bond ≤ 5 s, drop a stale one + re-discover ≤ 5 s, final connect ≥ 8 s, 1 s margin |
| `WAITING_PASSKEY` | 60 s |
| `PAIRING` | 15 s |
| `VERIFYING` | 10 s |
| `RESETTING` | 10 s |
| `IDLE`, `BONDED`, `ERROR` | none |

## ESP mapping (buspi today, #154's NimBLE port later)

Agent registration / `io_cap=KEYBOARD_ONLY` (BlueZ) or the NimBLE `ble_sm_io` setup are
**transport init, not an SM action** — they happen once, outside `step()`, before any event is
injected.

| SM symbol | BlueZ (buspi, `pairing_bluez.py`) | ESP32 (NimBLE + NVS, #154) |
|---|---|---|
| `EV_DEVICE_FOUND` | bleak scan callback, name == `VWCAMPER` | `BLE_GAP_EVENT_DISC` with a matching name in the adv payload |
| `EV_CONNECT_FAIL` | `BleakClient.connect()` raises (e.g. HCI 0x3e, le-connection-abort-by-local) | `BLE_GAP_EVENT_CONNECT` with nonzero status |
| `EV_PASSKEY_REQUESTED` | `org.bluez.Agent1.RequestPasskey` D-Bus call (agent capability `KeyboardOnly`) | `BLE_GAP_EVENT_PASSKEY_ACTION` with `action == BLE_SM_IOACT_INPUT` |
| `ACT_SEND_PASSKEY` | resolve the `asyncio.Future` the agent's `RequestPasskey` is blocked on | `ble_sm_inject_io()` with the typed passkey |
| `EV_PAIR_OK` / `EV_PAIR_FAIL` | `Device1.Pair()` D-Bus call result | `BLE_GAP_EVENT_ENC_CHANGE` status (0 = OK, nonzero = fail) |
| `ACT_VERIFY` | encrypted read of `VERSION_CHAR` + `AUTH_CHAR`, then count all readable state chars | encrypted read of the equivalent auth characteristic (no full-service enumeration needed on the ESP side) |
| `ACT_PERSIST_BOND` | BlueZ auto-persists the bond; we additionally cache the identity address in `~/.local/state/calictl/pairing.json` | NimBLE NVS bond store (`ble_store_util_*`) |
| `ACT_REMOVE_BOND` | `Adapter1.RemoveDevice()` D-Bus call | `ble_store_util_delete_peer()` |

## Verify-policy caveat

`EV_VERIFY_OK` carries `arg = readable-char count` — but the SM treats that count as an opaque
number; it does not know or care what "enough" means. Classifying a readable-char count into
`EV_VERIFY_OK`/`EV_VERIFY_FAIL` is **buspi transport policy** decided inside
`pairing_bluez.py::verify()`, not SM data. The **actual shipped policy**: `verify()` reads
`VERSION_CHAR` + `AUTH_CHAR` (must both succeed) and then attempts every other readable-flagged
characteristic, counting successes; it returns `None` (→ `EV_VERIFY_FAIL`) if the version/auth
reads fail **or** the readable-count comes back `0` (an unbonded RPA link can ACK the connection
and even the version/auth reads yet drop every real state-char read — a bare positive-vs-`None`
check without the zero-count guard would misclassify that as bonded). A positive count returns
as `EV_VERIFY_OK`. The full **20/20-readable-characteristics** signature (all state chars
readable, the strongest bonded/unbonded discriminator seen so far) is a stronger version of this
same check that the #157 van session will confirm live; today's shipped threshold is only
`count > 0`, not `count == 20`. The ESP equivalent almost certainly won't enumerate a whole GATT
table — it would do a single encrypted read of one known auth characteristic and treat
success/failure as `EV_VERIFY_OK`/`EV_VERIFY_FAIL` directly. Port the *shape* (an encrypted read
after `EV_PAIR_OK` that fails closed on any read problem), not the specific count threshold.

## Environment the wizard needs

Guided pairing is BLE-radio-sensitive in ways that are easy to get wrong on a Pi doing several
Bluetooth jobs at once. The how-to for owners is
[How to pair your camper](../howto-pair-your-camper.md); this is the mechanism behind its
"Before you start" checklist.

- **One radio.** `hci0` on buspi is shared with other BLE clients (the Home Assistant Bluetooth
  integration, BLE readers, calictl's own persistent session). Only one of them can hold the
  adapter's `Discovering` state or a connection at a time.
- **Co-resident scanners break new connections.** `BluezTransport.stop_scan` reads
  `Adapter1.Discovering` right after its own scan stops; if another client kept discovery
  running, `radio_busy` is set and surfaced to the wizard (the banner text in
  `calictl/webui/app.js`). A **btmon capture on buspi (2026-09-25)** caught the mechanism
  directly: `LE Connection Complete`, then BlueZ re-enabled active scanning at **100 % duty**
  (scan window == scan interval, 11.25 ms) because another client still held discovery, and
  ~300 ms later the connection failed to establish with **HCI error 0x3e**. With every other
  scanner stopped, the same connect attempt succeeded. The owner's phone holding the unit's
  single connection blocks a Pi connect the same way, for a different reason (the unit itself
  refuses a second link, not the local radio).
- **One connection slot on the unit.** The camper control unit accepts exactly one BLE central
  at a time; a phone connected via the CaliforniaOnTour app locks the Pi out regardless of the
  local radio's state.
- **A rotating advertising address.** The unit advertises from a private address that changes
  periodically (observed 2026-09-25: **four different addresses in about 45 minutes** of
  continuous scanning). The wizard finds it by name (`VWCAMPER`), not by address; only once
  bonded does the unit's identity address become available, and that is what
  `persist_bond()` stores. Seeing the scanned address change between attempts is expected.
- **`Passcode: ---` until a pairing request arrives.** The unit's own "Gerät verbinden" screen
  shows the literal placeholder `---` where the 6-digit passcode goes, and only replaces it once
  a central actually starts a pairing request against it (owner photo, 2026-09-25). A wizard
  stuck in `waiting_passkey` with the unit still showing `---` means the pairing request never
  reached the unit — recheck the checklist, not the typed digits.
- **The unpaired-daemon guard.** Before any bond exists, `CamperDevice._session()` refuses to
  even construct a `BleakClient` for the placeholder address (`device.UNPAIRED_ADDR`) — an
  unpaired daemon does not poll or scan at all. This matters here because a daemon that *did*
  scan on every failed poll would itself be one more client competing for discovery, working
  against the very radio the wizard needs quiet. `_meta.paired` reports this state to the UI.

## Exclusion model (single BLE owner rule, CLAUDE.md)

A guided-pairing flow can run for minutes — the user has to walk to the camper panel and read
off a passkey — so it deliberately does **not** hold `serve.py`'s `self._ble` lock for the whole
flow; doing so would freeze `/api/state` (and every other read) for that entire window. Instead:

- `pairing_command("start")` parks the persistent-session supervisor
  (`set_mode("disconnect")`) *before* arming the runner, so the supervisor will not open its own
  connection while the wizard owns the radio.
- `poll()` checks `pairing.snapshot()["state"]` and skips the **whole** read cycle (not just
  parts of it) whenever a pairing flow is active (state not in `idle`/`bonded`/`error`) — a
  second bleak `connect()` against `hci0` mid-pairing is exactly what the single-BLE-owner rule
  (`serve` is the only reader — CLAUDE.md) forbids.
- `pairing_bluez.py` never touches `serve.py`'s `_ble` lock at all — the pairing transport opens
  its own `bleak.BleakScanner`/`BleakClient` directly. The `_ble` lock is **never taken** by the
  pairing flow; exclusion is entirely the supervisor-park + poll-skip pair above, not lock
  sharing.

## NOT-LIVE-VERIFIED

Two tiers below the real van: `tests/test_pairing_sm.py` replays the pure SM against
`tests/vectors/pairing.json` (no transport at all), and `tests/test_pairing_link.py` drives the
runner + `BluezTransport` against a fake transport / a Bumble `LocalLink` fake unit (no real
dbus/bleak/BlueZ). The `pairing-real-stack` CI job (`tests/realstack/`, non-required check) goes
one tier further: inside a virtme-ng VM, the real dbus/bleak/BlueZ stack — agent registration,
scan, connect, `Device1.Pair()`, verify, `persist_bond`, the stale-bond probe/removal, and
`radio_busy` — runs against the Bumble fake unit over a virtual controller. That still is **not**
a real camper unit: it doesn't exercise RSSI jitter, the unit's own advertising/deep-sleep
policy, WiFi coexistence on buspi's shared radio, or another client scanning at full duty and
starving a real LE connect (HCI 0x3e) — the mechanism the 2026-09-25 btmon capture caught (see
[Environment the wizard needs](#environment-the-wizard-needs)) was observed on real buspi
hardware, but not yet as part of a full pairing run. **No real unbond→re-pair has been run
against the van yet** — that is a deliberate, human-in-the-loop step (typing a passkey off the
camper's own screen) tracked as a checkbox on
[#157](https://github.com/ckeller42/open-california/issues/157). Until that checkbox is
checked, treat the BlueZ transport's behaviour against a **real camper unit** (as opposed to the
VM's fake one) as **DECOMPILE/mock-tier**, not device-verified — same evidence-tier convention as
`evidence-ledger.md`. The [how-to](../howto-pair-your-camper.md) is written from the code and the
real-buspi radio evidence above, not from a completed live pairing.
