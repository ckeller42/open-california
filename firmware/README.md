# firmware — ESP32 satellite (#154)

ESP-IDF + NimBLE firmware for the camper-unit satellite. Work in progress: the host build
(`host/host_main.c` -> `cali-host`: console, pairing runner and session on the NimBLE Linux port),
the platform-free component `components/cali_core` (below), and the ESP-IDF project for the
esp32s3 / M5Stack CoreS3 (`main/app_main.c`; runs on a bench CoreS3 against the mock unit, not yet
against the real camper unit).

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
`build-qemu/`, `sdkconfig`, `sdkconfig.old`) are gitignored; `sdkconfig.defaults` is the committed
configuration.

**Two variants, one source — the console transport differs (ruling R9):**

| Variant | Console | Build |
|---|---|---|
| release (the CoreS3) | **USB-Serial/JTAG** (`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`): the CoreS3's USB-C is the S3's native USB (`/dev/cu.usbmodem*`, 303a:1001); UART0 (G43/G44) only reaches the M5-Bus header, so `pair`/`passkey` could never be typed over USB on a UART console | `idf.py build` |
| QEMU (Task 9) | **UART0** (`qemu/sdkconfig.qemu`: `CONFIG_ESP_CONSOLE_UART_DEFAULT=y`): QEMU's esp32s3 machine emulates UART, not USB-Serial/JTAG. Also `CONFIG_CALI_QEMU_PROBE=y` (below) | `idf.py -B build-qemu -D SDKCONFIG=build-qemu/sdkconfig -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;qemu/sdkconfig.qemu" build` |

`app_main.c` picks the driver at compile time (`usb_serial_jtag_read_bytes` + `usb_serial_jtag_vfs_use_driver`,
or `uart_read_bytes` + `uart_vfs_dev_use_driver`; any other console is an `#error`); the line
protocol is identical. Give the QEMU variant its own `SDKCONFIG` (above): `idf.py` otherwise
reuses `firmware/sdkconfig` of the release build, and a stale `sdkconfig` beats
`SDKCONFIG_DEFAULTS`.

| Component | ESP-IDF sources | Host-build-only files (not in the ESP build) |
|---|---|---|
| `main` | `app_main.c` — device twin of `host/host_main.c` | — |
| `cali_core` | `pairing_sm.c runner.c session.c console.c control.c control_run.c` (+ the WiFi/web files, `wifi_sm.c wifi_run.c http_core.c captive_dns.c web.c snapshot.c status.c display_model.c`) | `test/` |
| `cali_ble_nimble` | `ble_nimble.c ble_store_kv.c` (UNCHANGED from the host build) | `test/` |
| `platform` | `esp/platform_esp.c` — `cali_kv_*` on NVS namespace `cali`, same CRC record as the host (bad CRC -> -2); `cali_uptime_ms` from `esp_timer`; `cali_log` -> `printf("LOG …")`; `cali_fw_version` = `esp_app_get_description()->version`. `esp/net_esp.c` — `cali_net` on esp_wifi + esp_netif + lwIP sockets + mdns (`include/cali_net_esp.h`) | `host/`, `test/` |
| `csrc` | `../../../csrc/codec.c` (decode + encode) | — |

- **Writes: five control chars + `1003`, never the roof.** A control frame goes out only through
  the transport's `write`, which — like the sequencer `cali_core/control_run.c` before it — passes
  `cali_ctl_write_ok` (the generated allow-list: `1101`/`1201`/`1501`/`1601`/`1701` at their exact
  frame length; `R_FW_WRITE_ALLOWLIST` in `docs/firmware.md`). The 1003 heartbeat is the other
  write; `subscribe`'s CCCD descriptor writes only enable notifications. (Until #154 B the firmware
  was read-only — `CODEC_NO_ENCODE` + an `nm` check; `R_FW_READ_ONLY`, now retired.)
- **NimBLE options pinned to the host tier** (final review I1, `sdkconfig.defaults`): no in-stack
  connection re-attempt, one connection, legacy scan only (no extended adv/scan), NimBLE log
  level WARNING — so the device runs the configuration the host e2e tier proved.
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
  `CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=8192` (the host task runs all of cali_core), and
  `CONFIG_BT_NIMBLE_STATIC_TO_DYNAMIC=n` (next bullet). Bond/CCCD
  slots stay at the defaults, equal to the host's (`MAX_BONDS=3`, `MAX_CCCDS=8`).
- **esp-nimble replaces the bond store at sync unless told not to (Task 8 review C1).** With the
  IDF default `CONFIG_BT_NIMBLE_STATIC_TO_DYNAMIC=y`, `ble_hs_startup_go()` (no
  `store_gen_key_cb`) calls `ble_hs_pvcy_set_default_irk()` (esp-nimble
  `nimble/host/src/ble_hs_startup.c:563-578`), which under `#if MYNEWT_VAL(BLE_STATIC_TO_DYNAMIC)`
  runs `ble_store_config_init()` (`nimble/host/src/ble_hs_pvcy.c:327-338`): that sets
  `ble_hs_cfg.store_*_cb` to the RAM-only `ble_store_config` (`store/config/src/ble_store_config.c:1236-1238`)
  before `sync_cb` runs (`ble_hs_sync`, `ble_hs.c:466`: `startup_go` at `:477`, `sync_cb` at `:495-496`) — new bonds lost on reboot, NVS bonds
  invisible. Two guards: `STATIC_TO_DYNAMIC=n` (then `ble_store_config_init` is not even linked:
  absent from `cali_fw.elf`), and `cali_ble_store_ensure()` in the transport's sync callback, which
  puts our callbacks back and logs `LOG store: ERROR bond store callbacks were replaced by the BLE
  stack, re-installed`. **The host tier cannot catch this naturally:** upstream NimBLE 1.10's
  `ble_hs_pvcy.c` has no such call (it is an Espressif addition), so
  `tests/firmware/test_ble_store_kv.py::test_store_replaced_by_the_stack_is_reinstalled` emulates
  it (store-cli `clobber` = `ble_store_config_init()`) and proves only the guard. That esp-nimble
  itself leaves the store alone stays a **hardware watch item** (first flash: pair, reboot, the
  unit must reconnect by bond without a passkey; no `store: ERROR` line).
