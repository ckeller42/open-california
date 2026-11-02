/**
 * @file cali_thermostat.h
 * @brief Cabin thermostat decision: `calictl.thermostat.step` in C (pure, integer only).
 *
 * cali_thermo_step() is `calictl.thermostat.step` and cali_thermo_heater_state() is
 * `calictl.thermostat.heater_state`, branch for branch, held equal by `tests/vectors/thermostat.json`
 * (`tools/gen_thermostat_vectors.py`, `tests/firmware/test_thermostat_parity.py`). Temperatures are
 * centi-degrees C, demand and integrator milli-levels, time ms; division rounds half away from zero.
 * A Python `None` is CALI_THERMO_NONE (32-bit fields) or CALI_THERMO_NONE_MS (ms fields). The clock
 * is the caller's: the function only compares `now_ms` with the times in the state.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite decides the cabin thermostat's steps as calictl does
 *    :id: R_FW_THERMOSTAT_DECIDE
 *    :status: implemented
 *    :tags: esp32, thermostat, airheater
 *
 *    The firmware shall run calictl's thermostat decision (``R_THERMOSTAT_STATE``,
 *    ``R_THERMOSTAT_CONTROL``, ``R_THERMOSTAT_SESSION``) as a C99 twin with integer arithmetic
 *    only, no allocation and no clock of its own, held equal bit for bit by vectors generated
 *    from the Python functions: the same commands, reason code, mode, status and next state for
 *    every case, on a 32-bit and a 64-bit build.
 * @endverbatim
 *
 * C99, no malloc, no NimBLE/ESP-IDF includes.
 */
#ifndef CALI_THERMOSTAT_H
#define CALI_THERMOSTAT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Python `None` in a 32-bit field (a temperature, a level, a raw 1702 value). */
#define CALI_THERMO_NONE INT32_MIN
/** @brief Python `None` in a ms field. */
#define CALI_THERMO_NONE_MS INT64_MIN
/** @brief Size of a sensor alias buffer: `[a-z0-9_-]{1,16}` plus the terminator; "" = no alias. */
#define CALI_THERMO_ALIAS_LEN 17
/** @brief Most commands one step sends (`runtime`, `level`, `power on`). */
#define CALI_THERMO_MAX_CMDS 4

/** @brief The settings table (Python `THERMO_DEFAULTS`). */
typedef struct {
    int32_t period_ms;        /**< Control period, ms. */
    int32_t kp;               /**< Proportional gain, milli-level per degree C. */
    int32_t ki;               /**< Integral gain, milli-level per degree C per minute. */
    int32_t ka;               /**< Outside feed-forward gain, milli-level per degree C. */
    int32_t u_out_max;        /**< Cap of the feed-forward, milli-level. */
    int32_t hyst;             /**< Level-change hysteresis, milli-level. */
    int32_t min_dwell_ms;     /**< Least time between level changes, ms. */
    int32_t band_hi;          /**< Band above the target at the minimum level, centi-degrees C. */
    int32_t hot_hold_ms;      /**< Time above that band before the stop, ms. */
    int32_t overheat;         /**< Stop above target plus this, centi-degrees C. */
    int32_t min_level;        /**< Lowest heater level. */
    int32_t stale_after_ms;   /**< A sensor reading older than this is invalid, ms. */
    int32_t heater_stale_ms;  /**< A heater read older than this is unknown, ms. */
    int32_t batt_min_dv;      /**< Second battery limit, 0.1 V. */
    int32_t batt_hold_ms;     /**< Time below that limit before the stop, ms. */
    int32_t verify_ms;        /**< Time before an unconfirmed command is checked, ms. */
    int32_t runtime_min;      /**< Run time sent with a start from OFF, min. */
} cali_thermo_cfg_t;

/** @brief Initializer with the default settings (equal to Python `THERMO_DEFAULTS`). */
#define CALI_THERMO_DEFAULTS                                                                        \
    {                                                                                               \
        .period_ms = 60000, .kp = 1500, .ki = 50, .ka = 100, .u_out_max = 3000, .hyst = 500,        \
        .min_dwell_ms = 300000, .band_hi = 200, .hot_hold_ms = 900000, .overheat = 400,             \
        .min_level = 1, .stale_after_ms = 600000, .heater_stale_ms = 180000, .batt_min_dv = 119,    \
        .batt_hold_ms = 300000, .verify_ms = 10000, .runtime_min = 120                              \
    }

