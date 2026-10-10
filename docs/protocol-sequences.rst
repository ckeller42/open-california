Protocol sequence diagrams
==========================

The flows ``calictl`` (and the ESP32 :term:`satellite`) speak to the VW California camper :term:`unit`, and from
the daemon to Home Assistant, as sequence diagrams: the BLE wire flows, the daemon's poll cycle and
MQTT sink, the control commands, and the satellite's pairing, WiFi setup and command path. This page
is the **single canonical copy**: the lab-notes page ``docs/business-logic/protocol-sequences.md``
only points here. The diagram index below lists all of them.

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
   readback is only a write-through :term:`echo <Readback echo>` and proves nothing.
``mock-only``
   Exercised end-to-end only against ``tools/mock_unit`` / ``tools/applab`` (for the ESP32 satellite
   also the Bumble fake unit and a CoreS3 on the bench). The frames match the app, but
   ``calictl`` (or the satellite) has never run this sequence against the real unit.
``ci-real-stack``
   Verified in CI against the real host Bluetooth stack (BlueZ, ``bluetoothd``, D-Bus and the
   kernel inside a VM, the ``pairing-real-stack`` job) talking to the Bumble fake unit, but
   ``calictl`` has not yet run this sequence end to end against the real unit.
``unit-tested``
   Covered by unit tests of the code on both sides of the interface (here the MQTT sink and its
   Home Assistant entities, with no broker or Home Assistant in the loop). No end-to-end run is
   recorded in these docs.

Diagram index
-------------

