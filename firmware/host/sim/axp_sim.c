/* axp_sim.c — display.c's AXP2101 battery read (cali_display/axp2101_read.h, the same copy) on the sim's I2C,
 * with what cali_display_init and cali_display_tick do around it: the AXP2101 and AW9523 probes (either
 * NACK = no display = no battery read at all) and a read into cali_battery_update every CALI_BATT_READ_MS. */
#include "cali_battery.h"
#include "cali_platform.h"
#include "sim_idf.h"

#define AXP2101_ADDR 0x34
#define AW9523_ADDR 0x58
#define PROBE_MS 50
#define LTR_IO_MS 30
#define BUS_AXP 0

static uint32_t s_errs;   /* display.c counts these into its "sensor read errors" line */

static int io_ok(int dev, esp_err_t e) {
    (void)dev;
    if (e == ESP_OK) return 1;
    s_errs++;
    return 0;
}

#include "axp2101_read.h"

static int s_up;
static uint64_t s_last;

void sim_axp_start(void) {
    (void)bsp_i2c_init();
    if (i2c_master_probe(bsp_i2c_get_handle(), AXP2101_ADDR, PROBE_MS) != ESP_OK) {
        cali_log("display: unavailable (axp2101)");
        return;
    }
    if (i2c_master_probe(bsp_i2c_get_handle(), AW9523_ADDR, PROBE_MS) != ESP_OK) {
        cali_log("display: unavailable (aw9523)");
        return;
    }
    axp_init();
    s_up = 1;
}

void sim_axp_tick(uint64_t now_ms) {
    if (!s_up || (s_last && now_ms - s_last < CALI_BATT_READ_MS)) return;
    cali_batt_t b;
    s_last = now_ms ? now_ms : 1;
    axp_read(&b);
    cali_battery_update(&b, now_ms);
}