/** @brief Heater states (Python `HEATER_STATES`, same order). */
enum cali_thermo_heater_e {
    CALI_THERMO_H_OFF = 0,      /**< Off. */
    CALI_THERMO_H_RUNNING,      /**< Normal operation. */
    CALI_THERMO_H_CONTINUOUS,   /**< Permanent (continuous) operation. */
    CALI_THERMO_H_TIMER_ARMED,  /**< A departure timer is armed. */
    CALI_THERMO_H_FAULT,        /**< An error code or the fault bit; also not installed. */
    CALI_THERMO_H_LOCKED,       /**< ErrorCode 4. */
    CALI_THERMO_H_BLOCKED,      /**< ErrorCode 5. */
    CALI_THERMO_H_UNKNOWN       /**< No read, or a stale one. */
};

/** @brief Session modes. */
enum cali_thermo_mode_e {
    CALI_THERMO_OFF = 0,     /**< Not controlling. */
    CALI_THERMO_AUTO,        /**< Controlling the level. */
    CALI_THERMO_DEGRADED,    /**< No valid reference: level held at the minimum. */
    CALI_THERMO_STOPPING     /**< A stop waits for the link. */
};

/** @brief The owner's request this step. */
enum cali_thermo_req_e {
    CALI_THERMO_REQ_NONE = 0,  /**< No request. */
    CALI_THERMO_REQ_AUTO,      /**< Switch AUTO on. */
    CALI_THERMO_REQ_OFF        /**< Switch AUTO off. */
};

/** @brief Names of enum cali_thermo_heater_e ("OFF" ... "UNKNOWN"). */
extern const char *const cali_thermo_heater_names[8];
/** @brief Names of enum cali_thermo_mode_e ("OFF", "AUTO", "DEGRADED", "STOPPING"). */
extern const char *const cali_thermo_mode_names[4];

/** @brief The raw decoded 1702 values the thermostat reads; CALI_THERMO_NONE = key missing. */
typedef struct {
    int32_t installed;      /**< `Installed`. */
    int32_t level;          /**< `HeatingLevel` (1..10 is a level, anything else none). */
    int32_t error_code;     /**< `ErrorCode`. */
    int32_t fault_trigger;  /**< `FaultTriggerBit`. */
    int32_t permanent;      /**< `PermanentOperation`. */
    int32_t normal;         /**< `NormalOperation`. */
    int32_t op_mode;        /**< `OperationModeAirHeater` (3 = timer armed). */
} cali_thermo_heater_t;

/** @brief One temperature reading. */
typedef struct {
    int present;                          /**< 0 = no sensor (Python `None`). */
    char alias[CALI_THERMO_ALIAS_LEN];    /**< The sensor's alias. */
    int32_t value;                        /**< Centi-degrees C, or CALI_THERMO_NONE. */
    int64_t age_ms;                       /**< Age of the reading, or CALI_THERMO_NONE_MS. */
} cali_thermo_sensor_t;

/** @brief One step's inputs (Python `step()`'s `inp`). */
typedef struct {
    int64_t now_ms;                       /**< The caller's clock, ms. */
    int enabled;                          /**< The feature switch. */
    int request;                          /**< enum cali_thermo_req_e. */
    int32_t target;                       /**< Target, centi-degrees C. */
    int32_t runtime_min;                  /**< Run time sent with a start, min. */
    cali_thermo_sensor_t ref;             /**< The reference thermometer. */
    cali_thermo_sensor_t backup;          /**< The backup thermometer. */
    cali_thermo_sensor_t out;             /**< The outside thermometer. */
    int outside_on;                       /**< Use the outside feed-forward. */
    const cali_thermo_heater_t *heater;   /**< The last 1702 read, or NULL. */
    int64_t heater_age_ms;                /**< Its age, or CALI_THERMO_NONE_MS. */
    int link_ready;                       /**< The session can send. */
    int camping_on;                       /**< Camping mode is on. */
    int shore_on;                         /**< Shore power is on. */
    int ignition_on;                      /**< 1 on, 0 off, -1 unknown. */
    int32_t batt2_dv;                     /**< Second battery, 0.1 V, or CALI_THERMO_NONE. */
    const char *cmd_what;                 /**< The last command of the previous batch, or NULL. */
    const char *cmd_result;               /**< Its result ("OK", "REFUSED", "BUSY", "NOT_READY" ...), or NULL. */
} cali_thermo_in_t;

/** @brief The controller's state (Python `ctl`). */
typedef struct {
    int32_t i;               /**< Integrator, milli-level. */
    int64_t last_change_ms;  /**< Time of the last level change. */
    int64_t hot_since_ms;    /**< Start of the hot hold, or CALI_THERMO_NONE_MS. */
    int32_t u;               /**< Last demand, milli-level. */
    int32_t u_out;           /**< Last feed-forward, milli-level. */
} cali_thermo_ctl_t;

