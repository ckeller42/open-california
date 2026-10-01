/* cali_display.h — the CoreS3 status screen (#154). ESP only; cali_core never includes this. */
#ifndef CALI_DISPLAY_H
#define CALI_DISPLAY_H
#include <stdint.h>
/* Starts I2C, the panel and the backlight. 0 = ok. -1 (logged once, BLE/WiFi unaffected) = the I2C bus or
 * the AXP2101 power / AW9523 IO-expander chips are missing (probed before the board package starts).
 * A fault AFTER a successful probe still aborts inside the board package: BSP_ERROR_CHECK=y is required
 * (=n does not compile with espressif/m5stack_core_s3 4.1.0 + GCC 15). */
int cali_display_init(void);
/* Called from do_work every tick; repaints at most every DISPLAY_REFRESH_MS. */
void cali_display_tick(uint64_t now_ms);
#endif
