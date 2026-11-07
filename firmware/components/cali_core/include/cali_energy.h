/**
 * @file cali_energy.h
 * @brief Energy counters: the house load and Wh integrated since boot (solar, house load, battery net),
 *        for `/api/state` `device.energy` and the LoRa uplink.
 *
 * The house load is the C twin of the web UI's `houseLoad()` (docs/business-logic/signals.md "House
 * load"): the sum of the Victron chargers' battery-side currents plus the unit's installed sources
 * (DC-DC with the +2 A correction on AmbSwVersion 0409/0410, shore, the unit's own solar), minus the
 * second battery's net current. cali_energy_from_frame() reads the unit's part from a raw 1602 frame
 * the way `calictl.semantics.energy()` + `apply_sw_corrections()` do; the parity test runs both over
 * random frames.
 *
 * Counters are in deci-Wh and wrap at 24 bits (the batt_net counter is signed 24 bit), the width the
 * LoRa uplink carries. The ESP has no wall clock without the router, so a reader derives per-day energy
 * from differences between readings with the same `boot`; a new boot starts every counter at 0.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite integrates solar, house load and battery energy since boot
 *    :id: R_FW_ENERGY_COUNTERS
 *    :status: implemented
 *    :tags: esp32, energy
 *
 *    Once a second the firmware shall integrate, at the second battery's voltage: the charge into the
 *    second battery from solar (each configured Victron charger's positive battery-side current, plus the
 *    unit's own solar current when installed) as ``solar_wh``, the house load (signals.md "House load",
 *    negative results down to -0.5 A count as 0) as ``load_wh``, and the battery's signed net current as
 *    ``batt_net_wh``. A step counts only when the unit's energy frame is from the current link and the
 *    session stored a frame in the last 60 s. The chargers' share needs every configured charger's
 *    reading to be at most 60 s old; the load needs that and every installed unit source to have a value. A gap of
 *    more than 5 s between ticks is not integrated. ``/api/state`` reports ``device.energy``
 *    ``{boot, solar_wh, load_wh, batt_net_wh, load_a}`` (Wh with one decimal, wrapping as 24-bit deci-Wh;
 *    ``load_a`` the last house load or ``null``).
 * @endverbatim
 *
 * C99, no malloc, no ESP-IDF headers. Proven by `tests/firmware/test_energy.py` (`test/energy_cli.c`).
 */
#ifndef CALI_ENERGY_H
#define CALI_ENERGY_H

#include <stddef.h>
#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The integration cadence, in ms. */
#define CALI_ENERGY_TICK_MS 1000u
/** @brief A tick gap above this is not integrated, in ms. */
#define CALI_ENERGY_MAX_GAP_MS 5000u
/** @brief The age limit of the unit's and the chargers' readings, in ms (the web UI's 60 s). */
#define CALI_ENERGY_MAX_AGE_MS 60000u
/** @brief Counter width: deci-Wh wrap at 24 bits. */
#define CALI_ENERGY_MASK 0xFFFFFFu

/** @brief The unit's part of one step, from a raw energy frame. */
typedef struct {
    int valid;          /**< 1 when the second battery's voltage and current are in the frame */
    int sources_ok;     /**< 1 when every installed unit source has a value */
    double batt2_v;     /**< second battery, V */
    double batt2_a;     /**< second battery's net current, A, + = charging */
    double sources_a;   /**< the installed unit sources: DC-DC (corrected), shore, the unit's solar, A */
    double unit_solar_a; /**< the unit's own solar current when installed, else 0, A */
} cali_energy_in_t;

/** @brief The counters, deci-Wh. */
typedef struct {
    uint32_t solar_dwh;    /**< 24-bit wrap */
    uint32_t load_dwh;     /**< 24-bit wrap */
    int32_t batt_net_dwh;  /**< signed 24-bit wrap */
    double frac[3];        /**< the sub-deci-Wh carry of each counter */
} cali_energy_ctr_t;

/**
 * @brief The unit's part of a step from a raw energy (1602) frame.
 * @param frame   The raw frame.
 * @param len     Its length.
 * @param amb_sw  The general function's AmbSwVersion as 4 characters ("0410"), or NULL when unknown
 *                (no DC-DC correction, as `apply_sw_corrections()` without `general`).
 * @param out     Out: the inputs; `valid` 0 when the frame lacks the battery fields.
 */
void cali_energy_from_frame(const uint8_t *frame, size_t len, const char *amb_sw, cali_energy_in_t *out);

/**
 * @brief The AmbSwVersion of a raw general (1001) frame as 4 characters.
 * @param frame  The raw frame.
 * @param len    Its length.
 * @param out    Out: "0410" and a NUL.
 * @return 0, or -1 when the field is missing or not printable.
 */
int cali_energy_amb_sw(const uint8_t *frame, size_t len, char out[5]);

/**
 * @brief The house load.
 * @param in       The unit's part.
 * @param mppt_ok  1 when every configured charger has a fresh reading (or none is configured).
 * @param mppt_a   The sum of the chargers' battery-side currents, A.
 * @param out_a    Out: the load, A (a result from -0.5 A to 0 is 0).
 * @return 0, or -1 when unknown: a missing input, a stale charger, or a result below -0.5 A.
 */
int cali_energy_house_load(const cali_energy_in_t *in, int mppt_ok, double mppt_a, double *out_a);

/**
 * @brief Integrate one step into the counters.
 * @param c        The counters.
 * @param dt_s     The step, s.
 * @param in       The unit's part (nothing is integrated unless `valid`).
 * @param mppt_ok  As cali_energy_house_load().
 * @param mppt_a   As cali_energy_house_load().
 */
void cali_energy_step(cali_energy_ctr_t *c, double dt_s, const cali_energy_in_t *in, int mppt_ok, double mppt_a);

/** @brief Reset the counters and the tick state (boot; tests). */
void cali_energy_init(void);

/**
 * @brief The owner-task tick: once per CALI_ENERGY_TICK_MS, reads the session's energy and general
 *        frames and the configured Victron chargers and integrates one step.
 * @param now_ms  Uptime, ms.
 */
void cali_energy_tick(uint64_t now_ms);

/**
 * @brief The counters since boot (read on the owner task).
 * @return The counters.
 */
const cali_energy_ctr_t *cali_energy_counters(void);

/**
 * @brief Write `device.energy` `{boot, solar_wh, load_wh, batt_net_wh, load_a}`.
 * @param j  The writer, positioned at a value.
 */
void cali_energy_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_ENERGY_H */
