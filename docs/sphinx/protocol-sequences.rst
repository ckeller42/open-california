Protocol sequence diagrams
==========================

The BLE flows ``calictl`` speaks to the VW California camper unit, as sequence diagrams. Each is
a ``sphinx-needs`` **specification** (``S_SEQ_*``) that **links to the requirement it depicts**
(``R_*``, declared in the implementing code's docstring and collected in :doc:`api`), so every
diagram traces to the function that realises it. All are HCI-verified against the real app; the
lighting-apply and roof flows were additionally confirmed on-device (2026-07-13), and every flow
was cross-checked against the **decompiled app** (2026-07-14) — corrections from that pass are
folded in below (arm cadence, the lighting "commit" being a neutral flush not an app mechanism,
water being push-driven, and the ``0x0E`` being calictl's own observation).

Char short-UUIDs: ``1001`` VERSION, ``1003`` HEARTBEAT (liveness/arm counter), ``1004`` AUTH,
plus per-function state/control chars (``1101/1102`` cooler, ``1201`` camping, ``1401/1402``
roof, ``1501`` lighting).

Session foundation — connect, authenticate, subscribe
-----------------------------------------------------

.. spec:: Connect, authenticate, subscribe (session handshake)
   :id: S_SEQ_CONNECT
   :links: R_ACTUATE_ARM

   Every read/write session starts by connecting the **bonded** link, replaying the app's
   handshake (read ``1001`` VERSION + ``1004`` AUTH), and subscribing every notifiable/indicatable
   char. Decompile-confirmed 2026-07-14 (``pf/g`` handshake, ``s/a1`` GATT dispatcher): the order
   is 1001 → 1004 → subscribe-all; **1004 AUTH is a passive read** ("authentication" is implicit
   over the bonded/encrypted link — an empty read triggers a reconnect; there is no token/challenge
   write). The ``1002`` VIN char is **not** read or validated during connect (it's an ordinary
   later state read, not a gate). Implemented by :py:meth:`calictl.device.CamperDevice._session`
   + :py:meth:`calictl.device.CamperDevice._subscribe_all`.

.. mermaid::

    sequenceDiagram
        participant C as calictl (buspi)
        participant U as Camper unit
        Note over C,U: link is BONDED (LE pairing done once; the unit's RPA is resolved via the bond)
        C->>U: connect (retry on the le-connection-abort cascade)
        C->>U: discoverServices + requestMtu
        C->>U: read 1001 (VERSION)
        Note over C: app aborts the session if VERSION empty or > 2
        C->>U: read 1004 (AUTH — passive read over the bonded link; empty → reconnect)
        loop every notifiable / indicatable char
            C->>U: write CCCD (0100 notify / 0200 indicate)
        end
        Note over C,U: session ready — state reads fresh, writes can be armed

Notifications
-------------

.. spec:: Subscribe then receive pushed state notifications
   :id: S_SEQ_NOTIFY

   After subscribing, the unit pushes notifications on the status chars when state changes (the
   app uses these for live updates). calictl subscribes because it is part of the arm handshake,
   but **no-ops the payloads** and reads the state chars directly — so notifications are a
   handshake requirement, not calictl's data path.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Camper unit
        C->>U: write CCCD=0100 on a status char (subscribe)
        U-->>C: notify(status char, payload)  [on state change]
        Note over C: calictl no-ops the payload; it reads state chars directly

Heartbeat-armed control write
-----------------------------

.. spec:: Heartbeat-armed control write
   :id: S_SEQ_ACTUATE
   :links: R_ACTUATE_ARM

   The unit honours a control write only while a **+1 monotonic 4-byte-BE counter ticks on
   ``1003``** — the arm gate (issue #2). Decompile-confirmed 2026-07-14 (``d2/s`` driver, ``ag/b``
   builder): 4-byte BE uint32, ``+1`` per tick. The app ticks it at **~500 ms** for the whole
   connection (the ``+1`` seed value is unconfirmed — ``ag.b.v()`` resets to 0 but the sibling
   roof counter seeds random). calictl ticks it only across the write window (its own choice, not
   an app gate). Arm is the 1003 liveness *only* — no ignition/mode/enable gate (roof is the one
   exception, separately gated). Implemented by :py:meth:`calictl.device.CamperDevice.actuate`.

.. mermaid::

    sequenceDiagram
        participant C as calictl (buspi)
        participant U as Camper unit
        C->>U: connect (bonded link)
        C->>U: read 1001 (VERSION) + read 1004 (AUTH)
        C->>U: subscribe (see S_SEQ_CONNECT)
        loop every ~500 ms (calictl: warm-up ~3 s, then across the write; app: continuous)
            C-)U: write 1003 = N, N+1, N+2 …  (monotonic +1)
        end
        Note over U: armed — actuation writes honoured
        C->>U: write control_char = SET frame (full-packet)
        C->>U: read state_char (verify)
        C->>U: disconnect (heartbeat stops)

Lighting SET, then a flush frame (calictl workaround)
-----------------------------------------------------

.. spec:: Lighting SET then a neutral flush frame
   :id: S_SEQ_LIGHT_COMMIT
   :links: R_LIGHT_COMMIT

   calictl's SET frame is byte-identical to the app's, yet a single calictl SET is ACKed but
   **not applied**; sending a second (neutral) frame right after flushes it and the change applies
   (live-verified: kitchen 0→8). calictl sends ``0e00000000000000eeeeeeeeeeeeeeee`` as that
   follow — ``device.actuate(..., follow=control.LIGHT_COMMIT)``, see
   :py:func:`calictl.control.commit_for`.

   **What the decompile showed (2026-07-14) — important nuance:** ``0e00…`` is **not** an app
   "commit". The Lighting Mode enum (``dg/n``) is ``NO_MODE=0, SET_BRIGHTNESS=4, SET_COLOR=6,
   SET_DOUBLE=8, REQUEST_CONFIG=12, SET_PROFILE=16, WAKEUP_TIME=20, SYSTEM_TIME=24, PREVIEW=28``,
   so **Mode 0 = NO_MODE** (neutral), and ``0e00…`` is just the builder's **default frame**
   (ProfileNumber = the ``14`` "unchanged" sentinel + NO_MODE + all-brightness-unchanged). The
   **app self-applies** each Mode-tagged SET (it stages fields then transmits the full packet with
   the Mode tag); it does *not* send a commit after each SET. Leading theory for why calictl needs
   the follow: the unit applies a frame on the **next** write, and the app streams frames
   continuously (~500 ms) so every SET is naturally flushed — calictl's single write is not, so
   any neutral second frame flushes it. The "profile must be active" precondition is unit-side/UX,
   not in the app's write path.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Lighting (1501)
        Note over C,U: inside one heartbeat-armed session
        C->>U: SET_PROFILE (Mode 16, ProfileNumber=P)
        C->>U: flush (0e00… = NO_MODE neutral default frame)
        Note right of U: profile P active
        C->>U: SET_BRIGHTNESS (Mode 4, zone=N, others=14)
        Note right of U: single calictl SET → ACKed, NOT applied
        C->>U: flush (0e00…) — the unit applies the prior frame on the next write
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

Range validation and the 0x0E link drop
---------------------------------------

.. spec:: Out-of-range writes rejected build-side and by the unit (0x0E)
   :id: S_SEQ_REJECT

   calictl validates every field against its width + curated ``valid`` set **before** writing
   (:py:func:`calictl.protocol.check_value`). The unit-side ``0x0E`` behaviour below is **calictl's
   own on-device observation** (cooler ``State=3``, lighting bad ``Mode`` → ATT ``0x0E`` + link
   drop), not something confirmed in the app code — the app's own state fields carry the same
   range bounds (``sg.a(default, max)`` wrappers) so it never emits out-of-range in the first
   place. The build-side guard is what keeps a bad frame from ever reaching the vehicle.

.. mermaid::

    sequenceDiagram
        participant B as calictl (build)
        participant C as calictl (BLE)
        participant U as Camper unit
        B->>B: encode + check_value(field, width, valid-set)
        alt value out of range
            B--xC: raise (frame NEVER written)
        else in range
            B->>C: frame
            C->>U: write control_char
            alt unit rejects (out of range)
                U--xC: ATT 0x0E + link drop
            else accepted
                U-->>C: ACK
            end
        end

Fresh state read under heartbeat
--------------------------------

.. spec:: Read under a live heartbeat for fresh values
   :id: S_SEQ_READ
   :links: R_READ_HEARTBEAT_REFRESH

   Ticking ``1003`` during a read keeps the link up (a bare read otherwise drops it after ~15 s)
   and refreshes the chars the app re-reads (``1102`` cooler, ``1602`` energy, ``1902``, ``1004``).
   Implemented by :py:meth:`calictl.device.CamperDevice.read_all`.

   **Water is different — push-driven, and measurement-gated (decompile + on-device, 2026-07-14).**
   The app never gets fresh water from a heartbeat-refreshed *read*; it gets it from a **``1302``
   notification** and parses the pushed frame (VM ``qg/b``). **Fixed:** ``read_all``/``read`` now
   subscribe with a real handler (:meth:`~calictl.device.CamperDevice._subscribe_all` ``sink``) and
   **prefer a pushed value over the bare read**, so calictl captures the true level whenever the
   unit pushes it (during water use). While **parked** the unit pushes nothing (0 ``1302`` frames
   in 40 s on-device) — the level is only measured on pump activity, so no fresh value is fetchable
   while idle and it falls back to the last read; that residual staleness is a hardware limit, not
   a calictl one.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Camper unit
        C->>U: connect + subscribe-all (real handler → sink)
        loop ~500 ms (warm-up ~2 s, then read)
            C-)U: write 1003 heartbeat
        end
        U-->>C: notify 1302 → water (push-driven; only fires on pump activity)
        Note over U: heartbeat keeps link up + refreshes re-read chars (1102/1602/1902/1004)
        C->>U: read state_char (prefer a pushed value over the bare read)
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
