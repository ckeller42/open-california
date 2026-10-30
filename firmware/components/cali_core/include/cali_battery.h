/**
 * @file cali_battery.h
 * @brief The CoreS3's own battery: the last AXP2101 reading plus a runtime estimate, for
 *        `/api/state` `device.battery` and the screen footer.
 *
 * The platform reads the power chip every CALI_BATT_READ_MS (display.c, the owner task) and hands the
 * reading to cali_battery_update(). `/api/state` and the display model read it back on the same task.
 * Nothing calls cali_battery_update() on the host or in QEMU, so every member stays unknown (JSON null).
 *
 * **Runtime**: there is no current ADC, so the estimate is the measured slope of the fuel gauge's
 * percent, which averages the real mix of screen off, proximity-woken and dimmed. While discharging
 * (no USB, not charging) the samples of the last CALI_BATT_WINDOW_MS are kept. `runtime_min` is the
 * percent divided by the drop rate once the samples span at least CALI_BATT_MIN_SPAN_MS and the
 * percent dropped at least CALI_BATT_MIN_DROP points (the gauge is integer percent); otherwise it is
 * unknown. USB back, charging or an unknown percent resets the window.
 *
 * Right after a wake the estimate still reflects the screen-off rate (and the other way round) for up
 * to one window. Weighting recent samples would fix that if it ever matters.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite reports its own battery and a measured runtime estimate
 *    :id: R_FW_BATTERY
 *    :status: implemented
 *    :tags: esp32, display
 *
 *    The firmware shall read the CoreS3's AXP2101 every 10 s (fuel-gauge percent, battery voltage, USB
 *    present, charging) with only the battery-voltage ADC and the fuel-gauge enable bits ever written,
 *    and estimate the runtime from the measured percent slope while discharging (a 10-min window; an
 *    estimate only after ≥ 5 min and ≥ 2 points dropped; reset on USB or charging). ``/api/state``
 *    reports ``device.battery`` ``{pct, mv, usb, charging, runtime_min}`` (each ``null`` when unknown);
 *    the screen shows it in the station footer only (never a wake trigger), the shared UI's Device status
 *    shows it, EN + DE.
 * @endverbatim
 *
 * C99, no malloc, no ESP-IDF headers. Proven by `tests/firmware/test_battery.py` (`test/battery_cli.c`).
 */
#ifndef CALI_BATTERY_H
#define CALI_BATTERY_H

#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The platform's read cadence, in ms. */
#define CALI_BATT_READ_MS 10000u
/** @brief Samples kept for the slope, in ms: 10 minutes. */
#define CALI_BATT_WINDOW_MS 600000u
/** @brief The kept samples must span at least this many ms: 5 minutes. */
#define CALI_BATT_MIN_SPAN_MS 300000u
/** @brief The percent must have dropped by at least this many points. */
#define CALI_BATT_MIN_DROP 2
/** @brief Size of the sample ring: at least window / read cadence + 1. */
#define CALI_BATT_SAMPLES 64

/** @brief One battery reading. Each member is -1 when unknown (JSON null). */
typedef struct {
    int pct;          /**< Fuel gauge, 0..100. */
    int mv;           /**< Battery voltage in mV. */
    int usb;          /**< VBUS good: 1 on USB power, else 0. */
    int charging;     /**< 1 while the charger charges (not standby, not full, not discharging), else 0. */
    int runtime_min;  /**< The runtime estimate in minutes; ignored by cali_battery_update(). */
} cali_batt_t;

/** @brief Initializer for a cali_batt_t with every member unknown. */
#define CALI_BATT_UNKNOWN {-1, -1, -1, -1, -1}

/**
 * @brief Store a chip reading and feed the estimator.
 * @param r       The reading; its `runtime_min` is ignored.
 * @param now_ms  Uptime in ms at the reading.
 */
void cali_battery_update(const cali_batt_t *r, uint64_t now_ms);

/**
 * @brief Read back the last reading with its runtime estimate (all unknown before the first update).
 * @param out  Filled with the reading.
 */
void cali_battery_get(cali_batt_t *out);

/**
 * @brief Write the `device.battery` object: `pct`, `mv`, `usb`, `charging`, `runtime_min`, each null when unknown.
 * @param j  The JSON writer; the caller writes the key first.
 */
void cali_battery_json(cali_json_t *j);

/** @brief Back to all unknown with an empty window. Tests only. */
void cali_battery_reset(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_BATTERY_H */
