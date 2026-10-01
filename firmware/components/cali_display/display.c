#include "cali_display.h"
#include "bsp/esp-bsp.h"
#include "cali_platform.h"
#include "driver/i2c_master.h"
#include "lvgl.h"

#define AXP2101_ADDR 0x34 /* power chip: BSP 4.1.0 src/bsp_feature_en.c BSP_AXP2101_ADDR */
#define AW9523_ADDR 0x58  /* IO expander: ESP_IO_EXPANDER_I2C_AW9523_ADDRESS_00 (BSP_IO_EXPANDER_ADDRESS) */
#define PROBE_MS 50

static int s_ok;

int cali_display_init(void) {
    if (bsp_i2c_init() != ESP_OK) { cali_log("display: unavailable (i2c)"); return -1; }
    /* BSP_ERROR_CHECK=y: a fault inside the board package aborts, so prove the chips it needs answer first. */
    if (i2c_master_probe(bsp_i2c_get_handle(), AXP2101_ADDR, PROBE_MS) != ESP_OK) { cali_log("display: unavailable (axp2101)"); return -1; }
    if (i2c_master_probe(bsp_i2c_get_handle(), AW9523_ADDR, PROBE_MS) != ESP_OK) { cali_log("display: unavailable (aw9523)"); return -1; }
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
