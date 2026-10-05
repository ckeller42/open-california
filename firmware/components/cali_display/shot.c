/* shot.c — the "screenshot" console command (#154): dump the live screen as kws-de's RLE16 + base64
 * frame so tools/esp_shot.py can turn it into a PNG on the host (remote screen verification).
 *
 *   [SHOT <w> <h> RLE16 <nbytes>]
 *   <base64, 76 chars per line>
 *   [/SHOT]
 *
 * RLE16 = per-row (uint16 count LE, uint16 RGB565 LE) pairs. The LVGL lock is held only for the
 * snapshot (copy into a PSRAM buffer); encoding and printing happen outside it. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "bsp/esp-bsp.h"
#include "cali_display.h"
#include "cali_platform.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "net_consts.h"
#include "sdkconfig.h"
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
#include "driver/usb_serial_jtag.h"
#endif

#define SHOT_W 320
#define SHOT_H 240
#define SHOT_BYTES (SHOT_W * SHOT_H * 2)
#define RLE_BYTES (SHOT_W * SHOT_H * 4) /* worst case: no runs */
#define B64_LINE 76
#define LINES_PER_YIELD 32 /* let lower-priority tasks (and the idle watchdog) run while streaming */

static void b64_print(const uint8_t *p, size_t n) {
    static const char T[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    char line[B64_LINE + 1];
    int c = 0, lines = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)p[i] << 16;
        if (i + 1 < n) v |= (uint32_t)p[i + 1] << 8;
        if (i + 2 < n) v |= p[i + 2];
        line[c++] = T[(v >> 18) & 63];
        line[c++] = T[(v >> 12) & 63];
        line[c++] = (i + 1 < n) ? T[(v >> 6) & 63] : '=';
        line[c++] = (i + 2 < n) ? T[v & 63] : '=';
        if (c >= B64_LINE) {
            line[c] = 0;
            puts(line);
            c = 0;
            if (++lines % LINES_PER_YIELD == 0) vTaskDelay(1);
        }
    }
    if (c) {
        line[c] = 0;
        puts(line);
    }
}

/* Returns the RLE byte count, runs never cross a row. */
static size_t rle_encode(const lv_draw_buf_t *d, uint8_t *out) {
    size_t r = 0;
    uint32_t w = d->header.w, h = d->header.h, stride = d->header.stride;
    for (uint32_t y = 0; y < h; y++) {
        const uint16_t *row = (const uint16_t *)(d->data + (size_t)y * stride);
        for (uint32_t x = 0; x < w;) {
            uint16_t px = row[x];
            uint32_t run = 1;
            while (x + run < w && row[x + run] == px && run < 65535) run++;
            out[r++] = run & 0xff;
            out[r++] = run >> 8;
            out[r++] = px & 0xff;
            out[r++] = px >> 8;
            x += run;
        }
    }
    return r;
}

static void shot(void) {
    if (!cali_display_ready()) {
        cali_log("display: screenshot failed (no screen)");
        return;
    }
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    /* The frame is printed on the NimBLE host task through the driver's small tx ring: with no host
     * draining the CDC endpoint a puts could block indefinitely and stall BLE. */
    if (!usb_serial_jtag_is_connected()) {
        cali_log("display: screenshot failed (no host)");
        return;
    }
#endif
    lv_draw_buf_t d;
    lv_result_t res;
    size_t n;
    uint8_t *snap = heap_caps_aligned_alloc(LV_DRAW_BUF_ALIGN, SHOT_BYTES, MALLOC_CAP_SPIRAM);
    uint8_t *rle = heap_caps_malloc(RLE_BYTES, MALLOC_CAP_SPIRAM);
    if (!snap || !rle) {
        cali_log("display: screenshot failed (no memory)");
        goto out;
    }
    lv_draw_buf_init(&d, SHOT_W, SHOT_H, LV_COLOR_FORMAT_RGB565, 0, snap, SHOT_BYTES);
    if (!bsp_display_lock(DISPLAY_LOCK_TIMEOUT_MS)) {
        cali_log("display: screenshot failed (busy)");
        goto out;
    }
    res = lv_snapshot_take_to_draw_buf(lv_screen_active(), LV_COLOR_FORMAT_RGB565, &d);
    bsp_display_unlock();
    if (res != LV_RESULT_OK) {
        cali_log("display: screenshot failed (snapshot)");
        goto out;
    }
    n = rle_encode(&d, rle);
    printf("[SHOT %u %u RLE16 %u]\n", (unsigned)d.header.w, (unsigned)d.header.h, (unsigned)n);
    b64_print(rle, n);
    printf("[/SHOT]\n");
out:
    heap_caps_free(snap);
    heap_caps_free(rle);
}

int cali_display_shot_line(const char *line) {
    if (strcmp(line, "screenshot") != 0) return 0;
    shot();
    return 1;
}
