/* cali_display.h — the CoreS3 status screen (#154). ESP only; cali_core never includes this. */
#ifndef CALI_DISPLAY_H
#define CALI_DISPLAY_H
#include <stdint.h>

#include "cali_transport.h"
/* Starts I2C, the panel and the backlight and builds the status screen; t is the BLE transport (the
 * bonded address for the camper row, may be NULL). 0 = ok. -1 (logged once, BLE/WiFi unaffected,
 * cali_display_tick then does nothing) only when the AXP2101 power chip or the AW9523 IO expander does
 * not answer its I2C probe (NACK/timeout), when the LVGL lock is not free within
 * DISPLAY_LOCK_TIMEOUT_MS right after the board package started (UI left unbuilt), or with the
 * test-only CONFIG_CALI_DISPLAY_FORCE_FAIL. Every other display fault (I2C bus init, panel, LVGL port)
 * aborts inside the board package: BSP_ERROR_CHECK=y is required (=n does not compile with
 * espressif/m5stack_core_s3 4.1.0 + GCC 15). */
int cali_display_init(const cali_transport_t *t);
/* Called from do_work (the NimBLE host task) every tick; at most every DISPLAY_REFRESH_MS it reads the
 * LTR-553 light/proximity sensor (when it answered at init), runs the model and repaints.
 * The LVGL lock wait is bounded by DISPLAY_LOCK_TIMEOUT_MS: when it is busy this paint is skipped and
 * the next one repaints what changed. Residual: bsp_display_brightness_set (only after a successful
 * paint, only when the level changed) is an I2C write to the AXP2101 inside the board package and,
 * like the init probe, can still stall there or abort under BSP_ERROR_CHECK=y. */
void cali_display_tick(uint64_t now_ms);
/* 1 once cali_display_init succeeded (the screen exists). */
int cali_display_ready(void);
/* 1 while the LoRa screen (R_FW_LORA_SCREEN) is up. */
int cali_display_lora_shown(void);
/* shot.c, with bsp_display_lock held (ruling R46): 1 = a QR that holds keys is up (repair, LoRa setup or hub
 * QR) and *x1..*y2 is its canvas's screen rectangle, quiet zone included, to mask; 0 = nothing to mask (no
 * QR overlay, or the app QR); -1 = a secret QR is up but its rectangle cannot be read (refuse the frame). */
int cali_display_qr_mask_area(int *x1, int *y1, int *x2, int *y2);
/* Console hook (shot.c): handles the exact line "screenshot" (returns 1) by streaming the screen as
 * "[SHOT w h RLE16 n]", base64 lines of 76 chars, "[/SHOT]" (decode with tools/esp_shot.py). Refuses with
 * "LOG display: screenshot failed (no screen|lora screen|no host|no memory|busy|snapshot)" (lora screen:
 * a secret QR whose rectangle cannot be read); a frame with a secret QR is sent with it masked grey and no frame. Runs on the
 * caller's task (the NimBLE host task) and blocks it while the frame prints; the LVGL lock is held
 * only for the snapshot itself. Needs a host attached to the USB-Serial/JTAG console (refused with
 * "no host" otherwise: a puts with nobody draining the CDC could block the host task). Any other line returns 0. */
int cali_display_shot_line(const char *line);
/* Console hook (USB-console test aid, cali_display_touch_cmd): handles "touch tap" / "touch hold <1-10000 ms>"
 * (returns 1) by pressing the screen in the touch read for that long, as a finger; a bad argument logs the usage.
 * Any other line returns 0. Owner task. */
int cali_display_touch_line(const char *line);
#endif