/** @brief What the heater does (Python `expect` / `stop_snapshot`). */
typedef struct {
    int normal;      /**< Normal operation 0/1. */
    int permanent;   /**< Permanent operation 0/1. */
    int32_t level;   /**< Level, or CALI_THERMO_NONE. */
} cali_thermo_snap_t;

/** @brief A command sent and not yet confirmed (Python `pending`). */
typedef struct {
    const char *what;   /**< "power", "permanent" or "level". */
    int32_t value;      /**< The level, or 1 = on, 0 = off. */
    int64_t since_ms;   /**< Last send. */
    int32_t tries;      /**< Sends so far (1 or 2). */
    int64_t first_ms;   /**< First send. */
    int32_t before;     /**< The level before a level command, or CALI_THERMO_NONE. */
} cali_thermo_pending_t;

/** @brief The session state (Python `initial_state()`); never persisted. */
typedef struct {
    int mode;                           /**< enum cali_thermo_mode_e. */
    int active;                         /**< 0 = the reference sensor, 1 = the backup. */
    cali_thermo_ctl_t ctl;              /**< The controller state. */
    int has_pending;                    /**< `pending` is set. */
    cali_thermo_pending_t pending;      /**< The pending command. */
    int has_expect;                     /**< `expect` is set. */
    cali_thermo_snap_t expect;          /**< What the heater should do. */
    int64_t batt_low_since_ms;          /**< Start of the low second battery, or CALI_THERMO_NONE_MS. */
    int has_stop_snapshot;              /**< `stop_snapshot` is set. */
    cali_thermo_snap_t stop_snapshot;   /**< What to stop when the link is back (mode STOPPING). */
    int64_t last_ctl_ms;                /**< Last control step, or CALI_THERMO_NONE_MS. */
} cali_thermo_state_t;

/** @brief One command: `what` with an int value (a level, a run time, or 1 = on, 0 = off). */
typedef struct {
    const char *what;   /**< "runtime", "level", "power" or "permanent". */
    int32_t value;      /**< The value. */
} cali_thermo_cmd_t;

/** @brief The status the step reports. */
typedef struct {
    int heater;                                /**< enum cali_thermo_heater_e. */
    int32_t t_ref;                             /**< The active sensor's valid value, or CALI_THERMO_NONE. */
    int32_t t_out;                             /**< The outside value in use, or CALI_THERMO_NONE. */
    int32_t target;                            /**< The target. */
    int32_t level;                             /**< The heater level, or CALI_THERMO_NONE. */
    char active_alias[CALI_THERMO_ALIAS_LEN];  /**< The active sensor's alias, "" = none. */
    int outside_active;                        /**< The feed-forward is in use. */
    const char *end;                           /**< "run" while RUNNING, else "none". */
} cali_thermo_status_t;

/** @brief One step's output. */
typedef struct {
    cali_thermo_cmd_t cmds[CALI_THERMO_MAX_CMDS];  /**< Commands to send, in order. */
    int n;                                         /**< Number of commands. */
    const char *reason;                            /**< The reason code, or NULL (an idle step in mode OFF). */
    int mode;                                      /**< enum cali_thermo_mode_e after the step. */
    cali_thermo_status_t status;                   /**< The status. */
} cali_thermo_out_t;

/**
 * @brief The session state after boot: mode OFF (Python `initial_state()`).
 * @param st  The state to reset.
 */
void cali_thermo_init(cali_thermo_state_t *st);

/**
 * @brief Reduce a 1702 read to a heater state, a level and the error code.
 * @param h       The raw read, or NULL.
 * @param age_ms  Its age in ms, or CALI_THERMO_NONE_MS (counts as stale).
 * @param cfg     The settings (`heater_stale_ms`).
 * @param level   Out: the level 1..10, or CALI_THERMO_NONE.
 * @param code    Out: the ErrorCode (-1 not installed, 0 none).
 * @return enum cali_thermo_heater_e.
 */
int cali_thermo_heater_state(const cali_thermo_heater_t *h, int64_t age_ms, const cali_thermo_cfg_t *cfg,
                             int *level, int *code);

/**
 * @brief One thermostat decision (Python `step()`).
 *
 * The reason in `out` is one of the strings in Python `REASONS` (for example `AUTO_ON` or
 * `AUTO_REFUSED_DISABLED`), or NULL.
 *
 * @param in   This step's inputs.
 * @param cfg  The settings.
 * @param st   The session state; updated in place, like the Python function's returned state.
 * @param out  The commands, the reason, the mode and the status.
 */
void cali_thermo_step(const cali_thermo_in_t *in, const cali_thermo_cfg_t *cfg, cali_thermo_state_t *st,
                      cali_thermo_out_t *out);

#ifdef __cplusplus
}
#endif

#endif /* CALI_THERMOSTAT_H */
