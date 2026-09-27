/*
 * app_main.c — the firmware on the esp32s3 (#154): the device twin of firmware/host/host_main.c.
 * Same cali_core (console, pairing runner, session) and NimBLE transport; the console is the
 * S3's native USB-Serial/JTAG (CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG: the M5Stack CoreS3's USB-C port)
 * instead of stdin/stdout, or UART0 in the QEMU variant (qemu/sdkconfig.qemu: QEMU emulates UART,
 * not USB-Serial/JTAG). Same line protocol on both. The bond store is NVS.
 *
 * Tasks: the NimBLE host task (nimble_port_freertos_init) makes EVERY cali_core and transport
 * call, as main does on the host. The console task only reads UART lines into a queue and posts
 * one NimBLE event; the 100 ms esp_timer only sets a flag and posts the same event. That event's
 * callback (do_work) runs the tick and feeds queued lines to cali_console_line on the host task.
 *
 * No BLE controller (esp_bt_controller_init fails inside nimble_port_init, e.g. under QEMU): log
 * "LOG ble: controller unavailable" and run the console anyway on a transport that refuses every
 * operation; a small "core" task then plays the host task's part (same do_work, woken by a task
 * notification instead of the NimBLE event), so it still prints the idle STATE and answers lines.
 *
 * QEMU build (CONFIG_CALI_QEMU_PROBE, firmware/qemu/sdkconfig.qemu only): nimble_port_init() is not
 * called at all (QEMU does not model the controller; esp_bt_controller_init asserts there and the
 * chip reboots in a loop), so the no-controller path above runs; plus the "kvprobe set <hex>" /
 * "kvprobe get" console command (one record through cali_kv_* = NVS, the bond store's path) for the
 * QEMU reboot test (tests/firmware/test_qemu_boot.py). Never in the release image.
 */
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "sdkconfig.h"
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#elif CONFIG_ESP_CONSOLE_UART
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#else
#error "console must be USB-Serial/JTAG (device) or UART (QEMU): see firmware/README.md"
#endif
#include "esp_err.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "host/ble_hs.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"

#include "cali_ble_nimble.h"
#include "cali_console.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"

#define TICK_MS 100
#define NLINES 16
#define LINE_MAX_LEN 128

static QueueHandle_t s_lines;          /* NLINES x LINE_MAX_LEN: console task -> owner */
static atomic_int s_tick_due;          /* set by the timer, cleared by the owner */
static atomic_int s_ready;             /* the owner may take lines (NimBLE synced / core up) */
static struct ble_npl_event s_work_ev; /* BLE: the owner is the NimBLE host task */
static TaskHandle_t s_core_task;       /* no BLE: the owner is this task */

#if CONFIG_CALI_QEMU_PROBE
#define KVPROBE_KEY "kvprobe"
#define KVPROBE_MAX 64

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* "kvprobe set <hex>" -> "LOG kvprobe ok" | "LOG kvprobe error ..."; "kvprobe get" -> "LOG kvprobe
 * get <hex>" | "LOG kvprobe get missing|corrupt". Returns 1 if the line was a kvprobe command.
 * Runs on the owner task, like every other cali_kv_* caller. */
static int kvprobe_line(const char *line) {
    uint8_t buf[KVPROBE_MAX];
    size_t n = 0;
    if (strncmp(line, "kvprobe", 7) != 0 || (line[7] != ' ' && line[7] != 0)) return 0;
    if (strncmp(line, "kvprobe set ", 12) == 0) {
        const char *h = line + 12;
        size_t len = strlen(h);
        if (len == 0 || len % 2 || len / 2 > sizeof buf) {
            cali_log("kvprobe error: want 1..%d bytes of hex", KVPROBE_MAX);
            return 1;
        }
        for (; n < len / 2; n++) {
            int hi = hexval(h[2 * n]), lo = hexval(h[2 * n + 1]);
            if (hi < 0 || lo < 0) {
                cali_log("kvprobe error: not hex");
                return 1;
            }
            buf[n] = (uint8_t)(hi << 4 | lo);
        }
        if (cali_kv_set(KVPROBE_KEY, buf, n) == 0) cali_log("kvprobe ok");
        else cali_log("kvprobe error: cali_kv_set failed");
    } else if (strcmp(line, "kvprobe get") == 0) {
        char hex[2 * KVPROBE_MAX + 1];
        n = sizeof buf;
        int rc = cali_kv_get(KVPROBE_KEY, buf, &n);
        if (rc == CALI_KV_MISSING) {
            cali_log("kvprobe get missing");
        } else if (rc != CALI_KV_OK) {
            cali_log("kvprobe get corrupt");
        } else {
            for (size_t i = 0; i < n; i++) snprintf(hex + 2 * i, 3, "%02x", buf[i]);
            hex[2 * n] = 0;
            cali_log("kvprobe get %s", hex);
        }
    } else {
        cali_log("kvprobe error: usage kvprobe set <hex> | kvprobe get");
    }
    return 1;
}
#endif

/* Owner task: the due tick, then every queued line (only once ready). */
static void do_work(void) {
    char line[LINE_MAX_LEN];
    if (atomic_exchange(&s_tick_due, 0)) {
        uint64_t now = cali_uptime_ms();
        cali_runner_tick(now);
        cali_session_tick(now);
    }
    if (!atomic_load(&s_ready)) return;
    while (xQueueReceive(s_lines, line, 0) == pdTRUE) {
#if CONFIG_CALI_QEMU_PROBE
        if (kvprobe_line(line)) continue;
#endif
        cali_console_line(line);
    }
}

/* Any task: wake the owner. */
static void wake_owner(void) {
    if (s_core_task != NULL) xTaskNotifyGive(s_core_task);
    else ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &s_work_ev);
}

