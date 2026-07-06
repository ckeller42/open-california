# RE: The connect gate — why control WRITES are silently ignored

Investigation of the CaliforniaOnTour BLE layer (JADX decompile). All citations are
`file:line` under
`…/b137c4a5-…/scratchpad/decompile/src/sources` (clean output) and
`…/scratchpad/decompile/bad/sources` (jadx `--show-bad-code`, used where the clean
output skipped a method dump).

## TL;DR

There **is a per-session gate**, but it is **not a magic "enable" write** with secret
bytes. The app performs a fixed **connect handshake** before any per-feature control
write is honored:

```
connectGatt(LE) → discoverServices → requestMtu
   → READ  00001001 (version)                         [service 00001000]
   → READ  00001004 (AUTHENTICATED read, 60 s timeout) [service 00001000]  ← the gate
   → subscribe (CCCD/2902 enable) + read EVERY control unit's STATUS char
   → state = CONNECTED  → only now are control writes effective
```

Two concrete things a naive "connect + write to control char" skips:

1. **The authenticated read of `00001004-6c77-4b7d-bbf6-a5e587701f3d`** (in service
   `00001000-…`). This is an encryption-gated characteristic; reading it forces
   BLE bonding/encryption and acts as the session "unlock". The app will not proceed
   to enable controls until this read returns non-empty data.
2. **Notification subscription (CCCD write to descriptor `00002902`) on each control
   unit's STATUS characteristic** (fridge `00001102`, camping `00001202`, …) before the
   connection is considered CONNECTED.

No challenge/response, no token write, no "terminal-15" write exists. The
`setUpRemoteControl_*` strings are **UI wizard resource strings only** (localized text in
the account feature), not BLE payloads — verified below.

---

## (a) The enrollment / enable / handshake gate

### Lifecycle wiring
- `qd/f.java` is the `BluetoothGattCallback`.
  - `onConnectionStateChange` STATE_CONNECTED → dispatches `jb.b(...,24)`
    (`qd/f.java:69-73`).
  - `onServicesDiscovered` → `e(gVar,1)` (`qd/f.java:98-102`).
  - `onCharacteristicChanged`/`Read` feed notification/read values back to the queue
    (`qd/f.java:21-43,112-132`).
- `jb/b.java` case 24 `t()` (`jb/b.java:283-378`) runs on connect:
  sets gatt into the op queue, completes the pending connect op
  (`H(qd.m.class, true, null)`), does a reflection `refresh()`, then enqueues
  `qd.o` = **discoverServices** and `qd.p` = **requestMtu** (`jb/b.java:352-370`).

### The GATT executor (proves what each op does)
`s.a1.I()` — skipped in clean output, present in bad output
`bad/sources/s/a1.java:409-548`:
- `qd.m` → `connectGatt(ctx, false, callback, 2)` (transport LE) — `:428-431`
- `qd.q` → **write**; SDK≥33 `writeCharacteristic(char, bytes, 2)`, else
  `setWriteType(2)/setValue/writeCharacteristic` — `:447-461`  ← **writeType 2 = WRITE_TYPE_DEFAULT (write WITH response)**
- `td.c` → `readCharacteristic` — `:471-487`
- `qd.o` → `discoverServices` — `:489-494`
- `qd.l` → **enable notifications**: picks `ENABLE_INDICATION_VALUE` (props&32) or
  `ENABLE_NOTIFICATION_VALUE` (props&16), `setCharacteristicNotification(char,true)`,
  then `writeDescriptor(00002902,…)` — `:495-537`
- `qd.p` → `requestMtu` — `:540-546`

Op param classes confirm semantics: `qd/q.java` (`toString` literally says
`AndroidWriteCharacteristicOperation(… writeType=2 …)`), `qd/l.java`
(`AndroidChangeNotificationOperation(… isEnable=true)`), `qd/m.java`, `qd/p.java`.

### The handshake state machine (the actual gate)
- **Version check**: `defpackage/u1.java:549-553` (case 19) — logs *"Starting Version
  Check"* and reads `00001001` in service `00001000` (`td.c` read).
