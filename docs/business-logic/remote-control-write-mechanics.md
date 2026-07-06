# RE: How the CaliforniaOnTour app transmits a control frame

Goal: explain why `calictl`'s single `write_gatt_char(control_uuid, frame_bytes)` is silently
ignored while the app's write takes effect, even though our field bits match `f()` exactly.

Source root (all `file:line` below are under this tree):
`/private/tmp/claude-501/-Users-ckeller-src-open-california/b137c4a5-b11b-4b8b-b6d6-16b437dd2993/scratchpad/decompile/src/sources`

## TL;DR

The frame content is right and there is **no wrapper** (no header/length/seq/CRC). The app does a
**single write-with-response** of the packed field bits, then a one-shot "release" write 500 ms
later — it is **not** a repeating heartbeat for fridge/camping. The two mechanical things `calictl`
most likely gets wrong are:

1. **Write type** — the app uses `WRITE_TYPE_DEFAULT` (2) = **write WITH response**. If `calictl`
   writes without response, the local GATT stack reports success but the unit ignores it.
2. **Untouched-field defaults** — the app always serializes the **whole** field array with each
   slot's **sentinel "leave-unchanged" default** (e.g. 3 / 7 / 15), not zeros. Zeroing sibling
   fields yields a different frame that the unit can reject or that stomps `State→0` (off) so
   "nothing happens."

---

## The exact transmit path (call chain)

Every action setter does: `field.o(value); A()/B(); y(true)`.

1. **`m2.a.y(boolean z11)`** — `m2/a.java:152-170` (the base class of every control model,
   including `sf.a` fridge/heater and `lg.a` camping/roof):
   ```
   jn.a aVar = (jn.a) this.Y; if (aVar != null) aVar.a();   // cancel any pending timer
   pf.n nVar = (pf.n) this.X;                               // sendDataCallback
   String strN = n();                                       // control-char UUID
   Boolean[] boolArrF = f();                                // full bit array from ALL slots
   nVar.f21218x.b(new td.d(nVar.b(), strN, boolArrF, null)); // enqueue write NOW
   if (z11) this.Y = new jn.a(500L, dVar, false, false);    // arm ONE-SHOT 500ms timer
   ```
2. **`vd.b.b(td.b)`** (`vd/b.java`) is implemented by **`qd.g.b`** (`qd/g.java:86-95`): for a
   `td.d` it launches `h1.n3` (case 25) on the BLE coroutine scope.
3. **`h1.n3.l`** (`h1/n3.java:90-109`): converts and builds the write op:
   ```
   byte[] r6 = tt.u8.e(r0.f24966e);        // Boolean[] -> byte[]   (h1/n3.java:102)
   qd.q r9 = new qd.q(charUuid, svcUuid, r6, callback);
   r3.i(r9, ...)                            // enqueue into serialized GATT op queue (s.a1)
   ```
4. **`qd.q`** (`qd/q.java`) = `AndroidWriteCharacteristicOperation`, `toString` hardcodes
   **`writeType=2`** (`qd/q.java:53`) → `BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT`
   (write-with-response). `td.d` is constructed `super(5000L, true)` (`td/d.java:30`) = 5 s
   response timeout. (No `setWriteType(...)` appears in app sources; the actual
   `writeCharacteristic` is inside the BLE library that executes `qd.q`.)

---

## (a) Cadence / repetition — NO command heartbeat for fridge/camping

- `y(true)` writes the command frame **once immediately**, then arms **`new jn.a(500L, dVar,
  false, false)`** (`m2/a.java:168`).
- **`jn.a`** (`jn/a.java`) 3rd ctor arg = **repeat flag**. Proof by contrast across all callers:
  - control release: `jn.a(500L, …, false, false)` → **one-shot** (our case).
  - roof movement heartbeat: `ig/c.java:674` / `:764` `jn.a(1000L, …, true, false)` → **repeating**
    1 Hz. This is the "1000 ms repeating timer" the roof notes describe — a *different*
    mechanism, and it is `true`, not `false`.
  - others: `dg/d.java:146` `jn.a(100L,…,true,true)`, `dg/d.java:164` `jn.a(1000L,…,true,true)`,
    `pf/k.java:457` `jn.a(30000L,…,true,false)`.
- **What the 500 ms one-shot actually does** — its runnable is `b1.d` case 8
  (`b1/d.java:234-245`): it does **NOT** resend the command. It calls **`aVar.v()`** first (reset
  every slot to its sentinel "leave-unchanged" default — `sf/a.java:242-269`), then writes **one**
  frame. So the follow-up is a **release / idle** write, not a keep-alive.
