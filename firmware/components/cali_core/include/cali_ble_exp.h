/**
 * @file cali_ble_exp.h
 * @brief The BLE power experiment's schedule and the idle policy, with the glue that drives the
 *        session (ble_exp_run.c).
 *
 * Inside a nightly window the experiment runs the session modes hold, quiet, off, each for a phase,
 * with a probe at each phase end. Activity cuts the running phase short (aborted), returns to hold
 * and holds for CALI_BX_IDLE_MS before the next phase. Activity is a viewer (cali_bx_activity), the
 * ignition on an up link (ble_exp_run.c), or a mode change from outside (a command in quiet or off
 * back to hold, the console, `/api/ble`; also during the probe). The unit's own advert (cali_bx_seen,
 * the sleep indicator) is gone after CALI_BX_GONE_MS without a sighting. A reappearance in quiet or
 * off, after a gone logged in that mode with the link down, is a wake and counts as activity
 * (ble_exp_run.c).
 *
 * The functions are pure: cali_bx_step() returns what to do and the caller acts (ble_exp_run.c). The
 * `cali_bx_*` and `cali_bi_*` half is C99 with no platform and no session calls (the mode numbers
 * are the CALI_BLE_* values of cali_session.h). The `cali_ble_exp_*` half at the end is its glue.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: BLE power experiment
 *    :id: R_FW_BLE_EXP
 *    :status: open
 *
 *    ``ble exp <phase_min> [HH-HH]`` (console; ``POST /api/ble {"exp_min":N,"from":H,"to":H}``) runs
 *    the night experiment: inside the window (default 22-06, Europe/Berlin local time from SNTP or the
 *    unit's clock) it cycles the modes ``hold -> quiet -> off -> hold …``, each ``phase_min`` (1-720)
 *    long, with a probe at the end of every phase; outside the window the mode is ``hold``. Without a
 *    wall clock it is refused (``no_time``). A viewer (``GET /api/state``, or the page load ``GET /`` / ``/app``), a mode change from outside
 *    (a control command in ``quiet`` or ``off``, ``ble mode``, ``/api/ble``, pairing — also while the
 *    phase-end probe runs) or, on an up link, the van's ignition (terminal 15, ``cali_ac_ignition``)
 *    ends the running phase as ``aborted`` and returns to ``hold``; the cycle then holds 10 min
 *    (``CALI_BX_IDLE_MS``, renewed by every further viewer poll and every tick with the ignition on, so a
 *    night drive never reaches the next phase) and goes on with the next phase. The unit's own advert is
 *    the sleep indicator: a sensor window (``sensors on`` only — no window runs otherwise) that sees an
 *    advert whose address is the bond's identity (resolved by the controller, or an RPA resolving with
 *    the bond's IRK) logs ``evt unit_adv`` ``seen:true`` on the first sighting, ``seen:false`` after
 *    ``CALI_BX_GONE_MS`` (3 scan periods + one window, 185 s) without one (also from the start, when
 *    never seen). A reappearance in ``quiet`` or ``off`` after a ``seen:false`` logged in that same mode
 *    **with the link down** is a wake (door, ignition) and counts as activity. The first sighting is not,
 *    and neither is a sighting after a gone logged with the link up, nor the adverts after the link
 *    drops (our ``hold -> off`` disconnect, the unit dropping a ``quiet`` link). Each phase end logs
 *    ``evt ble_phase`` with its ``dur_ms``, start/stop ``evt ble_exp``; stopping or restarting
 *    mid-phase logs that phase ``aborted`` first; a refused phase-end probe logs ``evt probe`` with
 *    ``ok:false``, reason ``refused``. ``ble exp off`` (``{"exp_min":0}``) ends it in ``hold``; a
 *    reboot ends it too (not persisted). ``tools/esplab/soak_report.py`` polls ``/api/state`` and so
 *    counts as a viewer: do not run it during an experiment night.
 * @endverbatim
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: BLE idle policy
 *    :id: R_FW_BLE_IDLE
 *    :status: open
 *
 *    ``POST /api/ble {"idle_min":N}`` (0-60, stored in NVS key ``ble_idle``, read at boot; missing = 0;
 *    the Satellite settings' *Connection* select) sets how the session idles. ``0`` (the default) =
 *    ``hold``: the link stays up. ``N`` > 0: the link is held while someone is active and dropped
 *    otherwise — activity is a viewer (the page load ``GET /`` or ``/app``, so the reconnect starts while the
 *    page loads; ``GET /api/state``: the web UI polls it every 2 s; not ``/api/ble`` or ``/api/log``), a mode the
 *    policy did not set (a control command in ``off`` switches to ``hold``, ``ble mode``, ``/api/ble``),
 *    the van's ignition (terminal 15) on an up link (also during a probe), or a new setting; after
 *    ``CALI_BX_IDLE_MS`` (10 min) without one the mode goes ``off`` (``evt ble_mode`` ``why`` ``idle``),
 *    and every ``N`` minutes while ``off`` a :term:`Probe` reads every function (``evt probe``; skipped
 *    with no bond). Activity during a probe lets it finish, then ``hold``. A running experiment
 *    (``R_FW_BLE_EXP``) owns the session; the policy waits and holds 10 min after it ends. Outside
 *    ``hold`` the Pi wake and an auto-camper restore see the ignition only through a probe (``R_FW_BLE_MODE``),
 *    so they react up to ``N`` minutes late; a GUI opened while ``off`` shows the last probe's data until
 *    the reconnect (≈1 s) and the read-all (≈6 s) are done. The reasons for the policy are in
 *    ``docs/business-logic/DECISIONS.md``.
 * @endverbatim
 */
