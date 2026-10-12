/* sdcard_esp.c — microSD power, mount and the SD task behind cali_sdlog (R_FW_SD_LOG).
 * Contract: include/cali_sdcard_esp.h; the record format, rolling and seq recovery are cali_core's
 * sdlog.c (host-tested). Everything here runs on the SD task, except cali_sdcard_esp_start and put
 * (owner task).
 *
 * The card shares SPI3_HOST with the LCD (the board package's bus, set up by bsp_display_start —
 * never initialised again here), so card init, writes, fsync and unmount hold bsp_display_lock: no
 * LVGL flush runs between them. No BSP_ERROR_CHECK-wrapped helper (bsp_sdcard_mount and friends
 * abort on a missing card = a boot loop): esp_vfs_fat_sdspi_mount directly, format_if_mount_failed
 * false (a card that is not FAT is the owner's to format, never ours).
 *
 * Unmount (a write/fsync error, i.e. the card was pulled) logs "gpio: conflict found for GPIO[4]"
 * from IDF's own sdspi_host_remove_device: it re-configures its CS pin (GPIO4, still reserved as an
 * output by its own sdspi_host_init_device) as an input, which gpio_config flags. It is a warning
 * only (esp_driver_gpio "we just give a warning"), the same on the remount; nothing else owns GPIO4
 * on the CoreS3 (BSP_SD_SPI_CS). */
#include "cali_sdcard_esp.h"

#include <inttypes.h>

#include "bsp/esp-bsp.h"
#include "driver/sdspi_host.h"
#include "esp_heap_caps.h"
#include "esp_vfs_fat.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "sdmmc_cmd.h"

#include "cali_diag.h"
#include "cali_platform.h"
#include "cali_sdlog.h"

#define MOUNT_POINT "/sdcard"
#define LOG_DIR MOUNT_POINT "/log"
#define QUEUE_LEN 64                    /* ~16 KB of PSRAM: minutes of notifies, a whole snapshot burst */
#define RETRY_MS (5u * 60u * 1000u)     /* no card-detect pin: try again every 5 min */
#define SYNC_MS 10000u                  /* fflush + fsync: a power cut loses <= 10 s */
#define FULL_PCT 90                     /* above: delete the oldest segment */
#define BATCH 32                        /* records per display-lock hold */
#define TASK_STACK 8192
#define PAGE_BYTES (400u * 1024u)       /* /api/log: 2000 records of <= ~190 B, in PSRAM */
#define WAIT_MS 200                     /* queue wait: how soon a requested /api/log page is read */

static QueueHandle_t s_q;
static sdmmc_card_t *s_card;
static int s_powered;
static int s_last_state = -1;   /* log each state change once, not every 5-min retry */
static uint64_t s_next_try;     /* the next mount attempt while unmounted */
static uint64_t s_last_fail;    /* uptime of the last write/fsync failure (0 = none) */

static int put(const cali_sdlog_rec_t *r) { return xQueueSend(s_q, r, 0) == pdTRUE ? 0 : -1; }

static void set_state(int st) {
    uint64_t total = 0, freeb = 0;
    if (st == CALI_SD_OK && esp_vfs_fat_info(MOUNT_POINT, &total, &freeb) != ESP_OK) st = CALI_SD_ERROR;
    cali_sdlog_card(st, (uint32_t)(total >> 20), (uint32_t)(freeb >> 20));
    if (st != s_last_state) {
        static const char *const NAME[] = {"absent", "error", "ok"};
        cali_log("sd: %s (%" PRIu32 " of %" PRIu32 " MB free)", NAME[st], (uint32_t)(freeb >> 20),
                 (uint32_t)(total >> 20));
        s_last_state = st;
    }
}

/* Above FULL_PCT used: drop the oldest segments (never the open one). Display lock held. */
static void prune(void) {
    uint64_t total = 0, freeb = 0;
    while (esp_vfs_fat_info(MOUNT_POINT, &total, &freeb) == ESP_OK && total &&
           (total - freeb) * 100 > total * FULL_PCT && cali_sdlog_delete_oldest() == 0) {
    }
}

static void unmount(void) {
    cali_sdlog_close();
    if (s_card) (void)esp_vfs_fat_sdcard_unmount(MOUNT_POINT, s_card);   /* the GPIO[4] warning: above */
    s_card = NULL;
}

