# firmware — ESP32 satellite (#154)

ESP-IDF + NimBLE firmware for the camper-unit satellite. Work in progress: the host build
(`host/host_main.c` -> `cali-host`: console, pairing runner and session on the NimBLE Linux port),
the platform-free component `components/cali_core` (below), and the ESP-IDF project for the
esp32s3 / M5Stack CoreS3 (`main/app_main.c`, compile-only so far: no hardware yet).

## ESP-IDF build (`firmware/`, esp32s3)

`firmware/` is an ESP-IDF project (`CMakeLists.txt`, `sdkconfig.defaults`, `main/`,
`components/`). CI job: `firmware-build` (container `espressif/idf:v6.1`; uploads the images and
`flasher_args.json` as the `firmware-esp32s3` artifact). Locally, from the repo root (the whole
repo is mounted: `components/csrc` wraps the generated codec in `../csrc`):

```
docker run --rm -v "$PWD":/project -w /project/firmware espressif/idf:v6.1 idf.py build
docker run --rm -v "$PWD":/project -w /project/firmware espressif/idf:v6.1 idf.py size
```

(The image is multi-arch, amd64 + arm64: on Apple silicon it runs natively.) Outputs (`build/`,
`sdkconfig`, `sdkconfig.old`) are gitignored; `sdkconfig.defaults` is the committed configuration.

| Component | ESP-IDF sources | Host-build-only files (not in the ESP build) |
|---|---|---|
| `main` | `app_main.c` — device twin of `host/host_main.c` | — |
| `cali_core` | `pairing_sm.c runner.c session.c console.c` | `test/` |
| `cali_ble_nimble` | `ble_nimble.c ble_store_kv.c` (UNCHANGED from the host build) | `test/` |
| `platform` | `esp/platform_esp.c` — `cali_kv_*` on NVS namespace `cali`, same CRC record as the host (bad CRC -> -2); `cali_uptime_ms` from `esp_timer`; `cali_log` -> `printf("LOG …")` | `host/`, `test/` |
| `csrc` | `../../../csrc/codec.c` with `CODEC_NO_ENCODE` PUBLIC (read-only firmware) | — |

- **Read-only.** `codec_encode` is compiled out (`CODEC_NO_ENCODE`, as on the host), and a
  POST_BUILD step in `CMakeLists.txt` fails the build if `codec_encode` is in `cali_fw.elf` (the
  twin of the `cali-host` link check). The only write stays the 1003 heartbeat.
- **Warnings.** Our components build with the host's `-Wall -Wextra -Werror` strictness:
  `cmake/cali_strict.cmake` re-enables what ESP-IDF's global flags relax (`-Wno-error=extra`,
  `-Wno-unused-parameter`, `-Wno-sign-compare`, `-Wno-enum-conversion`, `-Wno-error=unused-*`);
  a plain `-Wall -Wextra -Werror` would be de-duplicated by CMake into the relaxed global flags.
- **Config** (`sdkconfig.defaults`): the plan's values plus the host `syscfg.h` SM settings —
  `CONFIG_BT_NIMBLE_SM_LEGACY=n` + `CONFIG_BT_NIMBLE_SM_SC_ONLY=1` (LE Secure Connections only),
  `CONFIG_BT_NIMBLE_HOST_BASED_PRIVACY=n` (RPA resolution in the controller, as the host harness
  emulates; the option is ESP32-only in Kconfig, so on the S3 it is off regardless and Kconfig notes
  the value as "not visible"), `CONFIG_BT_NIMBLE_NVS_PERSIST=n` (bonds go through
  `ble_store_kv.c` onto `cali_kv_*` = NVS: one store path on both builds), and
  `CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=8192` (the host task runs all of cali_core). Bond/CCCD
  slots stay at the defaults, equal to the host's (`MAX_BONDS=3`, `MAX_CCCDS=8`).
- **Tasks** (`main/app_main.c`): the NimBLE host task makes every cali_core/transport call, as
  `main` does on the host. The UART console task queues lines (FreeRTOS queue, 16 x 128) and posts
  one NimBLE event; the 100 ms `esp_timer` sets a tick flag and posts the same event; its callback
  runs the tick, then the queued lines (lines only after sync). Console = UART0
  (`CONFIG_ESP_CONSOLE_UART_DEFAULT`) through the UART driver + VFS; `quit` is ignored (no
  `cali_console_on_quit` on the device).