#ifndef CALI_BLE_EXP_H
#define CALI_BLE_EXP_H

#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief After a viewer, command or wake: hold this many ms, then go on. */
#define CALI_BX_IDLE_MS 600000u
/**
 * @brief The unit's advert is gone after this many ms without a sighting.
 *
 * It equals 3 times SENS_PERIOD_MS plus SENS_WINDOW_MS (sensors.c, 60 s and 5 s). The unit is seen
 * only in a sensor scan window, so the third missed window has ended before it counts.
 * `test_ble_exp.py` checks the sum.
 */
#define CALI_BX_GONE_MS 185000u

/** @brief The experiment's state. Start it with cali_bx_start(). */
typedef struct {
    int on;                 /**< The experiment runs. */
    int from_h;             /**< Window start, Berlin local hour. */
    int to_h;               /**< Window end, Berlin local hour. */
    uint64_t phase_ms;      /**< Length of one phase in ms. */
    uint64_t phase_end;     /**< Time the running phase ends. */
    uint64_t idle_until;    /**< Activity holds the cycle until this time. */
    int phase;              /**< Index into hold, quiet, off. */
    int want;               /**< The running phase's mode, -1 = none. */
    int probing;            /**< The phase-end probe runs. */
    int touched;            /**< Activity happened since the last step. */
    int adv;                /**< The unit's advert: 0 unknown (since start), 1 seen, -1 gone. */
    int adv_clock;          /**< `adv_ms` is set (the first check while unknown, else the last sighting). */
    uint64_t adv_ms;        /**< Time of the last sighting or first check. */
} cali_bx_t;

/** @brief What cali_bx_step() asks the caller to do. */
typedef struct {
    int set_mode;    /**< Set the session to this mode; -1 = nothing. */
    int probe;       /**< 1 = start a probe; 0 = nothing. */
    int done_mode;   /**< The phase of this mode ended; -1 = none. */
    int aborted;     /**< 1 = the phase that ended was cut short; 0 = it ran to its end. */
    int started;     /**< A phase of this mode started; -1 = none. */
} cali_bx_out_t;

/**
 * @brief Whether an hour lies in the window.
 * @param hour    Berlin local hour; -1 (no time) counts as 0.
 * @param from_h  Window start.
 * @param to_h    Window end; equal to `from_h` means all day, below it wraps midnight.
 * @return 1 inside the window, else 0.
 */
int cali_bx_in_window(int hour, int from_h, int to_h);

/**
 * @brief Start the experiment.
 * @param x          The state.
 * @param phase_min  Phase length in minutes.
 * @param from_h     Window start hour.
 * @param to_h       Window end hour.
 */
void cali_bx_start(cali_bx_t *x, unsigned phase_min, int from_h, int to_h);

/** @brief Stop the experiment. @param x The state. */
void cali_bx_stop(cali_bx_t *x);

/**
 * @brief A viewer, command or wake happened: cut the running phase and hold for CALI_BX_IDLE_MS.
 * @param x       The state.
 * @param now_ms  Uptime in ms.
 */
void cali_bx_activity(cali_bx_t *x, uint64_t now_ms);

/**
 * @brief One tick.
 *
 * Contract: the caller applies `set_mode` and starts the probe before the next step, so the next
 * call's `cur_mode` and `probe_busy` reflect them (a mode it did not set reads as an outside change
 * and aborts the phase). ble_exp_run.c meets it: cali_session_set_mode and cali_session_probe change
 * the session synchronously.
 *
 * @param x           The state.
 * @param now_ms      Uptime in ms.
 * @param hour        Berlin local hour, or -1.
 * @param cur_mode    The session's mode now.
 * @param probe_busy  A probe runs.
 * @return What to do.
 */
cali_bx_out_t cali_bx_step(cali_bx_t *x, uint64_t now_ms, int hour, int cur_mode, int probe_busy);

/**
 * @brief Feed the unit's advert state.
 * @param x         The state.
 * @param now_ms    Uptime in ms.
 * @param seen_now  1 a sighting, 0 a check (every tick).
 * @return 1 on a transition to seen (the first sighting, or from gone), -1 on a transition to gone
 *         (no sighting for CALI_BX_GONE_MS since the last one, or since the first check while
 *         unknown), else 0. Always 0 while the experiment is off.
 */
int cali_bx_seen(cali_bx_t *x, uint64_t now_ms, int seen_now);