- **Stop condition:** the timer fires exactly once, 500 ms after the *last* setter (each new setter
  calls `aVar.a()` to cancel and re-arms). It is a plain delay — **not** gated on any state
  read-back.

**Conclusion:** a single command write reproduces the app's immediate write. Cadence is **not** the
cause. Emulating the 500 ms release is optional and does not gate the command taking effect
(sentinels = "don't change", so the release does not undo a persisted setpoint).

## (b) Framing / wrapper — NONE

`tt.u8.e(Boolean[])` (`tt/u8.java:115-126`):
```
byte[] out = new byte[(n + 7) / 8];
for i in 0..n: if bits[i] out[i/8] |= (1 << (7 - (i % 8)));
```
- MSB-first packing, `ceil(nbits/8)` bytes. Fridge = 48 bits → **6 bytes**; camping = 24 bits →
  **3 bytes** (`lg/a.java:66` `new Boolean[24]`).
- **No** header, **no** length prefix, **no** sequence/counter, **no** CRC/checksum, **no** trailer.
- On-wire bytes == exactly the field bits from `f()`. The inverse for incoming state is
  `u8.d(byte[])` (`tt/u8.java:102-113`, used at `jb/b.java` `s()` on characteristicChanged).

So `calictl`'s bytes are correct **provided** it reproduces the *entire* bit array of `f()`,
including the sentinel defaults seeded in every untouched slot (fridge ctor `sf/a.java:275-284`:
`e0..h0=3, i0=7, j0=7, k0=30, l0=62, m0=31, n0=31`).

## (c) Write type + target char

- **Target = correct.** `n()` returns the control **write** UUID: fridge `00001101-…`
  (`sf/a.java:274`), camping `00001201-…` (`lg/a.java:29`). These are distinct from the read/status
  chars (fridge `00001100`/`00001102`). The app writes to the per-feature control char, exactly
  what `calictl` targets.
- **Write type = WRITE_TYPE_DEFAULT (2) = write WITH response** (`qd/q.java:53`; 5 s timeout via
  `td/d.java:30`). Executed through a **serialized GATT operation queue** (`qd.g.b → h1.n3 →
  qd.q → s.a1`), so writes never overlap.

## (d) Acknowledgement / echo

- The app relies on the **ATT-layer write response** (GATT `onCharacteristicWrite`, 5 s timeout).
  It does **not** gate the command on an application-level ack or a state read-back.
- State updates arrive **independently** as **notifications** on the status char, decoded by
  `u8.d` (`jb/b.java` `s()` → `sd.a(boolArr, svc, char)`). The 500 ms release timer is a plain
  delay, not cancelled by any ack.

---

## Concrete changes for `calictl` (ordered by likelihood)

1. **Write WITH response.** Use `write_gatt_char(control_uuid, frame, response=True)` (BlueZ/bleak
   default is already with-response; if you set `response=False` anywhere, remove it). This is the
   single most likely mechanical cause: the app uses `WRITE_TYPE_DEFAULT`. A char whose only write
   property is Write (0x08, not WriteNoResponse 0x04) will silently drop a no-response write while
   the local stack still returns success.
2. **Fill every untouched field with its sentinel default, not 0.** Build the full 6-byte (fridge)
   / 3-byte (camping) frame from a shadow of *all* slots, seeded to the ctor sentinels
   (fridge: `e0..h0=3, i0=7, j0=7, k0=30, l0=62, m0=31, n0=31`), then overwrite only the slot you
   are changing. Verify your emitted bytes byte-for-byte equal `u8.e(f())` for a known action.
3. **Frame length must match** — 6 bytes fridge, 3 bytes camping. A wrong length can be rejected.
4. (Optional) Replicate the 500 ms one-shot release: 500 ms after the last command, reset all
   slots to sentinels and write once. Not required for the command to take effect.

## If write-with-response + correct sentinel frame still fails

- Confirm the control char's GATT properties on *your* unit expose Write (0x08); confirm you are
  writing the char, not its CCCD/descriptor, and using the right handle/MTU.
- The app operates over a **bonded (encrypted)** link — ensure `calictl` is bonded, not just
  connected. Some units drop control writes on an unauthenticated link (state reads may still work,
  which matches the "reads fine, writes ignored" symptom).
- Sanity-check the bit **order/offset** by round-tripping: read the status char, `u8.d` it, and
  verify your field extraction matches before trusting your write packing.
