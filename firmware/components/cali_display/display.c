#include "cali_display.h"
#include "bsp/esp-bsp.h"
#include "cali_platform.h"
#include "lvgl.h"

static int s_ok;

int cali_display_init(void) {
    if (bsp_i2c_init() != ESP_OK) { cali_log("display: unavailable (i2c)"); return -1; }
    lv_display_t *d = bsp_display_start();
    if (!d) { cali_log("display: unavailable (panel)"); return -1; }
    bsp_display_lock(0);
    lv_obj_t *l = lv_label_create(lv_screen_active());
    lv_label_set_text(l, "calictl satellite");
    lv_obj_center(l);
    bsp_display_unlock();
    bsp_display_backlight_on();
    s_ok = 1;
    return 0;
}

void cali_display_tick(uint64_t now_ms) { (void)now_ms; (void)s_ok; }