- **No controller** (`nimble_port_init()` fails in `esp_bt_controller_init`, e.g. QEMU):
  `LOG ble: controller unavailable`, then the console runs on a transport that refuses every
  operation, owned by a `cali_core` FreeRTOS task (woken by task notifications instead of the
  NimBLE event): it prints the idle `STATE` and answers lines.
- **API skew vs the host's NimBLE 1.10: none.** `cali_ble_nimble` compiles unchanged against the
  IDF v6.1 esp-nimble (1.6.0-based) under the strict flags above: no `#if` was needed in
  `ble_nimble.c` or `ble_store_kv.c`. `ble_store_kv.c`'s `syscfg/syscfg.h` resolves to esp-nimble's
  (which maps `MYNEWT_VAL`s onto Kconfig via `esp_nimble_cfg.h`). Compile-time only: runtime
  behaviour on esp-nimble is unproven until hardware.

## Host build (`firmware/host`)

The CI end-to-end tests run the firmware's BLE code on Linux against the **upstream NimBLE Linux
port**, talking HCI over TCP to a Bumble virtual controller that shares a `LocalLink` with the repo's
fake unit (`tools/fake_unit_peripheral.py`). Harness: `tests/firmware/conftest.py`; CI job:
`firmware-host-e2e`.

```
firmware/host/fetch_nimble.sh      # clones the pinned NimBLE + Mbed TLS into firmware/host/_deps (gitignored)
make -C firmware/host cali-host    # Linux only; needs a 32-bit toolchain (see below)
python -m pytest tests/firmware -v
```