static void work_ev_cb(struct ble_npl_event *ev) {
    (void)ev;
    do_work();
}

static void tick_cb(void *arg) {
    (void)arg;
    atomic_store(&s_tick_due, 1);
    wake_owner();
}

static void on_sync(void) {
    cali_session_boot();          /* stored bond -> reconnect; none -> stay idle, never scan */
    cali_console_line("status");  /* the boot STATE line */
    atomic_store(&s_ready, 1);
    do_work();                    /* lines typed before the stack was ready */
}

static void host_task(void *param) {
    (void)param;
    nimble_port_run();            /* returns only after nimble_port_stop() */
    nimble_port_freertos_deinit();
}

/* ---- no BLE controller: a transport that refuses everything ---- */
static void nt_set_sink(cali_tsink_t sink, void *ctx) { (void)sink; (void)ctx; }
static int nt_name(const char *name) { (void)name; return -1; }
static int nt_u16(uint16_t c) { (void)c; return -1; }
static int nt_u32(uint32_t v) { (void)v; return -1; }
static int nt_void(void) { return -1; }
static int nt_no(void) { return 0; }
static const char *nt_identity(void) { return NULL; }

static const cali_transport_t s_no_ble = {
    .set_sink = nt_set_sink,
    .start_scan = nt_name,
    .stop_scan = nt_void,
    .connect_found = nt_void,
    .connect_bonded = nt_void,
    .pair = nt_void,
    .inject_passkey = nt_u32,
    .discover = nt_void,
    .read = nt_u16,
    .subscribe = nt_u16,
    .write_heartbeat = nt_u32,
    .disconnect = nt_void,
    .remove_bond = nt_void,
    .has_bond = nt_no,
    .identity = nt_identity,
};

static void core_task(void *param) {
    (void)param;
    cali_console_line("status");  /* the boot STATE line: idle, no bond */
    atomic_store(&s_ready, 1);
    for (;;) {
        do_work();
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    }
}

/* ---- console: USB-Serial/JTAG or UART bytes -> lines -> queue -> owner ---- */
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
static void console_io_init(void) {
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    usb_serial_jtag_vfs_use_driver();   /* stdout (cali_log, STATE, SNAP) through the driver */
}

static int console_read_byte(uint8_t *c) {
    return usb_serial_jtag_read_bytes(c, 1, portMAX_DELAY);
}
#else
static void console_io_init(void) {
    ESP_ERROR_CHECK(uart_driver_install(CONFIG_ESP_CONSOLE_UART_NUM, 256, 0, 0, NULL, 0));
    uart_vfs_dev_use_driver(CONFIG_ESP_CONSOLE_UART_NUM);  /* stdout through the driver */
}

static int console_read_byte(uint8_t *c) {
    return uart_read_bytes(CONFIG_ESP_CONSOLE_UART_NUM, c, 1, portMAX_DELAY);
}
#endif

/* Console task: never calls NimBLE or cali_core. */
static void console_task(void *param) {
    (void)param;
    char line[LINE_MAX_LEN];
    size_t n = 0;
    for (;;) {
        uint8_t c;
        if (console_read_byte(&c) != 1) continue;
        if (c != '\r' && c != '\n') {
            if (n < sizeof line - 1) line[n++] = (char)c;   /* overlong: truncated */
            continue;
        }
        if (n == 0) continue;     /* empty line, or the LF of a CRLF */
        line[n] = 0;
        n = 0;
        xQueueSend(s_lines, line, portMAX_DELAY);           /* owner behind: wait for a slot */
        wake_owner();
    }
}

static void console_init(void) {
    console_io_init();
    setvbuf(stdout, NULL, _IOLBF, 0);
}

void app_main(void) {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    console_init();
    if (cali_platform_init(NULL) != 0) cali_log("store: NVS namespace unusable, bonds will not persist");

    s_lines = xQueueCreate(NLINES, LINE_MAX_LEN);
    configASSERT(s_lines != NULL);

#if CONFIG_CALI_QEMU_PROBE
    err = ESP_ERR_NOT_SUPPORTED;  /* QEMU: no controller to init (see the header comment) */
#else
    err = nimble_port_init();     /* esp_bt_controller_init + enable, then the NimBLE host */
#endif
    if (err == ESP_OK) {
        cali_ble_nimble_init(on_sync);
        cali_console_init(cali_ble_nimble_transport());
        ble_npl_event_init(&s_work_ev, work_ev_cb, NULL);
    } else {
        cali_log("ble: controller unavailable");   /* exact text: the QEMU boot test (Task 9) */
#if CONFIG_CALI_QEMU_PROBE
        cali_log("ble: nimble_port_init skipped (QEMU build, CONFIG_CALI_QEMU_PROBE)");
#else
        cali_log("ble: nimble_port_init: %s", esp_err_to_name(err));
#endif
        cali_console_init(&s_no_ble);
    }

    const esp_timer_create_args_t targs = {.callback = tick_cb, .name = "cali_tick"};
    esp_timer_handle_t tick;
    ESP_ERROR_CHECK(esp_timer_create(&targs, &tick));

    if (err == ESP_OK) {
        nimble_port_freertos_init(host_task);
    } else {
        BaseType_t ok = xTaskCreate(core_task, "cali_core", 8192, NULL, 5, &s_core_task);
        configASSERT(ok == pdPASS);
    }
    ESP_ERROR_CHECK(esp_timer_start_periodic(tick, TICK_MS * 1000));
    BaseType_t ok = xTaskCreate(console_task, "cali_console", 3072, NULL, 5, NULL);
    configASSERT(ok == pdPASS);
}