.. list-table::
   :header-rows: 1
   :widths: 24 34 42

   * - Id
     - Section
     - Flow
   * - :need:`S_SEQ_CONNECT`
     - Session foundation
     - Connect with retry, handshake reads, subscribe-all.
   * - :need:`S_SEQ_PAIRING`
     - Guided pairing
     - The web wizard: scan, bond probe, passkey entry, verify, stale-bond recovery.
   * - :need:`S_SEQ_NOTIFY`
     - Notifications
     - Subscribe-time and event pushes, last frame wins, the lighting config latch.
   * - :need:`S_SEQ_SESSION`
     - Persistent session supervisor
     - Hold the armed session while the web UI is active, release it when idle.
   * - :need:`S_SEQ_READ`
     - Fresh state read under heartbeat
     - A cold per-op read: subscribe, heartbeat, read every char, later pushes win.
   * - :need:`S_SEQ_POLL`
     - Daemon poll cycle
     - Lock, read, decode, interpret, guards, cache with as-of time, fan out, offline path.
   * - :need:`S_SEQ_SLEEP`
     - Reachability / deep-sleep
     - A parked unit stops advertising, the daemon serves the last known state.
   * - :need:`S_SEQ_MQTT`
     - Home Assistant over MQTT
     - Discovery, state publish and command-in via ``serve.on_command``.
   * - :need:`S_SEQ_ACTUATE`
     - Heartbeat-armed control write
     - The ``1003`` heartbeat spanning one control write, cold and persistent paths.
   * - :need:`S_SEQ_COOLER`
     - Cooler command
     - The app-faithful cooler frame (ruling R1) from request to applied-check.
   * - :need:`S_SEQ_LIGHT_COMMIT`
     - Lighting SET and neutral flush
     - A bare brightness write, the flush frame, confirmation from the ``1502`` push.
   * - :need:`S_SEQ_WAKEUP`
     - Wake-up light and door contact
     - A config edit, the REQUEST_CONFIG pull (R5), config latched from the unit's frames.
   * - :need:`S_SEQ_ROOF`
     - Roof actuation
     - The roof view's STOP stream, press-and-hold on the same counter, STOP.
   * - :need:`S_SEQ_REJECT`
     - Range validation
     - Out-of-range values refused build-side and by the unit (``0x0E``).
   * - :need:`S_SEQ_ESP_PAIRING`
     - ESP32 satellite
     - Console-driven pairing and the bonded session.
   * - :need:`S_SEQ_ESP_WIFI`
     - ESP32 satellite
     - WiFi provisioning through the setup hotspot and captive portal.
   * - :need:`S_SEQ_ESP_COMMAND`
     - ESP32 satellite
     - Station-mode ``POST /api/command`` through the write allow-list.

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
     - Read once in the handshake. The app aborts if it is empty or ``CommunicationVersion`` is
       above its maximum: 2 in app 5.0.8, **3 since app 5.4.0**, which also branches the energy
       and general-purpose decode on it (V2 / V3 / V4). This van reports 2. ``calictl`` does not
       refuse other values; it flags anything but 2 as ``firmware_untested``.
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
     - State only (no ``1301``). Read after subscribing, like every char; the last frame wins (the
       app's order, decompile 2026-10-07). The old "push-only" rule was dropped.
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
   * - ``1603``, ``f002``, ``2200``–``2202``
     - energy measurements, general-purpose write, VirtualBattery
     - **Not on this van, not used.** New in app 5.4.0 for units reporting CommunicationVersion
       3 (``1603``, ``f002`` heater night reduction) or 4 + California Next (``2200``–``2202``).
       ``calictl`` neither reads nor writes them and must never write ``f002`` to a V2 unit.
       Layouts: ``business-logic/protocol-alignment.md`` "App 5.4.0".

Session foundation — connect, handshake, subscribe
--------------------------------------------------

.. spec:: Connect (with retry), handshake, subscribe
   :id: S_SEQ_CONNECT
   :status: live-verified
   :links: R_ACTUATE_ARM, R_PERSISTENT_SESSION

   **Contract.** Every ``calictl`` session starts on the **bonded** link
   (:py:meth:`calictl.device.CamperDevice._session`):

   * Refuse at once with ``ConnectionUnavailable`` while no unit is paired
     (:py:attr:`~calictl.device.CamperDevice.paired` is false). No ``BleakClient`` is built, so the
     placeholder address never makes BlueZ run a discovery that starves the pairing wizard.
   * Connect with a per-attempt timeout of ``CALICTL_CONNECT_TIMEOUT_S`` (default 30 s), at most
     3 attempts, 4 s apart. With ``CALICTL_ADAPTER_RESET=1`` the adapter is power-cycled once
     after the first failure (default off, because ``hci0`` is shared). After the third failure,
     raise ``ConnectionUnavailable``. The unit is asleep, or Bluetooth is disabled on the unit. (A
     phone app that holds a link does not block ``calictl``: the unit served the app, buspi and the
     ESP32 satellite at the same time on 2026-10-10.)
   * Paths that write, and the persistent session, then read ``1001`` VERSION and ``1004``
     vehicle (read failures are swallowed and logged as a weak handshake). The per-op read path
     skips these two reads.
   * Subscribe every notify/indicate char (:py:meth:`~calictl.device.CamperDevice._subscribe_all`).
     There are 12 on this unit. The session is then ready.
   * The app's ``1002`` VIN check, its version-above-maximum abort (2 in app 5.0.8, 3 since 5.4.0) and its empty-``1004`` reconnect are
     app-only. ``calictl`` does none of them.

.. mermaid::

    sequenceDiagram
        participant C as calictl (buspi)
        participant U as Camper unit
        participant A as App (reference only)
        Note over C,U: link is BONDED (LE passkey pairing done once, see Guided pairing), the RPA resolves via the bond
        Note over C: no paired unit yet means ConnectionUnavailable at once, nothing is scanned
        loop up to 3 attempts, each bounded by CALICTL_CONNECT_TIMEOUT_S (30 s), 4 s apart
            C->>U: connect (optional adapter power-cycle after attempt 1)
        end
        Note over C: still failing after 3 attempts, so raise ConnectionUnavailable
        U-->>C: connected, services discovered by bleak
        A->>U: requestMtu(26) then read 1002, compare with SHA-256(VIN) bytes 16..32 (real phone with cached GATT, no discovery)
        Note over A,U: app only, a mismatch disconnects about 20 ms later (Wrong vehicle found)
        Note over A,U: real app 2026-10-10 subscribes only the 8 chars of the fitted functions, then reads each state char
        C->>U: read 1001 (VERSION)
        Note over A: app only, aborts if VERSION is empty or above its max (2, or 3 since app 5.4.0)
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

**CAPTURE 2026-10-10** (the real CaliforniaOnTour app on a Fairphone 6 against the van's unit,
AmbSw ``0410`` / CommunicationVersion 2, Android HCI snoop; evidence-ledger 2026-10-10). With the
GATT table cached the phone runs **no service discovery**. It reads ``1002`` (identity), ``1001``,
``1004``; writes CCCD ``0100`` for ``1702``, ``1502``, ``1102``, ``1602``, ``1004``, ``1302``,
``1402``, ``1202`` (in that order); then reads ``1702``, ``1102``, ``1001``, ``1602``, ``1302``,
``1004``, ``f001``, ``1402``, ``1502``, ``1202``; then the ``1003`` heartbeat starts. That is 8
subscriptions, the chars of the functions fitted on this van plus ``1004``, not the 12 the app lab
saw against a fake unit with every function fitted. ``calictl`` still subscribes all 12; the unit
accepts both. The 8 match app 5.4.0's per-variant subscribe gate (decompile 2026-10-10, ``gg/o``):
on a T7 it no longer subscribes stairs, satellite, roof A/C and living-room heater, whatever their
``Installed`` bit. App 5.4.0 keeps this handshake order and the ``1003`` timing (random
750–850 ms, seed 1..1e6) unchanged from 5.0.8.

Guided pairing — passkey entry and stale-bond recovery
------------------------------------------------------

.. spec:: Guided pairing with passkey entry and stale-bond recovery
   :id: S_SEQ_PAIRING
   :status: ci-real-stack
   :links: R_PAIRING_SM, R_PAIRING_RUNNER, R_PAIRING_BLUEZ_TRANSPORT, R_PAIRING_STALE_BOND_RECOVERY, R_FAKE_UNIT_FIDELITY

   **Contract.** ``POST /api/pairing`` drives the platform-free state machine
   (:py:func:`calictl.pairing.step`) through :py:class:`calictl.pairing_bluez.PairingRunner` and the
   BlueZ transport (:py:class:`calictl.pairing_bluez.BluezTransport`):

   * ``start`` parks the persistent-session supervisor (``set_mode("disconnect")``), marks the
     start pending (the poll loop skips while it is set), then takes the ``_ble`` lock just long
     enough to enter SCANNING — an in-flight poll or command finishes first. The HTTP request waits
     at most ``PAIRING_START_WAIT_S`` (2 s) and answers ``scanning`` while the start still waits.
     The lock is released once SCANNING is entered; for the rest of the flow the parked supervisor
     and the poll skip keep ``serve`` off the radio.
   * Entering SCANNING registers the ``KeyboardOnly`` ``Agent1`` **before any link exists** (the
     kernel fixes an LE link's IO capability when the link is created). The registration is
     transactional: the agent is kept only when both ``RegisterAgent`` and ``RequestDefaultAgent``
     succeeded, otherwise it is unregistered and unexported again and the error propagates.
   * SCANNING (30 s) for the name ``VWCAMPER``. A unit BlueZ already knows and currently hears (a
     ``Device1`` with that name and an ``RSSI``) is adopted directly, since BlueZ raises no new
     discovery callback for it while another client keeps discovery running. When our scan stops
     and ``Adapter1.Discovering`` is still true, the snapshot reports ``radio_busy`` (another client
     is scanning, so a new LE connect will likely fail with HCI 0x3e) and the wizard shows a hint.
   * CONNECTING (20 s). Every step shares one deadline, 18 s after the margins. **Bond probe
     first:** when BlueZ already holds a bond, a bounded connect plus the auth-gated ``1004`` read
     (at most 5 s) tells a working bond from a stale one. A **working bond is kept**: its link is
     reused and PAIRING reports success at once without pairing again. The bond is dropped only
     when the probe **proves** the key stale — an authentication-class failure ("PIN or Key
     Missing", HCI 0x05/0x06, ATT 0x05/0x0F, ``NotPermitted``/``NotAuthorized``) or a probe that
     timed out after ``Device1.Connected`` was seen true (the link came up but the read never
     completed). Then ``remove_bond(clear_cache=False)`` removes the BlueZ device object and bond
     but **keeps** ``pairing.json`` (the identity does not change), and the unit is re-discovered
     under its current address (at most 5 s) before a fresh connect, which always keeps at least
     8 s. Any other probe or connect failure (an asleep or out-of-range unit, HCI 0x3e on a busy
     radio, no budget left) is ``EV_CONNECT_FAIL`` with the bond **and** ``pairing.json`` kept.
   * ``EV_CONNECT_FAIL`` and ``EV_PAIR_FAIL`` retry from a fresh scan, up to 3 attempts in all,
     then ERROR ``connect_failed`` / ``pairing_failed``. Any state timeout leads to ERROR
     ``timeout``. ``cancel`` returns to IDLE.
   * PAIRING (15 s): ``Device1.Pair()`` → WAITING_PASSKEY (60 s) until the user types the 6-digit
     passkey the unit displays → VERIFYING (10 s): ``1001`` + ``1004`` must read, and at least one
     readable char → BONDED: cache the identity address in ``pairing.json`` (mode 0600) and
     retarget the running daemon live.
   * **Stale-bond recovery at Pair() (#200)**: when ``Pair()`` still fails with
     ``org.bluez.Error.AlreadyExists``, drop the bond the same way (``_drop_bond_and_rediscover()``,
     cache kept, re-discovery bounded by the PAIRING budget) and retry ``Pair()`` exactly once. Any
     other error propagates unchanged.
   * **Flow end** (BONDED, ERROR, or IDLE after cancel/reset): the transport's ``aclose()``
     releases the wizard's own link (at most 5 s) so ``serve`` can read right after BONDED,
     unregisters the agent and drops the D-Bus connection. A late result from an abandoned
     attempt neither emits an event nor keeps a link.
   * Only the explicit ``reset`` (``ACT_REMOVE_BOND``) clears ``pairing.json``.

.. mermaid::

    sequenceDiagram
        participant W as User (web wizard)
        participant C as calictl (serve, runner, transport)
        participant B as BlueZ
        participant U as Camper unit
        W->>C: POST /api/pairing start
        C->>C: park session supervisor, mark start pending, poll loop skips
        C->>C: take the _ble lock after any in-flight poll, release it once scanning
        C->>B: register Agent1 KeyboardOnly and request default agent (before any link)
        C->>B: start scan (SCANNING, 30 s)
        U-->>B: advertises VWCAMPER (rotating private address)
        B-->>C: device found (or an already-known device with a current RSSI is adopted)
        C->>B: stop scan, check Adapter1.Discovering (true means radio_busy)
        opt BlueZ already holds a bond (CONNECTING, 20 s budget)
            C->>U: bond probe, connect and read 1004 (up to 5 s)
            alt read OK
                C->>C: keep the bond and its link, skip connect and Pair(), go to VERIFYING
            else auth-class failure, or link up but the read timed out
                C->>B: remove_bond with clear_cache False (Adapter1.RemoveDevice)
                C->>B: re-discover the unit (up to 5 s), pairing.json kept
            else unit asleep, busy radio, or no budget left
                C->>C: EV_CONNECT_FAIL, bond and pairing.json kept, retry from scan
            end
        end
        C->>U: connect (no bond, or after the drop, at least 8 s left)
        C->>B: Device1.Pair() (PAIRING, 15 s)
        opt Pair() fails with AlreadyExists
            C->>B: drop the bond, re-discover, retry Pair() exactly once
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
        C->>U: release the wizard link (flow end, up to 5 s)
        C->>B: unregister the agent, drop the D-Bus connection

**Evidence.** The state machine, runner and transport logic are unit-tested
(``tests/vectors/pairing.json``, ``tests/test_pairing_runner.py``,
``tests/test_pairing_bluez_transport.py``). On every pull request the real runner and state machine
pair with the Bumble fake unit (:py:mod:`tools.fake_unit_peripheral`) over a Bumble ``LocalLink``
(``tests/test_pairing_link.py``). The ``pairing-real-stack`` CI job runs calictl's real
``BluezTransport`` against real BlueZ and ``bluetoothd`` inside a VM (``tests/realstack/``): agent,
scan, connect, Pair, verify, persist, link release at flow end, the stale-bond probe and removal,
an asleep unit keeping its bond, and ``radio_busy``. That job is not yet a required check. The fake
unit's pairing behaviour was cross-checked against the vendor app on 2026-09-27 (`protocol-crosscheck-applab.md
<https://ckeller42.github.io/open-california/business-logic/protocol-crosscheck-applab.html>`_,
"Pairing"). The one live contact with the real unit was a re-pair at the van on 2026-09-18, which
hit ``AlreadyExists`` from a stale local bond left by a unit-side Bluetooth reset and motivated
#200. CI does **not** cover the real unit's radio behaviour (RSSI jitter, advertising policy and
deep sleep, WiFi coexistence on buspi, other clients starving LE connects); the full live unbond
and re-pair is still open (#157). Details: `guided-pairing.md
<https://ckeller42.github.io/open-california/business-logic/guided-pairing.html>`_; owner guide:
:doc:`howto-pair-your-camper`; test layers: :doc:`simulation-and-testing`.

Notifications
-------------

.. spec:: Subscribe, then consume pushed state notifications
   :id: S_SEQ_NOTIFY
   :status: live-verified
   :links: R_READ_HEARTBEAT_REFRESH, R_READ_LAST_FRAME_WINS, R_CAMPING_OBSERVER, R_VEHICLE_1004, R_LIGHT_CONFIG_LATCH

   **Contract.** Subscribing is part of every handshake, and pushes are a real ``calictl`` data
   path. They are never discarded:

   * ``_subscribe_all`` sinks every payload, keyed by char UUID. It also calls an ``on_push`` hook
     when one is given.
   * ``read_all`` (per-op and persistent) follows the app: subscribe, then **read every char**,
     water ``1302`` included. The last frame wins: the read replaces the subscribe-time push, and
     a push that lands after the read replaces the read (``R_READ_LAST_FRAME_WINS``; lighting is
     excluded, its config frames are latched by ``serve``). No char is served from the push cache
     instead of a read, so a subscribe-time value can never pin a char.
   * On the persistent session, the daemon's ``on_push`` is
     :py:meth:`calictl.serve.Server._on_push`. It hands every push to
     :py:meth:`calictl.observer.CampingObserver.on_push`, which decodes ``1202`` camping, ``1004``
     vehicle and ``1102`` cooler pushes and **logs** changes (``camping-push``) without writing
     served state. It also **latches the lighting configuration** from ``1502`` pushes (wake-up
     Mode 20, door contact Mode 16 / PN 8, stored favourites Mode 12) into the cached lighting
     state, so a REQUEST_CONFIG reply of several frames in a row is not reduced to the last one.
     It does so only once a lighting decode is cached.
   * After a lighting write, a newer ``1502`` push (Mode-4 ramp) is the confirmation. It updates the
     served lighting state. The ``1502`` readback is only an echo.

.. mermaid::

    sequenceDiagram
        participant C as calictl
        participant U as Camper unit
        C->>U: write CCCD=0100 on each status char (subscribe-all)
        U-->>C: one push per char with its current value, right after its CCCD write
        Note over C: sink stores it by char UUID, then calictl reads each char and the read wins
        opt water in use (water system powered)
            U-->>C: notify 1302 on a measured change, replaces the earlier read
        end
        opt camping, ignition or cooler change (persistent session only)
            U-->>C: notify 1202 / 1004 / 1102
            Note over C: observer on_push decodes and logs camping-push, passive
        end
        opt unit reports its lighting config (after a REQUEST_CONFIG, or on its own)
            U-->>C: notify 1502 Mode 12, Mode 20 or Mode 16 PN 8 frames
            Note over C: Server on_push latches favourites, wake-up and door contact into the cached lighting state
        end
        opt after an applied lighting SET
            U-->>C: notify 1502 Mode-4 ramp frames (real brightness stepping to target)
            Note over C: lighting confirm, served lighting state updated from the push
        end

**Evidence.** The first real-unit trace (buspi, 2026-09-16) showed each of the 12 subscribed chars
notifying **once, right after its CCCD write**, and no periodic stream in 150 s. That contradicted
an older "``1602`` ~3×/s" note. Event-driven pushes were observed for ``1502`` (Mode-4 ramp,
2026-08-16) and ``1302`` (water, during use). The app subscribes ``1202``. For water it subscribes ``1302``
and then reads it, and the read and any push go to the same decoder (VM ``qg/b``), so the last
frame wins (decompile 2026-10-07, enigma ``38a0d6b``). Live ``1202``/``1004`` push *changes* on
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
     daemon's ``on_push`` (camping observer plus the lighting config latch, see :need:`S_SEQ_NOTIFY`),
     start the ``1003`` heartbeat (``CALICTL_HEARTBEAT_PERIOD_S``, default
     0.6 s) and wait ``CALICTL_HEARTBEAT_WARMUP_S`` (2 s). The heartbeat then ticks for as long as
     the session is held.
   * **Held**: polls and commands run over it. Writes skip the handshake and the 3 s arm delay
     (``arm=False``), so they land in well under a second. A command first waits up to
     ``CALICTL_SESSION_WAIT_S`` (6 s) for the session to come up instead of racing it cold. A roof
     move is the exception: it neither nudges nor waits (see Roof).
   * **Idle**: close the session under the ``_ble`` lock. (The old reason, "so the phone app gets
     the single slot", is disproven: the unit served the phone app, buspi and the satellite at once,
     CAPTURE 2026-10-10.)
     Polls fall back to brief cold per-op reads. While the supervisor is mid-connect the poll loop
     naps 2 s instead of racing it with a cold connect.
   * **Unreachable**: back off 5 / 10 / 30 / 60 s (capped). After 4 consecutive failures the state
     is ``asleep``. A command resets the backoff and reconnects immediately (keep-warm nudge).
   * **Roof**: a roof move or STOP runs **inside** an already-live session, with its ``1003``
     heartbeat still ticking, as the app's does (#235). It never warms the session first: a roof
     command skips the keep-warm nudge and the ``CALICTL_SESSION_WAIT_S`` wait, and with no
     session up it opens its own connection (heartbeat on). After a move, the daemon nudges the
     supervisor, which reconnects once the lock is free. Guided pairing parks the session with
     ``set_mode("disconnect")``.

.. mermaid::

    sequenceDiagram
        participant W as Web UI
        participant S as serve (supervisor, poll, commands)
        participant U as Camper unit
        W->>S: GET /api/state every ~2 s (marks UI active)
        S->>U: connect, read 1001 and 1004, subscribe-all with the daemon on_push
        loop while held, calictl ~0.6 s (real app about 0.78 s)
            S-)U: write 1003 = N, N+1, N+2 (monotonic +1)
        end
        W->>S: POST /api/command
        S->>U: control frame over the live session (no handshake, no 3 s arm delay)
        opt roof move or STOP
            S->>U: roof frames over the live session, the 1003 heartbeat keeps ticking
        end
        Note over W,S: no UI activity for CALICTL_UI_IDLE_S (25 s) or a manual Disconnect
        S->>U: close the session under the _ble lock
        Note over S,U: idle polls fall back to cold per-op read_all
        Note over S,U: connect failures back off 5, 10, 30, 60 s, asleep after 4, a command nudges now

**Evidence.** On buspi (2026-09-16 trace) the session repeatedly came up on a web nudge and was
released for UI idleness (``persistent session released (web UI idle)``). No unit-side drop was
found while the heartbeat ticked. The 2026-07-13 stability spike held the link 180 s with 100 %
uptime and 0 drops. On 2026-10-10 the parked, locked unit held buspi's persistent session for
3 min (a viewer open, 11:45) while the phone app held its own link and the satellite cycled.
The roof running inside the live session (#235, replacing the #198 handover),
and the roof command skipping the session warm-up, are covered by tests only; the roof itself has
never
moved under ``calictl`` (see :need:`S_SEQ_ROOF`). Design notes:
`protocol-crosscheck-applab.md
<https://ckeller42.github.io/open-california/business-logic/protocol-crosscheck-applab.html>`_.

Heartbeat-armed control write
-----------------------------

.. spec:: Heartbeat-armed control write
   :id: S_SEQ_ACTUATE
   :status: live-verified
   :links: R_ACTUATE_ARM, R_POST_WRITE_CHECK

   **Contract.** The unit honours a control write only while a **+1 monotonic 4-byte big-endian
   counter ticks on** ``1003`` (the arm gate, issue #2). There is no ignition, mode or enable gate
   (the roof is the one exception). :py:meth:`calictl.device.CamperDevice.actuate` (cold path,
   CLI) does the following:

   * Connect, then handshake (``1001`` + ``1004`` reads, subscribe-all).
   * Start the heartbeat (``CALICTL_HEARTBEAT_PERIOD_S``, default **0.6 s**, from a fixed start
     ``0x00100000``) and wait ``CALICTL_ARM_DELAY_S`` (**3.0 s**).
   * Write the **full-packet** frame with response. For the five functions the satellite also
     carries (cooler, camping mode, lighting, air heater, energy), every untargeted field holds the
     app's own leave-unchanged value (2-bit fields ``3``, lighting zones ``14``, and so on). Nothing
     is copied from the unit's current state. The one exception is the cooler's ``night_on`` /
     ``night_off``, which carry the live schedule because the unit takes those hour bytes literally
     (see :need:`S_SEQ_COOLER`). The builders of the functions not installed on this van (roof A/C,
     stairs, living-room heater) still carry the current state for their untargeted fields.
   * Optionally write a ``follow`` frame ``CALICTL_FOLLOW_DELAY_S`` (0.3 s) later (lighting only).
   * If ``verify``, wait ``CALICTL_SETTLE_S`` (2.5 s) and read the state char. Then stop the
     heartbeat and disconnect.

   The persistent-session path (:need:`S_SEQ_SESSION`) writes immediately over its already-ticking
   heartbeat, with no handshake and no arm delay. A verified write still waits ``SETTLE_S`` before
   its readback. ``calictl`` sends no trailing neutral frame, except the lighting flush.

   The daemon wraps either path (``Server.on_command``). Writes are refused outright while the
   daemon runs read-only (the default, ``--enable-writes`` lifts it). A command first passes
   ``control.command_precondition``, then is built by ``control.build``. After the write, the
   readback is compared with the target field by ``postcheck.set_check`` and reported as applied,
   not applied, or unknown. A readback is a write-through echo, so for lighting the daemon waits
   for the unit's own ``1502`` push instead (:need:`S_SEQ_LIGHT_COMMIT`).

.. mermaid::

    sequenceDiagram
        participant C as calictl (buspi)
        participant U as Camper unit
        participant A as App (reference only)
        C->>U: connect, read 1001 and 1004, subscribe-all (see S_SEQ_CONNECT)
        loop calictl ~0.6 s across the write window only (real app about 0.78 s, continuous while in the foreground)
            C-)U: write 1003 = N, N+1, N+2 (monotonic +1)
        end
        Note over C,U: calictl waits ARM_DELAY_S (3.0 s) after the first beat before writing
        Note over U: armed, control writes are honoured
        C->>U: write control char = SET frame (full-packet, untargeted fields at leave-unchanged, write with response)
        A->>U: 500 ms later a neutral all-sentinel frame (cooler ff771e3e1f1f, heater 3f7b007f1f3f, camping ff)
        C->>U: after SETTLE_S (2.5 s) read the state char (verify, daemon runs set_check on it)
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

**CAPTURE 2026-10-10** (real app on the real unit, HCI snoop, evidence-ledger 2026-10-10). The app
writes ``1003`` with write-request, a 4-byte BE counter ``+1`` per write, every **0.76–0.79 s**, for
as long as it is in the foreground. The value is **not 0 at connect**: values such as
``0x00049363…`` and, later, ``0x00061b62`` were seen. This contradicts the "seed 0" decompile
reading above and agrees with the later call-stack reading of a random seed in 1–1 000 000 and a
750–850 ms period (`protocol-alignment.md
<https://ckeller42.github.io/open-california/business-logic/protocol-alignment.html>`_). The two
values alone do not show whether the counter continues across links or is reseeded per link.
When the app goes to the background, the phone disconnects (HCI ``0x13`` from the phone). In the
foreground, with the van parked and locked, the unit did **not** drop the app's link: it was held
46 min, and 93 s of that while locked. Every non-motor app write was byte-identical to
``control.build``. Each cooler write was followed ~500 ms later by ``ff771e3e1f1f``, each
camping-mode write by ``ff``, each lighting write by the commit ``0e00…``.

Cooler command — the app's frame
--------------------------------

.. spec:: Cooler command: the app-faithful frame, then the applied-check
   :id: S_SEQ_COOLER
   :status: mock-only
   :links: R_COOLER_APP_FRAMES, R_ONOFF_STRICT, R_ACTUATE_ARM, R_POST_WRITE_CHECK, R_PERSISTENT_SESSION, R_APP_FIDELITY

   **Contract.** A cooler command (``power``, ``level``, ``mode``, ``timer_set``, ``timer_start``,
   ``timer_cancel``, ``night_on``, ``night_off``) reaches the unit the same way from the web UI, the
   CLI and Home Assistant (:need:`S_SEQ_MQTT`). :py:meth:`calictl.serve.Server.on_command` does the
   following:

   * Refuse everything while the daemon is read-only.
   * **Gate** with ``control.command_precondition`` before anything is built or woken. An on/off
     value must be ``on/off``, ``true/false`` or ``1/0`` (``R_ONOFF_STRICT``). ``timer_set`` and
     ``timer_start`` are refused while the fridge is **on**. ``mode``, ``night_on`` and
     ``night_off`` are refused while it is **off** (the app greys those rows). ``night_on`` and
     ``night_off`` are also refused while no cooler state is known (ruling R4).
   * Claim intent: clear a manual Disconnect, mark the UI active, nudge the supervisor, and wait up
     to ``CALICTL_SESSION_WAIT_S`` (6 s) for a live session. Then take the ``_ble`` lock and read
     the cooler state once if the cache is cold. An unreachable unit skips the command.
   * **Build** with :py:func:`calictl.control.build`. Only the targeted field is set. **Every
     other control field sits at its dictionary default**, the app's leave-unchanged value: 2-bit
     fields ``3``, ``Level`` and ``Mode`` 7, ``TimerHour``/``TimerMin`` 30/62, night hours 31
     (ruling R1). The neutral frame is ``ff771e3e1f1f``. Power on is ``fd771e3e1f1f``, off
     ``fc771e3e1f1f``, level 5 ``ff751e3e1f1f``, quiet mode ``ff271e3e1f1f``, timer start
     ``f7771e3e1f1f``. All are byte-identical to the app's recording. The state is not copied into
     the frame. The only exception is ``night_on``/``night_off``, which carry the current schedule,
     because the unit takes those hour bytes literally and a default would clobber it (#99).
   * ``protocol.encode`` checks width and ``CONTROL_RANGES`` (``State`` admits ``0``, ``1`` and the
     sentinel ``3``).
   * **Write**: over the live session at once, with the heartbeat already ticking. With no session,
     over the cold armed path (:need:`S_SEQ_ACTUATE`: handshake, heartbeat, 3 s arm delay). The
     cooler has no follow frame.
   * **Check**: after ``SETTLE_S`` (2.5 s) read ``1102``, cache the decode, and compare the targeted
     field with ``postcheck.set_check``. The result is applied, not applied, or unknown. The
     readback is an echo, not proof of cooling.

.. mermaid::

    sequenceDiagram
        participant H as Web UI, CLI or Home Assistant
        participant S as serve (on_command)
        participant U as Cooler (1101 / 1102)
        participant A as App (reference only)
        H->>S: set cooler level 5
        S->>S: refuse when read-only, then command_precondition (on/off strict, fridge on or off rules)
        S->>S: claim intent, nudge the session, wait up to 6 s for it, take the _ble lock
        S->>S: build ff751e3e1f1f (Level 5, every other field at its leave-unchanged default)
        alt live session
            S->>U: write 1101 at once (heartbeat already ticking, no arm delay)
        else no session
            S->>U: connect, handshake, heartbeat, wait 3 s, then write 1101
        end
        A->>U: 500 ms later neutral frame ff771e3e1f1f (calictl sends none)
        Note over S,U: wait SETTLE_S (2.5 s)
        S->>U: read 1102
        S->>S: cache the decode, set_check compares Level with 5 (applied, not applied or unknown)
        S-->>H: applied result

**Evidence.** The frames are **APP-RECORDED** (``tests/vectors/app/cooler.jsonl``, replayed
byte for byte by ``tests/test_app_recordings.py``) and held by ``tests/test_calictl.py``. Before
ruling R1 (2026-10-06) calictl re-asserted the unit's current ``State``/``Mode``/``Level`` and
schedule in every untargeted field. **That older state-carry frame is the one live-verified on the
unit** (2026-07-07 power, and the schedule write of 2026-08-26 that showed the hour bytes are
taken literally). Until 2026-10-10 the app-faithful frames, ``State=3`` in the level, mode and
timer frames, had only run against the mock. The 2026-07-05 ``0x0E`` drop of ``State=3`` predates the heartbeat
(:need:`S_SEQ_REJECT`), so the first live level or mode write is the check (van check #230).
**CAPTURE 2026-10-10:** the real app wrote these app-faithful frames to the real unit (level
``ff74…`` / ``ff73…``, quiet ``ff27…`` / ``ff47…`` / ``ff07…``, timer ``f777…`` / ``df77…``, power
``fd77…`` / ``fc77…``), byte-identical to ``control.build``. The unit accepted every one (no
``0x0E``) and the level writes actuated. So the ``State=3`` frame works on the unit; ``calictl`` itself has still not sent it
(status stays ``mock-only``, evidence-ledger 2026-10-10). See
`DECISIONS.md <https://ckeller42.github.io/open-california/business-logic/DECISIONS.html>`_ (ruling
R1, R3, R4) and `control-and-actuation.md §5
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
   * No lighting write is preceded by REQUEST_CONFIG ``0d0c000000000000eeeeeeeeeeeeeeee``. It is the
     app's screen-open config pull, retired as a write preamble on 2026-08-29. The daemon sends it
     for one other purpose only: to read the wake-up configuration before a wake-up edit whose
     config is unknown (ruling R5, :need:`S_SEQ_WAKEUP`). CLI paths never send it.

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
        Note over C: calictl needs none of that to actuate (it pulls the config only for a wake-up edit)
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

**CAPTURE 2026-10-10** (real app on the real unit, evidence-ledger 2026-10-10). Opening the
Lighting screen writes REQUEST_CONFIG ``0d0c…`` and the commit, as drawn above. The app's group
switches (Reading, Kitchen, Pop-up roof, Exterior) each write **one** ``SET_BRIGHTNESS`` frame
``0904…`` (Mode 4, ProfileNumber 9) with every zone of the group at ``11`` (DEFAULT) for on or
``0`` for off, then the commit. The camping screen's "sliding door" toggle writes the lighting
door-contact frame (``1501``, ProfileNumber 8, ``0810…01`` / ``…00``), not ``1201``. All frames
byte-identical to ``control.build`` where ``calictl`` has a builder.

Wake-up light and door contact — config edit
--------------------------------------------

.. spec:: Wake-up and door-contact edit over the unit's own config
   :id: S_SEQ_WAKEUP
   :status: mock-only
   :links: R_LIGHT_WAKEUP, R_LIGHT_DOOR_CONTACT, R_LIGHT_CONFIG_LATCH, R_LIGHT_CONFIG_PULL, R_LIGHT_COMMIT

   **Contract.** The wake-up light, the sliding-door light and the stored favourites are
   *configuration the unit owns*. They are readable only from its own ``1502`` frames: Mode 20
   (wake-up time and packed light value), Mode 16 with ProfileNumber 8 (door contact) and the
   Mode 12 reply (stored-favourite bits).

   * **Latch** (:py:func:`calictl.semantics.lighting_config`): the config is kept until a newer
     such frame replaces it. It is latched from the poll's decoded frames and from the live
     session's pushes (:py:meth:`calictl.serve.Server._on_push`), and **only** from the unit's own
     frames, never from calictl's own write. An ACKed write that the unit did not apply must stay
     unknown (ruling R4).
   * **Edit.** ``wakeup HH:MM [areas] [brightness] [ramp] [on|off]`` is merged over the latched
     config (:py:func:`calictl.control.wakeup_request`), else over the app's defaults. The enabled
     bit is the one the unit reported, and only ``on``/``off`` changes it (ruling R3). The frame is
     the app's Mode 20: ProfileNumber 14, ``Timestamp`` = the next local ``HH:MM`` packed as if UTC,
     the packed ``LightValue``, zones unchanged. It is followed by the ``0e00…`` flush, like every
     lighting write, and needs no arming on an awake unit.
   * **Unknown config (ruling R5).** An edit that gives no ``on``/``off`` while no wake-up config
     has been reported would silently disarm the light, so the gate returns ``WAKEUP_UNKNOWN``. The
     daemon then does what the app's lighting page does
     (:py:meth:`calictl.serve.Server._pull_lighting_config`): on the **live session** it writes the
     REQUEST_CONFIG ``0d0c…`` (Mode 12, ProfileNumber 13) plus the flush, and waits up to
     ``CALICTL_CONFIG_PULL_S`` (2 s) for the wake-up frame to be latched, then re-runs the gate.
     With no live session nothing is sent. If the config is still unknown the edit is refused and
     **nothing is written**, the web API answers ``refused``. The CLI has no latch and needs an
     explicit ``on`` or ``off``.
   * **Door contact.** ``door_contact on|off`` is a SET_PROFILE frame, ProfileNumber 8, LightValue
     1 or 0. It needs no pull.
   * **Confirm.** The daemon waits up to ``CALICTL_FAST_CONFIRM_S`` (1.2 s) for a ``1502`` push
     newer than the pre-write snapshot. For ``wakeup`` and ``door_contact`` that push only echoes
     the config, so the cached ProfileNumber and Mode stay as they were. The latch is refreshed
     from the unit's frame.

.. mermaid::

    sequenceDiagram
        participant W as Web UI or Home Assistant
        participant S as serve
        participant U as Lighting (1501 / 1502)
        participant A as App (reference only)
        A->>U: on screen open REQUEST_CONFIG 0d0c plus flush 0e00
        U--)A: 1502 config frames, Mode 12 favourites, Mode 20 wake-up, Mode 16 PN 8 door
        W->>S: set lighting wakeup 07:00 (no on or off given)
        S->>S: command_precondition finds no unit-reported wake-up config, so WAKEUP_UNKNOWN
        opt live session is up
            S->>U: REQUEST_CONFIG 0d0c (Mode 12, PN 13) then flush 0e00
            U--)S: 1502 Mode 12, Mode 20 and Mode 16 PN 8 frames
            Note over S: Server on_push latches the config from the unit's own frames only
            S->>S: wait up to 2 s for the wake-up frame, then run the gate again
        end
        alt config still unknown
            S-->>W: refused, nothing written
        else config known
            S->>S: wakeup_request merges 07:00 over the latched config, enabled bit as the unit reported it
            S->>U: Mode 20 frame (PN 14, Timestamp next 07:00 packed as UTC, LightValue)
            S->>U: 0.3 s later flush 0e00
            U--)S: 1502 Mode 20 echo within about 1.2 s
            Note over S: confirmed from the push, latch refreshed, cached ProfileNumber and Mode unchanged
            S-->>W: applied
        end
        W->>S: set lighting door_contact on
        S->>U: SET_PROFILE frame PN 8, LightValue 1, then flush 0e00
        U--)S: 1502 Mode 16 PN 8 echo latches DoorContact

**Evidence.** The wake-up time edit and the on/off switch, and the door-contact switch, are
**APP-RECORDED** and reproduced byte for byte (``tests/vectors/app/lighting-wakeup.jsonl``,
``door-contact.jsonl``). The "edit keeps the unit's enabled bit" rule (R3) and the pull before an
unknown-config edit (R5) were settled from the 2026-10-06 decompile cross-check (``si/h.j``
takes each field from the edit, else from the config only the Mode-20 decode writes). The app
waits up to 2000 ms for the reply, which is where ``CALICTL_CONFIG_PULL_S`` comes from. The daemon
side (the pull, the latch, the refusal when the config stays unknown) is covered by
``tests/test_web_serve.py`` against a faked session. No wake-up edit has been run against the real
unit, and the real unit's Mode-20 echo within ~3 s is still to be confirmed at the van. See
`evidence-ledger.md
<https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html>`_ and
`lighting-energy-water-sat-roof.md
<https://ckeller42.github.io/open-california/business-logic/lighting-energy-water-sat-roof.html>`_.

Roof actuation — press-and-hold, SafetyCounter-gated
----------------------------------------------------

.. spec:: Roof press-and-hold move stream on the roof screen's SafetyCounter stream
   :id: S_SEQ_ROOF
   :status: mock-only
   :links: R_ROOF_ACTUATE, R_ROOF_VIEW_STREAM, R_SESSION_SUPERVISOR, R_ROOF_ALERT

   **Contract.** SAFETY-SENSITIVE. The unit itself requires ignition ON. The daemon refuses a
   move up front when ``control.command_precondition`` blocks it (a blocking ``InfoPopUp`` alert,
   or ``Position`` 15 = error). STOP is never gated. ``calictl`` follows the real app's roof
   screen (owner decision 2026-10-10, CAPTURE 2026-10-10):

   * **Roof view** (``R_ROOF_VIEW_STREAM``): while the web UI's roof page is open it posts
     ``POST /api/roof {"action":"view"}`` (refreshed every ~5 s; ``leave`` when the page is left
     or the tab hidden). The daemon claims the persistent session and, on it, runs a
     :py:class:`calictl.device.RoofStream`: STOP frames ``[0x00][SafetyCounter]`` to ``1401``
     every ``CALICTL_ROOF_PERIOD_S`` (0.5 s), the counter +1 per frame from a random seed in
     1..1 000 000 (big-endian uint32), with the session's ``1003`` heartbeat ticking. With no
     refresh for ``CALICTL_ROOF_VIEW_LAPSE_S`` (15 s) the stream ends; a move in flight holds the
     view open, and ``leave`` ends a held move with STOP. Read-only: refused.
   * **Press**: the move switches the SAME stream to the direction byte (open ``0x01`` / close
     ``0x04``) at once, the first move frame **repeating the current counter** (the last STOP's),
     then +1 per tick. The unit has already validated that counter, so it does not withhold the
     motor (the real app's motor started ~1.2 s after the press). **Without a view** (API, CLI,
     HA, or a press before the session is up) the move starts a stream of its own at the press —
     a fresh counter, so the unit withholds the motor ~3 s — and closes it after the final STOP.
   * **Connection**: a roof command does **not** warm the persistent session (no keep-warm
     nudge, no ``CALICTL_SESSION_WAIT_S`` wait); the view does. Under the ``_ble`` lock, a live
     session carries the move (:py:meth:`calictl.device.PersistentSession.actuate_roof`): no
     second connection. With no session up, ``actuate_roof`` opens its own.
   * **Arm = the app's**: the ``1003`` heartbeat **ticks during the move**, as the app's
     session-global heartbeat does (decompile ``zf/d:183``, ``d2/s:795-802``, ``mj/d:247``,
     ``c/i:349-367``, #235; **CAPTURE 2026-10-10**: the real app's heartbeat ran through a full
     open and close on the real unit). A live session's heartbeat is already running; an own connection
     replays the handshake (``1001`` + ``1004`` reads, subscribe-all) and starts it. There is
     **no** ``ARM_DELAY_S`` **pre-arm**, so the counter streams immediately (#150).
   * At 3 s, read ``1402`` once. If ``SafetyCounterValid`` (bit 7) is still clear, abort (the app's
     dead-man). Poll ``1402`` every ``CALICTL_ROOF_LIMIT_POLL_S`` (1 s) and cease at the end of
     travel: ``InfoPopUp`` 8 (the unit's ``2308``, about 1 s before the final Position, where the
     app stops too) or the direction's limit ``Position`` (open ``1``, closed ``0``/``14``). Also
     cease on release (``stop_event``) or at ``CALICTL_ROOF_MAX_TRAVEL_S`` (30 s; the real
     travel is ~28 s open / ~23 s close).
   * **Always** switch to STOP ``[0x00][counter]`` (again repeating the current counter), which is
     best-effort on a dropped link. Then wait ``CALICTL_SETTLE_S`` (2.5 s) and read ``1402``. On
     the view's stream the STOP frames go on; an own stream ends, and an own connection
     disconnects. A live session stays up.
   * **Release** is lock-free: it sets ``stop_event``. Each press gets a fresh stop token
     **before** it waits for the ``_ble`` lock, so a release that arrives while the press still
     queues behind a poll or another write cancels the move before it starts. A new press stops an
     earlier one. The interval wait is interruptible, so STOP follows a release at once. A
     cancelled move (shutdown) still attempts STOP.
   * A **standalone STOP** (nothing in flight) is a zero-length roof move: on the view's stream
     (already STOP), or over the live session, or else an own connection with the heartbeat on,
     one STOP with a live counter. It never waits ``ARM_DELAY_S``. The web UI ignores a move press
     within **1000 ms** of the previous move start (``ROOF_REPRESS_MS``; it only matters without a
     view). STOP is never debounced.

.. mermaid::

    sequenceDiagram
        participant W as web UI (roof page)
        participant S as serve
        participant C as calictl (RoofStream, actuate_roof)
        participant U as Roof (1401 / state 1402)
        W->>S: POST /api/roof view (every 5 s while open)
        S->>S: claim and nudge the persistent session
        loop the whole view, calictl ~0.6 s (the app ticks it too)
            C-)U: write 1003 = N, N+1 (session heartbeat)
        end
        loop while the page is open and nothing is pressed
            C->>U: STOP frame [0x00] plus SafetyCounter every 0.5 s, +1 per frame
        end
        Note right of U: counter validated, 1402 bit 7 set, no motor withhold left
        W->>S: press and HOLD open or close (path-clear confirm)
        C->>U: move frame [0x01 open / 0x04 close] at once, SAME counter as the last STOP
        loop every 0.5 s while held, +1 per frame
            C->>U: move frame plus SafetyCounter
            C->>U: every 1 s read 1402, cease at InfoPopUp 8 or the limit Position
        end
        U--)C: 1402 pushes, 030c then 230c, at the end 2308 then 1300 open or 0300 closed
        W->>S: release (lock-free STOP)
        C->>U: STOP frame [0x00] at once, SAME counter as the last move frame
        Note right of U: halts, a mid-travel release reports 2303 then 2300
        loop the STOP stream goes on while the page stays open
            C->>U: STOP frame plus SafetyCounter every 0.5 s
        end
        W->>S: POST /api/roof leave (or no refresh for 15 s)
        S->>C: the stream ends, the session stays up

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
``R_ROOF_ALERT``). **CAPTURE 2026-10-10 (real app, real unit)**, a full open + close: the first
open press after the page opened answers ``0302`` (InfoPopUp 2, the app shows its pre-open safety
checklist, no motion; after OK a fresh press moves), then ``030c`` → ``230c`` while moving
(InfoPopUp 12), ``2308`` at end of travel (8), then ``1300`` (open) or ``0300`` (closed); a release
mid-travel gives ``2303`` (3) then ``2300``. These are prompt/progress codes, not alerts: none of
them blocks a move or STOP. **APP-OBSERVED 2026-09-16** (``tools/applab``): the roof page pre-streams
``[0x00][counter]`` from the moment it opens. While a button is held the rate rises to ~8 frames/s,
with four consecutive frames carrying the same counter. Without terminal 15 the page hides its
controls ("Switch on the ignition"). The no-pre-arm stream is from the 2026-08-30 decompile
cross-check (#150). The heartbeat during the move follows the app (decompile + the ``roof-hold``
recording, #235); it replaced the #198 no-heartbeat handover. **Mock-tested only**: ``calictl`` has never driven
the real motor.

**CAPTURE 2026-10-10** (the real app on the real unit, the owner's finger on the button, ignition
on, stationary, a full open and a full close; evidence-ledger 2026-10-10 roof row):

* **Heartbeat.** The app's ``1003`` heartbeat ticks through the whole move. This **confirms** the
  #235 contract above (``calictl``'s roof path ticks it too).
* **Stream.** With the roof screen open and nothing pressed, the app streams STOP frames
  ``00 <counter>``, one per ~500 ms tick, +1 each (0.4997 s per increment over the whole
  capture). A press switches the direction byte to ``01`` (open) or ``04`` (close) at once and
  continues the **same** counter: the first move frame repeats the last STOP's value, and the
  first STOP after a release repeats the last move frame's (26 of 26 direction changes). While
  held, a second frame repeats the current value about once a second, so frames arrive every
  ~0.1–0.5 s — exactly the decompiled two-timer model (the 500 ms counter timer plus the 1000 ms
  ``ig/c`` re-send). This **contradicts** the 2026-09-16 lab reading above (about 8 frames/s
  while held, four frames per counter value). ``calictl``'s roof view follows the stream and the
  repeat-on-change (``R_ROOF_VIEW_STREAM``); it omits the 1000 ms re-send.
* **First press.** The first open press after the screen opened got ``1402`` = ``0302``
  (closed, counter valid, InfoPopUp 2). The app showed its pre-open safety checklist dialog and the
  roof did not move. After OK, a fresh press moved it. (The InfoPopUp names are owned by
  ``alert-states.md``; only the wire codes are recorded here.)
* **Travel.** ``1402`` while moving: ``030c`` → ``230c`` (Position 2 = between, InfoPopUp 12). At
  the end of travel ``2308`` (InfoPopUp 8), then ``1300`` (Position 1, open) or ``0300``
  (Position 0, closed). A release mid-travel gave ``2303`` → ``2300``. Opening took ~28 s of hold,
  closing ~23 s.

See `lighting-energy-water-sat-roof.md (Roof)
<https://ckeller42.github.io/open-california/business-logic/lighting-energy-water-sat-roof.html>`_
and `alert-states.md
<https://ckeller42.github.io/open-california/business-logic/alert-states.html>`_.

Range validation and the 0x0E link drop
---------------------------------------

.. spec:: Out-of-range writes rejected build-side and by the unit (0x0E)
   :id: S_SEQ_REJECT
   :status: live-verified
   :links: R_CONTROL_INT_RANGE, R_ONOFF_STRICT

   **Contract.** No out-of-range value is ever written. There are two build-side guards:

   * The builders coerce operator values with ``_int_range`` (``R_CONTROL_INT_RANGE``), which
     raises a ``ValueError`` naming the field and the accepted range. ``control.build`` re-raises
     it as ``CommandError`` (a ``ValueError`` subclass), which the web API answers with HTTP 400.
   * :py:func:`calictl.protocol.encode` runs :py:func:`calictl.protocol.check_value` on every
     field: it must fit its width and any curated ``valid`` set (``overrides.CONTROL_RANGES``).
   * Before any builder runs, an on/off command whose value is not ``on/off``, ``true/false`` or
     ``1/0`` is refused (``R_ONOFF_STRICT``, ruling R3). It is never read as OFF.

   A raise or a refusal means the frame is never written. If a bad frame *does* reach the unit, it
   answers ATT ``0x0E`` and drops the link.

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
The cooler observation dates from 2026-07-05, **before** the ``1003`` heartbeat existed, and has not
been re-tested: since ruling R1 (2026-10-06) ``State=3`` is the app's own leave-unchanged value in
the cooler level, mode and timer frames, ``CONTROL_RANGES`` admits ``{0, 1, 3}``, and the first live
cooler write of that frame is the open van check (#230). On 2026-10-10 the real app sent that
frame (cooler level, quiet mode and timer, ``State=3``) to the real unit under its heartbeat and
the unit accepted it (CAPTURE, evidence-ledger 2026-10-10). A ``power`` command is always 0 or 1.
The app's state fields carry the same bounds (``sg.a(default, max)`` wrappers), so the app never
emits out-of-range values. Status ``live-verified`` refers to the on-device rejections. See
`DECISIONS.md <https://ckeller42.github.io/open-california/business-logic/DECISIONS.html>`_
(2026-07-07).

Fresh state read under heartbeat
--------------------------------

.. spec:: Read under a live heartbeat for fresh values
   :id: S_SEQ_READ
   :status: live-verified
   :links: R_READ_HEARTBEAT_REFRESH, R_READ_RETRY_SHARED, R_READ_LAST_FRAME_WINS, R_WATER_STALE_GUARD

   **Contract.** A cold per-op read (:py:meth:`calictl.device.CamperDevice.read_all`) follows the
   app's order, subscribe and then read, under the ``1003`` heartbeat that keeps the link up and
   refreshes the re-read chars:

   * Connect, then subscribe-all with a sink. There are no ``1001``/``1004`` reads on this path.
   * Start the heartbeat (0.6 s) and wait ``CALICTL_HEARTBEAT_WARMUP_S`` (2 s).
   * **Read every state char**, water ``1302`` included, in function order (3 tries, 0.8 s backoff,
     abort the cycle on a link drop; one shared policy for the per-op and the persistent path). Just
     before each read, the sink's current push for that char is remembered.
   * **Last frame wins** (``R_READ_LAST_FRAME_WINS``): the read replaces the subscribe-time push,
     and a push that arrived *after* a char's read replaces the read. Lighting is excluded, its
     config frames are latched by ``serve``. No char is served from the push cache instead of a
     read, so a subscribe-time value can never pin a char.
   * An optional wait for a fresh water push (``CALICTL_WATER_PUSH_WAIT_S``) is **disabled by
     default** (unvalidated). It keeps the same rule, a new push replaces the read.
   * Stop the heartbeat and disconnect.
   * The persistent session (:need:`S_SEQ_SESSION`) runs the same read loop over its live link, with
     no connect, subscribe or warm-up of its own.
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
        Note over U: heartbeat keeps the link up and refreshes re-read chars (1102, 1602, 1902, 1004)
        loop every state char in function order, water included
            Note over C: remember the sink value for this char
            C->>U: read the state char (3 tries, 0.8 s backoff)
            U-->>C: value, replaces the subscribe-time push
        end
        opt a push landed after a char was read (for example water 1302 in use)
            U-->>C: notify, the newer frame wins over the read
        end
        C->>U: disconnect

**Evidence.** Without a heartbeat the unit drops a link after ~15 s. With one, the link held 64 s
on-device (2026-07-09). Water is **measurement-gated**: the unit measures only while its water
system is powered. While parked, it pushed zero ``1302`` frames in 40 s, and a bare read returns
the stale latch. The 2026-07-09 "1 L → 11 L after a heartbeat" was correlation, not cause. The
earlier "water is push-only" rule (decompile 2026-07-14) was **dropped on 2026-10-07**: the app
subscribes ``1302`` and then *reads* it, and a read and a push go to the same decoder (VM ``qg/b``),
so the last frame wins. A persistent session used to serve the subscribe-time push forever, which
may have caused the parked "1 L". Whether the stale guard is still needed is open until a van trace
(#230). See `value-freshness.md
<https://ckeller42.github.io/open-california/business-logic/value-freshness.html>`_.

Daemon poll cycle
-----------------

.. spec:: One poll cycle: read, decode, guard, cache, fan out
   :id: S_SEQ_POLL
   :status: live-verified
   :links: R_READ_HEARTBEAT_REFRESH, R_READ_LAST_FRAME_WINS, R_WATER_STALE_GUARD, R_SERVE_STATE_CACHE, R_SESSION_SUPERVISOR, R_CAMPING_OBSERVER, R_FIRMWARE_DRIFT_CAPTURE, R_PLAUSIBILITY_ANCHORS, R_AUTO_CAMPER_CONTROLLER

   **Contract.** ``serve`` is the single BLE owner. Every ``interval`` seconds (30 s by default,
   shortened by the observer for a burst after an engine start) :py:meth:`calictl.serve.Server.poll`
   does the following, in this order:

   * **Skip while someone else owns the radio.** The loop naps 2 s while the supervisor is
     mid-connect. ``poll`` returns at once, logging once, while a guided pairing flow is pending or
     active (:need:`S_SEQ_PAIRING`), and checks again after taking the lock, because a pairing
     start can win the race for it.
   * **Read** every function under the ``asyncio.Lock`` (``_ble``, created inside the running
     loop). Over the live persistent session when one is up, else a cold per-op read
     (:need:`S_SEQ_READ`). A command takes the same lock, so a write never races a poll.
   * **Decode** each frame with ``protocol.decode`` (lighting also merges the latched config, see
     :need:`S_SEQ_WAKEUP`), **interpret** it with ``semantics.interpret`` and apply the firmware
     corrections.
   * **Guards** on the interpreted states. A change of firmware identity dumps a raw-frame snapshot
     (``R_FIRMWARE_DRIFT_CAPTURE``). Plausibility anchors flag a decode drift in ``_meta``
     (``R_PLAUSIBILITY_ANCHORS``). The **water stale-latch guard** compares fresh water with the
     last plausible reading (``_water_good``). A fresh drop to at most 1 L (``WATER_LATCH_MAX_L``,
     the observed latch value) while the grey tank is exactly frozen is the parked latch, so the last plausible reading is published instead and flagged stale
     (``R_WATER_STALE_GUARD``).
   * **Cache** (only if a read produced states): rebind ``_last`` whole (the web thread reads it
     unlocked), stamp ``_last_ok_ts``, run the camping
     observer (passive), run the auto-camper step (which may command through ``on_command``), then
     persist the state file atomically (``R_SERVE_STATE_CACHE``).
   * **Fan out** the interpreted states: MQTT discovery once per newly installed function, then one
     state message per installed function (:need:`S_SEQ_MQTT`), and InfluxDB points for the
     installed functions.
   * The loop records the cycle's outcome in a small JSONL log: ``ok``, ``asleep`` (the supervisor
     is at its backoff cap), ``ble_error`` or ``error``. A missing stretch with no rows means the
     daemon itself was down.

   **Unreachable.** ``ConnectionUnavailable`` ends the cycle before the cache and the sinks are
   touched, so nothing is published and the cache keeps the last good state
   (:need:`S_SEQ_SLEEP`). The **web path never reads BLE**:
   :py:meth:`calictl.serve.ServeBackend.state` interprets the cache again, substitutes the held
   water reading (both tanks flagged stale), and adds ``_meta`` (``last_seen``, ``age_s``,
   ``online``, session state and mode, firmware, anchors). The UI shows the last values with their
   age and an offline banner whenever ``online`` is false. ``online`` follows the session state once
   the persistent machinery runs, else the recency of the last good poll.

.. mermaid::

    sequenceDiagram
        participant L as serve poll loop
        participant U as Camper unit
        participant C as Cache (memory and state file)
        participant K as Sinks (MQTT, InfluxDB)
        participant W as Web UI (state API)
        loop every interval, 30 s by default
            Note over L: skip while the supervisor connects or a pairing flow owns the radio
            L->>L: take the _ble lock
            alt live persistent session
                L->>U: read every state char over the live link
            else no session
                L->>U: cold read_all (connect, subscribe, heartbeat, read)
            end
            U-->>L: raw frames
            L->>L: protocol.decode, semantics.interpret, firmware corrections
            L->>L: drift snapshot, plausibility anchors, water stale-latch guard
            L->>C: rebind last state, stamp as-of time, history sample, save the state file
            L->>L: camping observer and auto-camper step
            L->>K: MQTT discovery once, state per installed function, Influx points
            L->>L: record outcome ok
        end
        W->>C: GET /api/state (no BLE read)
        C-->>W: interpreted state with water hold and meta (last_seen, age, online)
        Note over L,U: unit asleep, ConnectionUnavailable, cache and sinks untouched, outcome asleep or ble_error
        Note over W: online false, the UI shows last values with their age and the offline banner

**Evidence.** This is the loop buspi has run since the daemon went live, and the outcome log is
how telemetry gaps are classified after the fact (deep-sleep rows versus connection-loss rows
versus no rows at all). The pairing skip, the lock ordering, the cache persistence across a
restart and the offline flag are covered by ``tests/test_web_serve.py``. The water guard is
described in `value-freshness.md
<https://ckeller42.github.io/open-california/business-logic/value-freshness.html>`_.

Reachability / deep-sleep
-------------------------

.. spec:: Parked deep-sleep makes access intermittent
   :id: S_SEQ_SLEEP
   :status: live-verified
   :links: R_SESSION_SUPERVISOR, R_SERVE_STATE_CACHE

   **Contract.** Parked and idle, the unit BLE-deep-sleeps and stops advertising. Only physical use
   (door, ignition) wakes it. Access is therefore inherently intermittent:

   * A connect exhausts its retries and raises ``ConnectionUnavailable``. The poll is skipped and
     logged ``asleep`` or ``ble_error`` in the outcome log.
   * The supervisor backs off 5 / 10 / 30 / 60 s. After 4 consecutive failures its state is
     ``asleep``.
   * ``serve`` keeps serving the persisted last-known state with an "as of" time, and the web UI
     shows an offline banner (:need:`S_SEQ_POLL`). Nothing is published to MQTT meanwhile, so the
     Home Assistant sensors expire and go unavailable (:need:`S_SEQ_MQTT`).
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
that will not resolve by itself.

**Several centrals at once (CAPTURE 2026-10-10).** The unit served three centrals at the same
time: the phone app holding a link, buspi polling (99 clean connect / read / release cycles in an
hour, no errors) and the ESP32 satellite. While parked, the unit dropped the satellite's held link
(HCI ``0x13`` ~15–20 s after each connect) but **not** the app's held link (46 min) and **not**
buspi's held persistent session (3 min with a viewer, 11:45). **Root cause found, fix in PR:**
right after connect the unit sends every central an ATT Exchange MTU Request (Client RX MTU 247).
BlueZ and Android answer it. The satellite's NimBLE was built without a GATT server (in IDF v6.1
``BT_NIMBLE_GATT_SERVER`` depends on ``BT_NIMBLE_ROLE_PERIPHERAL``, which was off), so esp-nimble
silently drops the request, and the unit's ATT transaction timeout then ends the link (``0x13``).
The fix enables both options in the satellite's ``sdkconfig``. It is a missing ATT response, not
a parked-unit policy against idle links.

The connection failure modes are in `value-freshness.md
<https://ckeller42.github.io/open-california/business-logic/value-freshness.html>`_ ("Connection
failure modes").

Home Assistant over MQTT
------------------------

.. spec:: MQTT sink: discovery, state publish and command-in
   :id: S_SEQ_MQTT
   :status: unit-tested
   :links: R_MQTT_DISCOVERY_STATE, R_MQTT_COMMAND_TOPICS, R_ONOFF_STRICT

   **Contract.** MQTT is an optional sink of the single daemon. Without ``paho-mqtt``, or with an
   unreachable broker, ``serve`` logs a warning and carries on with polling and the web UI.
   Broker, user and password come from ``MQTT_HOST``, ``MQTT_PORT``, ``MQTT_USER`` and
   ``MQTT_PASSWORD``.

   * **Connect.** The client sets a last-will ``calivan/status`` = ``offline`` (retained). On
     connect it publishes ``online`` (retained) and subscribes to every command topic
     ``calivan/<function>/<what>/set`` (:py:func:`calictl.mqtt.command_topics`).
   * **Discovery.** After a poll, for each function that newly reports ``installed``, the daemon
     publishes the retained configs from :py:func:`calictl.mqtt.render_discovery` under
     ``homeassistant/<component>/vwcamper_<function>_<key>/config``, once per daemon run.
     Read-only sensors read one field of ``calivan/<function>`` through a ``value_template``, use
     ``calivan/status`` as availability and carry ``expire_after`` (300 s). Command entities are
     a ``switch`` (payloads ``on`` and ``off``) or a ``number`` (min and max) with a
     ``command_topic`` and do not expire. Functions not installed on the van get nothing.
   * **State.** Every successful poll publishes one flattened JSON of the interpreted state per
     installed function to ``calivan/<function>`` (not retained). Nothing is published while the
     unit is unreachable, so the sensors expire and become unavailable instead of showing a
     days-old value as live. A clean shutdown publishes ``offline`` itself, a crash leaves it to
     the last-will.
   * **Command-in.** The paho network thread's ``on_message`` maps the topic to ``(function, what)``
     and takes the payload text. It schedules ``Server.on_command`` on the daemon loop with
     ``run_coroutine_threadsafe``. From there it is the same path as a web or CLI command: the
     read-only gate, ``command_precondition`` (a payload that is not an on/off token is refused),
     the ``_ble`` lock, ``control.build``, the write (:need:`S_SEQ_COOLER` is the worked example).
     The result is not reported back. A failure is only logged. The entity follows the unit when
     the next poll publishes the new state.

.. mermaid::

    sequenceDiagram
        participant S as serve (poll loop, command path)
        participant M as Mosquitto broker
        participant H as Home Assistant
        S->>M: connect with last-will calivan/status offline (retained)
        S->>M: publish calivan/status online (retained)
        S->>M: subscribe to every calivan function what set topic
        Note over S: a poll reports a function as installed
        S->>M: publish retained discovery configs for that function (sensors, switches, numbers)
        M-->>H: homeassistant config topics create the entities
        loop every successful poll
            S->>M: publish calivan/function with the flattened interpreted state
            M-->>H: value_template picks one field per entity
        end
        Note over S,H: poll fails (unit asleep), nothing is published, sensors expire after 300 s and go unavailable
        H->>M: publish calivan/cooler/power/set with payload on
        M-->>S: on_message in the paho thread
        S->>S: run_coroutine_threadsafe(on_command) on the daemon loop
        Note over S: read-only gate, preconditions, _ble lock, build, write, applied-check
        Note over S,M: nothing is published for the command, the next poll publishes the new state
        Note over S,M: daemon dies, the broker publishes the last-will offline, a clean exit publishes it itself

**Evidence.** ``tests/test_ha.py`` covers the topic map, the discovery entities (switch and number
with their ranges, installed gating, ``expire_after`` on read-only sensors only) and the round trip
from a topic back to a ``(function, what)``. ``tests/test_web_serve.py`` covers the broker-less
start. The broker, the retained configs and Home Assistant itself are not part of any test, and this
page records no end-to-end run, hence the status. **Known gap:** the lighting ``brightness`` number
(0 to 15) and the heater ``level`` number (0 to 15) are advertised, but ``control.build`` has no
``lighting brightness`` control (``CommandError: unknown lighting control``) and rejects a heater
level outside 1 to 10. Such a command is logged as failed and never reaches the unit.

ESP32 satellite
---------------

The satellite (``firmware/``, the M5Stack CoreS3) is a second, independent implementation: its own
NimBLE stack, its own WiFi and HTTP server, no connection to buspi. Its flows are drawn here
because they reuse the same protocol and the same frames. The firmware is proven on a Linux host
build against the Bumble fake unit, in QEMU, and on a CoreS3 on a bench against the mock unit.
Since 2026-10-08 it has also bonded to the real camper unit and run commands on it (evidence-ledger
2026-10-08 to 2026-10-10); the three flows keep the ``mock-only`` status until that evidence is
folded into them. Details:
:doc:`firmware` and the owner guide :doc:`howto-esp-wifi-setup`.

.. spec:: Satellite pairing from the USB console, then the bonded session
   :id: S_SEQ_ESP_PAIRING
   :status: mock-only
   :links: R_FW_PAIRING_SM, R_FW_PAIRING_RUNNER, R_FW_IO_CAP_BEFORE_LINK, R_FW_SESSION

   **Contract.** Pairing on the satellite is driven from the console (USB-Serial/JTAG on the
   CoreS3), by the platform-free C twin of the guided-pairing state machine
   (:need:`S_SEQ_PAIRING` is the Pi's version).

   * **I/O capability first.** ``cali_ble_nimble_init`` sets keyboard-only I/O capability and MITM
     **before** the host syncs, so no link exists yet when NimBLE reads them
     (``R_FW_IO_CAP_BEFORE_LINK``). Setting them inside the pairing call negotiates Just Works and
     the unit refuses it.
   * **Boot never scans.** With a stored bond the session reconnects by bond, with none it stays
     idle. Only the console ``pair`` starts a flow. There is no bond probe: ``forget`` is the only
     way to drop a bond.
   * ``pair`` starts SCANNING (30 s) for the name ``VWCAMPER``. Found: stop the scan and CONNECTING
     (20 s). Connected: PAIRING (15 s), NimBLE starts SMP. The unit shows a 6-digit code and the
     stack asks for it: WAITING_PASSKEY (60 s). The user types ``passkey N`` (1 to 6 digits). Link
     encrypted: VERIFYING (10 s), a read of ``1004`` must succeed. Then BONDED, and the console
     prints a ``STATE`` line with the identity address.
   * A failed connect or pairing retries from a fresh scan, up to the state machine's attempt
     limit, then ERROR. Every state has the same timeouts as ``calictl.pairing``. As on the Pi, a
     link drop while waiting for the passkey does not end the wait, the 60 s timeout does.
   * The bond itself is persisted by NimBLE's store callbacks into the platform key-value store
     (NVS on the chip). The runner's persist step has nothing left to do.
   * **Bonded session** (``cali_session``): the ``1003`` heartbeat starts at once and then runs for
     the whole link. Discover, subscribe to every state char, warm up
     ``CODEC_HEARTBEAT_WARMUP_MS`` (2 s), then read every function in order. A read and a push both
     replace a function's frame, so the last frame wins. The first ``SNAP`` line follows. Water
     ``1302`` is re-read every 30 s. A lost link reconnects by bond after 1 s, doubling to 60 s.

.. mermaid::

    sequenceDiagram
        participant K as User (USB console)
        participant E as Satellite (runner, session)
        participant N as NimBLE
        participant U as Camper unit
        Note over E,N: at init, before the host syncs, IO capability keyboard-only and MITM on
        Note over E: boot with no bond stays idle and never scans, with a bond it reconnects by bond
        K->>E: pair
        E->>N: start scan for VWCAMPER (SCANNING, 30 s)
        U-->>N: advertises VWCAMPER
        N-->>E: device found
        E->>N: stop scan, connect (CONNECTING, 20 s)
        E->>N: pair (PAIRING, 15 s)
        N->>U: SMP pairing request
        Note over U: unit shows a 6-digit passkey
        N-->>E: passkey requested (WAITING_PASSKEY, 60 s)
        K->>E: passkey 123456
        E->>N: inject the passkey
        N->>U: LE passkey entry completes, link encrypted
        N-->>E: encryption OK, bond stored through the NimBLE store callbacks
        E->>U: read 1004 (VERIFYING, 10 s)
        U-->>E: value, state BONDED with the identity address
        E-)U: write 1003 heartbeat every period for the whole link
        E->>U: discover, subscribe to every state char
        Note over E,U: warm-up 2 s, then read every function, last frame wins
        E-->>K: SNAP line, then SNAP on each later push
        Note over E,U: link lost, reconnect by bond after 1 s, doubling to 60 s

**Evidence.** The state machine and the runner are held to the same golden vectors as
``calictl.pairing`` (``tests/firmware/test_pairing_sm_parity.py``, ``test_runner_fake.py``). The
session is tested on a scripted transport (``test_session_fake.py``) and, over real NimBLE, against
the Bumble fake unit (``test_host_e2e.py``). ``make cali-host-jw`` rebuilds the late-I/O-capability
bug on purpose and the fake unit refuses it. The CoreS3 bench ran the read side against the mock
unit over real BLE. On the real unit (2026-10-10) the parked unit drops the satellite's held link
~15–20 s after each connect while it keeps the app's and buspi's links: the satellite did not
answer the unit's ATT Exchange MTU Request (no GATT server built in); root cause found, fix in PR
(:need:`S_SEQ_SLEEP`). See :doc:`firmware`
("Console line protocol", "Design rulings worth knowing").

.. spec:: Satellite WiFi setup through the hotspot and captive portal
   :id: S_SEQ_ESP_WIFI
   :status: mock-only
   :links: R_FW_WIFI_PROVISION, R_FW_HTTP_STATUS, R_FW_WIFI_BLE_COEX, R_NET_CONSTS_SINGLE_SOURCE

   **Contract.** A satellite with no saved WiFi network opens its own setup hotspot at boot. The
   state machine (``wifi_sm.c``, a C twin of ``tools/wifi_sm_ref.py``) and the runner
   (``wifi_run.c``) are platform-free C.

   * **Hotspot.** WPA2 ``calictl-esp-setup`` (passphrase ``calictl-setup``), address
     ``192.168.4.1``. The captive DNS answers every A query with that address. The HTTP server
     answers the OS captive-portal probe paths (``/generate_204``, ``/hotspot-detect.html``,
     ``/connecttest.txt`` and the other known ones) with ``302`` to ``http://192.168.4.1/``, and any
     other path in setup mode with ``302`` to ``/``. A phone that joins is led to the page.
   * **Page.** ``GET /`` serves the setup page (the same bytes as ``GET /device``). It polls
     ``GET /api/wifi`` every 2 s: mode, why the last join failed, and the last scan's networks. A
     scan is deferred while a BLE pairing flow is active (``R_FW_WIFI_BLE_COEX``) and starts on the
     first tick after it.
   * **Submit.** ``POST /api/wifi`` takes exactly ``{"ssid","psk"}``. The SSID is 1 to 32 bytes and
     the passphrase 8 to 63, open networks are not supported. Valid credentials go to the key-value
     store (``wifi_ssid``, ``wifi_psk``) and the runner joins as a station (CONNECTING). The
     hotspot moves to the network's channel, so the phone may drop off for a moment.
   * **Joined.** The station gets an address: ONLINE, mDNS announces ``calictl-esp.local``
     (``_http._tcp``, port 80), the next page poll reports station mode, and the hotspot closes
     30 s later so the phone can read the answer first. From now on ``GET /`` serves the calictl
     web UI and ``/api/state`` reports ``device.control.writes`` true (:need:`S_SEQ_ESP_COMMAND`).
   * **Failed.** Credentials typed in *this* setup flow are cleared and the state returns to
     SETUP_AP with the hotspot still up. ``last_error`` is ``auth``, ``not_found`` or ``other`` and
     the page turns it into a sentence.
   * **Saved credentials that stop working** (router off, van moved) are never wiped: RETRYING with
     a 1 s doubling to 60 s backoff, and after 5 minutes the hotspot reopens alongside while the
     retries go on (SETUP_AP_RETRYING). ``wifi forget`` on the console or ``DELETE /api/wifi``
     erases the credentials from any state and reopens the hotspot. The WiFi never calls into the
     BLE session, which keeps its link and heartbeat through a WiFi loss.

.. mermaid::

    sequenceDiagram
        participant P as Phone or laptop
        participant E as Satellite (hotspot, DNS, HTTP, wifi runner)
        participant R as Home router
        Note over E: boot with no saved credentials, state SETUP_AP
        E-->>P: hotspot calictl-esp-setup (WPA2), DNS answers every name with 192.168.4.1
        P->>E: join the hotspot
        P->>E: OS probe GET /generate_204 (or hotspot-detect.html and the others)
        E-->>P: 302 to http://192.168.4.1/ so the sign-in page opens
        P->>E: GET / (the setup page)
        loop page poll every 2 s
            P->>E: GET /api/wifi
            E-->>P: mode, last_error, scan list (a scan waits while a BLE pairing flow is active)
        end
        P->>E: POST /api/wifi with ssid and psk
        E->>E: validate (ssid 1 to 32 bytes, psk 8 to 63 bytes), store in the kv store
        E->>R: join as a station (CONNECTING), the hotspot follows the channel
        alt joined
            R-->>E: DHCP address
            E->>E: ONLINE, announce calictl-esp.local through mDNS
            E-->>P: next poll shows station mode and the address
            Note over E: 30 s later the hotspot closes, GET / now serves the calictl UI
        else join failed
            E->>E: clear the credentials typed in this flow, back to SETUP_AP, last_error auth, not_found or other
            E-->>P: the page says why, the hotspot is still open
        end
        Note over E,R: saved credentials later unreachable, RETRYING 1 s doubling to 60 s, never wiped, after 5 min the hotspot reopens alongside

**Evidence.** The state machine is held to golden vectors against the Python reference
(``tests/firmware/test_wifi_sm_parity.py``). The captive DNS, the HTTP core, the endpoints and a
scripted fake WiFi run on the host tier (``test_captive_dns.py``, ``test_http_core.py``,
``test_web_handlers.py``, ``test_net_host.py``, ``test_host_e2e.py``). On 2026-10-01 a real CoreS3
joined a 2.4 GHz network through its hotspot and the page, with a Linux laptop on the hotspot, not a
phone. A phone's sign-in notice, the captive-portal probes of each OS and a van WiFi are still to be
confirmed (`howto-esp-wifi-setup.md` status box and :doc:`firmware`, "Network watch items").

.. spec:: Satellite station-mode control command through the write allow-list
   :id: S_SEQ_ESP_COMMAND
   :status: mock-only
   :links: R_FW_CONTROL_API, R_FW_CONTROL_TWIN, R_FW_WRITE_ALLOWLIST, R_FW_SHARED_UI

   **Contract.** On the home network the satellite serves calictl's own web UI and accepts
   ``POST /api/command`` in calictl's request and response shape. The answer is deferred until the
   write is done. In order:

   * **Station mode only.** Anywhere else the answer is ``403 setup_mode``, before the body is
     read. Then a fixed-shape JSON parse (``400 bad_json``), ``function`` and ``what`` present, and
     ``confirm`` for ``airheater`` and ``roof`` (calictl's own ``400`` codes).
   * **Submit** (``control_run.c``): ``409 busy`` while a command, or a timed-out write that is
     still unacknowledged, is in flight. Then ``cali_ctl_plan``, the C twin of
     ``command_precondition`` / ``build`` / ``preface_for`` / ``commit_for`` held byte for byte to
     ``tests/vectors/control.json``. The **roof, the wake-up light and every function but the five**
     (cooler, camping mode, lighting, energy, air heater) answer ``200`` with ``refused`` = "Only via
     buspi or the app", with no link needed. An unbuildable value is ``400 bad_value`` or
     ``unknown_control``. A buildable command needs an **armed link** (up for
     ``CODEC_ARM_DELAY_MS`` = 3 s with its first read-all done) and a frame of that function read on
     this link, else ``503 not_connected``. A gate's refusal is ``200`` with ``refused`` = calictl's
     text (the same ``REASON_*`` strings).
   * **Write.** The frames go out one at a time, with response. For each, ``cali_ctl_write_ok``
     admits only the control chars ``1101``, ``1201``, ``1501``, ``1601`` and ``1701`` at exactly
     their frame length, and the NimBLE transport's ``t_write`` asks again. The roof's ``1401``
     and the heartbeat char cannot pass. The lighting commit follows a frame by at least 300 ms
     after its ACK. The ``1003`` heartbeat keeps ticking on its own fixed-target write.
   * **Outcome.** Every write ACKed: ``200`` with ``applied`` null (the satellite does no readback,
     so the UI says "Sent — the unit didn't confirm it"). An ATT error: ``502 write_failed`` and no
     further frame. No ACK within 4 s (``CALI_CTL_DEADLINE_MS``): ``504 write_timeout``. A link
     lost before any command frame went out fails the command (``502 write_failed``). A link lost
     after one went out answers ``200`` with ``applied`` null and ``"unconfirmed": true``: the
     parked unit kicks idle links, and a frame it applied can lose its ACK to the kick (field
     2026-10-09, #264), so the page keeps watching the unit's state across the reconnect (20 s).
     The kick is seen on the satellite's link only; the parked unit keeps the app's and buspi's
     held links (CAPTURE 2026-10-10): the satellite left the unit's ATT Exchange MTU Request
     unanswered, so the unit's ATT timeout ended its link. Root cause found, fix in PR
     (:need:`S_SEQ_SLEEP`).

.. mermaid::

    sequenceDiagram
        participant B as Browser (shared calictl UI)
        participant W as web.c (single-connection HTTP core)
        participant C as control (plan, allow-list, sequencer)
        participant N as NimBLE transport
        participant U as Camper unit
        B->>W: POST /api/command with function, what, value, confirm
        alt not station mode
            W-->>B: 403 setup_mode
        else station mode
            W->>W: parse the fixed shape, function and what present, confirm for airheater and roof
            W->>C: cali_ctl_submit
            alt a command is still in flight
                C-->>B: 409 busy
            else roof, wake-up light or any other function
                C-->>B: 200 refused, Only via buspi or the app
            else bad value or unknown control
                C-->>B: 400 bad_value or unknown_control
            else no armed link or no frame read on this link
                C-->>B: 503 not_connected
            else a command_precondition gate refuses
                C-->>B: 200 refused with calictl's text
            else accepted
                C-->>W: pending, the HTTP core holds the connection
                loop each planned frame, one at a time
                    C->>C: cali_ctl_write_ok(char, length) allow-list
                    C->>N: write with response
                    N->>N: t_write checks the allow-list again
                    N->>U: ATT write to 1101, 1201, 1501, 1601 or 1701
                    U-->>N: write response
                    N-->>C: written, a lighting commit follows at least 300 ms after the ACK
                end
                C-->>W: done
                W-->>B: 200 ok, applied null (no readback)
                opt ATT error or no ACK within 4 s
                    W-->>B: 502 write_failed or 504 write_timeout, no further frame
                end
                opt link lost after a frame went out
                    W-->>B: 200 ok, applied null, unconfirmed true
                end
            end
        end
        Note over N,U: the 1003 heartbeat ticks for the whole life of the link, never through this path

**Evidence.** The Python builders are the authority and the C twin must reproduce every vector
(``tests/firmware/test_control_parity.py``, ``tests/test_control_vectors.py``). The allow-list is
tested pure and at the transport (``T_FW_WRITE_ALLOWLIST_PURE``, ``test_control_e2e.py``: the unit
never sees a roof or unknown write). The sequencer is tested on a scripted transport
(``test_session_fake.py``), the endpoint on a fake sequencer (``test_web_handlers.py``), and every
app-recorded cooler, camping, lighting, heater and energy action went through ``POST /api/command``
to the mock unit **byte-exact** on a CoreS3 bench on 2026-10-07, with the roof, the wake-up edits
and stairs refused and zero ``1401`` writes. On the real unit, a satellite write actuated
(kitchen ambient → 0, 2026-10-09), and on 2026-10-10, with the van parked and locked, the
``"unconfirmed": true`` answer (#271) fired: the 5th of a series of no-op lighting writes lost its
ACK to the parked unit's kick and was answered ``200`` with ``applied`` null and
``"unconfirmed": true`` (evidence-ledger 2026-10-10). The cooler ``State=3`` frames are the ones
the real app sent to the real unit on 2026-10-10 (:need:`S_SEQ_COOLER`).
See :doc:`firmware` ("Control path") and `evidence-ledger.md
<https://ckeller42.github.io/open-california/business-logic/evidence-ledger.html>`_.