- **Tasks** (`main/app_main.c`): the NimBLE host task makes every cali_core/transport call, as
  `main` does on the host. The UART console task queues lines (FreeRTOS queue, 16 x 128) and posts
  one NimBLE event; the 100 ms `esp_timer` sets a tick flag and posts the same event; its callback
  runs the tick, then the queued lines (lines only after sync). Console = USB-Serial/JTAG (release)
  or UART0 (QEMU), each through its driver + VFS (table above); `quit` is ignored (no
  `cali_console_on_quit` on the device).
- **No controller** (`nimble_port_init()` returns an error, or the QEMU build skips it):
  `LOG ble: controller unavailable`, then the console runs on a transport that refuses every
  operation, owned by a `cali_core` FreeRTOS task (woken by task notifications instead of the
  NimBLE event): it prints the idle `STATE` and answers lines. Under QEMU `esp_bt_controller_init`
  does **not** return an error: it asserts (`btdm_low_power_mode_init`, `assert(select_src_ret &&
  set_div_ret)`, QEMU models no BT low-power clock) and the chip reboots in a loop, so the QEMU
  build does not call `nimble_port_init()` at all (`CONFIG_CALI_QEMU_PROBE`).
- **API skew vs the host's NimBLE 1.10: none.** `cali_ble_nimble` compiles unchanged against the
  IDF v6.1 esp-nimble (1.6.0-based) under the strict flags above: no `#if` was needed in
  `ble_nimble.c` or `ble_store_kv.c`. `ble_store_kv.c`'s `syscfg/syscfg.h` resolves to esp-nimble's
  (which maps `MYNEWT_VAL`s onto Kconfig via `esp_nimble_cfg.h`). Compile-time only: runtime
  behaviour on esp-nimble is unproven until hardware.

### WiFi build (Task 9: `cali_net` on the device)

