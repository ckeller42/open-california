/**
 * @file cali_auto_camper.h
 * @brief Auto camper mode on the satellite: restore camping after you park, as `calictl serve` does
 *        (`calictl/automation.py`, `docs/business-logic/auto-camper-mode.md`).
 *
 * **Decide** (auto_camper.c, pure): cali_ac_decide() is `calictl.automation.auto_camper_restore_decide`
 * in C, with the same branches and the same notice texts, held equal by `tests/vectors/auto_camper.json`
 * (`tools/gen_auto_camper_vectors.py`, `tests/firmware/test_auto_camper_parity.py`). A boolean that may
 * be unknown is -1 (Python `None`); `restore_until` -1 is `None`. The clock is the caller's: the function
 * only compares `now` with `restore_until`, so the ESP passes uptime ms and a window in ms.
 *
 * **Run** (auto_camper_run.c, owner task): every CALI_AC_STEP_MS (serve's 30 s poll) while the session
 * is ready, one decide step over this link's frames (vehicle TerminalOneFive, campingmode, energy).
 * An actuate goes out through the control sequencer as campingmode master on, then usb and lights on
 * (cali_ctl_submit, the same path as `POST /api/command`). The toggle (default ON) and the restore
 * debt persist in the kv store (NVS namespace `cali`, keys CALI_AC_ON_KEY and CALI_AC_STATE_KEY). The
 * log lines read `auto-camper[EVENT]: ...` like serve's.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite restores camping mode after you park, as calictl serve does
 *    :id: R_FW_AUTO_CAMPER
 *    :status: implemented
 *    :tags: esp32, automation, camping
 *
 *    The firmware shall run calictl's restore-after-park decision (``R_AUTO_CAMPER_RESTORE``) as a C
 *    twin held equal by vectors generated from the Python function: remember the pre-drive camping
 *    config when the engine sheds it, re-assert it through the control path on the ignition falling
 *    edge within a bounded window and tries, respect a manual off and stand down on low battery or a
 *    warning. The toggle (default on) and the restore debt persist in NVS; ``/api/state`` reports
 *    ``device.auto_camper`` ``{enabled, state}``, ``POST /api/auto_camper`` switches it (station mode
 *    only) and Satellite settings shows and switches it (EN + DE).
 * @endverbatim
 *
 * C99, no malloc, no NimBLE/ESP-IDF includes.
 */
#ifndef CALI_AUTO_CAMPER_H
#define CALI_AUTO_CAMPER_H

#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Restore window after the ignition falls, in ms (calictl.automation's default). */
#define CALI_AC_WINDOW_MS 120000
/** @brief Restore tries before the controller gives up. */
#define CALI_AC_MAX_FAILS 3
/** @brief Leisure battery level in percent below which the restore stands down. */
#define CALI_AC_MIN_SOC 20
/** @brief The step period in ms: serve's poll interval, one decision (and at most one restore try) per step. */
#define CALI_AC_STEP_MS 30000u

/** @brief kv key of the toggle: 1 byte, 0 off, 1 on, missing = on. */
#define CALI_AC_ON_KEY "ac_on"
/** @brief kv key of the restore debt (see auto_camper_run.c). */
#define CALI_AC_STATE_KEY "ac_state"

/** @brief The decision's carried state (the Python function's state tuple). */
typedef struct {
    int owe;                 /**< A restore is owed (`owe_restore`). */
    int pre;                 /**< The pre-drive config is known (its master is always on). */
    int pre_usb;             /**< The pre-drive USB setting. */
    int pre_lights;          /**< The pre-drive lights setting. */
    int64_t until;           /**< `restore_until`, -1 = None. */
    int fails;               /**< Failed restore tries so far. */
    int prev_ign;            /**< Previous ignition, -1 = None. */
    int prev_master;         /**< Previous camping master, -1 = None. */
    int prev_usb;            /**< Previous USB setting, -1 = None. */
    int prev_lights;         /**< Previous lights setting, -1 = None. */
} cali_ac_state_t;

/** @brief One step's inputs. Each flag is -1 when unknown this step, except `warning`. */
typedef struct {
    int ign;        /**< Ignition. */
    int master;     /**< Camping master. */
    int usb;        /**< Camping USB. */
    int lights;     /**< Camping lights. */
    int soc;        /**< Leisure battery in percent, -1 = None. */
    int warning;    /**< The unit's battery warning: a Boolean, any nonzero value stands the restore down, so pass 0 when unknown. */
    int64_t now;    /**< The caller's clock, in the same unit as the window. */
} cali_ac_in_t;

/** @brief The decision's tuning. */
typedef struct {
    int min_soc;      /**< Stand down below this percent. */
    int max_fails;    /**< Give up after this many tries. */
    int64_t window;   /**< The restore window, in the clock's unit. */
} cali_ac_cfg_t;

/** @brief One step's decision. */
typedef struct {
    int actuate;          /**< Write master on (plus usb and lights per the state's `pre_*`). */
    int restored;         /**< The step saw the owed restore take effect (notice "camping restored after park"). */
    const char *notice;   /**< NULL, or the one-line notice (may point into `buf`). */
    char buf[96];         /**< Storage for a formatted notice. */
} cali_ac_out_t;

/**
 * @brief Decide one poll: what to write now, and the new carried state.
 * @param in   This step's inputs.
 * @param cfg  The tuning (CALI_AC_MIN_SOC, CALI_AC_MAX_FAILS, the window).
 * @param st   The carried state; updated in place, like the Python function's returned state.
 * @param out  The decision: actuate, restored, and the notice.
 */
void cali_ac_decide(const cali_ac_in_t *in, const cali_ac_cfg_t *cfg, cali_ac_state_t *st, cali_ac_out_t *out);

/** @brief Load the toggle and the restore debt. Call on the owner task after cali_platform_init. */
void cali_ac_init(void);

/**
 * @brief Run one controller step when CALI_AC_STEP_MS has passed. Call on the owner task after cali_session_tick.
 * @param now_ms  Uptime in ms.
 */
void cali_ac_tick(uint64_t now_ms);

/**
 * @brief Switch the feature and persist the toggle. Turning it off, and off to on, clears the debt.
 * @param on  1 on, 0 off.
 * @return 0 ok, -1 not saved (a failed debt write stores no toggle).
 */
int cali_ac_set_enabled(int on);

/**
 * @brief This link's ignition: vehicle TerminalOneFive OR campingmode Enable.
 *
 * It is the step's input and is shared with the Pi wake (cali_pw_step).
 *
 * @return 1 or 0, or -1 when neither is read on this link.
 */
int cali_ac_ignition(void);

/**
 * @brief Whether a camping restore is owed (`device.auto_camper.state` is armed or restoring).
 *
 * The power-save rest stays away from a link while this is 1 (`R_FW_BLE_REST`).
 *
 * @return 1 while a restore is owed, else 0.
 */
int cali_ac_owed(void);

/**
 * @brief Set the step period. Tests only (cali-host "acstep").
 * @param ms  The step period in ms.
 */
void cali_ac_set_step_ms(uint32_t ms);

/**
 * @brief Write the `device.auto_camper` value: `{"enabled":BOOL,"state":"idle"|"armed"|"restoring"}`.
 * @param j  The JSON writer; the caller writes the key first.
 */
void cali_ac_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_AUTO_CAMPER_H */
