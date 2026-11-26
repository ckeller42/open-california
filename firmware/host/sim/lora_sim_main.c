/* lora_sim_main.c — lora-sim: the real cali_lora/lora_esp.c (the RAK3172 driver and the LoRa task) on the
 * simulated CoreS3 (coresim: sim/shims, sim/coresim), with the real cali_core lora.c on an owner task as app_main runs it.
 * Driven by tests/firmware/esp_sim.py (coresim's fake RAK3172 + AW9523) over stdin/stdout (sim/shims/sim_hw.c).
 *
 * Usage: lora-sim [--store <dir>] [--until <virtual ms>]
 * The battery reading comes from the fake AXP2101 through display.c's own read code (axp_sim.c).
 * Events the fakes can schedule besides RX (sim_event): "TICK" (answered with "HW <t> TICK <json>", the
 * json holding device.lora and device.battery), "CONSOLE <line>" (a console line: "lora on", "lora keys ...").
 *
 * The camper side is a stub: two fixed frames as the session's (so ports 3/4 have content), no sensors,
 * zero counters, boot 1, and cali_ctl_submit only logs. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_battery.h"
#include "cali_control.h"
#include "cali_display.h"
#include "cali_json.h"
#include "cali_lora.h"
#include "cali_lora_esp.h"
#include "cali_platform.h"
#include "sim_idf.h"

int sim_hw_req(const char *fmt, ...);
void sim_axp_start(void);
void sim_axp_tick(uint64_t now_ms);

/* The virtual clock for every caller outside platform_host.c (linked with --wrap=cali_uptime_ms). */
uint64_t __wrap_cali_uptime_ms(void) { return sim_now_ms(); }

int cali_display_ready(void) { return 0; }   /* no screen in this build: no LVGL lock (lora_esp.c) */

/* ---- the camper side (stubs) ---- */

int cali_session_frame(size_t i, const uint8_t **frame, size_t *len) {
    static const uint8_t F[2][3] = {{0x01, 0x02, 0x03}, {0x04, 0x05, 0x06}};
    if (i >= 2) return 0;
    *frame = F[i];
    *len = sizeof F[i];
    return 1;
}
int cali_sensors_first(const char *type, cali_sensor_reading_t *out) { return (void)type, (void)out, -1; }
const cali_energy_ctr_t *cali_energy_counters(void) {
    static const cali_energy_ctr_t c;
    return &c;
}
uint32_t cali_time_boot(void) { return 1; }
int cali_ctl_submit(const char *fn, const char *what, const char *value, int64_t local_now, cali_ctl_done_t done,
                    const char **reason, const char *src) {
    (void)local_now, (void)done, (void)reason;
    cali_log("sim: submit %s %s %s (%s)", fn, what, value, src);
    return CALI_CTL_PENDING;
}

/* ---- events from the fakes ---- */

void sim_event(const char *ev) {
    if (!strcmp(ev, "TICK")) {
        char buf[1024];
        cali_json_t j;
        cali_json_begin(&j, buf, sizeof buf);
        cali_json_key(&j, "lora");
        cali_lora_json(&j);
        cali_json_key(&j, "battery");
        cali_battery_json(&j);
        sim_hw_req("TICK %s", cali_json_end(&j) < 0 ? "{\"overflow\":1}" : buf);
    } else if (!strncmp(ev, "CONSOLE lora ", 13)) {
        (void)cali_lora_console(ev + 13);
    } else if (!strncmp(ev, "CONSOLE i2c ", 12)) {
        (void)cali_i2c_dump_line(ev + 8);   /* cali_display/i2c_dump.c, the board's own */
    } else {
        printf("SIM unknown event: %s\n", ev);
        exit(4);
    }
}

int main(int argc, char **argv) {
    const char *store = ".";
    unsigned long long until = UINT64_MAX;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--store") && i + 1 < argc) store = argv[++i];
        else if (!strcmp(argv[i], "--until") && i + 1 < argc) until = strtoull(argv[++i], NULL, 10);
        else {
            fprintf(stderr, "usage: %s [--store <dir>] [--until <virtual ms>]\n", argv[0]);
            return 2;
        }
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (cali_platform_init(store) != 0) {
        fprintf(stderr, "lora-sim: store directory %s unusable\n", store);
        return 2;
    }
    sim_start(until);
    sim_hw_req("BOOT");      /* the fakes schedule their first events */
    vTaskDelay(0);           /* ... and those due now fire before the firmware starts */
    sim_axp_start();         /* app_main.c: cali_display_init before the LoRa task */
    cali_lora_esp_start();   /* idle, no pin touched, until exp_lora is on */
    for (;;) {               /* the owner task's tick: cali_display_tick (the battery read), cali_lora_tick */
        sim_axp_tick(sim_now_ms());
        cali_lora_tick(sim_now_ms());
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
