Protocol sequence diagrams
==========================

The BLE flows ``calictl`` speaks to the VW California camper unit, as sequence diagrams. This page
is the **single canonical copy**: the lab-notes page ``docs/business-logic/protocol-sequences.md``
only points here.

Each flow is a ``sphinx-needs`` **specification** (``S_SEQ_*``) that **links to the requirement it
depicts** (``R_*``, declared in the implementing code's docstring and collected in :doc:`api`), so
every diagram traces to the function that realises it. Every section has the same three parts:

* **Contract** — what ``calictl`` does *today*, normative and short (the spec body).
* **Diagram** — the wire sequence. Steps only the official app performs are drawn on a separate
  participant, ``App (reference only)``. ``calictl`` does not send them.
* **Evidence** — how it was established: dated captures, decompile citations, corrections. The
  full lab-note history lives in the `RE lab notes
  <https://ckeller42.github.io/open-california/business-logic/index.html>`_.

Verification status
-------------------

Every ``S_SEQ_*`` spec carries a ``:status:`` saying how far the flow is proven (shown in the
traceability table on the index page):

``live-verified``
   ``calictl`` has run this sequence against the real unit and the outcome was observed.
``photon-verified``
   Live-verified, and a human watched the *physical* effect (a lamp), because the state-char
   readback is only a write-through echo and proves nothing.
``mock-only``
   Exercised end-to-end only against ``tools/mock_unit`` / ``tools/applab``. The frames match the
   app, but ``calictl`` has never run this sequence against the real unit.
``not-live-verified``
   Unit-tested (or only partly exercised live). The contract is the *intended* behaviour, not a
   proven one.

Characteristics
---------------

Vendor UUID ``0000XXXX-6c77-4b7d-bbf6-a5e587701f3d``. Per subsystem, ``NN01`` is the control
(write) char and ``NN02`` the state (read + notify) char. Source: ``protocol/dictionary.yaml`` and
``calictl/device.py``. The 12 notifiable chars are ``1004`` and every ``NN02`` below except
``1001``/``f001``.

.. list-table::
   :header-rows: 1
   :widths: 14 22 64

   * - Char
     - Name
     - Role in the sequences
   * - ``1001``
     - VERSION (``general``)
     - Read once in the handshake. The app aborts if it is empty or the version is above 2.
       ``calictl`` reads it and does not check the value.
   * - ``1002``
     - VIN check
     - 16 opaque bytes = ``SHA-256(VIN)[16:32]``. The **app** compares them with the VIN the user
       entered and disconnects on a mismatch. ``calictl`` never reads it (not modelled).
   * - ``1003``
     - HEARTBEAT / Counter
     - Write-only liveness counter, 4-byte big-endian, ``+1`` per tick. Ticking it arms control
       writes (issue #2) and keeps a read session alive.
   * - ``1004``
     - vehicle
     - Ignition (terminal-15, bit 7), car variant, unit RTC, roll/pitch leveling
       (``R_VEHICLE_1004``). Encryption-required: its first read is what forces bonding, which is
       why the app, and ``calictl``'s constant ``AUTH_CHAR``, call it the "auth" read. It is a
       passive read, not a token exchange. Also notifiable.
   * - ``1101`` / ``1102``
     - cooler
     - Control / state.
   * - ``1201`` / ``1202``
     - campingmode
     - Control / state. ``1202`` pushes are logged by the observer.
   * - ``1302``
     - water
     - State only (no ``1301``). A bare read returns a stale latch. The fresh level arrives as a
       notification, so this is the one push-only function (``PUSH_ONLY_FUNCS``).
   * - ``1401`` / ``1402``
     - roof
     - 5-byte move frame ``[direction][SafetyCounter]`` / Position, Installed,
       SafetyCounterValid, InfoPopUp.
   * - ``1501`` / ``1502``
     - lighting
     - 16-byte frame. ``1502`` notifications carry the real brightness (Mode-4 ramp).
   * - ``1601`` / ``1602``
     - energy
     - Control (charging mode) / battery, solar, DC-DC and shore telemetry.
   * - ``1701``–``2102``, ``f000``
     - air heater, stairs, satellite, roof A/C, living-room heater, generic
     - Same ``NN01``/``NN02`` pattern. No special sequence.

Session foundation — connect, handshake, subscribe
--------------------------------------------------

.. spec:: Connect (with retry), handshake, subscribe
   :id: S_SEQ_CONNECT
   :status: live-verified
   :links: R_ACTUATE_ARM, R_PERSISTENT_SESSION

   **Contract.** Every ``calictl`` session starts on the **bonded** link
   (:py:meth:`calictl.device.CamperDevice._session`):

   * Connect with a per-attempt timeout of ``CALICTL_CONNECT_TIMEOUT_S`` (default 30 s), at most
     3 attempts, 4 s apart. With ``CALICTL_ADAPTER_RESET=1`` the adapter is power-cycled once
     after the first failure (default off, because ``hci0`` is shared). After the third failure,
     raise ``ConnectionUnavailable``. The unit is asleep, the phone app holds the single slot, or
     Bluetooth is disabled on the unit.
   * Paths that write, and the persistent session, then read ``1001`` VERSION and ``1004``
     vehicle (read failures are swallowed and logged as a weak handshake). The per-op read path
     skips these two reads.
   * Subscribe every notify/indicate char (:py:meth:`~calictl.device.CamperDevice._subscribe_all`).
     There are 12 on this unit. The session is then ready.
   * The app's ``1002`` VIN check, its version-above-2 abort and its empty-``1004`` reconnect are
     app-only. ``calictl`` does none of them.

.. mermaid::

    sequenceDiagram
        participant C as calictl (buspi)
        participant U as Camper unit
        participant A as App (reference only)
        Note over C,U: link is BONDED (LE passkey pairing done once, see Guided pairing), the RPA resolves via the bond
        loop up to 3 attempts, each bounded by CALICTL_CONNECT_TIMEOUT_S (30 s), 4 s apart
            C->>U: connect (optional adapter power-cycle after attempt 1)
        end
        Note over C: still failing after 3 attempts, so raise ConnectionUnavailable
        U-->>C: connected, services discovered by bleak
        A->>U: requestMtu(26) then read 1002, compare with SHA-256(VIN) bytes 16..32
        Note over A,U: app only, a mismatch disconnects about 20 ms later (Wrong vehicle found)
        C->>U: read 1001 (VERSION)
        Note over A: app only, aborts if VERSION is empty or above 2
        C->>U: read 1004 (vehicle, handshake read, forces encryption)
        Note over A: app only, an empty 1004 read means reconnect
        loop every notifiable / indicatable char (12 on this unit)
            C->>U: write CCCD (0100 notify / 0200 indicate)
            U-->>C: one notification with the current value
        end
        Note over C,U: session ready, state reads are fresh, writes can be armed

**Evidence.** The order 1001 → 1004 → subscribe-all was decompile-confirmed 2026-07-14 (``pf/g``
handshake, ``s/a1`` GATT dispatcher). The ``1004`` read is the app's *authenticated read*
(``pf/g.java:52``, 60 s timeout). On an unbonded link it fails with ATT ``0x05`` (insufficient
authentication), which triggers SMP passkey pairing. Its value is only checked for empty vs
non-empty and is never used as a token (``DECISIONS.md``, "1004 is NOT a session token"). There is
no challenge/response write. The app's ``1002`` gate was **APP-OBSERVED 2026-09-16** against
``tools/applab`` (``d2/g1`` case 7 "Starting Vin Check" → ``ny/c`` case 8). ``calictl`` skips it:
the bond is its identity, and the gate only protects the app user from a neighbour's van. The
per-attempt timeout became tunable in #199: a unit that advertises but ignores connection requests
(seen 2026-09-18) burns the whole timeout three times, so a shorter timeout samples its brief
connectable windows more often. See `control-and-actuation.md §2
<https://ckeller42.github.io/open-california/business-logic/control-and-actuation.html>`_ and
`protocol-crosscheck-applab.md
<https://ckeller42.github.io/open-california/business-logic/protocol-crosscheck-applab.html>`_.

Guided pairing — passkey entry and stale-bond recovery
------------------------------------------------------

.. spec:: Guided pairing with passkey entry and stale-bond recovery
   :id: S_SEQ_PAIRING
   :status: not-live-verified
   :links: R_PAIRING_SM, R_PAIRING_RUNNER, R_PAIRING_BLUEZ_TRANSPORT, R_PAIRING_STALE_BOND_RECOVERY

   **Contract.** ``POST /api/pairing`` drives the platform-free state machine
   (:py:func:`calictl.pairing.step`) through :py:class:`calictl.pairing_bluez.PairingRunner` and the
   BlueZ transport (:py:class:`calictl.pairing_bluez.BluezTransport`):

   * ``start`` first parks the persistent-session supervisor (``set_mode("disconnect")``). While a
     flow is active the poll loop skips its whole read cycle. The pairing flow never takes the
     ``_ble`` lock. Exclusion comes from parking the supervisor and skipping polls.
   * SCANNING (30 s) for the name ``VWCAMPER`` → CONNECTING (15 s) → PAIRING (15 s):
     ``Device1.Pair()`` with a ``KeyboardOnly`` ``Agent1`` → WAITING_PASSKEY (60 s) until the user
     types the 6-digit passkey the unit displays → VERIFYING (10 s): ``1001`` + ``1004`` must read,
     and at least one readable char → BONDED: cache the identity address in ``pairing.json``
     (mode 0600) and retarget the running daemon live.
   * A failed pair retries by rescanning, up to 3 attempts, then ERROR. Any state timeout leads to
     ERROR. ``cancel`` returns to IDLE.
   * **Stale-bond recovery (#200)**: when ``Pair()`` fails with ``org.bluez.Error.AlreadyExists``
     (a local bond the unit has forgotten), clear the stale bond and retry ``Pair()`` exactly
     once. Any other error propagates unchanged. **NOT-LIVE-VERIFIED** (unit-tested only).

.. mermaid::

    sequenceDiagram
        participant W as User (web wizard)
        participant C as calictl (serve, runner, transport)
        participant B as BlueZ
        participant U as Camper unit
        W->>C: POST /api/pairing start
        C->>C: park session supervisor, poll loop skips while the flow is active
        C->>B: start scan (SCANNING, 30 s)
        U-->>B: advertises VWCAMPER
        B-->>C: device found
        C->>U: stop scan, connect (CONNECTING, 15 s)
        C->>B: register Agent1 KeyboardOnly, Device1.Pair() (PAIRING, 15 s)
        alt Pair() fails with AlreadyExists (stale local bond, 2026-09-18)
            C->>B: clear the stale bond (Adapter1.RemoveDevice)
            C->>B: retry Pair() exactly once (NOT-LIVE-VERIFIED)
        end
        B->>U: SMP pairing request
        Note over U: unit shows a 6-digit passkey on its screen
        B->>C: Agent1.RequestPasskey (WAITING_PASSKEY, 60 s)
        C-->>W: wizard asks for the passkey
        W->>C: POST /api/pairing passkey (6 digits)
        C->>B: passkey returned by the agent
        B->>U: LE passkey entry completes, link encrypted and bonded
        B-->>C: Pair() returns OK
        C->>U: read 1001 and 1004, count readable chars (VERIFYING, 10 s)
        alt all reads OK and at least one char readable
            C->>C: BONDED, write pairing.json (0600), daemon retargets the address live
        else read failed or zero readable
            C->>C: ERROR verify_failed
        end
        C->>B: unregister the agent (flow end)

**Evidence.** The state machine, runner and wizard are unit- and mock-tested
(``tests/vectors/pairing.json``, ``tests/test_pairing_runner.py``,
``tests/test_pairing_bluez_transport.py``). The BlueZ transport is mock-tier. The one live contact
was a re-pair at the van on 2026-09-18, which hit ``AlreadyExists`` from a stale local bond left by
a unit-side Bluetooth reset and motivated #200, whose recovery is so far unit-tested only. The
full live unbond and re-pair is still open (#157). Details: `guided-pairing.md
<https://ckeller42.github.io/open-california/business-logic/guided-pairing.html>`_.

Notifications
-------------

.. spec:: Subscribe, then consume pushed state notifications
   :id: S_SEQ_NOTIFY
   :status: live-verified
   :links: R_READ_HEARTBEAT_REFRESH, R_CAMPING_OBSERVER, R_VEHICLE_1004

   **Contract.** Subscribing is part of every handshake, and pushes are a real ``calictl`` data
   path. They are never discarded:

   * ``_subscribe_all`` sinks every payload, keyed by char UUID. It also calls an ``on_push`` hook
     when one is given.
   * **Per-op** ``read_all``/``read`` prefer any pushed value over a bare read. The **persistent
     session** prefers a push only for ``PUSH_ONLY_FUNCS`` (water ``1302``) and live-reads
     everything else, so a subscribe-time value can never pin a char.
   * On the persistent session, the daemon's ``on_push`` is
     :py:meth:`calictl.observer.CampingObserver.on_push`. It decodes ``1202`` camping, ``1004``
     vehicle and ``1102`` cooler pushes and **logs** changes (``camping-push``). It is passive and
     does not write served state.
   * After a lighting write, a newer ``1502`` push (Mode-4 ramp) is the confirmation. It updates the
     served lighting state. The ``1502`` readback is only an echo.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Camper unit
        C->>U: write CCCD=0100 on each status char (subscribe-all)
        U-->>C: one push per char with its current value, right after its CCCD write
        Note over C: sink stores it by char UUID, per-op reads prefer it over the bare read
        opt water in use (water system powered)
            U-->>C: notify 1302, fresh level (the only fresh source, PUSH_ONLY_FUNCS)
        end
        opt camping, ignition or cooler change (persistent session only)
            U-->>C: notify 1202 / 1004 / 1102
            Note over C: observer on_push decodes and logs camping-push, passive
        end
        opt after an applied lighting SET
            U-->>C: notify 1502 Mode-4 ramp frames (real brightness stepping to target)
            Note over C: lighting confirm, served lighting state updated from the push
        end

**Evidence.** The first real-unit trace (buspi, 2026-09-16) showed each of the 12 subscribed chars
notifying **once, right after its CCCD write**, and no periodic stream in 150 s. That contradicted
an older "``1602`` ~3×/s" note. Event-driven pushes were observed for ``1502`` (Mode-4 ramp,
2026-08-16) and ``1302`` (water, during use). The app subscribes ``1202`` and parses the pushed
water frame (VM ``qg/b``), per the 2026-07-14 decompile. Live ``1202``/``1004`` push *changes* on
this unit are decompile-asserted; the observer's ``camping-push`` log is how they are being
confirmed (`auto-camper-mode.md
<https://ckeller42.github.io/open-california/business-logic/auto-camper-mode.html>`_).

Persistent session supervisor
-----------------------------

.. spec:: Hold an armed session while the web UI is active, release it when idle
   :id: S_SEQ_SESSION
   :status: live-verified
   :links: R_PERSISTENT_SESSION, R_SESSION_SUPERVISOR

   **Contract.** The daemon holds one persistent armed session only while the web UI is active
   (:py:class:`calictl.session.SessionSupervisor`, :py:class:`calictl.device.PersistentSession`).
   It is on unless ``CALICTL_PERSISTENT_SESSION=0``.

   * **Active** means a ``/api/state`` poll or a command within ``CALICTL_UI_IDLE_S`` (default
     25 s). ``POST /api/session {"action":"disconnect"}`` forces inactive until ``connect`` or a
     command.
   * **Up**: connect (the retry cascade above), read ``1001`` + ``1004``, subscribe-all with the
     observer's ``on_push``, start the ``1003`` heartbeat (``CALICTL_HEARTBEAT_PERIOD_S``, default
     0.6 s) and wait ``CALICTL_HEARTBEAT_WARMUP_S`` (2 s). The heartbeat then ticks for as long as
     the session is held.
   * **Held**: polls and commands run over it. Writes skip the handshake and the 3 s arm delay
     (``arm=False``), so they land in well under a second. A command first waits up to
     ``CALICTL_SESSION_WAIT_S`` (6 s) for the session to come up instead of racing it cold.
   * **Idle**: close the session under the ``_ble`` lock, so the phone app gets the single slot.
     Polls fall back to brief cold per-op reads.
   * **Unreachable**: back off 5 / 10 / 30 / 60 s (capped). After 4 consecutive failures the state
     is ``asleep``. A command resets the backoff and reconnects immediately (keep-warm nudge).
   * **Handover**: a roof move or STOP calls ``drop_for_handover()``, which closes the session
     *without* setting the manual release. The supervisor reconnects once the lock is free. Guided
     pairing parks the session with ``set_mode("disconnect")``.

.. mermaid::

    sequenceDiagram
        participant W as Web UI
        participant S as serve (supervisor, poll, commands)
        participant U as Camper unit
        W->>S: GET /api/state every ~2 s (marks UI active)
        S->>U: connect, read 1001 and 1004, subscribe-all with on_push
        loop while held, calictl ~0.6 s (app 500 ms)
            S-)U: write 1003 = N, N+1, N+2 (monotonic +1)
        end
        W->>S: POST /api/command
        S->>U: control frame over the live session (no handshake, no 3 s arm delay)
        opt roof move or STOP (handover)
            S->>U: drop_for_handover closes the session, the roof opens its own connection
            S->>U: afterwards the supervisor reconnects by itself
        end
        Note over W,S: no UI activity for CALICTL_UI_IDLE_S (25 s) or a manual Disconnect
        S->>U: close the session under the _ble lock (slot free for the phone app)
        Note over S,U: idle polls fall back to cold per-op read_all
        Note over S,U: connect failures back off 5, 10, 30, 60 s, asleep after 4, a command nudges now

**Evidence.** On buspi (2026-09-16 trace) the session repeatedly came up on a web nudge and was
released for UI idleness (``persistent session released (web UI idle)``). No unit-side drop was
found while the heartbeat ticked. The 2026-07-13 stability spike held the link 180 s with 100 %
uptime and 0 drops. The roof handover (#198) is covered by tests only; the roof itself has never
moved under ``calictl`` (see :need:`S_SEQ_ROOF`). Design notes:
`protocol-crosscheck-applab.md
<https://ckeller42.github.io/open-california/business-logic/protocol-crosscheck-applab.html>`_.

Heartbeat-armed control write
-----------------------------

.. spec:: Heartbeat-armed control write
   :id: S_SEQ_ACTUATE
   :status: live-verified
   :links: R_ACTUATE_ARM

   **Contract.** The unit honours a control write only while a **+1 monotonic 4-byte big-endian
   counter ticks on** ``1003`` (the arm gate, issue #2). There is no ignition, mode or enable gate
   (the roof is the one exception). :py:meth:`calictl.device.CamperDevice.actuate` (cold path,
   CLI) does the following:

   * Connect, then handshake (``1001`` + ``1004`` reads, subscribe-all).
   * Start the heartbeat (``CALICTL_HEARTBEAT_PERIOD_S``, default **0.6 s**, from a fixed start
     ``0x00100000``) and wait ``CALICTL_ARM_DELAY_S`` (**3.0 s**).
   * Write the **full-packet** frame with response. Untargeted fields carry the current values, or
     the leave-unchanged sentinel for action fields.
   * Optionally write a ``follow`` frame ``CALICTL_FOLLOW_DELAY_S`` (0.3 s) later (lighting only).
   * If ``verify``, wait ``CALICTL_SETTLE_S`` (2.5 s) and read the state char. Then stop the
     heartbeat and disconnect.

   The persistent-session path (:need:`S_SEQ_SESSION`) writes immediately over its already-ticking
   heartbeat. ``calictl`` sends no trailing neutral frame, except the lighting flush.

.. mermaid::

    sequenceDiagram
        participant C as calictl (buspi)
        participant U as Camper unit
        participant A as App (reference only)
        C->>U: connect, read 1001 and 1004, subscribe-all (see S_SEQ_CONNECT)
        loop calictl ~0.6 s across the write window only (app 500 ms, continuous while connected)
            C-)U: write 1003 = N, N+1, N+2 (monotonic +1)
        end
        Note over C,U: calictl waits ARM_DELAY_S (3.0 s) after the first beat before writing
        Note over U: armed, control writes are honoured
        C->>U: write control char = SET frame (full-packet, write with response)
        A->>U: 500 ms later a neutral all-sentinel frame (cooler ff771e3e1f1f, heater 3f7b007f1f3f)
        C->>U: after SETTLE_S (2.5 s) read the state char (verify)
        C->>U: disconnect (heartbeat stops, the load stays latched)

**Evidence.** Live-verified on-device 2026-07-07 (issue #2) for the cooler (power/level) and
campingmode (master/lights/USB). The jadx decompile (2026-07-14) matches: ``d2/s`` driver and
``ag/b`` builder, a 4-byte BE uint32 with ``+1`` per tick (``b1/d``), a **500 ms** timer (``zf/d``
``J0=500L``) and **seed 0**. ``calictl``'s 0.6 s cadence and arbitrary start value satisfy the
same liveness/monotonicity check; the value itself does not matter. **APP-OBSERVED 2026-09-16**
(``tools/applab``): the connected app beats ``1003`` continuously at ~750 ms (its 500 ms timer plus
GATT round-trips). Its writes are full-packet with untargeted fields at the model defaults (2-bit
``3``; heater ``HeatingLevel 11 / RunningTime 127``, cooler ``Level 7 / Mode 7``, timer hours
``30/62/31``). 500 ms later it sends a neutral frame with **every** field at its sentinel
(cooler ``ff771e3e1f1f``, heater ``3f7b007f1f3f``, camping ``ff``, energy ``30``, lighting
``0e00…``). The unit accepts both styles. See `control-and-actuation.md §1
<https://ckeller42.github.io/open-california/business-logic/control-and-actuation.html>`_.

Lighting SET and neutral flush
------------------------------

.. spec:: Lighting SET, then a neutral flush frame, confirmed by the 1502 push
   :id: S_SEQ_LIGHT_COMMIT
   :status: photon-verified
   :links: R_LIGHT_COMMIT

   **Contract.** The actuation gate for lighting is the unit being **awake**. The unit needs no
   arming frame, no REQUEST_CONFIG preamble and no heartbeat. ``calictl`` does the following:

   * Write ``SET_BRIGHTNESS`` (Mode 4, ProfileNumber hardcoded to 9 like the app, target zone =
     level 0–11, other zones = ``14`` leave-unchanged) or ``SET_PROFILE`` to ``1501``.
   * 0.3 s later write the neutral flush :data:`calictl.control.LIGHT_COMMIT` ``0e00…`` (NO_MODE
     default frame), via ``device.actuate(..., follow=control.commit_for("lighting"))``.
   * Daemon: over the live session the write is immediate (``verify=False``). The command then waits
     up to ``CALICTL_FAST_CONFIRM_S`` (1.2 s) for a ``1502`` push newer than the pre-write
     snapshot. A matching push reports applied, otherwise the result is "sent". Without a session
     it takes the cold armed path and does not confirm.
   * CLI: cold armed path (:need:`S_SEQ_ACTUATE`) with a readback, which is **an echo, not proof**.
   * ``calictl`` never sends REQUEST_CONFIG ``0d0c000000000000eeeeeeeeeeeeeeee`` (retired
     2026-08-29). It is the app's screen-open config pull and is kept here as the RE record.

   Both ``calictl`` paths happen to write over a heartbeat-armed link. The unit does not require
   that; the photon trials included un-armed writes.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Lighting (1501 / 1502)
        participant A as App (reference only)
        Note over C,U: unit must be AWAKE, the actual actuation gate
        A->>U: on screen open REQUEST_CONFIG 0d0c (Mode 12, PN 13, zones 14) then 0e00 flush
        U--)A: 1502 config dump, Mode-tagged (0x0c, 0x06, 0x08, 0x10, 0x14, 0x18)
        C->>U: SET_BRIGHTNESS (Mode 4, PN 9, zone N, others 14)
        C->>U: 0.3 s later flush 0e00 (NO_MODE neutral default frame)
        U--)C: 1502 Mode-4 ramp notifications (real brightness stepping to N)
        Note right of U: lamp PHYSICALLY changes (photon-verified 2026-08-16, bare SET included)
        Note over C: daemon confirms from the newer 1502 push within 1.2 s, never from the readback echo

**Evidence.** For over a month a byte-identical ``calictl`` SET was ACKed and echoed while the lamp
stayed dark. This was settled on **2026-08-16**: six evening trials were photon-confirmed,
including an immediate write on a cold connection. The owner watched Kochen L7 go 0 → dark,
8 → 80 %, 0 → dark. A morning HCI capture (``tools/capture_diff.py``) had suggested REQUEST_CONFIG
was a required arming step. The evening trials falsified that: a sleepy morning unit plus the
preamble's ~3.3 s delay was the confound. The decompile agrees: the app's set-one-zone
``dg/h.java:174`` ``E()`` writes directly and never calls the config request ``d0()``
(``dg/h.java:471``).

``0e00…`` is **not an app "commit"**. The Mode enum (``dg/n``) is ``NO_MODE=0, SET_BRIGHTNESS=4,
SET_COLOR=6, SET_DOUBLE=8, REQUEST_CONFIG=12, SET_PROFILE=16, WAKEUP_TIME=20, SYSTEM_TIME=24,
PREVIEW=28``. So ``0e00…`` is just the builder's default frame (ProfileNumber ``14`` + NO_MODE +
all-unchanged). The app self-flushes by streaming frames every ~500 ms. ``calictl``'s single write
needs the explicit flush. The old "a profile must be active first" precondition was an artefact of
the echo.

The config dump that REQUEST_CONFIG triggers arrives as ``1502`` notifications, tagged by Mode:
``0x0c`` config echo, ``0x06`` colour state, ``0x08`` SET_DOUBLE, ``0x10`` profile state
(profile 8), ``0x14`` wake time, ``0x18`` system time (ticking unix seconds). After an *applied*
SET_BRIGHTNESS the unit pushes Mode-4 ramp frames showing the real brightness stepping to the
target (e.g. 01 → 03 → 04 → 05). These are normal ``1502`` state frames, decodable with
:py:func:`calictl.protocol.decode`. They tracked real actuation in every observed case, armed or
not. Still open: whether a *deep-asleep* unit needs any arming. Full history:
`control-and-actuation.md §4
<https://ckeller42.github.io/open-california/business-logic/control-and-actuation.html>`_ and
`lighting-energy-water-sat-roof.md
<https://ckeller42.github.io/open-california/business-logic/lighting-energy-water-sat-roof.html>`_.

Roof actuation — press-and-hold, SafetyCounter-gated
----------------------------------------------------

.. spec:: Roof press-and-hold move stream, unit self-gated ~3 s by the SafetyCounter
   :id: S_SEQ_ROOF
   :status: mock-only
   :links: R_ROOF_ACTUATE, R_SESSION_SUPERVISOR, R_ROOF_ALERT

   **Contract.** SAFETY-SENSITIVE. The unit itself requires ignition ON. The daemon refuses a
   move up front when ``control.command_precondition`` blocks it (a blocking ``InfoPopUp`` alert,
   or ``Position`` 15 = error). STOP is never gated. For a move,
   :py:meth:`calictl.device.CamperDevice.actuate_roof` does the following:

   * **Slot handover (#198)**: under the ``_ble`` lock, ``drop_for_handover()`` closes the live
     persistent session. The unit has one connection slot, and that session's ``1003`` heartbeat
     is forbidden during a roof move. Then open a **dedicated connection**. The supervisor
     reconnects after the move.
   * **Arm = handshake only** (``_handshake``: ``1001`` + ``1004`` reads, subscribe-all). There is
     **no 1003 heartbeat and no** ``ARM_DELAY_S`` **pre-arm**, so the counter streams immediately
     (#150). A gap would make the unit see a fresh counter and withhold the motor for another
     ~3 s.
   * Stream ``[direction][SafetyCounter]`` to ``1401`` every ``CALICTL_ROOF_PERIOD_S`` (0.5 s).
     The direction is open ``0x01`` / close ``0x04``. The counter is app-style: a random seed in
     1..1 000 000 plus 1 per 500 ms of wall-clock, big-endian uint32.
   * At 3 s, read ``1402`` once. If ``SafetyCounterValid`` (bit 7) is still clear, abort (the app's
     dead-man). Poll ``Position`` every ``CALICTL_ROOF_LIMIT_POLL_S`` (1 s) and cease at the
     direction's limit (open ``1``, closed ``0``/``14``). Also cease on release (``stop_event``)
     or at ``CALICTL_ROOF_MAX_TRAVEL_S`` (30 s).
   * **Always** write STOP ``[0x00][counter]``, which is best-effort on a dropped link. Then read
     ``1402`` and disconnect.
   * **Release** is lock-free: it sets ``stop_event``. A **standalone STOP** (nothing in flight)
     also takes the slot and goes through the armed ``device.actuate`` with a zero counter. The web
     UI ignores a move press within **1000 ms** of the previous move start (``ROOF_REPRESS_MS``).
     STOP is never debounced.

.. mermaid::

    sequenceDiagram
        participant S as serve
        participant C as calictl (actuate_roof)
        participant U as Roof (1401 / state 1402)
        participant A as App (reference only)
        Note over C,U: ignition ON, no blocking InfoPopUp, roof path clear
        S->>S: drop_for_handover closes the persistent session (one slot, no heartbeat allowed)
        C->>U: dedicated connect, read 1001 and 1004, subscribe-all (no 1003 heartbeat, no pre-arm)
        loop app only, while the roof page is open and before any press
            A->>U: frame [0x00 stop] plus SafetyCounter every ~500 ms (pre-validates the counter)
        end
        Note over C,U: user presses and HOLDS open or close (web UI debounces a re-press within 1000 ms)
        loop calictl every 0.5 s while held (app about 8 frames per second, counter still +1 per 500 ms)
            C->>U: move frame [0x01 open / 0x04 close] plus SafetyCounter (seed + elapsed/500 ms)
            C->>U: every 1 s read Position (1402), cease at the limit (open 1, closed 0 or 14)
        end
        Note right of U: motor withheld about 3 s until the counter validates, then 1402 bit 7 is set
        C->>U: at 3 s read 1402, SafetyCounterValid still clear means abort to STOP
        Note over C,U: after about 3 s the pop-top travels while frames continue
        C->>U: STOP frame [0x00] on release, limit, abort or the 30 s cap (always sent)
        Note right of U: halts (frames ceasing is the hardware dead-man, unverified here)
        C->>U: read 1402, disconnect
        S->>U: supervisor reconnects the persistent session afterwards

**Evidence.** Settled 2026-07-13 from the **decompiled roof class** (``w8/a``), reconciled against
an at-the-van capture of a full open and close. Control char ``1401`` is ATT handle ``0x0037``;
state ``1402`` is handle ``0x0033``. The frame is 5 bytes, and the direction bytes match
:py:func:`calictl.control.roof_frame` byte for byte. There is **no confirmation or STOP-hold
phase**: an earlier "safety hold" reading was the user releasing the button while reading the
safety dialog. The **SafetyCounter is app-generated** (``b1/d.java:352``) and never echoed. The old
"echoes the unit's counter, ~0.8 per frame" reading came from **two overlapping senders**: the
500 ms counter engine and a secondary 1000 ms ``ig/c`` direction re-send repeating the current
value, which averaged ~0.8 increments per observed frame. The ~3 s wait is **unit-enforced**. The
app's 3000 ms dead-man only reports "SafetyCounter is invalid after 3 seconds"
(``dialog_info_popUpRoof_safetyCheck``). The perceived ~4 s is 3 s plus BLE latency; there is no
4 s countdown constant.

``1402`` layout, MSB-first per ``dictionary.yaml``: ``Position@0`` is byte 0's high nibble
(``0``/``14`` closed, ``1`` open, ``2`` middle, ``15`` error), then ``Installed@6`` and
``SafetyCounterValid@7`` in byte 0's low bits. So ``03xx`` means closed + installed + counter
valid, and ``23xx`` means middle. ``InfoPopUp@12`` is byte 1's low nibble (the alert enum,
``R_ROOF_ALERT``). **APP-OBSERVED 2026-09-16** (``tools/applab``): the roof page pre-streams
``[0x00][counter]`` from the moment it opens. While a button is held the rate rises to ~8 frames/s,
with four consecutive frames carrying the same counter. Without terminal 15 the page hides its
controls ("Switch on the ignition"). The no-heartbeat arm is from the 2026-08-30 decompile
cross-check (#150), and the handover is #198. **Mock-tested only**: ``calictl`` has never driven
the real motor. See `lighting-energy-water-sat-roof.md (Roof)
<https://ckeller42.github.io/open-california/business-logic/lighting-energy-water-sat-roof.html>`_
and `alert-states.md
<https://ckeller42.github.io/open-california/business-logic/alert-states.html>`_.

Range validation and the 0x0E link drop
---------------------------------------

.. spec:: Out-of-range writes rejected build-side and by the unit (0x0E)
   :id: S_SEQ_REJECT
   :status: live-verified
   :links: R_CONTROL_INT_RANGE

   **Contract.** No out-of-range value is ever written. There are two build-side guards:

   * The builders coerce operator values with ``_int_range`` (``R_CONTROL_INT_RANGE``), which
     raises a ``ValueError`` naming the field and the accepted range.
   * :py:func:`calictl.protocol.encode` runs :py:func:`calictl.protocol.check_value` on every
     field: it must fit its width and any curated ``valid`` set.

   A raise means the frame is never written. If a bad frame *does* reach the unit, it answers ATT
   ``0x0E`` and drops the link.

.. mermaid::

    sequenceDiagram
        participant B as calictl (build)
        participant C as calictl (BLE)
        participant U as Camper unit
        B->>B: builder _int_range, then encode runs check_value (width, valid set)
        alt value out of range
            B--xC: raise ValueError (frame NEVER written)
        else in range
            B->>C: frame
            C->>U: write control char
            alt unit rejects (semantically invalid)
                U--xC: ATT 0x0E and link drop
            else accepted
                U-->>C: write response
            end
        end

**Evidence.** The ``0x0E`` rejection is ``calictl``'s own on-device observation (cooler
``State=3``; lighting ``ProfileNumber=14`` with ``Mode=4``). It does not come from the app code.
The app's state fields carry the same bounds (``sg.a(default, max)`` wrappers), so the app never
emits out-of-range values. Status ``live-verified`` refers to that on-device rejection. See
`DECISIONS.md <https://ckeller42.github.io/open-california/business-logic/DECISIONS.html>`_
(2026-07-07).

Fresh state read under heartbeat
--------------------------------

.. spec:: Read under a live heartbeat for fresh values
   :id: S_SEQ_READ
   :status: live-verified
   :links: R_READ_HEARTBEAT_REFRESH, R_READ_RETRY_SHARED, R_WATER_STALE_GUARD

   **Contract.** A cold per-op read (:py:meth:`calictl.device.CamperDevice.read_all`) runs under
   the ``1003`` heartbeat, which keeps the link up and refreshes the re-read chars:

   * Connect, then subscribe-all with a sink. There are no ``1001``/``1004`` reads on this path.
   * Start the heartbeat (0.6 s) and wait ``CALICTL_HEARTBEAT_WARMUP_S`` (2 s).
   * For each function, use a pushed value if one landed, else read the state char (3 tries,
     0.8 s backoff, abort the cycle on a link drop). An optional wait for a fresh water push
     (``CALICTL_WATER_PUSH_WAIT_S``) is **disabled by default** (unvalidated).
   * Stop the heartbeat and disconnect.
   * Water freshness is not in reach of the heartbeat. The daemon's stale guard
     (``freshness.implausible_water_drop``) holds the last plausible reading and flags it stale.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Camper unit
        C->>U: connect, subscribe-all (real handler into the sink)
        U-->>C: one push per subscribed char (current values)
        loop calictl ~0.6 s, warm-up 2 s then across the reads (app 500 ms, continuous)
            C-)U: write 1003 heartbeat
        end
        opt water system powered
            U-->>C: notify 1302, fresh water level
        end
        Note over U: heartbeat keeps the link up and refreshes re-read chars (1102, 1602, 1902, 1004)
        C->>U: read each state char unless a pushed value is already in the sink
        C->>U: disconnect

**Evidence.** Without a heartbeat the unit drops a link after ~15 s. With one, the link held 64 s
on-device (2026-07-09). Water is **measurement-gated**: the unit measures only while its water
system is powered. While parked, it pushed zero ``1302`` frames in 40 s, and a bare read returns
the stale latch. The 2026-07-09 "1 L → 11 L after a heartbeat" was correlation, not cause. The app
also gets water only from the ``1302`` push (VM ``qg/b``, decompile 2026-07-14). See
`value-freshness.md
<https://ckeller42.github.io/open-california/business-logic/value-freshness.html>`_.

Reachability / deep-sleep
-------------------------

.. spec:: Parked deep-sleep makes access intermittent
   :id: S_SEQ_SLEEP
   :status: live-verified
   :links: R_SESSION_SUPERVISOR

   **Contract.** Parked and idle, the unit BLE-deep-sleeps and stops advertising. Only physical use
   (door, ignition) wakes it. Access is therefore inherently intermittent:

   * A connect exhausts its retries and raises ``ConnectionUnavailable``. The poll is skipped and
     logged ``asleep`` or ``ble_error`` in the outcome log.
   * The supervisor backs off 5 / 10 / 30 / 60 s. After 4 consecutive failures its state is
     ``asleep``.
   * ``serve`` keeps serving the persisted last-known state with an "as of" time, and the web UI
     shows an offline banner.
   * Once the unit advertises again, the next connect succeeds and polling resumes.

.. mermaid::

    sequenceDiagram
        participant C as buspi (serve)
        participant U as Camper unit
        Note over U: parked and idle, BLE deep-sleep (no advertising)
        C--xU: connect, 3 attempts fail, ConnectionUnavailable
        Note over C: supervisor backs off, state asleep, serve shows last-known state and the offline banner
        Note over U: door or ignition wakes it, advertising resumes
        U-->>C: advertising
        C->>U: connect OK, polls resume (fresh reads)

**Evidence.** Once awake, with buspi holding the session and the heartbeat ticking, the link is
stable. A 180 s spike (2026-07-13) held 100 % uptime with 0 drops, and locking the van did not
drop it. A one-off 2026-07-13 outage had a different root cause: **Bluetooth had been disabled on
the unit itself**, so it stayed unreachable for days even though the van was in use. That is why
``ConnectionUnavailable``'s message lists "Bluetooth disabled in the unit's settings" as a cause
that will not resolve by itself. See `value-freshness.md
<https://ckeller42.github.io/open-california/business-logic/value-freshness.html>`_ ("Connection
failure modes").