- **What runs:** `main/app_main.c` inits `esp_netif` + the default event loop (NimBLE creates none),
  then `cali_net_esp_init()` (`components/platform/esp/net_esp.c`): STA + AP netifs, the AP pinned
  to `NET_AP_ADDR`/24 with its DHCP server offering itself as DNS (the captive DNS), esp_wifi in STA
  mode with `WIFI_STORAGE_RAM` (credentials live in `cali_kv`, `CONFIG_ESP_WIFI_NVS_ENABLED=n`), the
  setup AP pre-configured (WPA2-PSK, channel 1, 4 clients) so switching to APSTA never shows the
  driver's open default AP. Then the WiFi runner, the web endpoints on port 80 and the runner's
  boot. The 100 ms tick runs `cali_net_esp_poll` -> `cali_wifi_run_tick` -> `cali_captive_dns_poll`
  -> `cali_web_poll` (host_main.c's order) on the owner task.
- **Events:** the ESP event task's handlers only copy WiFi/IP events into a 16-entry ring
  (critical section; full = the oldest is dropped, logged once as `LOG net: event queue full, oldest
  dropped`); the poll on the owner task is the only caller of the runner's sink. Two watchdogs end
  every started operation: a join with no outcome after 30 s -> `STA_FAILED(other)` (the WiFi SM has
  no CONNECTING timeout; e.g. associated but no DHCP lease), a scan without `SCAN_DONE` after 15 s
  -> an empty `SCAN_DONE`. Old-join events are never attributed to a new join: join generations
  (every station event is tagged in the ring with the generation current when it was queued; a
  replacing `sta_start`, `sta_stop` and the watchdog bump it, so earlier events are dropped whatever
  their reason; the end of an attempt abandoned by our own `esp_wifi_disconnect` is swallowed once,
  for at most 5 s). `sta_start` with the same ssid+psk as a join in flight keeps it (the SM's
  1/2/4 s retries do not restart the driver). A GOT_IP with a changed address while up is ignored,
  and the queue-overflow line is logged once per boot. Failure reasons: `NO_AP_FOUND` -> `not_found`; `AUTH_FAIL`,
  `4WAY_HANDSHAKE_TIMEOUT`, `HANDSHAKE_TIMEOUT` -> `auth`; anything else -> `other`.
- **Sockets:** lwIP BSD, `O_NONBLOCK`, `SO_REUSEADDR` on the listener, `TCP_NODELAY` on accepted
  sockets, bound to `INADDR_ANY`; `CONFIG_LWIP_MAX_SOCKETS=6` (listener + connection + one closing +
  captive DNS = 4). DHCP hostname = `NET_HOSTNAME`, set in code (`esp_netif_set_hostname` on both netifs), never
  copied into `sdkconfig.defaults`.
- **mDNS:** managed component **`espressif/mdns` pinned `==1.13.1`** (`components/platform/idf_component.yml`;
  the newest the registry served for IDF v6.1 on 2026-09-28). The repo does not commit
  `dependencies.lock` (gitignored with `managed_components/`), so the exact pin is the manifest's.
  `mdns_announce` = `mdns_init` (once) + `mdns_hostname_set` + `mdns_service_add(NULL, "_http",
  "_tcp", port)` (or `mdns_service_port_set` when it exists).
- **The page:** one source of bytes. `web.c` serves `strings_gen.h`'s `WEB_INDEX_HTML` (the rendered
  `web/index_gen.html`, generated by `tools/gen_c_dict.py`) on both tiers; there is no `EMBED_FILES`
  (the brief's `EMBED_FILES ../web/index.html` is superseded: it would have been a second copy).
- **Routes:** `/` in station mode = the calictl UI (`app_bundle_gen.h`, `Content-Encoding: gzip`);
  `/device` = the status/setup page in every mode (and `/` outside station mode); `POST /api/command`
  = calictl's control request, station mode only (403 `setup_mode` elsewhere), answered after the
  write ACKs in calictl's shape (`cali_web.h`).
- **Version:** `firmware/CMakeLists.txt` sets `PROJECT_VER` from `git -c safe.directory=* describe
  --always --tags --dirty` at configure time ("unknown" without git) -> `esp_app_desc_t.version` ->
  `/api/state` `device.fw`. The QEMU boot prints it (`app_init: App version: e281649-dirty`).
- **No WiFi driver:** `CONFIG_CALI_WIFI=n` (the QEMU overlay; `main/Kconfig.projbuild`) compiles no
  esp_wifi call (`esp_wifi_init`/`mdns_init` absent from the QEMU ELF), and a release image whose
  `esp_wifi_init`/`esp_wifi_start` fails takes the same path: `LOG wifi: driver unavailable` once,
  the WiFi SM stays `WIFI_UNPROVISIONED` (STATE has no `wifi` member, `wifi status` -> `LOG wifi: not
  enabled`), nothing listens, no reboot.
- **Partition table (ruling R18):** `partitions.csv` (`CONFIG_PARTITION_TABLE_CUSTOM=y`) replaces
  IDF's single-app-large table: the same nvs (0x9000, 0x6000) + phy_init (0xf000, 0x1000) and one
  factory app at 0x10000, now **3 MB (0x300000)** instead of 1500K; flash stays 16 MB. Flash offsets
  unchanged (`flasher_args.json`: bootloader 0x0, partition table 0x8000, app 0x10000); the QEMU
  flash image is padded to the build's `flash_size` (16MB), so it matches without change. After
  switching, delete the stale `firmware/sdkconfig` / `build-qemu/sdkconfig` (and
  `build-qemu/qemu_flash.bin`, whose partition table is the old one).
- **Size (`idf.py size`, release):** `cali_fw.bin` before WiFi 0x71190 = 463,248 B (70 % of the old
  1,536,000 B app partition free) -> with WiFi **0x107660 = 1,078,880 B** (+615,632 B: net80211,
  wpa_supplicant + PSA crypto, lwIP, pp/phy, mdns) = **66 % of the 3 MB partition free** (it would
  have been 30 % of the old 1.5 MB one). The plan's ~250 KB estimate for WiFi + lwIP was wrong by
  about 2.5x. DIRAM 103,802 -> 160,018 B used (46.8 %). QEMU image (no WiFi): 0x55cf0 B, 89 % free.
- **Size with the status display (#154, Task 1 spike):** `cali_fw.bin` **0x172400 = 1,516,544 B**
  (+437,664 B: LVGL 9.6 + `esp_lvgl_port` + the CoreS3 board package) = **52 % of the 3 MB partition
  free**. DIRAM 178,222 B (52.2 %; 160,018 B before the display): LVGL uses the C heap (PSRAM) and the
  draw buffer is one 320x20 internal DMA strip. QEMU image (`CONFIG_CALI_DISPLAY=n`): `cali_fw.bin`
  0x5d630 = 382,512 B, 88 % free (was 0x55cf0 B: the shared `sdkconfig.defaults` PSRAM lines are off
  there, so the delta is not display code; the QEMU map links no lvgl/BSP member). The `idf.py size`
  "IRAM 100 %" row is the fixed 16 KB slice on the S3, not a budget; DIRAM is the real one.
- **Size with the painted status screen (#154, Task 4):** `cali_fw.bin` **0x17e130 = 1,564,976 B**
  (+48,432 B: the two generated Latin-1 fonts, 16 + 24 px, 4 bpp uncompressed, plus the painter) =
  **50 % of the 3 MB partition free**. DIRAM 178,558 B (52.2 %, +336 B). QEMU image unchanged in
  content (`CONFIG_CALI_DISPLAY=n`): 0x5d740 = 382,784 B.
- **Size with the shared calictl UI (#154, shared-UI Task 8):** `cali_fw.bin` **0x18b370 = 1,618,800 B**
  (+53,824 B: the 51,574 B gzipped bundle plus the `/device` route) = **49 % of the 3 MB partition
  free**. DIRAM 178,558 B (52.2 %, unchanged). First load on the bench (thinky → CoreS3, 2.4 GHz WiFi):
  median 960 ms to `load`, 1065 ms to a live state (`tools/esplab_ui_load.py`, 5 cold runs, 2026-10-02).
- **Display package pin:** `components/cali_display/idf_component.yml` pins `espressif/m5stack_core_s3`
  `==4.1.0` (newest on the registry for IDF v6.1 on 2026-10-01), which resolves `esp_lvgl_port` 2.9.0 and
  `lvgl/lvgl` 9.6.0~1 (kws-de's 2.0.1 / LVGL 9.5.0 pairing is the older IDF 5.5 one). LVGL 9.6 renamed
  `CONFIG_LV_MEM_SIZE_KILOBYTES` to `CONFIG_LV_MEM_SIZE` (bytes; the old name is a `-Werror` `#warning`).
  `main` requires `cali_display` unconditionally: `CONFIG_*` is undefined while IDF expands component
  requirements on a clean build, so the component itself compiles to nothing when `CONFIG_CALI_DISPLAY=n`.

## QEMU boot tier (`firmware/qemu`, CI job `firmware-qemu`)

The "chip simulator" tier: the real ESP-IDF image boots on Espressif's QEMU esp32s3 machine. It
proves boot, the console line protocol on the no-controller path, and NVS (the bond store's
`cali_kv_*` path) surviving a reboot. **No Bluetooth** (QEMU has no radio): BLE stays with the host
tier (`test_host_e2e.py`) and hardware.

- **QEMU pin:** the one ESP-IDF v6.1 pins in `tools/tools.json` — `espressif/qemu`
  **`esp-develop-9.2.2-20260417`** (`qemu-system-xtensa`, "QEMU emulator version 9.2.2
  (esp_develop_9.2.2_20260417)"), preinstalled in the `espressif/idf:v6.1` image (amd64 + arm64)
  and on `PATH` after `export.sh`. Nothing is downloaded.
- **Variant:** `qemu/sdkconfig.qemu` = UART0 console + `CONFIG_CALI_QEMU_PROBE=y`
  (`main/Kconfig.projbuild`): skips the BT controller init (above) and adds the console command
  `kvprobe set <hex>` -> `LOG kvprobe ok` / `kvprobe get [key]` -> `LOG kvprobe get <hex>|missing|corrupt`
  (key default `kvprobe`, through `cali_kv_set/get`) / `kvprobe corrupt <key>` (writes a record with a
  valid length prefix and a wrong CRC32 straight through `nvs_set_blob`, bypassing `cali_kv_set`).
  The probe build also loads the bond store (`cali_ble_store_init`) on the no-controller path, as
  `cali_ble_nimble_init` does on the BLE path, so damaged bond records meet the real NVS code. **Never in the release image:** a
  POST_BUILD step in `CMakeLists.txt` fails any build without the option whose ELF contains the
  string `kvprobe`. CI builds the variant in its own job and
  uploads nothing from it.
- **`qemu/run_qemu.sh [BUILD_DIR [FLASH_FILE]]`** (defaults `build-qemu`,
  `build-qemu/qemu_flash.bin`): `idf.py qemu`'s esp32s3 arguments (machine, eFuse image, watchdog
  off), except that the flash image is merged (`esptool merge-bin`, padded to 16MB) only when it
  is missing or older than the app — `idf.py qemu` re-merges on every run and so wipes NVS — and
  UART0 is `-serial stdio -monitor none` (no QEMU monitor multiplexed onto the console stream).
  Delete the flash file for a factory-fresh chip. Stop QEMU with SIGTERM.
- **Test:** `tests/firmware/test_qemu_boot.py` (skipped unless `CALI_QEMU=1`, and when
  `qemu-system-xtensa` is not on `PATH`): boot -> `LOG ble: controller unavailable` + idle `STATE`,
  `status` -> idle, `kvprobe set 0a0b0c`, power off, boot the same flash file, `kvprobe get` ->
  `0a0b0c`; and a CRC-broken `sec_peer_0` (the first peer-bond slot, `ble_store_kv.c`) in real NVS
  boots `LOG store: corrupt record sec_peer_0 ignored` + idle, keeps the intact records, and does not
  reboot (exactly one `ESP-ROM:` banner after 3 s); and the no-WiFi-driver path (`CONFIG_CALI_WIFI=n`):
  `LOG wifi: driver unavailable` exactly once, `status` idle without a `wifi` member, `wifi status`
  -> `LOG wifi: not enabled`, no reboot. The `qemu` fixture (`conftest.py`) runs `run_qemu.sh` under the same `Firmware` line
  reader as the host tier. Boot to `STATE` takes ~0.3 s.

Locally (repo root; the image runs natively on Apple silicon):

```
docker run --rm -v "$PWD":/project -w /project/firmware espressif/idf:v6.1 bash -c '
  . $IDF_PATH/export.sh >/dev/null &&
  idf.py -B build-qemu -D SDKCONFIG=build-qemu/sdkconfig -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;qemu/sdkconfig.qemu" build &&
  cd /project && pip install -q pytest && CALI_QEMU=1 python -m pytest tests/firmware/test_qemu_boot.py -v'
```

Interactive: `docker run --rm -it -v "$PWD":/project -w /project/firmware espressif/idf:v6.1 bash -c
'. $IDF_PATH/export.sh >/dev/null && qemu/run_qemu.sh'`, type `status` / `kvprobe get`, Ctrl-C to stop.

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
`status`, `quit`, `set <function> <what> [value]` in; `STATE {json}` (calictl's `/api/pairing` keys),
`SNAP {"t":ms,"fn":{...}}` (every state function, `codec_decode`d) and `LOG text` out. Without a
stored bond it boots `idle` and never scans; with one it reconnects by bond. The NimBLE host task
(main thread) makes every cali_core/transport call; the stdin thread only queues lines and posts
one NimBLE event; a 100 ms callout drives the runner/session timers.

**Console `set`** (both builds, `console.c` → `cali_ctl_submit`): a control command in calictl's
`set` vocabulary — `set cooler power on`, `set lighting kitchen 5`, `set lighting save_profile 3
amber` (the value is the rest of the line; absent = `""`, which an on/off command refuses as not
on/off, never reads as OFF). Same gates, frames and allow-list as `POST /api/command`
(`docs/firmware.md` "Control path"); the console is physical access, so it works in every WiFi
mode. Answers: `LOG control: <fn>/<what> sending` then `… sent`, or `refused: <reason>`,
`bad value`, `no such control`, `not ready (no armed link or no state yet)`, `busy`, `failed: …`,
`timed out`; `set` alone → `LOG control: usage: set <function> <what> [value]`.

**`twrite` — a test-only stdin line of `cali-host`, not a console command.** `host_main.c` (and only
it — never `console.c`, never the ESP image) also takes `twrite <char hex> <frame hex | ->` and
hands the bytes straight to the NimBLE transport's `write`, past the sequencer, so
`tests/firmware/test_control_e2e.py` can prove the `t_write` allow-list choke point refuses a roof
frame (`1401`), the heartbeat char (`1003`), a wrong-length or an empty frame (`LOG ble: write
…/… refused: not on the control allow-list`, nonzero rc, nothing at the unit) while an allowed
frame does go out. It is a probe for that test; do not port it to the console.

`--http <port> [--fake-wifi <script>]` adds the WiFi side on the same tick: `net_host.c`'s
`cali_net` (POSIX sockets on 127.0.0.1 + the scripted fake WiFi, `cali_net_host.h`), the WiFi runner
(`wifi_run.c`), the captive DNS (UDP 53, only where bindable) and the status/setup page + `/api/*`
on `127.0.0.1:<port>`; the console gains `wifi set <ssid> <psk>` / `wifi status` / `wifi forget` /
`wifi scan` and a `"wifi"` member in `status`'s STATE line. Without `--http` nothing network-related
runs (`--fake-wifi` without `--http` is a usage error; so is a port outside 1..65535). Proven end to
end by `tests/firmware/test_web_e2e.py`. By hand (needs an HCI controller on the port, e.g. the
Bumble fake unit the tests start — `tests/firmware/conftest.py`):

```
cali-host --hci-port 9000 --store /tmp/cali --http 8081 --fake-wifi my.wifi   # then open http://127.0.0.1:8081/
```

**Fake-WiFi script** (`--fake-wifi PATH`, grammar from `components/platform/include/cali_net_host.h`):
one rule per line, `#` comments and blank lines ignored; an SSID is one whitespace-free token of
1..`NET_SSID_MAX` characters.

```
ap <ssid> <rssi> <secure>                 # a visible network (scan lists them in file order, at most NET_SCAN_MAX)
join <ssid> ok <a.b.c.d>                  # sta_start(ssid) -> STA_GOT_IP with that address
join <ssid> fail <not_found|auth|other>   # sta_start(ssid) -> STA_FAILED(reason)
join <ssid> ok-after <n> [a.b.c.d]        # the first n sta_start(ssid) fail with OTHER, then GOT_IP (default 192.168.1.100)
drop-after <ms>                           # STA_LOST <ms> after each STA_GOT_IP delivery
```

An SSID without a join rule fails `NOT_FOUND`; no script (or a missing/empty file) = no networks
visible (ruling R2). The PSK is validated (empty = open, else 8..63 characters) but otherwise ignored — the rule
decides. Every fake operation only queues its outcome, delivered by the next
`cali_net_host_poll()` on the tick (the ESP event shape). The captive DNS binds UDP 53 only where
that is allowed (root, e.g. the CI container); otherwise `LOG wifi: captive DNS unavailable` and
the rest runs.

**Console `wifi` commands** (both builds; `components/cali_core/include/cali_console.h`):

| Command | Effect / output |
|---|---|
| `wifi set <ssid> <psk>` | Stores kv `wifi_ssid`/`wifi_psk` (like `POST /api/wifi`) and hands them to the runner. Single tokens (an SSID or a password with spaces needs the page); SSID 1..32, PSK 8..63 bytes, else `LOG wifi: usage: wifi set <ssid> <psk>` / `LOG wifi: bad ssid` / `LOG wifi: bad psk`. While online/retrying/mid-join it replaces the old credentials (`LOG wifi: credentials replaced, reconnecting`) and re-joins as a setup-flow join (a typo -> setup). The line buffers are wiped after the command (on the ESP the console queue's slot keeps its copy until a later line reuses it); the PSK is never printed. |
| `wifi status` | `LOG wifi: <setup\|station\|off> ssid=<ssid\|-> ip=<a.b.c.d\|-> rssi=<dBm\|-> scan=<n>` |
| `wifi forget` | Erase the credentials, stop the station, open the setup hotspot (`cali_wifi_run_forget`). |
| `wifi scan` | Ask for a scan (held back while a BLE pairing flow is active). |

Before the WiFi runtime booted (host without `--http`, a device without a working WiFi driver)
every `wifi …` line answers `LOG wifi: not enabled` and `status` has no `wifi` member.

**The Linux-only tier on a Mac** (`test_host_e2e.py`, `test_ble_store_kv.py`, `test_web_e2e.py`):
run it in an arm64 Ubuntu container with an i686 cross gcc + qemu-i386 binfmt. The image used on
this branch (`oc-fw-host`) was built from `ubuntu:24.04` with the steps below (reconstructed
from its `docker history`; the build itself was not re-run for this doc, the test run was):

```
docker run --name oc-fw-host-build -v "$PWD":/w -w /w ubuntu:24.04 bash -c '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq gcc-i686-linux-gnu g++-i686-linux-gnu \
      libc6-dev-i386-cross qemu-user-static binfmt-support build-essential git make \
      python3 python3-pip python3-venv >/dev/null
  pip install -q --break-system-packages -r requirements-dev.txt
  ln -sf /usr/i686-linux-gnu/lib/ld-linux.so.2 /lib/ld-linux.so.2'
docker commit oc-fw-host-build oc-fw-host && docker rm oc-fw-host-build
firmware/host/fetch_nimble.sh                  # once, on the host: NimBLE + Mbed TLS into firmware/host/_deps
docker run --rm -v "$PWD":/w -w /w -e CROSS_COMPILE=i686-linux-gnu- \
    -e QEMU_LD_PREFIX=/usr/i686-linux-gnu oc-fw-host python3 -m pytest tests/firmware -v -rs
```

That run (2026-09-28, `test_web_e2e.py`: 11 passed) skips the two `test_page_renders` cases —
the image has no Playwright/Chromium; they run in CI (`firmware-host-e2e`, `CALI_REQUIRE_CHROMIUM=1`
makes them fail instead of skip) and on a Linux box with `requirements-e2e.txt` + `python -m
playwright install --with-deps chromium`.

### The page and the network constants are generated

`python3 -m tools.gen_c_dict` writes every generated file; `--check` exits 1 if a checked-in one is
stale (run by `tests/test_gen_c_dict.py`, `tests/test_web_strings.py` and CI). The network targets:

| Source | Generated | What |
|---|---|---|
| `tools/wifi_consts.py` (`CONSTS`) + `tools/wifi_sm_ref.py` (enums) | `csrc/net_consts.h` | `NET_*` constants (SSID, PSK, address, hostname, timings, size limits) + the WiFi SM's `WIFI_*`/`WEV_*`/`WACT_*` enums |
| `firmware/web/index.html` + `firmware/web/page.js` + `firmware/web/strings.json` | `firmware/web/index_gen.html` | the page with its `{{NET_*}}` placeholders, the EN/DE string table and the page script (`{{PAGE_JS}}`) filled in |
| the same | `firmware/web/strings_gen.h` | `WEB_STR_EN_*`/`WEB_STR_DE_*` + `WEB_INDEX_HTML` — `index_gen.html` byte for byte as a byte array |
| `calictl/webui/*` (`index.html`, `app.css`, the scripts incl. `semantics.js`) | `firmware/web/app_bundle_gen.h` | `WEB_APP_HTML_GZ` / `WEB_APP_HTML_GZ_LEN` — calictl's web UI inlined into one document, gzipped (`mtime=0`), served at `/` in station mode; `--check` compares the decompressed document |

`tools/wifi_sm_ref.py` also generates the WiFi SM's golden vectors: `python3 -m
tools.gen_wifi_vectors [--check]` -> `tests/vectors/wifi_sm.json`. Edit the page only in
`index.html`/`page.js`/`strings.json` (EN + DE for every key), never the generated files. The page
script is its own file so `tools/ci.sh webcheck` can type-check it (`tsc --checkJs` over
`firmware/web/jsconfig.json`, 0 errors; `globals.d.ts` types the `CFG`/`STR` globals the generated
page defines before it); `tests/firmware/test_web_e2e.py` clicks the setup flow in Chromium. **One source of
bytes:** `web.c` serves `WEB_INDEX_HTML` on both tiers — no `EMBED_FILES`, no LittleFS — and the docs
screenshots (`python -m tools.ux_gallery --esp`) render the same `index_gen.html`.

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

### Status display (CoreS3 screen, `R_FW_STATUS_DISPLAY`)

The board build paints a 320 x 240 status screen: title + `fw <version>`, then three rows with a
coloured dot — **Gerät/Device** (running · uptime), **WLAN/WiFi** (setup hotspot / joining /
retrying = amber, SSID · IP · RSSI = green, not connected + reason = red), **Camper** (not paired
= grey, connecting / pairing / enter the code = amber, connected · data age = green, link lost /
no data for more than 10 s / pairing failed = red) — and a footer: `http://calictl-esp.local`, or
in setup mode the hotspot SSID and passphrase. The full row table with the German texts is in
`docs/howto-esp-wifi-setup.md` "What the screen tells you". Pieces: `cali_core/status.c` (one
`cali_status_t`, shared with `/api/state`), `cali_core/display_model.c` (pure model, host-tested),
`components/cali_display/` (LVGL painter + `screenshot`, board only).

- **Timing and brightness** come from `csrc/net_consts.h` (generated from `tools/wifi_consts.py`):
  `DISPLAY_REFRESH_MS` 500, `DISPLAY_STALE_MS` 10000, `DISPLAY_DIM_AFTER_MS` 60000,
  `DISPLAY_BRIGHT_PCT` 100, `DISPLAY_DIM_PCT` 10, `DISPLAY_LOCK_TIMEOUT_MS` 50. Full brightness at
  boot and on any change of a row's colour or wording or of the footer mode (a ticking age or
  uptime does not count); dimmed after 60 s without one. The painter logs
  `LOG display: brightness <pct>` on every change.
- **Language:** German by default; `CONFIG_CALI_DISPLAY_LANG_EN=y` selects English. Texts are the
  `d_*` keys of `firmware/web/strings.json` (same generator as the page); the fonts are generated
  Latin-1 subsets (`tools/gen_display_font.sh`, OFL notice in `components/cali_display/FONTS-LICENSE`).
- **No screen:** an init failure logs `LOG display: unavailable (<reason>)` once and the firmware runs
  on without it. `CONFIG_CALI_DISPLAY_FORCE_FAIL=y` (test only, never ship) forces that path:
  on the bench (2026-10-01) it logged `unavailable (forced)`, `screenshot` answered `no screen`,
  `/api/state` kept answering and the mock unit kept being read.
- **PSRAM** is on for the board build (`CONFIG_SPIRAM`, quad, 80 MHz): LVGL's heap and the
  screenshot buffer live there.
- **Size (2026-10-01, `4bfd38e`):** `cali_fw.bin` 0x17e810 = 1,566,736 B, 50 % of the 3 MB partition
  free. With `FORCE_FAIL` the linker drops the painter: 0x11c050 B.
- **Version label:** `fw` is `git describe` at configure time. A build from a **git worktree** in the
  Docker container shows `fw unknown` (the worktree's `.git` file points outside the mounted
  directory); pass `-DPROJECT_VER=$(git describe --always --tags --dirty)` to `idf.py` there.
- **Bench walk:** `tools/esplab_display_walk.sh` runs on the Linux bench (CoreS3 on USB, the mock
  unit `tools/applab/fake_unit_ble.py` on a USB BLE dongle, a second WiFi stick with a profile for
  the setup hotspot and one for the target network) and screenshots the spec's seven states (setup,
  joining, online, pairing, connected, stale — mock frozen with SIGSTOP: link up, no data — link lost)
  plus dimmed and reconnected; not WiFi red, WiFi retrying, pairing failed, link-up-no-data amber or the
  English build (those are host-tested in `test_display_model.py`). Its header lists the environment variables; no secret is stored in it.

### Remote screen check (`screenshot`)

On a CoreS3 build the console command `screenshot` streams the live screen as `[SHOT 320 240 RLE16 <n>]`,
base64 lines of 76 characters, `[/SHOT]` (kws-de's format). Capture the serial log, then
`python -m tools.esp_shot decode <log> <out_dir>` writes one PNG per frame. Refusals are
`LOG display: screenshot failed (no screen|no host|no memory|busy|snapshot)`; `no host` means no reader is
attached to the USB-Serial/JTAG port (the command needs one, else printing could block BLE). It runs on the NimBLE host task and
blocks it while printing (manual debug command). The host check only sees the USB bus, not an open
port: on the bench (2026-10-01) three `screenshot`s whose sender closed the port at once left BLE
alone all the same — the mock unit kept being read about once a second, `/api/state` kept
`device.link.up` true, no reboot — and the next `screenshot` with a reader came through whole.

## Pins

| What | Pin | Why |
|---|---|---|
| ESP-IDF | **v6.1** (`firmware-build`, container `espressif/idf:v6.1`) — the highest stable release on 2026-09-27; GitHub's newest non-prerelease *by date* is the v5.3.6 maintenance release, so the pin is by version, not by date | its `components/bt/host/nimble/nimble` submodule is esp-nimble `139cada0ae93` on branch `nimble-1.6.0-idf`, i.e. based on **upstream NimBLE 1.6.0** (merge base "Prepare for NimBLE 1.6.0 release") + ~440 Espressif commits |
| QEMU (`firmware-qemu`) | `espressif/qemu` **`esp-develop-9.2.2-20260417`** (QEMU 9.2.2) | not chosen separately: the build ESP-IDF v6.1 pins in `tools/tools.json` and ships in `espressif/idf:v6.1`, so it moves with the IDF pin |
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
`sphinx -b needs` build today.

Four more requirements are authored directly on the rendered doc page rather than in a test
docstring shim (they describe cross-cutting properties, not one C module), all in
**`docs/firmware.md`**'s traceability section — that page is the primary human-readable trace for
firmware; this file stays the build/porting reference:

- **R_FW_CONTROL_TWIN** — `cali_core/control.c` plans exactly the writes `calictl.control` would
  (same gate texts, same frames, same lighting commit) on every vector of
  `tests/vectors/control.json`; verified by `T_CONTROL_VECTORS`, `T_FW_CONTROL_PARITY`,
  `T_FW_CONTROL_E2E`.
- **R_FW_WRITE_ALLOWLIST** — only the five control chars at their frame length and the `1003`
  heartbeat are ever written, decided by `cali_ctl_write_ok` in the sequencer and in the transport's
  `write`; verified by `T_FW_WRITE_ALLOWLIST_PURE`, `T_FW_SESSION_FAKE`, `T_FW_HOST_E2E`,
  `T_FW_CONTROL_E2E` (incl. the `twrite` probe above). It supersedes **R_FW_READ_ONLY** (retired
  with #154 B; kept on the page as history).
- **R_FW_CONTROL_API** — armed link, one command at a time, station mode only, the deferred
  `POST /api/command` answer; verified by the `T_FW_CONTROL_*` session-fake tests,
  `T_FW_COMMAND_API` / `T_FW_COMMAND_STATION_ONLY` / `T_FW_HTTP_PENDING`, `T_FW_CONTROL_E2E`,
  `T_FW_UI_LIVE_CONTROL` and `T_SAT_UI_LIVE`; on the CoreS3 bench against the mock unit 2026-10-07
  (BOARD rows in `docs/business-logic/evidence-ledger.md`).
- **R_FW_IO_CAP_BEFORE_LINK** — the SM's I/O capability and MITM flag must be set before the host
  syncs, i.e. before any link exists — the exact shape of the 2026-09-26 `calictl` bug where the
  pairing agent arrived after SMP had already started; reproduced on purpose by the
  `make cali-host-jw` regression build and verified by `T_FW_HOST_E2E`.

**R_FW_WIFI_PROVISION**, **R_FW_HTTP_STATUS**, **R_FW_WIFI_BLE_COEX** (the WiFi/web slice) are
defined in `docs/firmware.md` too, with their verifying tests (`T_WIFI_SM_REF`,
`T_FW_WIFI_SM_PARITY`, `T_FW_CAPTIVE_DNS`, `T_FW_NET_HOST`, `T_FW_QEMU_NO_WIFI_DRIVER`,
`T_FW_HTTP_CORE`, `T_FW_WEB_HANDLERS`, `T_FW_JSON_WRITER`, `T_FW_WEB_STRINGS`,
`T_FW_ESP_SCREENSHOT_FIXTURES`, `T_FW_WIFI_LOSS_SESSION`, `T_FW_WEB_E2E`) declared in the test
modules' docstrings.

## On-board verification

The bring-up plan for a real M5Stack CoreS3 on the bench, in order (run 2026-10-01 against the
mock unit: the Board tier in `docs/firmware.md` and the evidence ledger record what has been proven):

1. **Flash from the build's own flasher args — never hand-type offsets.** `idf.py build` (the
   release variant, table above) writes `firmware/build/flasher_args.json` alongside the images;
   flash with `idf.py -p /dev/cu.usbmodemXXXX flash` (it reads that file itself) or, equivalently,
   `cd firmware/build && python -m esptool --chip esp32s3 -p /dev/cu.usbmodemXXXX write_flash
   @flash_args` (the same `@flash_args` pattern `qemu/run_qemu.sh` uses to merge the image for
   QEMU) — both read the addresses/sizes out of the build, so a bootloader/partition-table/app
   offset never gets hand-typed and drifts from the build. See the `flashing-cores3-on-bar` skill
   for the CoreS3-specific port-finding and serial-capture mechanics on the bar Mac.
2. **Console over USB-Serial/JTAG.** The CoreS3's USB-C enumerates as the S3's native USB (not a
   UART bridge): `/dev/cu.usbmodem*` (macOS) or `/dev/ttyACM*` (Linux) at any baud (the driver
   ignores it) — `idf.py -p <port> monitor`, or a plain serial terminal, speaks the console line
   protocol above directly. **Watch item 4**: this exact path has never been tried.
3. **Pause `calictl` on buspi first — the unit has a single BLE connection slot.** `sudo systemctl
   stop calictl` on buspi (or unplug it) before pairing the firmware — a live buspi daemon holds
   the only slot the unit will grant, and the firmware's `pair` would simply never connect.
   `sudo systemctl start calictl` (or `--now` re-enable) afterwards.
4. **Pair with the real unit via the console passkey.** Send `pair` on the console, type the
   6-digit passkey the unit's own screen displays with `passkey N`, watch for `STATE
   {"state":"bonded",...}`. This is also the first real test of **watch items 1 and 2** (the bond
   store guard, the local-IRK `WARN`) and of **ruling R9** (whether USB-Serial/JTAG input actually
   reaches the console) all at once.
5. **Compare one `SNAP` against buspi's `/api/state`.** With the firmware bonded and reading
   (`status` shows `bonded`; watch the console for the first `SNAP` line), pull
   `curl -s http://buspi:8088/api/state` (or the cache, `~/.cache/calictl/last_state.json`) and
   diff the raw decoded fields of one function present in both (e.g. `vehicle` or `energy`) — same
   `codec_decode`/`protocol.decode` output, two independent implementations of the same wire
   protocol, against the same real unit. A mismatch on a field both sides claim to decode is a
   firmware (or calictl) bug, not a protocol question.
6. **Reboot with the bond in place.** Power-cycle the board; it must reconnect by bond (`STATE
   {"state":"idle",...}` then, once the session comes up, a fresh `SNAP`) with no passkey prompt
   and no `LOG store: ERROR` line — closes out **watch item 1**.
7. **The control path (BOARD 2026-10-07 against the mock unit; never the real unit without the
   owner watching).** With the board on the home WiFi and `curl -s
   http://calictl-esp.local/api/state | jq .device.control` → `{"writes": true}`:
   `tools/esplab_control_walk.py --url http://calictl-esp.local --fifo <mock fifo> --record <mock
   recording>` must report `"problems": []` (every app-recorded cooler/camping/lighting/air-heater/
   energy action byte-exact at the mock, lighting commits ≥ 300 ms after their frame); a roof
   POST and `set roof stop` on the console must answer `Only via buspi or the app` with no `1401`
   in the recording; a POST over the setup hotspot (after `wifi forget`) must be `403 setup_mode`;
   a fridge toggle from the UI in a browser must land as one `1101` write. Then add dated BOARD rows
   to `docs/business-logic/evidence-ledger.md` (watch item 7 in `docs/firmware.md`). **Ran
   2026-10-07** on thinky (CI image of `8b1eda0`): 3 clean walks of 31 cases, no `1401`, `403` over
   the hotspot, the UI toggle landed — against the mock unit only; the real unit is still to come.
   **Owed since the wake-up light (2026-10-07):** the same walk now posts `local_now` and injects
   the unit's config frames, so it also covers the app's four wake-up edits (`lighting-wakeup.jsonl:239`
   as the REQUEST_CONFIG pull + `WAKEUP_UNKNOWN` refusal and as `07:00 off`); plus a wake-up card
   edit from the browser must land as `control.build("lighting","wakeup",…)`'s frame + commit.

Carry the hardware watch items from `docs/firmware.md` into this run explicitly (repeated here so
this checklist is self-contained):

- **Bond-store guard** — esp-nimble must not silently replace the NVS-backed store with a RAM-only
  one; no `store: ERROR` line, and a reboot must reconnect by bond.
- **Local-IRK `WARN`** — `WARN Failed to persist local IRK (rc=…)` is expected and believed benign
  (a new random local IRK every boot); confirm it does not block bonding or reconnect.
- **Host-task stack** — `CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=8192` is unmeasured; check the
  FreeRTOS high-water mark once the firmware is running real traffic.
- **USB-Serial/JTAG console** — confirm `pair`/`passkey N` typed over the native USB port actually
  reach `cali_console_line()` (step 2 above).
- **Device-only NimBLE options** (`docs/firmware.md` watch item 6) — pinned to the host tier in
  `sdkconfig.defaults`; confirm a failed connect is one SM/session retry (no silent in-stack
  re-attempt), the unit is found by the legacy scan, and no NimBLE INFO lines interleave with the
  console lines.
- **WiFi/web (network watch items, `docs/firmware.md`)** — BLE+WiFi coexistence with the hotspot
  up (heartbeat period and `SNAP` cadence unchanged), the captive portal popping on iOS/macOS/
  Android/Windows, `calictl-esp.local` resolving from iOS/macOS/Android, the single-connection
  HTTP core with a real browser, the ESP event assumptions (`ASSOC_LEAVE` on our own disconnect,
  the SSID in the disconnect event, `SCAN_DONE` when a join aborts a scan), typo-then-fix within
  ~2 s (`wifi: online`, no `wifi: failed auth`), and a saved network out of range for minutes then
  back (at most one `esp_wifi_connect` per driver attempt, `ONLINE` within one retry period).
- **`esp_bt_controller_init` under QEMU** — QEMU-only, not expected on real hardware (the S3 has a
  real BT low-power clock); listed here only so it is not mistaken for a hardware regression if
  seen again.