- **Authenticated read**: `pf/g.java:52` (case 1 of the version-data callback) — logs
  *"Starting Authenticated Read Check"* and reads
  `00001004-6c77-4b7d-bbf6-a5e587701f3d` in service `00001000-…` with a **60000 ms**
  timeout. The long timeout + the retry logic below = it is waiting on the OS pairing
  dialog / bonding.
- **On auth-read data received** → `pf/g.java` default/case 3 (`:126-135`): logs
  *"Subscribing all control units"* → `n3` case 20.
- **On auth-read failure** → `pf/g.java:63-91` (case 2): logs *"Authenticated read check
  failed"* and, once, *"Try to reconnect in the hope that the pairing was already
  successful"* → reconnect (`qd.r`). This is the bonding round-trip: the first
  authenticated read triggers bonding, the link drops, the reconnect finds the bond
  established and the read then succeeds.
- **Subscribe-all** — `bad/sources/h1/n3.java:994-1058` (case 20): for each of 13
  control-unit groups (`pf.k.H0`, built in `pf/k.java:143`) it dispatches
  `jb.b(pf.n, …, 18)`. When the success count == group count it logs
  *"All control units subscribed: CONNECTED"* and flips state to connected.
- Per-group subscribe+read — `jb/b.java:100-158` (case 18): for every characteristic
  spec `qf.a` in the group, if `qf.a.b()` is true it issues `td.a` (→ `qd.l` enable
  notifications) **and** `td.c` (read).

**Bottom line for (a):** the gate is *read `00001004` (authenticated) then subscribe to
all status chars*, not a byte-payload enable write.

## (b) Does the app subscribe to notifications before writing? — YES

- Subscription is a hard precondition of reaching the CONNECTED state
  (`n3` case 20 above), which is the state the UI requires before it will send control
  writes.
- The subscribed characteristic is each control unit's **STATUS** char, distinct from
  the control char it writes:
  - Fridge group `vf/c.java`: service `b()` = `00001100-…` (`vf/c.java:134,294-297`);
    char list `a()` = `[sf.b(1)]` (`vf/c.java:133,289-292`); `sf/b.java:94` char =
    `00001102-…`, `sf/b.java:99-102` `b()` = **true** (subscribe). The status frame is
    parsed in `vf/c.java:341-388` ("Incoming Data for Cooler").
  - The fridge CONTROL char is `00001101-…` (`sf/a.java:274`), reached via the write
    path `m2/a.java:y(...)` → `td.d(service=00001100, char=00001101, bits, …)`
    (`m2/a.java` `y()` method).
  - Camping group `tf/a.java`: service `00001200-…` (`tf/a.java:78`); control char
    `00001201-…` (`lg/a.java:29`); status char (subscribed) `00001202`.
- Enable is a proper CCCD write to `00002902` with ENABLE_NOTIFICATION/INDICATION value
  (`bad/sources/s/a1.java:501-524`).

## (c) Anything else the write path requires

- **Write type = WRITE_TYPE_DEFAULT (2), i.e. write WITH response**
  (`bad/sources/s/a1.java:455,458`; `qd/q.java:52 toString`). If our Python client uses
  write-without-response, some peripherals silently drop it — switch to write-with-
  response.
- **Bonding/encryption**: implied by the authenticated read of `00001004`. There is **no
  explicit `createBond`** call in the code — Android initiates bonding automatically when
  it hits the encryption-required characteristic. (Note: if the link were *not* encrypted
  the write would normally FAIL with insufficient-authentication, not succeed silently —
  see "Interpreting your symptom" below.)
- **MTU**: the app requests MTU = 26 (`qd/g.java:55` `f22251d0=26`, passed to `qd.p`).
  Payloads here are tiny, so MTU is not the blocker.
- **Connect transport** is forced to LE (`connectGatt(…, 2)`).
- All GATT ops are **serialized** through one queue (`s.a1`, methods `i()`/`g()`/`I()`/
  `H()`/`d()` at `src/sources/s/a1.java:436,585,709,911` and executor in the bad output).
  Writes issued back-to-back without waiting for `onCharacteristicWrite` may be dropped by
  a device that only accepts one outstanding op; mirror the one-at-a-time discipline.

