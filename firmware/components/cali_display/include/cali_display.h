/* cali_display.h — the CoreS3 status screen (#154). ESP only; cali_core never includes this. */
#ifndef CALI_DISPLAY_H
#define CALI_DISPLAY_H
#include <stdint.h>

#include "cali_transport.h"
/* Starts I2C, the panel and the backlight and builds the status screen; t is the BLE transport (the
 * bonded address for the camper row, may be NULL). 0 = ok. -1 (logged once, BLE/WiFi unaffected,
 * cali_display_tick then does nothing) only when the AXP2101 power chip or the AW9523 IO expander does
 * not answer its I2C probe (NACK/timeout), or with the test-only CONFIG_CALI_DISPLAY_FORCE_FAIL. Every
 * other display fault (I2C bus init, panel, LVGL port) aborts inside the board package:
 * BSP_ERROR_CHECK=y is required (=n does not compile with espressif/m5stack_core_s3 4.1.0 + GCC 15). */
int cali_display_init(const cali_transport_t *t);
/* Called from do_work every tick; repaints at most every DISPLAY_REFRESH_MS. */
void cali_display_tick(uint64_t now_ms);
#endif
