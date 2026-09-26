# firmware — ESP32 satellite (#154)

ESP-IDF + NimBLE firmware for the camper-unit satellite. Work in progress: the host-build spike
(`host/spike_main.c`, deleted once the real host target lands) plus the first platform-free
component, `components/cali_core` (below).

## Host build (`firmware/host`)

The CI end-to-end tests run the firmware's BLE code on Linux against the **upstream NimBLE Linux
port**, talking HCI over TCP to a Bumble virtual controller that shares a `LocalLink` with the repo's
fake unit (`tools/fake_unit_peripheral.py`). Harness: `tests/firmware/conftest.py`; CI job:
`firmware-host-e2e`.

```
firmware/host/fetch_nimble.sh      # clones the pinned NimBLE + Mbed TLS into firmware/host/_deps (gitignored)
make -C firmware/host cali-spike   # Linux only; needs a 32-bit toolchain (see below)
python -m pytest tests/firmware -v
```

## Components (`firmware/components`)

`cali_core` is platform-free C: no BLE, no strings, no clock — safe to compile and unit-test on
any host. First member: the pairing state machine.

| File | What |
|---|---|
| `include/cali_pairing_sm.h` | Public interface — `cali_pair_state_t`, `cali_pair_action_t`, `cali_pair_step()`. |
| `pairing_sm.c` | A line-for-line transcription of `calictl/pairing.py`'s `step()`, using the pinned enums from `csrc/pairing_consts.h` (GENERATED from the same Python module, #156's "one dictionary, two consumers" pattern extended to the pairing SM). |
| `test/pairing_sm_cli.c` | Line-protocol driver used ONLY by `tests/firmware/test_pairing_sm_parity.py` — not part of the ESP build. |

Proven equal to the Python original by replaying `tests/vectors/pairing.json` (the same golden
vectors `tests/test_pairing_sm.py` checks) through both implementations:

```
cc -std=c99 -Wall -Wextra -Werror \
   -I firmware/components/cali_core/include -I csrc \
   firmware/components/cali_core/pairing_sm.c firmware/components/cali_core/test/pairing_sm_cli.c \
   -o pairing_sm_cli
python3 -m pytest tests/firmware/test_pairing_sm_parity.py -v
```

This test has no BLE/NimBLE dependency (pure C, no radio) and runs on any host with a C compiler
— macOS included, unlike the BLE e2e tests below (`tests/firmware/test_spike_link.py`, marked
`linux_only`, which need the 32-bit NimBLE Linux host build).

## Pins

