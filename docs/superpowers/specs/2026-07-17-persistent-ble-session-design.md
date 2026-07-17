# Persistent armed BLE session — design

**Status:** approved 2026-07-17 (brainstorm). Not yet implemented.
**Goal:** cut web-UI button→actuator latency from ~6–12 s to **< 1 s** while the van is awake,
by holding one connected BLE session with the `1003` heartbeat ticking continuously — the way the
phone app does — instead of connecting fresh for every action.

## Problem

Every daemon BLE action opens a fresh session and disconnects afterward: connect → subscribe every
notifiable char → start the `1003` heartbeat → wait `ARM_DELAY_S` (3 s) for the unit to register
liveness → write → disconnect. The physical actuator fires only after that ~6–12 s chain. The phone
app feels instant because it keeps one connection open with the heartbeat always ticking, so a tap
writes into an already-armed link.

**Key enabling fact (owner-confirmed 2026-07-17):** the van accepts buspi **and** the phone app
connected simultaneously. So a persistent buspi session costs the phone nothing — the main objection
to holding the link is gone.

## Non-goals

- Not changing the CLI. `calictl set`/`status` are inherently one-shot (connect, do one thing, exit)
  and keep the connect-per-op path.
- Not waking a parked van. When the unit deep-sleeps it stops advertising; nothing buspi does can
  reach it. The session simply stays degraded until physical use wakes it.
- No zoom/telemetry/analytics scope creep. This is latency + a read-only status indicator.

## Architecture

Reuse the proven "operate on a connected client" core; add a supervisor that keeps one client alive.

### device.py — split "connect" from "operate"

- Extract the write core to `_actuate_on(client, func, frame, *, follow=None, arm=True, verify=True)`,
  mirroring the existing `_read_all_on(client, funcs)`. `arm=True` keeps the 3 s `ARM_DELAY`
  (cold session); `arm=False` skips it (the background heartbeat already armed the unit).
- `read_all()` / `actuate()` keep wrapping the `_on` cores with `_session()` + `_safe_disconnect()`.
  **CLI uses these, unchanged** — the highest-risk change can't regress manual on-device verification.

### device.PersistentSession — owns the single client

- `start()`: connect → handshake (VERSION/AUTH reads, `_subscribe_all`) → launch the continuous
  `_heartbeat` task. One-time ~5 s warmup per wake, not per tap.
- `read_all()` → `_read_all_on(client, funcs)`; `actuate(...)` → `_actuate_on(client, ..., arm=False)`.
- `is_up`: driven by bleak's `disconnected_callback` + `client.is_connected`.
- `aclose()`: stop the heartbeat, disconnect.
- The heartbeat counter increments forever from `HEARTBEAT_START` — monotonic within the session,
  which is all the arm-gate needs.

### serve.Server — supervisor + routing

- A background **supervisor task** keeps the session up (state machine below) alongside the poll loop.
- `poll()` calls `session.read_all()` (live link, no reconnect); `on_command()` calls
  `session.actuate(...)` (no connect, no subscribe, no arm) — the < 1 s path.
- Both run under the existing `_ble` lock (see Concurrency).
- Gated behind `CALICTL_PERSISTENT_SESSION` (default **on** for the daemon; `0` → fall back to the
  proven connect-per-op model with no redeploy).

## Concurrency & lock model

`read_all` **already** runs the heartbeat concurrently with reads on one client (proven at 100%
uptime in the 2026-07-13 spike), so BlueZ serializes GATT ops under the hood. This design relies on
that same tested behavior rather than introducing new locking.

- **Heartbeat**: a free-running background task, ticking `1003` every `HEARTBEAT_PERIOD_S` (0.6 s)
  for the life of the connection.
- **`_ble` asyncio lock** changes meaning: it no longer wraps connect-op-disconnect; it serializes a
  **poll read-batch vs. a command write** so their multi-step sequences don't interleave. The
  heartbeat is outside it.
- A command waits at most for an in-flight poll batch — now a fast read over a live link, not a
  connect+subscribe. Worst-case command wait drops from ~10 s to ~1–2 s.