/* Display lock held. */
static void mount(void) {
    esp_err_t e;
    if (!s_powered) {   /* AW9523 P0.4 + AXP2101 ALDO4 3.3 V; idempotent, so once is enough */
        e = bsp_feature_enable(BSP_FEATURE_SD, true);
        if (e != ESP_OK) cali_log("sd: power: %s", esp_err_to_name(e));
        s_powered = 1;
    }
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = BSP_SDSPI_HOST;
    sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot.gpio_cs = BSP_SD_SPI_CS;
    slot.host_id = BSP_SDSPI_HOST;
    const esp_vfs_fat_sdmmc_mount_config_t mc = {
        .format_if_mount_failed = false, .max_files = 2, .allocation_unit_size = 16 * 1024};
    e = esp_vfs_fat_sdspi_mount(MOUNT_POINT, &host, &slot, &mc, &s_card);
    if (e != ESP_OK) {
        s_card = NULL;
        /* ESP_FAIL: a card answered but holds no usable FAT; anything else: no card (timeouts) */
        if (s_last_state != (e == ESP_FAIL ? CALI_SD_ERROR : CALI_SD_ABSENT)) cali_log("sd: mount: %s", esp_err_to_name(e));
        set_state(e == ESP_FAIL ? CALI_SD_ERROR : CALI_SD_ABSENT);
        return;
    }
    if (cali_sdlog_open(LOG_DIR) != 0) {
        unmount();
        set_state(CALI_SD_ERROR);
        return;
    }
    prune();
    uint64_t total = 0, freeb = 0;   /* still full after pruning (not our files): no room to log */
    if (esp_vfs_fat_info(MOUNT_POINT, &total, &freeb) == ESP_OK && freeb < (uint64_t)CALI_SDLOG_SEGMENT_BYTES) {
        cali_log("sd: card full (not by the log), not logging");
        unmount();
        set_state(CALI_SD_ERROR);
        return;
    }
    set_state(CALI_SD_OK);
}

/* A write or fsync failed: the card was pulled or broke. Remount at once (that sets the state:
 * absent for a pulled card) — but a second failure within RETRY_MS (a card that mounts but cannot
 * be written) is an error and waits out RETRY_MS, never a remount storm under the display lock. */
static void io_failed(const char *what) {
    uint64_t now = cali_uptime_ms();
    int again = s_last_fail && now - s_last_fail < RETRY_MS;
    cali_log("sd: %s failed%s", what, again ? " again, retry in 5 min" : ", remounting");
    s_last_fail = now;
    s_next_try = now + RETRY_MS;
    unmount();
    if (again) set_state(CALI_SD_ERROR);
    else mount();
    cali_diag_evt("sd", "\"err\":\"%s\",\"again\":%s", what, again ? "true" : "false");   /* lands if remounted */
}

static void sd_task(void *arg) {
    (void)arg;
    uint64_t last_sync = 0;
    int dirty = 0;
    cali_sdlog_rec_t r;
    for (;;) {
        uint64_t now = cali_uptime_ms();
        if (!s_card && now >= s_next_try) {
            s_next_try = now + RETRY_MS;
            bsp_display_lock(0);   /* 0 = wait: the card's init must not interleave an LCD flush */
            mount();
            bsp_display_unlock();
        }
        if (s_card && cali_sdlog_page_pending()) {   /* GET /api/log: a page the owner task asked for */
            /* ponytail: the page read holds the display lock (shared SPI bus) for up to ~1 s every 5 min */
            bsp_display_lock(0);
            cali_sdlog_page_serve();
            bsp_display_unlock();
        }
        int got = xQueueReceive(s_q, &r, pdMS_TO_TICKS(WAIT_MS)) == pdTRUE;
        if (got && s_card) {
            bsp_display_lock(0);
            for (int n = 0; got && s_card && n < BATCH; n++) {
                int rc = cali_sdlog_write(&r);
                if (rc < 0) io_failed("write");
                else if (rc == 1) {   /* a new segment: room check, fresh free space */
                    prune();
                    set_state(CALI_SD_OK);
                }
                dirty = 1;
                got = n + 1 < BATCH && xQueueReceive(s_q, &r, 0) == pdTRUE;
            }
            bsp_display_unlock();
        }
        now = cali_uptime_ms();
        if (dirty && s_card && now - last_sync >= SYNC_MS) {
            bsp_display_lock(0);
            if (cali_sdlog_sync() != 0) io_failed("fsync");
            bsp_display_unlock();
            last_sync = now;
            dirty = 0;
        }
    }
}

void cali_sdcard_esp_start(void) {
    /* PSRAM: 256 B records (a text record's body); only tasks touch it, never with the cache off */
    s_q = xQueueCreateWithCaps(QUEUE_LEN, sizeof(cali_sdlog_rec_t), MALLOC_CAP_SPIRAM);
    if (!s_q || xTaskCreate(sd_task, "cali_sd", TASK_STACK, NULL, 3, NULL) != pdPASS) {
        cali_log("sd: no SD task (out of memory)");   /* cali_sdlog stays uninitialised: nothing queued */
        return;
    }
    cali_sdlog_init(put);
    char *page = heap_caps_malloc(PAGE_BYTES, MALLOC_CAP_SPIRAM);
    if (page) cali_sdlog_page_buffer(page, PAGE_BYTES);   /* none: /api/log answers 503 */
    else cali_log("sd: no PSRAM for the /api/log page buffer");
}
