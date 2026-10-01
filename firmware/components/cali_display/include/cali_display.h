/* cali_display.h — the CoreS3 status screen (#154). ESP only; cali_core never includes this. */
#ifndef CALI_DISPLAY_H
#define CALI_DISPLAY_H
#include <stdint.h>
/* Starts I2C, the panel and the backlight. 0 = ok; -1 = no screen (logged once, BLE/WiFi unaffected). */
int cali_display_init(void);
/* Called from do_work every tick; repaints at most every DISPLAY_REFRESH_MS. */
void cali_display_tick(uint64_t now_ms);
#endif