## Session state machine

| State | Meaning | Behavior |
|---|---|---|
| **connecting** | establishing | one-time handshake + heartbeat start (~5 s) |
| **up** | connected, heartbeat ticking | poll & command run instantly over it |
| **degraded** | link dropped / recent connect failures | exponential-backoff reconnect: 5 → 10 → 30 → 60 s (cap 60) |
| **asleep** | backoff has reached the 60 s cap after ≥4 consecutive failed attempts | keep retrying at the cap; distinct only as a UI label — the reconnect behavior is identical to `degraded` |

`degraded` and `asleep` are the **same reconnect loop**; `asleep` is purely a UI distinction so a
parked van reads "Asleep" rather than a perpetual "Reconnecting". The boundary (backoff at cap after
≥4 fails ≈ ~2 min of failure) is a display threshold only, not a behavior change.

- **Command while `up`** → write immediately, **< 1 s** (no arm).
- **Command while not `up`** → one immediate connect attempt; if it fails fast, return
  **"van unreachable"** rather than block the web thread. Deep-sleep can't be woken by us anyway.
  `PersistentSession.connect()` is single-flight (an internal lock), so a command-triggered connect
  and the supervisor's backoff attempt never race — whichever starts first wins and the other awaits
  its result.
- `_meta.online` becomes `session.is_up` — a fact, not an inference from poll recency, so the offline
  banner stops lying during the gap between polls.

## GUI status indicator (read-only)

A small header pill, driven by a new `_meta.session` field — **displayed, never toggled** from the
UI (same precedent as the write-enable lock: the unauthenticated trusted-LAN UI must not change
daemon-wide infra state).

| `_meta.session` | Pill | Meaning |
|---|---|---|
| `up` | 🟢 Live · fast | persistent session up, commands instant |
| `degraded` | 🟡 Reconnecting | link dropped, backing off |
| `asleep` | ⚪ Asleep | van parked / unreachable |
| `off` | (no pill) | `CALICTL_PERSISTENT_SESSION=0`, per-op mode |

Mode still changes only via `CALICTL_PERSISTENT_SESSION` on the daemon.

## Error handling & operations

- **Shared `hci0`**: a held connection + light heartbeat coexists with the Anker/Victron/Govee
  readers (BLE central supports concurrent peripherals). `CALICTL_ADAPTER_RESET` stays opt-in **off**
  so a reconnect never disrupts the other readers.
- **Rollback**: `CALICTL_PERSISTENT_SESSION=0` instantly reverts to connect-per-op, no redeploy. This
  is the escape hatch if a 24/7 held link destabilizes the shared adapter over days (the spike only
  proved 180 s — long-run stability is the main real-world unknown).
- **Backoff** prevents spinning the shared adapter while the van is parked.
- Telemetry (Influx sink) and MQTT paths are unaffected — they consume `poll()` output as today.

## Testing

- **Mock** (`tools/mock_unit.py`): extend `MockCamperUnit`/`MockBleakClient` to hold a persistent
  connection, accept the continuous heartbeat, honor writes while it ticks, and simulate a **drop**
  (deep-sleep) to exercise backoff/reconnect.
- **Unit**: a live-session command issues **no connect and no `ARM_DELAY`** (assert the fast path);
  reconnect-with-backoff after a drop; command-while-down returns unreachable; poll reads over the
  live client; `CALICTL_PERSISTENT_SESSION=0` uses the old path unchanged.
- **e2e**: assert command latency against the mock daemon is now sub-second — a regression guard for
  the entire point of this work; plus the session pill renders `up` and no red-flag text.
- Preserve the **stdlib-only import rule** (bleak imported lazily inside functions); CLI tests
  unchanged.
- Sphinx-needs: `R_PERSISTENT_SESSION` ← `T_PERSISTENT_SESSION`.

## Success criteria

Button→actuator **< 1 s** while `up`; graceful backoff when parked; phone app unaffected; reads stay
continuously fresh (retires the read-staleness workarounds while awake); CLI unchanged; full suite +
mock + e2e green; instant rollback via the env flag.
