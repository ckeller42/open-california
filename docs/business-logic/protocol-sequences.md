# Protocol sequence diagrams — moved

The BLE sequence diagrams have **one canonical copy**:
[**Protocol sequence diagrams**](https://ckeller42.github.io/open-california/protocol-sequences.html)
(source `docs/protocol-sequences.rst`). There, every flow is a `sphinx-needs` spec (`S_SEQ_*`) with
a verification status, a short contract, the diagram, and the evidence behind it. The RE detail that
used to live only on this page has been folded in: the `1502` config-dump modes, the `1402` byte
layout, the "two overlapping senders" explanation of the old roof-counter reading, and the
2026-07-13 "Bluetooth disabled on the unit" root cause. This page keeps the old section numbers as
stubs, so existing "`protocol-sequences.md` §N" references still resolve.

## 0. Session foundation and notifications

Now [Session foundation](https://ckeller42.github.io/open-california/protocol-sequences.html#session-foundation-connect-handshake-subscribe)
(`S_SEQ_CONNECT`, including the connect retry cascade and the app-only `1002` VIN check) and
[Notifications](https://ckeller42.github.io/open-california/protocol-sequences.html#notifications)
(`S_SEQ_NOTIFY`). Two related sections are new:
[Guided pairing](https://ckeller42.github.io/open-california/protocol-sequences.html#guided-pairing-passkey-entry-and-stale-bond-recovery)
(`S_SEQ_PAIRING`) and
[Persistent session supervisor](https://ckeller42.github.io/open-california/protocol-sequences.html#persistent-session-supervisor)
(`S_SEQ_SESSION`).

## 1. Heartbeat-armed control write

Now [Heartbeat-armed control write](https://ckeller42.github.io/open-california/protocol-sequences.html#heartbeat-armed-control-write)
(`S_SEQ_ACTUATE`).

## 2. Lighting SET

Now [Lighting SET and neutral flush](https://ckeller42.github.io/open-california/protocol-sequences.html#lighting-set-and-neutral-flush)
(`S_SEQ_LIGHT_COMMIT`). That section also keeps the retired REQUEST_CONFIG frame
`0d0c000000000000eeeeeeeeeeeeeeee` as the RE record. The full lighting history is in
[control-and-actuation.md](control-and-actuation.md) §4.

## 3. Roof actuation

Now [Roof actuation](https://ckeller42.github.io/open-california/protocol-sequences.html#roof-actuation-press-and-hold-safetycounter-gated)
(`S_SEQ_ROOF`, status mock-only). It covers the press-and-hold stream, the app-generated
SafetyCounter, the ~3 s unit self-gate, the no-heartbeat handshake-only arm (#150), the
connection-slot handover (#198) and the 1000 ms re-press debounce.

## 3b. Range validation and the 0x0E link drop

Now [Range validation](https://ckeller42.github.io/open-california/protocol-sequences.html#range-validation-and-the-0x0e-link-drop)
(`S_SEQ_REJECT`).

## 4. Fresh state read under heartbeat

Now [Fresh state read under heartbeat](https://ckeller42.github.io/open-california/protocol-sequences.html#fresh-state-read-under-heartbeat)
(`S_SEQ_READ`).

## 5. Reachability / deep-sleep

Now [Reachability / deep-sleep](https://ckeller42.github.io/open-california/protocol-sequences.html#reachability-deep-sleep)
(`S_SEQ_SLEEP`).
