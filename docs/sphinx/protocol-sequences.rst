Protocol sequence diagrams
==========================

The BLE flows ``calictl`` speaks to the VW California camper unit, as sequence diagrams. Each is
a ``sphinx-needs`` **specification** (``S_SEQ_*``) that **links to the requirement it depicts**
(``R_*``, declared in the implementing code's docstring and collected in :doc:`api`), so every
diagram traces to the function that realises it. All are HCI-verified against the real app; the
lighting-apply and roof flows were additionally confirmed on-device on 2026-07-13.

Char short-UUIDs: ``1001`` VERSION, ``1003`` HEARTBEAT (liveness/arm counter), ``1004`` AUTH,
plus per-function state/control chars (``1101/1102`` cooler, ``1201`` camping, ``1401/1402``
roof, ``1501`` lighting).

Heartbeat-armed control write
-----------------------------

.. spec:: Heartbeat-armed control write
   :id: S_SEQ_ACTUATE
   :links: R_ACTUATE_ARM

   The unit honours a control write only while a +1 4-byte-BE counter ticks on ``1003``
   (~0.6 s) — the arm gate (issue #2). Implemented by :py:meth:`calictl.device.CamperDevice.actuate`.

.. mermaid::

    sequenceDiagram
        participant C as calictl (buspi)
        participant U as Camper unit
        C->>U: connect (bonded link)
        C->>U: read 1001 (VERSION)
        C->>U: read 1004 (AUTH)
        C->>U: subscribe CCCD=0100 on notifiable chars
        loop every ~0.6 s (warm-up ~3 s)
            C-)U: write 1003 = N, N+1, N+2 …
        end
        Note over U: armed — actuation writes honoured
        C->>U: write control_char = SET frame (full-packet)
        C->>U: read state_char (verify)
        C->>U: disconnect (heartbeat stops)

Lighting SET + COMMIT (apply gate)
----------------------------------

.. spec:: Lighting SET then COMMIT
   :id: S_SEQ_LIGHT_COMMIT
   :links: R_LIGHT_COMMIT

   calictl's SET frame is byte-identical to the app's; the unit **applies** it only after the
   commit frame ``0e00…`` (Mode 0). Sent via ``device.actuate(..., follow=control.LIGHT_COMMIT)``
   — see :py:func:`calictl.control.commit_for`. A profile must be active first.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Lighting (1501)
        Note over C,U: inside one heartbeat-armed session
        C->>U: SET_PROFILE (Mode 16, ProfileNumber=P)
        C->>U: COMMIT (0e00… Mode 0)
        Note right of U: profile P active
        C->>U: SET_BRIGHTNESS (Mode 4, zone=N, others=14)
        Note right of U: staged — ACKed, NOT applied
        C->>U: COMMIT (0e00… Mode 0)
        Note right of U: change APPLIED
        C->>U: read 1502 → BrightnessL<zone> == N

Roof actuation (press-and-hold move stream, unit self-gated by a 3 s SafetyCounter)
-----------------------------------------------------------------------------------

.. spec:: Roof press-and-hold move stream, unit self-gated ~3 s by the SafetyCounter
   :id: S_SEQ_ROOF
   :links: R_ROOF_ACTUATE

   Requires ignition ON. Settled 2026-07-13 from the **decompiled roof class** (authoritative),
   reconciled against an at-the-van capture (char ``1401`` = ATT handle ``0x0037``; 5-byte frame
   ``[direction][4-byte BE SafetyCounter]``; direction open ``0x01`` / close ``0x04`` / stop
   ``0x00``). Roof movement is **press-and-hold**: the app streams move frames while the button is
   held and stops (``0x00`` / frames cease) on release — there is **no protocol confirmation phase
   and no app-streamed STOP phase**. The **SafetyCounter is APP-GENERATED**, a monotonic BE-uint32
   ``seed + elapsed_ms/500`` (random seed) ⇒ **≈ +1 every 500 ms**; it is **not** echoed from the
   unit. The unit only checks liveness/monotonicity and reports ``SafetyCounterValid`` (``1402``
   bit 7). The safety wait is **unit-enforced ~3 s** (the vehicle withholds motor actuation until
   the counter validates); the app arms a **3000 ms** dead-man that aborts if still invalid at 3 s
   (dialog ``dialog_info_popUpRoof_safetyCheck``). The perceived ~4 s ≈ 3 s + BLE latency — there
   is **no ~4 s app-countdown constant**. Implemented by
   :py:meth:`calictl.device.CamperDevice.actuate_roof`, which does **not** yet generate a live
   monotonic counter, account for the 3 s self-gate, or use the ~500 ms cadence — roof actuation
   stays NOT-LIVE-VERIFIED until it does.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Roof (1401 / state 1402)
        Note over C,U: ignition ON, roof path clear
        Note over C,U: user presses & HOLDS open/close
        loop press-and-hold, move frames @ ~500 ms
            C->>U: move frame [0x01 open / 0x04 close] + app-generated monotonic SafetyCounter (+1/500 ms)
        end
        Note right of U: unit validates SafetyCounter (~3 s); motor withheld until valid → 1402 bit 7
        Note over C,U: after ~3 s pop-top travels while frames continue
        C->>U: STOP frame [0x00] on release / end (or frames cease → dead-man halt)
        Note right of U: halts

Fresh state read under heartbeat
--------------------------------

.. spec:: Read under a live heartbeat for fresh values
   :id: S_SEQ_READ
   :links: R_READ_HEARTBEAT_REFRESH

   A bare read returns a stale latch (fresh-water 1 L vs true 11 L) and the link drops after
   ~15 s. Ticking ``1003`` during the read drives sensor measurement + keeps the link up.
   Implemented by :py:meth:`calictl.device.CamperDevice.read_all`.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Camper unit
        C->>U: connect + subscribe
        loop ~0.6 s (warm-up ~2 s, then read)
            C-)U: write 1003 heartbeat
        end
        Note over U: heartbeat drives measurement + keeps link up
        C->>U: read state_char → FRESH value
        C->>U: disconnect

Reachability / deep-sleep
-------------------------

.. spec:: Parked deep-sleep makes access intermittent
   :id: S_SEQ_SLEEP

   Parked + idle, the unit BLE-deep-sleeps and stops advertising; only physical use wakes it, so
   ``serve`` serves last-known state behind an "offline" banner. Once awake with buspi holding the
   session, the link is stable (180 s spike: 100 % uptime, 0 drops).

.. mermaid::

    sequenceDiagram
        participant C as buspi
        participant U as Camper unit
        Note over U: parked + idle → BLE deep-sleep (no advertising)
        C--xU: connect → BleakDeviceNotFoundError
        Note over C: serve serves last-known state + offline banner
        Note over U: door / ignition → wakes, advertises
        U-->>C: advertising resumes
        C->>U: connect OK → poll resumes (fresh reads)
