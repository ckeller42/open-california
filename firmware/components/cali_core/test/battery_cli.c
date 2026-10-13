/* battery_cli.c — host driver for battery.c: one stdin line per reading "<now_ms> <pct> <mv> <usb>
 * <charging>" (-1 = unknown), one stdout line per reading: device.battery's JSON after the update.
 * "get" prints it without an update; "reset" empties the estimator. Driven by tests/firmware/test_battery.py. */
#include <stdio.h>
#include <string.h>

#include "cali_battery.h"
#include "cali_json.h"

static void print(void) {
    char buf[160];
    cali_json_t j;
    cali_json_begin(&j, buf, sizeof buf);
    cali_json_key(&j, "battery");
    cali_battery_json(&j);
    if (cali_json_end(&j) < 0) return;
    puts(buf);
}

int main(void) {
    char line[128];
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long now;
        cali_batt_t r = CALI_BATT_UNKNOWN;
        if (!strncmp(line, "reset", 5)) {
            cali_battery_reset();
            continue;
        }
        if (!strncmp(line, "get", 3)) {
            print();
            continue;
        }
        if (sscanf(line, "%llu %d %d %d %d", &now, &r.pct, &r.mv, &r.usb, &r.charging) != 5) continue;
        cali_battery_update(&r, now);
        print();
    }
    return 0;
}
