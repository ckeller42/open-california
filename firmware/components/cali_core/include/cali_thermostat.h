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
 * **Run** (thermostat_run.c, owner task, like auto_camper_run.c): cali_thermo_run_tick() runs one
 * step every `period_ms`, and at once after an owner request, a switch of the feature, or the end of
 * a command batch. A step's inputs are this ESP's: the stored 1702 frame and its age (no frame, or
 * one with no fields, is no heater), 1202 State, 1602 StateLandAfs (1 or 2 = shore power),
 * UTwoBattBemAfs, cali_ac_ignition(), cali_session_ready(), and the configured thermometers'
 * `temp_c` by alias (cali_sensors_value(); an alias with no reading is an invalid sensor, never
 * another one). The step's commands go to the control sequencer as `airheater` commands with source
 * `thermostat`, one at a time (the next from the previous one's done callback); the batch stops at
 * the first result that is not OK, and that result goes into the next step's `cmd_*` input. The
 * request is a one-shot: one step sees it. The feature switch is the kv key CALI_THERMO_ON_KEY
 * (missing = off); the mode is never stored (OFF after boot).
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite runs the cabin thermostat on its own link
 *    :id: R_FW_THERMOSTAT_RUN
 *    :status: implemented
 *    :tags: esp32, thermostat, airheater
 *
 *    The firmware shall step the thermostat decision (``R_FW_THERMOSTAT_DECIDE``) once per
 *    ``period_ms`` and at once after an owner request or a command result, from this link's frames,
 *    the stored 1702 frame's age and the configured thermometers' readings by alias; send its
 *    commands through the control sequencer one at a time, stopping at the first result that is not
 *    OK and handing that result to the next step; ship with the feature off (kv ``th_on``, missing =
 *    off) and never store the mode. While the mode is not OFF it holds the session: no power-save
 *    rest, no idle demotion, and 1702 re-read every ``CALI_SESSION_HEATER_REREAD_MS``. It logs one
 *    ``thermo`` record per step and one ``evt`` ``thermo`` record per mode change and command result,
 *    only while the feature is on or a mode is active.
 * @endverbatim
 *
 * **API** (thermostat_run.c, served by web.c): ``GET /api/thermostat`` reads the configuration and the
 * status, ``POST /api/thermostat`` changes it through cali_thermo_apply(). The switch, an AUTO request
 * and the settings need ``confirm``; every value is range-checked before anything changes; the
 * configuration is stored whole in kv ``th_cfg``; the mode is never stored.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite serves the cabin thermostat's settings and switch
 *    :id: R_FW_THERMOSTAT_API
 *    :status: implemented
 *    :tags: esp32, thermostat, airheater, web
 *
 *    The firmware shall serve ``GET /api/thermostat`` (``rev``, ``mode``, ``target``, ``runtime_min``,
 *    the thermometer aliases, ``outside_on``, all settings, the status with the reason) and
 *    ``POST /api/thermostat`` (station mode only) with any of ``enabled``, ``mode``, ``target``,
 *    ``runtime_min``, ``ref``, ``backup``, ``out`` (an alias of a configured Govee, or null),
 *    ``outside_on``, ``cfg``, ``rev`` and ``confirm``. The switch, an AUTO request and ``cfg`` shall be
 *    refused without ``confirm: true``; a value outside its range (target 500..3000 in steps of 50,
 *    run time 10..120 min, ``min_level`` 1..10, ``batt_min_dv`` 100..150, periods and times above
 *    0, gains not below 0) shall be refused before anything changes; a ``rev`` other than the current
 *    one shall be refused, except for a body that is only ``mode`` ``OFF`` (a stop is never blocked);
 *    every accepted POST shall count ``rev``. The settings, the target, the
 *    aliases and ``outside_on`` shall persist in kv ``th_cfg``, the switch in ``th_on``, the mode
 *    never. ``/api/state`` shall carry ``device.thermostat`` (``mode``, ``target``, ``t_ref``,
 *    ``level``, ``reason``) only while a reference thermometer is selected.
 * @endverbatim
 *
 * C99, no malloc, no NimBLE/ESP-IDF includes.
 */
#ifndef CALI_THERMOSTAT_H
#define CALI_THERMOSTAT_H

#include <stdint.h>

#include "cali_json.h"

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
    const char *why;    /**< A stop's cause ("USER_OFF", "DISABLED", "OVERHEAT", "TARGET_EXCEEDED_AT_MIN_LEVEL", "BATTERY_LOW"), else NULL. */
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
    const char *stop_why;               /**< That stop's cause (as cali_thermo_pending_t.why). */
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

/** @brief kv key of the feature switch: 1 byte, 0 off, 1 on, missing = off. */
#define CALI_THERMO_ON_KEY "th_on"
/** @brief Default target, centi-degrees C. */
#define CALI_THERMO_TARGET_DEFAULT 2000

/** @brief The runner's configuration (stored whole under CALI_THERMO_CFG_KEY). */
typedef struct {
    cali_thermo_cfg_t s;                   /**< The settings (`runtime_min` is the start's run time). */
    int32_t target;                        /**< Target, centi-degrees C. */
    char ref[CALI_THERMO_ALIAS_LEN];       /**< Reference thermometer alias, "" = none. */
    char backup[CALI_THERMO_ALIAS_LEN];    /**< Backup thermometer alias, "" = none. */
    char out[CALI_THERMO_ALIAS_LEN];       /**< Outside thermometer alias, "" = none. */
    int outside_on;                        /**< Use the outside feed-forward. */
} cali_thermo_run_cfg_t;

/** @brief Load the feature switch and reset the session to OFF. Call on the owner task after cali_platform_init. */
void cali_thermo_run_init(void);

/**
 * @brief Run a step when one is due. Call on the owner task after cali_session_tick.
 * @param now_ms  Uptime in ms.
 */
void cali_thermo_run_tick(uint64_t now_ms);

/** Longest wait of an AUTO request from OFF for the link it woke, ms; then the step answers it. */
#define CALI_THERMO_LINK_WAIT_MS 30000u

/**
 * @brief Pass an owner request to the next step (once).
 *
 * "AUTO" with the feature on also asks the session to connect now (cali_session_connect_now): with the
 * link down, a request from mode OFF waits up to CALI_THERMO_LINK_WAIT_MS for a ready link before the step answers it.
 *
 * @param mode  "AUTO" or "OFF".
 * @return 0, or -1 for another text.
 */
int cali_thermo_request(const char *mode);

/**
 * @brief Switch the feature and store the switch (CALI_THERMO_ON_KEY). Off while a mode is active stops it.
 * @param on  1 on, 0 off.
 * @return 0, or -1 not stored (the switch still changes).
 */
int cali_thermo_set_enabled(int on);

/**
 * @brief The runner's configuration, to change in place on the owner task.
 * @return The configuration.
 */
cali_thermo_run_cfg_t *cali_thermo_run_cfg(void);

/**
 * @brief Set one setting by its `cali_thermo_cfg_t` member name (for example "period_ms").
 * @param key    The member name.
 * @param value  The value.
 * @return 0, or -1 for an unknown name.
 */
int cali_thermo_cfg_set(const char *key, int32_t value);

/**
 * @brief Write the status object: `{"enabled","mode","reason","heater","t_ref","t_out","target","level","active","outside_active","end"}`.
 *
 * `reason` is the last step's reason that was not NULL (null before any); temperatures and the
 * level are null when unknown.
 *
 * @param j  The JSON writer; the caller writes the key first.
 */
void cali_thermo_state_json(cali_json_t *j);

/** @brief kv key of the stored configuration (settings, target, aliases, outside_on). */
#define CALI_THERMO_CFG_KEY "th_cfg"
/** @brief Number of settings in cali_thermo_cfg_t (the keys of the `cfg` object). */
#define CALI_THERMO_CFG_N 17
/** @brief Lowest target, centi-degrees C. */
#define CALI_THERMO_TARGET_MIN 500
/** @brief Highest target, centi-degrees C. */
#define CALI_THERMO_TARGET_MAX 3000
/** @brief Target step, centi-degrees C. */
#define CALI_THERMO_TARGET_STEP 50

/** @brief The settings' names in cali_thermo_cfg_t order (the keys of the API's `cfg` object). */
extern const char *const cali_thermo_cfg_keys[CALI_THERMO_CFG_N];

/** @brief Which members of a cali_thermo_patch_t are given. */
enum cali_thermo_patch_e {
    CALI_THERMO_P_ENABLED = 1 << 0,  /**< `enabled`. */
    CALI_THERMO_P_MODE = 1 << 1,     /**< `mode`. */
    CALI_THERMO_P_TARGET = 1 << 2,   /**< `target`. */
    CALI_THERMO_P_RUNTIME = 1 << 3,  /**< `runtime_min`. */
    CALI_THERMO_P_REF = 1 << 4,      /**< `ref` (an empty alias = null). */
    CALI_THERMO_P_BACKUP = 1 << 5,   /**< `backup`. */
    CALI_THERMO_P_OUT = 1 << 6,      /**< `out`. */
    CALI_THERMO_P_OUTSIDE = 1 << 7,  /**< `outside_on`. */
    CALI_THERMO_P_CFG = 1 << 8,      /**< `cfg` (members in `cfg_has`). */
    CALI_THERMO_P_REV = 1 << 9,      /**< `rev`. */
    CALI_THERMO_P_CONFIRM = 1 << 10  /**< `confirm`. */
};

/** @brief A parsed `POST /api/thermostat` body. */
typedef struct {
    unsigned has;                          /**< CALI_THERMO_P_* of the members given. */
    int enabled;                           /**< The feature switch. */
    int outside_on;                        /**< Use the outside feed-forward. */
    int confirm;                           /**< `confirm: true`. */
    char mode[8];                          /**< "AUTO" or "OFF"; anything else (also a longer text) is refused. */
    int64_t target;                        /**< Target, centi-degrees C (any value; range-checked by cali_thermo_apply()). */
    int64_t runtime_min;                   /**< Run time with a start, min (range-checked likewise). */
    int64_t rev;                           /**< The revision the client saw. */
    char ref[CALI_THERMO_ALIAS_LEN];       /**< Reference alias; a longer text must arrive as "!" (never an alias). */
    char backup[CALI_THERMO_ALIAS_LEN];    /**< Backup alias. */
    char out[CALI_THERMO_ALIAS_LEN];       /**< Outside alias. */
    unsigned cfg_has;                      /**< Bit i = `cfg[i]` given (cali_thermo_cfg_keys order). */
    int64_t cfg[CALI_THERMO_CFG_N];        /**< The settings given. */
} cali_thermo_patch_t;

/** @brief cali_thermo_apply() results; 0 = applied. */
enum cali_thermo_err_e {
    CALI_THERMO_E_STALE = 1,    /**< `rev` is not the current one. */
    CALI_THERMO_E_VALUE = 2,    /**< A value is out of range, or the mode text is wrong. */
    CALI_THERMO_E_SENSOR = 3,   /**< An alias is not a configured Govee. */
    CALI_THERMO_E_CONFIRM = 4,  /**< The switch, AUTO or `cfg` without `confirm: true`. */
    CALI_THERMO_E_STORE = 5     /**< The kv store failed. */
};

/**
 * @brief Check, apply and store a patch, all or nothing; count `rev` when it applied.
 *
 * Order of the checks: `rev` (skipped for a body that is only `mode` "OFF"), the values (mode text,
 * target, run time, aliases, settings), `confirm`.
 * A stored change goes to kv first; the switch (CALI_THERMO_ON_KEY) and an AUTO or OFF request follow.
 *
 * @param p  The patch.
 * @return 0 or a CALI_THERMO_E_* code (nothing changed, except E_STORE of the switch, see cali_thermo_set_enabled()).
 */
int cali_thermo_apply(const cali_thermo_patch_t *p);

/**
 * @brief Write the `GET /api/thermostat` members: `"rev","mode","target","runtime_min","ref","backup","out","outside_on","cfg","status"`.
 * @param j  The JSON writer, just after cali_json_begin() (which writes the object's `{`).
 */
void cali_thermo_api_json(cali_json_t *j);

/**
 * @brief Whether a reference thermometer is selected (`device.thermostat` is shown).
 * @return 1 or 0.
 */
int cali_thermo_has_ref(void);

/**
 * @brief Write the `device.thermostat` object: `{"mode","target","t_ref","level","reason"}`.
 * @param j  The JSON writer; the caller writes the key first.
 */
void cali_thermo_brief_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_THERMOSTAT_H */
