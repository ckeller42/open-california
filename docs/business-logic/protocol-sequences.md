# Protocol sequence diagrams

> This page renders on GitHub. The **canonical, implementation-traced** version is the Sphinx doc
> `docs/sphinx/protocol-sequences.rst` — there each diagram is a `sphinx-needs` `spec` linked to
> the requirement (`R_*`) in the implementing code's docstring (build: `docs/sphinx/README.md`).

The BLE protocol flows calictl speaks to the VW California camper unit, as sequence diagrams.
All are **HCI-verified** against the real CaliforniaOnTour app (`idevicebtlogger` captures) and,
where noted, confirmed on-device via readback. Char short-UUIDs: `1001` VERSION, `1003` HEARTBEAT
(the liveness/arm counter), `1004` AUTH, plus per-function state/control chars
(`1101/1102` cooler, `1201` camping, `1401/1402` roof, `1501` lighting, …).

Implementation: `calictl/device.py` (`actuate`, `actuate_roof`, `read_all`, `_heartbeat`),
`calictl/control.py` (frame builders + `LIGHT_COMMIT`).

## 1. Heartbeat-armed control write (`device.actuate`)

The unit only honours a control write while a **+1 4-byte-BE counter ticks on `1003`** (~0.6 s
cadence) — the liveness/arm gate (issue #2). The write path replays the app's connect handshake,
starts the heartbeat, writes, reads back, then tears down.

```mermaid
sequenceDiagram
    participant C as calictl (buspi)
    participant U as Camper unit
    C->>U: connect (bonded link)
    C->>U: read 1001 (VERSION)
    C->>U: read 1004 (AUTH)
    C->>U: subscribe CCCD=0100 on every notifiable char
    C-)U: write 1003 = N   (heartbeat start)
    loop every ~0.6 s (ARM_DELAY_S warm-up ≈ 3 s)
        C-)U: write 1003 = N+1, N+2, …
    end
    Note over U: armed — actuation writes now honoured
    C->>U: write <control_char> = SET frame (full-packet)
    C->>U: read <state_char>   (verify, after SETTLE_S)
    C->>U: disconnect          (heartbeat stops)
```

## 2. Lighting SET + COMMIT — the apply gate (cracked 2026-07-13)

calictl's SET frame is **byte-identical to the app's**; the missing piece was a trailing
**commit frame** `0e00000000000000eeeeeeeeeeeeeeee` (Mode 0, profile + all zones = the `14`
"unchanged" sentinel). The unit stages the SET and only **applies** it once the commit lands.
A profile must be active first (itself SET_PROFILE + commit). Sent as `actuate(..., follow=LIGHT_COMMIT)`.

```mermaid
sequenceDiagram
    participant C as calictl
    participant U as Lighting (1501)
    Note over C,U: (all inside one heartbeat-armed session)
    C->>U: SET_PROFILE  (Mode 16, ProfileNumber=P)
    C->>U: COMMIT       (0e00… Mode 0)
    Note right of U: profile P now active
    C->>U: SET_BRIGHTNESS (Mode 4, zone=N, others=14)
    Note right of U: staged — ACKed, NOT yet applied
    C->>U: COMMIT       (0e00… Mode 0)
    Note right of U: change APPLIED ✅
    C->>U: read 1502 → BrightnessL<zone> == N
```

Without the commit the SET is ACKed but never applied — the "lighting looks broken" gap that
stood the whole project. `control.commit_for("lighting")` returns the commit; other functions
return `None`.

## 3. Roof move-heartbeat (`device.actuate_roof`)

Verified 2026-07-13. **Requires ignition (terminal-15) ON.** The app drives the pop-top with a
*repeated* move frame — `[direction byte][4-byte SafetyCounter]` — and a final STOP.
Direction bytes: **open `0x01`** (Up), **close `0x04`** (Down), **stop `0x00`**. The unit halts
when the move frames cease (dead-man), so STOP is sent unconditionally at the end.

```mermaid
sequenceDiagram
    participant C as calictl
    participant U as Roof (1401)
    Note over C,U: ignition ON, armed session, roof path clear
    loop ~1 Hz until deadline or STOP
        C->>U: move frame  [0x01 open / 0x04 close] + SafetyCounter
        Note right of U: pop-top travels
    end
    C->>U: STOP frame  [0x00] + SafetyCounter
    Note right of U: halts (also halts if the move heartbeat stops)
```

> ⚠️ **Open gap:** in the capture the SafetyCounter tracked the **unit's own running counter**
> (echoed, ~0.8 increments/frame — not a pure app `+1`). `actuate_roof` currently injects its own
> `+1` from a fixed start; to drive the roof for real, read + echo the unit's live SafetyCounter.

## 4. Fresh state read under heartbeat (`device.read`/`read_all`)

A bare read returns a **stale latched** value (fresh-water 1 L vs the true 11 L) and the unit
drops the link after ~15 s. Ticking `1003` during the read **drives the sensor measurement** and
keeps the link alive, so the value comes back fresh (verified 2026-07-09).

```mermaid
sequenceDiagram
    participant C as calictl
    participant U as Camper unit
    C->>U: connect + subscribe
    loop ~0.6 s (HEARTBEAT_WARMUP_S ≈ 2 s, then read)
        C-)U: write 1003 heartbeat
    end
    Note over U: heartbeat drives measurement + keeps link up
    C->>U: read <state_char> → FRESH value
    C->>U: disconnect
```

## 5. Reachability / deep-sleep (why access is intermittent)

Parked + idle, the unit **BLE-deep-sleeps and stops advertising** — buspi (bonded, in the van)
can't even connect. Only physical use wakes it. (A one-off root cause on 2026-07-13: Bluetooth
had been *disabled on the unit itself*, so it was unreachable for days despite the van being used.)

```mermaid
sequenceDiagram
    participant C as buspi
    participant U as Camper unit
    Note over U: parked + idle → BLE deep-sleep (no advertising)
    C--xU: connect → BleakDeviceNotFoundError
    Note over C: serve serves last-known state + "offline" banner
    Note over U: door / ignition → wakes, advertises again
    U-->>C: advertising resumes
    C->>U: connect OK → poll resumes (fresh reads)
```

Once awake with buspi holding the session (heartbeat ticking), the link is **stable** — a 180 s
spike held 100 % uptime, 0 drops (2026-07-13), and locking the van did not drop it. Intermittent
access is purely the deep-sleep ceiling, not a flaky link.