/**
 * @brief Largest idle-policy interval in minutes.
 *
 * The idle policy (`R_FW_BLE_IDLE`): `probe_min` 0 is hold (the link always up). 1..CALI_BI_MAX_MIN
 * holds while active (a viewer, a command, the ignition: CALI_BX_IDLE_MS after the last) and
 * otherwise goes off with a probe every `probe_min`. A running experiment owns the session
 * (`exp_on`): the policy then waits and holds CALI_BX_IDLE_MS after it. A mode it did not set (a
 * command back to hold, the console, `/api/ble`) counts as activity.
 */
#define CALI_BI_MAX_MIN 60u

/** @brief The idle policy's state. Set it with cali_bi_set(). */
typedef struct {
    unsigned probe_min;     /**< The probe interval in minutes, 0 = hold. */
    uint64_t idle_until;    /**< Activity holds the link until this time. */
    uint64_t next_probe;    /**< Time of the next probe. */
    int want;               /**< The mode the policy last chose, -1 = none (after a set or an experiment). */
    int touched;            /**< Activity happened since the last step. */
} cali_bi_t;

/** @brief What cali_bi_step() asks the caller to do. */
typedef struct {
    int set_mode;   /**< Set the session to this mode; -1 = nothing. */
    int probe;      /**< 1 = start a probe; 0 = nothing. */
} cali_bi_out_t;

/**
 * @brief Set the interval; the policy then starts active (it holds for CALI_BX_IDLE_MS).
 * @param x          The state.
 * @param probe_min  Probe interval in minutes, 0 = hold.
 * @param now_ms     Uptime in ms.
 */
void cali_bi_set(cali_bi_t *x, unsigned probe_min, uint64_t now_ms);

/** @brief Note activity: a viewer, a command or the ignition. @param x The state. */
void cali_bi_activity(cali_bi_t *x);

/**
 * @brief One tick; same contract as cali_bx_step() (the caller applies `set_mode` and starts the probe).
 * @param x           The state.
 * @param now_ms      Uptime in ms.
 * @param cur_mode    The session's mode now.
 * @param probe_busy  A probe runs.
 * @param exp_on      The experiment runs and owns the session.
 * @return What to do.
 */
cali_bi_out_t cali_bi_step(cali_bi_t *x, uint64_t now_ms, int cur_mode, int probe_busy, int exp_on);

/* ---- the run side (ble_exp_run.c, owner task): the one scheduler driving the session ---- */

/** @brief Set up the scheduler and set cali_runner_on_unit_adv. Call on the owner task next to cali_pw_init. */
void cali_ble_exp_init(void);

/**
 * @brief Start the experiment.
 * @param phase_min  Phase length, 1..720 minutes.
 * @param from_h     Window start hour, 0..23.
 * @param to_h       Window end hour, 0..23.
 * @return 0 started, -1 no wall clock (cali_time_ts NONE), -2 bad arguments.
 */
int cali_ble_exp_start(unsigned phase_min, int from_h, int to_h);

/** @brief Stop the experiment: a no-op when off, else back to hold (reason "exp_off"). */
void cali_ble_exp_stop(void);

/**
 * @brief A viewer (GET of the page, the app or `/api/state`) arrived: cut the phase and hold 10 minutes.
 * @param now_ms  Uptime in ms.
 */
void cali_ble_exp_activity(uint64_t now_ms);

/** @brief kv key of the idle policy: 1 byte, the probe interval 0..CALI_BI_MAX_MIN, missing = 0 (hold). */
#define CALI_BI_KEY "ble_idle"

/**
 * @brief Store and apply the idle policy.
 * @param min  Probe interval in minutes, 0 = hold.
 * @return 0 ok, -1 out of range or the store failed (nothing changed).
 */
int cali_ble_idle_set(unsigned min);

/** @brief kv key of the rest poll (`R_FW_BLE_REST`): 1 byte, minutes 0..240, missing = 30. */
#define CALI_BR_KEY "ble_rest"

/**
 * @brief Store and apply the rest poll.
 * @param min  Probe interval in minutes, 0 = none.
 * @return 0 ok, -1 out of range or the store failed (nothing changed).
 */
int cali_ble_rest_set(unsigned min);

/**
 * @brief The owner tick: step the experiment and the policy, then act (set the mode, probe, log events).
 * @param now_ms  Uptime in ms.
 */
void cali_ble_exp_tick(uint64_t now_ms);

/**
 * @brief Write the `device.ble` object.
 *
 * Members: `mode`, `probing`, `exp`, `idle_min`, `rest_poll_min`, `unit_adv_seen_ms_ago`. The last
 * is the time in ms since the unit's advert was last seen, at any time and not only in the
 * experiment; null means never since boot. It is the live check of the sighting and carries no address.
 *
 * @param j     The JSON writer; the caller writes the key first.
 * @param diag  1 for the diag members.
 */
void cali_ble_exp_json(cali_json_t *j, int diag);

/**
 * @brief Write the same members as cali_ble_exp_json() without the braces: the top level of `/api/ble`.
 * @param j     The JSON writer.
 * @param diag  1 for the diag members.
 */
void cali_ble_exp_fields(cali_json_t *j, int diag);

#ifdef __cplusplus
}
#endif

#endif /* CALI_BLE_EXP_H */