| What | Pin | Why |
|---|---|---|
| ESP-IDF | v6.1 (Task 8's `firmware-build`) | its `components/bt/host/nimble/nimble` submodule is esp-nimble `139cada0ae93` on branch `nimble-1.6.0-idf`, i.e. based on **upstream NimBLE 1.6.0** (merge base "Prepare for NimBLE 1.6.0 release") + ~440 Espressif commits |
| Upstream NimBLE (host build) | `nimble_1_10_0_tag` | **gap vs ESP-IDF: 1.6.0 → 1.10.0.** Upstream's TCP socket transport (`BLE_SOCK_USE_TCP`) is incomplete up to 1.9.0 — `ble_hci_sock_cmdevt_tx` (and, before 1.8.0, `ble_hci_sock_acl_tx`) exists only for the `linux_blue`/`nuttx` variants, so a 1.6.0–1.9.0 TCP build does not link (verified for 1.6.0: `undefined reference to ble_hci_sock_cmdevt_tx`). 1.10.0 is the first release whose `linux_tcp` choice is complete. The host API used here (GAP disc/connect, SM passkey, GATT client, `ble_store_config`) is unchanged across the gap. |
| Mbed TLS (host build) | `mbedtls-3.6.5` | NimBLE 1.10 dropped the bundled TinyCrypt; SM crypto needs Mbed TLS, and the build is 32-bit (below), so it is built from source — same version upstream NimBLE's port CI uses |

## Host build notes (every NimBLE / Bumble adaptation)

0. **NimBLE pin (ruling R5).** ESP-IDF v6.1 vendors esp-nimble based on NimBLE 1.6.0, whose socket
   transport does not link in TCP mode (no `ble_hci_sock_cmdevt_tx` for `BLE_SOCK_USE_TCP`), so the
   host build pins upstream `nimble_1_10_0_tag`. API skew between the two is caught by Task 8, which
   compiles `cali_ble_nimble` against esp-nimble in the `firmware-build` job.

1. **Warnings.** Our C (`OWN_OBJ` in the Makefile: `spike_main.o`, later all repo C under
   `firmware/`) builds with `-Wall -Wextra -Werror`; the fetched NimBLE/Mbed TLS sources do not.
   The test suite skips `tests/firmware` (with the reason) when no 32-bit toolchain links;
   run it locally with `tools/ci.sh firmware`.
1. **32-bit build.** NimBLE's `porting/nimble/Makefile.defs` forces `-m32` ("places in NimBLE assume
   4-byte pointers"); the ESP32-S3 is 32-bit too, so we keep it. CI installs `gcc-multilib
   g++-multilib`. On a non-x86 Linux (e.g. Docker on Apple silicon) cross-build with
   `CROSS_COMPILE=i686-linux-gnu-` and run the binary through qemu-i386 binfmt.
2. **Mbed TLS from source, static.** A 32-bit `libmbedtls-dev` is not installable on stock
   Ubuntu, so `fetch_nimble.sh` clones Mbed TLS and the Makefile builds `libmbed{tls,x509,crypto}.a`
   with `-m32` (headers from the same tree, not the system's).
3. **syscfg.** `host/syscfg/syscfg.h` is the 1.10 example's `syscfg.h` with exactly these changes:
   `BLE_SOCK_TYPE__linux_blue 0`, `BLE_SOCK_TYPE__linux_tcp 1`, `BLE_SM_BONDING 1`, `BLE_SM_SC 1`,
   `BLE_SM_MITM 1`, `BLE_SM_IO_CAP BLE_HS_IO_KEYBOARD_ONLY`, `BLE_SM_OUR_KEY_DIST 0x07`,
   `BLE_SM_THEIR_KEY_DIST 0x07`, plus **LE Secure Connections only**: `BLE_SM_LEGACY 0`,
   `BLE_SM_SC_ONLY 1` — a peer that answers without the SC bit gets SMP Pairing Failed
   (Authentication Requirements, 0x03) before any passkey (checked against a fake unit with
   `sc=False`). NimBLE includes `"syscfg/syscfg.h"`, so the include path lists
   `firmware/host` itself (not `firmware/host/syscfg`) **before** the example's include dir.
4. **Init order (1.10).** `nimble_port_init()` already runs `ble_transport_ll_init()` →
   `ble_hci_sock_init()`, which connects to `127.0.0.1:<port>` — so `ble_hci_sock_set_device(port)`
   must come first, and the Bumble TCP server must already listen. The socket transport's RX queue
   needs its own thread (`ble_hci_sock_ack_handler`); the host runs `nimble_port_run()` on the main
   thread. The bond store is `ble_store_config_init()` (1.10; RAM-only here).
5. **Bumble: legacy advertising reports.** Bumble's `Controller` answers even a legacy scan
   (`HCI_LE_Set_Scan_Enable`, what NimBLE sends with `BLE_EXT_ADV=0`) with LE *Extended*
   Advertising Reports while it advertises the `LE_EXTENDED_ADVERTISING` feature; NimBLE ignores
   those and never sees the unit. The harness clears that feature bit on the firmware-side
   controller (a real controller reports in the format of the scan command used).
6. **Bumble: public-address links.** Bumble's `LocalLink` stamps every LE ACL packet with the
   sender controller's `random_address`, even when the host connected with its public address. The
   harness gives the firmware-side controller a public address (Bumble defaults to `00:00:00:00:00:00`,
   which NimBLE treats as "no public address") and mirrors it, typed PUBLIC, into `random_address`.
7. **Pairing.** The fake unit refuses Just Works, so the host pairs as KEYBOARD_ONLY + MITM + SC; the
   unit displays the passkey (`FakeUnit.next_passkey()`), the test types it on the firmware's stdin.
   A wrong passkey ends in SMP Pairing Failed (confirm value failed) → `FAIL enc_change 1284`.
8. **Bumble: ACL takes a connection event.** Bumble's `LocalLink` delivers ACL with zero latency.
   NimBLE's host drains its whole ACL RX queue in one go (`ble_hs_process_rx_data_queue`) while
   HCI events wait behind it on the event queue; with zero latency the unit's first
   key-distribution PDU (Identity Information, sent the instant its side sees the encryption
   change) was processed before our Encryption Change event, and NimBLE failed pairing with SMP
   "Unspecified reason" (seen on the x86 CI runner, not under qemu locally). On a real radio that
   PDU needs at least one more connection event (>= 7.5 ms), so the harness delays LE ACL by 10 ms
   (`ACL_LATENCY_S`), FIFO; LL control PDUs stay immediate.

## Traceability

**R_FW_PAIRING_SM** — C twin of the pairing SM. `firmware/components/cali_core/pairing_sm.c` is a
platform-free C port of `calictl/pairing.py`'s `step()` (itself `R_PAIRING_SM`): same pinned
state/event/action enums (`csrc/pairing_consts.h`, GENERATED from the Python module), the same
transition table, no strings/clock/BLE calls in the SM itself — only `PAIR_EV_TIMEOUT` from a
platform timer and the opaque `PAIR_ACT_PERSIST_BOND` action cross the boundary. Verified by
**T_FW_PAIRING_SM_PARITY**: replaying `tests/vectors/pairing.json` through both the Python and C
implementations and asserting identical `(state, actions)` at every step
(`tests/firmware/test_pairing_sm_parity.py`).

These `.. req::` / `.. test::` IDs are declared in that test module's own docstring — a Python
"shim" sphinx-needs can parse, since the real implementation is C (whose comments sphinx-needs
does not collect). `docs/api.rst` pulls the module in via `.. automodule::
tests.firmware.test_pairing_sm_parity`, so both objects and their `:links:` resolve in the
`sphinx -b needs` build today. Task 10 gives firmware its own `docs/firmware.md` page and may move
the autodoc entry there; either way this file is the human-readable trace back to the source.
