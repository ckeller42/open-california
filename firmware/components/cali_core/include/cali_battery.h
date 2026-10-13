/* cali_battery.h — the CoreS3's own battery (R_FW_BATTERY): the last AXP2101 reading plus a runtime
 * estimate, for /api/state device.battery and the screen footer.
 *
 * The platform reads the power chip every CALI_BATT_READ_MS (display.c, the owner task) and hands the
 * reading to cali_battery_update; /api/state and the display model read it back on the same task.
 * Nothing calls cali_battery_update on the host / QEMU: every member stays unknown (JSON null).
 *
 * Runtime: there is no current ADC, so the estimate is the measured slope of the fuel gauge's
 * percent, which averages the real mix of screen off / proximity-woken / dimmed. While discharging
 * (no USB, not charging) the samples of the last CALI_BATT_WINDOW_MS are kept; runtime_min =
 * pct / drop rate once they span >= CALI_BATT_MIN_SPAN_MS AND the percent dropped
 * >= CALI_BATT_MIN_DROP points (the gauge is integer %), else unknown. USB back, charging or an
 * unknown percent resets the window.
 * ponytail: right after a wake the estimate still reflects the screen-off rate (and vice versa) for
 * up to one window; weight recent samples if that ever matters.
 *
 * C99, no malloc, no ESP-IDF headers. Proven by tests/firmware/test_battery.py (test/battery_cli.c). */
#ifndef CALI_BATTERY_H
#define CALI_BATTERY_H

#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_BATT_READ_MS 10000u        /* the platform's read cadence */
#define CALI_BATT_WINDOW_MS 600000u     /* samples kept for the slope: 10 min */
#define CALI_BATT_MIN_SPAN_MS 300000u   /* ... spanning at least 5 min */
#define CALI_BATT_MIN_DROP 2            /* ... and at least 2 points dropped */
#define CALI_BATT_SAMPLES 64            /* >= window / read cadence + 1 */

/* Each member -1 = unknown (JSON null). usb / charging: 0 or 1. */
typedef struct {
    int pct;          /* fuel gauge, 0..100 */
    int mv;           /* battery voltage */
    int usb;          /* VBUS good: on USB power */
    int charging;     /* the charger is charging (not standby / full, not discharging) */
    int runtime_min;  /* the estimate; ignored by cali_battery_update */
} cali_batt_t;

#define CALI_BATT_UNKNOWN {-1, -1, -1, -1, -1}

/* Stores r (a chip reading taken at now_ms, runtime_min ignored) and feeds the estimator. */
void cali_battery_update(const cali_batt_t *r, uint64_t now_ms);
/* The last reading with its runtime estimate (all unknown before the first update). */
void cali_battery_get(cali_batt_t *out);
/* device.battery: {"pct","mv","usb","charging","runtime_min"}, each null when unknown. */
void cali_battery_json(cali_json_t *j);
/* Back to all unknown, the window empty (tests). */
void cali_battery_reset(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_BATTERY_H */