## setUpRemoteControl_* — ruled out as a BLE payload
`grep` hits are all in `v9/e.java`, `v9/f.java`, `x9/*.java`, `xp/g.java` etc. as
`"string:setUpRemoteControl_description_…"` composeResources entries (`v9/e.java:37`),
i.e. localized wizard text, plus a preference flag in `xp/g.java`. None reaches the GATT
layer. No write to a "remote control enable" characteristic exists anywhere.

## Interpreting your symptom (write returns SUCCESS, nothing happens)
Because you report the write returns GATT SUCCESS (not insufficient-auth) and you are
already bonded (see memory `vwcamper-ble-access`), the encryption gate is likely already
satisfied. The remaining difference between your client and the app is the **per-session
handshake the device tracks internally**:
- it has not seen this session perform the **authenticated read of `00001004`**, and/or
- it has not seen you **subscribe (CCCD) to the status char**.
Firmware that arms "remote control" only after that handshake will ACK writes at the ATT
layer (hence SUCCESS) yet not act on them.

## Concrete replication steps (Python / bleak)
1. Connect (LE); ensure bonded/encrypted (you already are).
2. `discover services`.
3. (Optional) request MTU.
4. **Read** `00001001-6c77-4b7d-bbf6-a5e587701f3d` (service `00001000-…`).
5. **Read** `00001004-6c77-4b7d-bbf6-a5e587701f3d` (service `00001000-…`). Expect this to
   force pairing on first ever run; allow a reconnect. Confirm it returns non-empty data.
6. For each control unit you care about, **enable notifications** (write `00002902`
   ENABLE_NOTIFICATION) on its STATUS char and read it:
   fridge status `00001102` (svc `00001100`), camping status `00001202` (svc `00001200`).
7. Now issue the control write **with response (WRITE_TYPE_DEFAULT)** to the control char
   (fridge `00001101` / camping `00001201`), one op at a time, waiting for the write
   callback before the next.
8. Watch the STATUS-char notification to confirm the unit changed state.

If writes are *still* ignored after 4–7, the next thing to try is toggling write-with vs
write-without response and confirming the link is actually encrypted at connect time (some
stacks report a stale bond).

## Key files
- Callback / lifecycle: `src/sources/qd/f.java`, `src/sources/jb/b.java` (cases 18,23,24)
- Connection manager: `src/sources/qd/g.java`
- Op queue + executor: `src/sources/s/a1.java` + `bad/sources/s/a1.java:409-548`
- Op→GATT translation: `src/sources/h1/n3.java` case 25 (`:50-139`) and
  `bad/sources/h1/n3.java:994-1058` case 20
- Handshake: `src/sources/defpackage/u1.java:549-553`, `src/sources/pf/g.java`,
  `src/sources/pf/k.java`
- Frame/feature model: `src/sources/m2/a.java`, `src/sources/pf/n.java`,
  `src/sources/qf/a.java`, `src/sources/vf/c.java`, `src/sources/sf/a.java`,
  `src/sources/sf/b.java`, `src/sources/tf/a.java`, `src/sources/lg/a.java`
- Op params: `src/sources/qd/{q,l,m,p}.java`, `src/sources/td/{a,b,c,d}.java`

## Update: the 1004 value is NOT a session token (ruled out)

Traced the auth-read response through the write path (`pf/g.java` default case): the
`1004` data (`boolArr2`) is only used for `.length` (empty vs non-empty) then discarded —
never stored, never fed into any write. No nonce / signed-write / challenge exists in the
GATT layer (`qd`/`s`/`m2`/`jb`). So writes are **not** gated by an auth token derived from
the read; the auth read is purely an encryption/bonding trigger + a "did it return data"
gate.

**Sharpened next lead:** `CONNECTED` requires **every notifiable characteristic** to be
subscribed (`jb/b.java:122` counts `b()==true` chars; `:131-139` subscribes each). A group
(`pf.k.H0`) can have **more than one** char. Our handshake subscribed only ONE char per
function — if any group has multiple notifiable chars we never reach the armed `CONNECTED`
state, which would explain writes being ACKed-but-ignored. Next: extract the full per-group
char list and diff against what we subscribe.