`cali-host --hci-port <tcp-port> [--store <dir>]` speaks the console line protocol on
stdin/stdout (`components/cali_core/include/cali_console.h`): `pair`, `passkey N`, `forget`,
`status`, `quit` in; `STATE {json}` (calictl's `/api/pairing` keys), `SNAP {"t":ms,"fn":{...}}`
(every state function, `codec_decode`d) and `LOG text` out. Without a stored bond it boots `idle`
and never scans; with one it reconnects by bond. The NimBLE host task (main thread) makes every
cali_core/transport call; the stdin thread only queues lines and posts one NimBLE event; a 100 ms
callout drives the runner/session timers.

On a Mac, run the Linux-only tests in Docker (arm64 image with an i686 cross gcc + qemu-i386
binfmt, as used for this branch):

```
docker run --rm -v "$PWD":/w -w /w -e CROSS_COMPILE=i686-linux-gnu- \
    -e QEMU_LD_PREFIX=/usr/i686-linux-gnu <image> python3 -m pytest tests/firmware -v
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
— macOS included, unlike the BLE e2e tests (`tests/firmware/test_host_e2e.py`, marked
`linux_only`, which need the 32-bit NimBLE Linux host build; the default test run deselects them
with `-m "not linux_only"`).

## Pins

| What | Pin | Why |
|---|---|---|
| ESP-IDF | **v6.1** (`firmware-build`, container `espressif/idf:v6.1`) — the highest stable release on 2026-09-27; GitHub's newest non-prerelease *by date* is the v5.3.6 maintenance release, so the pin is by version, not by date | its `components/bt/host/nimble/nimble` submodule is esp-nimble `139cada0ae93` on branch `nimble-1.6.0-idf`, i.e. based on **upstream NimBLE 1.6.0** (merge base "Prepare for NimBLE 1.6.0 release") + ~440 Espressif commits |
| Upstream NimBLE (host build) | `nimble_1_10_0_tag` | **gap vs ESP-IDF: 1.6.0 → 1.10.0.** Upstream's TCP socket transport (`BLE_SOCK_USE_TCP`) is incomplete up to 1.9.0 — `ble_hci_sock_cmdevt_tx` (and, before 1.8.0, `ble_hci_sock_acl_tx`) exists only for the `linux_blue`/`nuttx` variants, so a 1.6.0–1.9.0 TCP build does not link (verified for 1.6.0: `undefined reference to ble_hci_sock_cmdevt_tx`). 1.10.0 is the first release whose `linux_tcp` choice is complete. The host API used here (GAP disc/connect, SM passkey, GATT client, `ble_store_config`) is unchanged across the gap. |
| Mbed TLS (host build) | `mbedtls-3.6.5` | NimBLE 1.10 dropped the bundled TinyCrypt; SM crypto needs Mbed TLS, and the build is 32-bit (below), so it is built from source — same version upstream NimBLE's port CI uses |

## Host build notes (every NimBLE / Bumble adaptation)

0. **NimBLE pin (ruling R5).** ESP-IDF v6.1 vendors esp-nimble based on NimBLE 1.6.0, whose socket
   transport does not link in TCP mode (no `ble_hci_sock_cmdevt_tx` for `BLE_SOCK_USE_TCP`), so the
   host build pins upstream `nimble_1_10_0_tag`. API skew between the two is caught by Task 8, which
   compiles `cali_ble_nimble` against esp-nimble in the `firmware-build` job.

1. **Warnings.** Our C (`OWN_OBJ` in the Makefile: `host_main.o`, `store_cli.o` plus the component
   objects `platform_host.o`, `ble_store_kv.o`, `ble_nimble.o`, `runner.o`, `pairing_sm.o`,
   `console.o`, `session.o` built from `firmware/components/`, `codec.o` from `csrc/`, and
   `ble_nimble_jw.o` for the `cali-host-jw` regression build — note 11) builds with `-Wall -Wextra -Werror`; the fetched NimBLE/Mbed TLS sources do not.
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
   thread. The persistent store, `cali_ble_store_init()` (`components/cali_ble_nimble/ble_store_kv.c`), keeps
   `ble_store_config.c`'s matching rules but persists each record through the CRC-checked
   `cali_kv_*` store (`components/platform`; host: one `<key>.kv` file per key under `--store`),
   so a torn/corrupt record reads as "no bond" (`LOG store: corrupt record <key> ignored`), and a
   duplicate left by a delete-compaction cut short by power loss loads once (`LOG store: duplicate
   record <key> ignored`). `make store-cli` builds the store's test driver against the NimBLE
   objects (`tests/firmware/test_ble_store_kv.py`, `linux_only`).
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
   A wrong passkey ends in SMP Pairing Failed (confirm value failed) → ENC_FAIL → the runner
   retries (`pairing_failed` after 3 attempts).
8. **Bumble: ACL takes a connection event.** Bumble's `LocalLink` delivers ACL with zero latency.
   NimBLE's host drains its whole ACL RX queue in one go (`ble_hs_process_rx_data_queue`) while
   HCI events wait behind it on the event queue; with zero latency the unit's first
   key-distribution PDU (Identity Information, sent the instant its side sees the encryption
   change) was processed before our Encryption Change event, and NimBLE failed pairing with SMP
   "Unspecified reason" (seen on the x86 CI runner, and under qemu-i386 with a 10 ms delay). On a
   real radio that PDU needs at least one more connection event — the interval NimBLE requests by
   default is 30-50 ms (`BLE_GAP_INITIAL_CONN_ITVL_MIN/MAX`) — so the harness delays LE ACL by
   30 ms (`ACL_LATENCY_S`), FIFO; LL control PDUs stay immediate.
9. **Reconnect by bond.** `connect_bonded()` connects to the stored identity address and then
   calls `ble_gap_security_initiate` itself (the stored LTK re-encrypts; no passkey), so the
   session sees CONNECTED then ENC_OK/ENC_FAIL. Every heartbeat write completion is reported as
   `CALI_TEV_HEARTBEAT`; a failed or unwritable beat counts as a lost link (reconnect with backoff).
   A GATT operation that fails with `BLE_HS_ENOTCONN` is not reported (the queue is dropped and the
   disconnect follows): reporting it made the session start its next read on the dying link, a
   GATT procedure NimBLE then kept for the connection handle the next link reused, stalling that
   link until the 30 s ATT timeout. `disconnect()` during a connect tracks the cancel until its
   CONNECT event: a scan asked for meanwhile is deferred, a link that completed first is dropped.

10. **Bumble: resolving list, connect cancel, lost host.** The firmware-side controller is a
   `Controller` subclass (`tests/firmware/conftest.py`, `_fw_controller_class`) adding three
   controller duties Bumble leaves out. (a) *LL privacy:* NimBLE writes the bonded unit's identity
   + IRK to the controller's resolving list and reconnects by the identity address; the controller
   must match the unit's current RPA. Bumble ignores the list, so a restart after the unit rotated
   its address never connected. The harness resolves the advertiser's RPA with the listed IRK,
   connects to it and reports the identity in LE Connection Complete. (b) *LE Create Connection
   Cancel* now ends the pending connection with a Connection Complete (Unknown Connection
   Identifier), per Vol 4 Part E 7.8.13; Bumble answered success and kept it pending, so NimBLE's
   connect procedure never ended. (c) *A lost host ends its links:* when `cali-host` exits (the
   ESP32 reboots) the unit sees a supervision timeout; Bumble's controller outlived the TCP host
   and held the unit's only connection slot. Test knobs on the same controller make the
   connect-cancel races deterministic (`hold_connects`, `cancel_delay_s`, `connect_on_cancel`,
   used by `test_unit_forgot_us_repairs`), and the fake unit's `drop_on_read` hangs up on one GATT
   read so a link is lost mid read-all (`test_link_drop_mid_read_all_reconnects`).
11. **Just Works regression build (`make cali-host-jw`).** `ble_nimble.c` compiled with
   `-DCALI_TEST_LATE_IO_CAP`: the IO capability is NoInputNoOutput (no MITM) until `pair()` has
   sent the Pairing Request, then set to KEYBOARD_ONLY — the 2026-09-26 calictl bug (the agent
   arrived after SMP had started). NimBLE copies the capability into the Pairing Request inside
   `ble_gap_security_initiate`, so "late" means after that call. SC stays on (the build is SC-only),
   so the unit's refusal is of Just Works itself (`confirm()`), not of legacy pairing.
   `test_just_works_build_is_refused` asserts it ends in `error`. The macro is an `#error` in an
   ESP-IDF build (`ESP_PLATFORM`).

## Traceability

**R_FW_PAIRING_SM** — C twin of the pairing SM. `firmware/components/cali_core/pairing_sm.c` is a
platform-free C port of `calictl/pairing.py`'s `step()` (itself `R_PAIRING_SM`): same pinned
state/event/action enums (`csrc/pairing_consts.h`, GENERATED from the Python module), the same
transition table, no strings/clock/BLE calls in the SM itself — only `PAIR_EV_TIMEOUT` from a
platform timer and the opaque `PAIR_ACT_PERSIST_BOND` action cross the boundary. Verified by
**T_FW_PAIRING_SM_PARITY**: replaying `tests/vectors/pairing.json` through both the Python and C
implementations and asserting identical `(state, actions)` at every step
(`tests/firmware/test_pairing_sm_parity.py`).

**R_FW_PAIRING_RUNNER** — the pairing runner. `firmware/components/cali_core/runner.c` drives that
SM from BLE transport events (`cali_transport.h`; NimBLE implementation
`components/cali_ble_nimble/ble_nimble.c`) per `docs/business-logic/guided-pairing.md` "ESP
mapping", runs its actions as transport calls and its `PAIR_TIMEOUT_S` timeouts from a tick.
Verified by **T_FW_RUNNER_FAKE** (`tests/firmware/test_runner_fake.py`): a scripted fake
transport (`cali_core/test/runner_fake.c`) asserts the call/state sequences — happy path, ignored
passkey, retries to `connect_failed`/`pairing_failed`, timeouts (incl. a pairing NimBLE refuses
without an encryption change), link drops, verify failure, forwarding, forget.

**R_FW_SESSION** — the session and console. `cali_core/session.c` discovers, subscribes, reads
every `CODEC_CHARS` function and holds one whole frame per function, runs the 1003 heartbeat and
reconnects by bond with 1 s -> 60 s backoff; `cali_core/console.c` is the line protocol. Verified
by **T_FW_SESSION_FAKE** (`tests/firmware/test_session_fake.py`, scripted transport, macOS too) and
end to end by **T_FW_HOST_E2E** (`tests/firmware/test_host_e2e.py`, `linux_only`): `cali-host`
pairs with the Bumble fake unit, its `SNAP` equals `calictl.protocol.decode` of every frame the
fake served (a truncated frame too), the heartbeat keeps the link for 20 s, a pushed notification
replaces exactly that function, and a passkey typed while idle is ignored.

These `.. req::` / `.. test::` IDs are declared in that test module's own docstring — a Python
"shim" sphinx-needs can parse, since the real implementation is C (whose comments sphinx-needs
does not collect). `docs/api.rst` pulls the module in via `.. automodule::
tests.firmware.test_pairing_sm_parity`, so both objects and their `:links:` resolve in the
`sphinx -b needs` build today. Task 10 gives firmware its own `docs/firmware.md` page and may move
the autodoc entry there; either way this file is the human-readable trace back to the source.
