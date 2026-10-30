/**
 * @file cali_control.h
 * @brief The satellite's control path: calictl.control's builders and gates in C (control.c, pure)
 *        and the one-command-at-a-time sequencer onto the session's link (control_run.c).
 *
 * **Plan** (control.c): cali_ctl_plan() answers what calictl would for `set <fn> <what> <value>`
 * over the decoded state behind get(). The answer is ELSEWHERE for the roof and for any function
 * but the five in control_consts.h. `lighting wakeup` without a `local_now` is ELSEWHERE with
 * CALI_REASON_WAKEUP_CLOCK (the page is the ESP's clock: its wall clock read as UTC, in seconds).
 * Otherwise the answer is REFUSED with command_precondition's text, or BAD_VALUE or NONE
 * (control.build raised or returned None), or OK with the writes in calictl.device.actuate's order
 * (preface, commit, frame, commit; commits only for lighting, each at least CODEC_FOLLOW_DELAY_MS
 * after the previous write's ACK). The value is calictl's string form of the JSON value (an
 * integer's decimal text; null = ""), shorter than CALI_CTL_VALUE_MAX. Held equal to Python by
 * tests/vectors/control.json (tests/firmware/test_control_parity.py).
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The firmware's control frames and gates are byte-identical to calictl's
 *    :id: R_FW_CONTROL_TWIN
 *    :status: implemented
 *    :tags: esp32, control
 *
 *    For cooler, campingmode, lighting (power, zones, ``all``, ``profile``, ``save_profile`` with its
 *    SET_COLOR preface, ``door_contact``), airheater and energy, the firmware shall plan exactly the
 *    writes ``calictl.control`` would — the same ``command_precondition`` refusal texts, the same
 *    frames from ``control.build``, the same lighting commit after each frame — on every vector of
 *    ``tests/vectors/control.json`` (generated from the Python, ``--check`` in CI). The roof is
 *    answered "Only via buspi or the app"; ``lighting wakeup`` follows ``R_FW_WAKEUP``. The twin is
 *    ``firmware/components/cali_core/control.c`` (``cali_ctl_plan``), pure C99 over the generated
 *    ``csrc/control_consts.h``.
 * @endverbatim
 *
 * **The write allow-list** (cali_ctl_write_ok): a characteristic write is allowed only to one of
 * the five control chars of CALI_CTL_CHARS at exactly its frame length, never the roof's control
 * char. The 1003 heartbeat is the transport's own write_heartbeat. Both control_run.c (before
 * every write) and the NimBLE transport's write() (for any caller) call it: the single choke point.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The firmware writes only the five control chars at their frame length, and the 1003 heartbeat
 *    :id: R_FW_WRITE_ALLOWLIST
 *    :status: implemented
 *    :tags: esp32, control, safety
 *
 *    The firmware's only characteristic-value writes shall be the ``1003`` liveness heartbeat
 *    (``write_heartbeat``) and control frames to the five chars of ``CALI_CTL_CHARS``
 *    (``csrc/control_consts.h``, generated from ``tools/gen_c_dict.ESP_CONTROL_FUNCTIONS``: ``1101``
 *    cooler, ``1201`` camping mode, ``1501`` lighting, ``1601`` energy, ``1701`` air heater), each at
 *    exactly its control frame length. One function, ``cali_ctl_write_ok``
 *    (``firmware/components/cali_core/control.c``), decides it, and both the sequencer
 *    (``control_run.c``, before every write) and the NimBLE transport's ``write`` (``ble_nimble.c``
 *    ``t_write``, for any caller) call it. The roof's ``1401`` is never on the list (the generator
 *    asserts it); no roof builder exists. CCCD writes only enable notifications.
 * @endverbatim
 *
 * **Run** (control_run.c, on the owner task like everything in cali_core): cali_ctl_submit()
 * refuses at once (BUSY, NOT_READY, REFUSED, ELSEWHERE, BAD_VALUE, NONE, each logged as
 * `control: <fn>/<what> ...`) or accepts (PENDING). The frames then go out on cali_ctl_tick(), one
 * write with response at a time (transport write, then CALI_TEV_WRITTEN, then cali_ctl_on_written).
 * done(result, reason) is called exactly once with:
 * - OK;
 * - FAILED: a write not issued, refused with an ATT error, or the link lost before any command
 *   frame went out;
 * - UNCONFIRMED: the link lost after a command frame went out (the unit may have actuated it with
 *   the ACK lost to the drop);
 * - TIMEOUT: CALI_CTL_DEADLINE_MS after the submit;
 * - REFUSED: a wake-up whose config the pull did not bring, reason CALI_WAKEUP_UNKNOWN. The reason
 *   is NULL for every other result.
 *
 * A `plan.pull` refusal on an armed link becomes PENDING: REQUEST_CONFIG and a commit go out, then
 * up to CODEC_CONFIG_PULL_MS after the commit's ACK the command is planned again every tick over
 * the unit's reply frames (calictl serve._pull_lighting_config), then written or refused. That is
 * one deadline, CALI_CTL_DEADLINE_MS + CODEC_CONFIG_PULL_MS, under the HTTP core's
 * CALI_HTTP_PENDING_MAX_MS. `local_now` is the page's wall clock read as UTC (s); below 0 = none
 * (the console). NOT_READY means cali_session_ready() is 0 (no armed link: the link up for
 * CODEC_ARM_DELAY_MS with its first read-all done) or the function's state was not read (or pushed)
 * on this link. ELSEWHERE, NONE and BAD_VALUE answer without waiting for it, armed or not.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: Control commands: armed link, one at a time, station mode only
 *    :id: R_FW_CONTROL_API
 *    :status: implemented
 *    :tags: esp32, control, web
 *
 *    The firmware shall accept a control command from ``POST /api/command`` (calictl's request and
 *    response shape) only in station mode, and from the console ``set <fn> <what> [value]``; a
 *    command is accepted only on an armed link (up ``CODEC_ARM_DELAY_MS`` with its first read-all
 *    done) and when the function's state has been read on that link; it writes the planned frames
 *    one at a time with response, a lighting commit at least ``CODEC_FOLLOW_DELAY_MS`` after the
 *    previous ACK; it reports
 *    success only after every write was ACKed, a refused write or a lost link as a failure (no
 *    further frame), and gives up after ``CALI_CTL_DEADLINE_MS``; a second command while one runs
 *    — or while a timed-out write still awaits its ACK — is refused (busy). The sequencer is
 *    ``firmware/components/cali_core/control_run.c``, ticked by the session. ``POST /api/command``
 *    (``web.c``) answers in calictl's request and response shape — ``applied`` never ``true`` (no
 *    readback check), a gate's refusal as ``{"applied":false,"refused":<text>}`` — and only after the
 *    sequencer's done callback: the HTTP core holds the connection (``CALI_HTTP_PENDING``, no idle
 *    timeout meanwhile) so a refused or timed-out write is a 502/504, never "Sent". Outside station
 *    mode (setup hotspot, setup-flow join, unprovisioned) it is ``403 setup_mode`` before anything
 *    reaches the control module; ``/api/state`` reports it as ``device.control.writes``.
 * @endverbatim
 *
 * C99, no malloc, no NimBLE or ESP-IDF includes.
 */
#ifndef CALI_CONTROL_H
#define CALI_CONTROL_H

#include <stddef.h>
#include <stdint.h>

#include "cali_transport.h"
#include "codec.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Result codes of cali_ctl_plan (plan) and of a command (run, in done and cali_ctl_submit). */
enum cali_ctl_rc_e {
    CALI_CTL_OK = 0,      /**< Plan: frames to write. Run: every frame written and ACKed. */
    CALI_CTL_REFUSED,     /**< A command_precondition gate; reason = its text. */
    CALI_CTL_ELSEWHERE,   /**< Not on the ESP; reason = CALI_REASON_ELSEWHERE. */
    CALI_CTL_BAD_VALUE,   /**< control.build raised. */
    CALI_CTL_NONE,        /**< control.build returned None. */
    CALI_CTL_PENDING,     /**< Run: accepted, frames going out. */
    CALI_CTL_BUSY,        /**< Run: a command (or an unacknowledged write) is still in flight. */
    CALI_CTL_NOT_READY,   /**< Run: no armed link yet, or the function's state is unknown. */
    CALI_CTL_FAILED,      /**< Run: a write not issued, refused by the unit, or the link lost. */
    CALI_CTL_TIMEOUT,     /**< Run: not done within CALI_CTL_DEADLINE_MS. */
    CALI_CTL_UNCONFIRMED  /**< Run: the link dropped after a command frame went out; it may have landed. */
};

/** Most frames one plan holds. */
#define CALI_CTL_MAX_FRAMES 4
/** Longest value text, with the terminator. */
#define CALI_CTL_VALUE_MAX 64
/** A command is given up this long (ms) after the submit. */
#define CALI_CTL_DEADLINE_MS 4000u
/** The session tick period in ms (TICK_MS in host_main.c and app_main.c). */
#define CALI_CTL_TICK_MS 100u

/** @brief One write of a plan. */
typedef struct {
    uint16_t chr;       /**< Control char short id. */
    uint16_t delay_ms;  /**< Wait after the previous write's ACK, in ms. */
    uint8_t len;        /**< Frame length in bytes. */
    uint8_t data[CODEC_FRAME_MAX]; /**< The frame. */
} cali_ctl_frame_t;

/** @brief The answer of cali_ctl_plan. */
typedef struct {
    int rc;              /**< CALI_CTL_OK, REFUSED, ELSEWHERE, BAD_VALUE or NONE. */
    const char *reason;  /**< REFUSED and ELSEWHERE: static text, else NULL. */
    int pull;            /**< 1 = REFUSED only for want of the unit's lighting config (reason
                          *   CALI_WAKEUP_UNKNOWN): the sequencer pulls it with REQUEST_CONFIG and plans again. */
    size_t n;            /**< Number of frames in f. */
    cali_ctl_frame_t f[CALI_CTL_MAX_FRAMES]; /**< The writes, in order. */
} cali_ctl_plan_t;

/**
 * @brief Reader of the decoded state behind a plan.
 * @param fn Function name.
 * @param field Field name.
 * @param out Receives the decoded value.
 * @return 1 with *out set, or 0 when unknown (no frame, or the frame too short for the field).
 */
typedef int (*cali_ctl_get_t)(const char *fn, const char *field, uint32_t *out);

/**
 * @brief Completion callback of a command; called exactly once.
 * @param result CALI_CTL_OK, FAILED, UNCONFIRMED, TIMEOUT or REFUSED.
 * @param reason REFUSED's text, else NULL.
 */
typedef void (*cali_ctl_done_t)(int result, const char *reason);

/**
 * @brief Keys of the lighting configuration the unit reports in its own 1502 frames.
 *
 * This is semantics.lighting_config in C: Mode 20 = wake-up (Timestamp and LightValue), Mode 16
 * with ProfileNumber 8 = door contact (LightValue), Mode 12 with ProfileNumber other than 13 (13 =
 * the app's own REQUEST echo) = stored favourites (LightValue & 0x7f), Mode 4 with ProfileNumber
 * 1 to 7 adds that favourite's bit once the bits are known. Held equal to Python by
 * T_FW_LIGHT_CFG_PARITY.
 */
enum cali_lcfg_key_e {
    CALI_LCFG_WAKE_TS, /**< Wake-up timestamp. */
    CALI_LCFG_WAKE_LV, /**< Wake-up light value. */
    CALI_LCFG_DOOR,    /**< Door-contact light value. */
    CALI_LCFG_FAVS,    /**< Stored favourites bit set. */
    CALI_LCFG_N        /**< Number of keys. */
};

/** Key names, in enum order (semantics.LIGHT_CONFIG_KEYS). */
extern const char *const CALI_LCFG_KEYS[CALI_LCFG_N];

/** @brief The lighting configuration read so far. */
typedef struct {
    unsigned have;          /**< Bit k set: v[k] is known. */
    uint32_t v[CALI_LCFG_N]; /**< Values, indexed by CALI_LCFG_*. */
} cali_light_cfg_t;

/**
 * @brief Read the lighting configuration (semantics.lighting_config(None, state)).
 *
 * Takes the keys get() already answers (a latch carried over), then this frame's own config.
 *
 * @param get Reader of the decoded state.
 * @param out Receives the configuration.
 */
void cali_light_cfg(cali_ctl_get_t get, cali_light_cfg_t *out);

/**
 * @brief Whether the 1502 frame get() answers reports the lamps and the active profile.
 *
 * True for Mode 4 outside ProfileNumber 1 to 7 and Mode 16 outside ProfileNumber 8; false for a
 * config or ack frame (semantics.lighting_reports_state).
 *
 * @param get Reader of the decoded state.
 * @return 1 when the frame reports state, else 0.
 */
int cali_light_reports_state(cali_ctl_get_t get);

/**
 * @brief Plan a command exactly as calictl would.
 * @param fn Function name.
 * @param what Command name.
 * @param value calictl's string form of the JSON value; shorter than CALI_CTL_VALUE_MAX.
 * @param local_now The page's wall clock read as UTC, in seconds; below 0 = none.
 * @param get Reader of the decoded state the gates run on.
 * @param out Receives the plan.
 */
void cali_ctl_plan(const char *fn, const char *what, const char *value, int64_t local_now,
                   cali_ctl_get_t get, cali_ctl_plan_t *out);

/**
 * @brief The plan that pulls the unit's lighting config: REQUEST_CONFIG at 0, the commit at
 *        CODEC_FOLLOW_DELAY_MS.
 * @param out Receives the plan.
 */
void cali_ctl_pull_plan(cali_ctl_plan_t *out);

/**
 * @brief The write allow-list: whether a characteristic write may go out.
 * @param chr Short id of the characteristic.
 * @param len Length of the frame in bytes.
 * @return 1 for one of the five control chars of CALI_CTL_CHARS at exactly its frame length, else 0.
 */
int cali_ctl_write_ok(uint16_t chr, size_t len);

/**
 * @brief Set the sequencer up on a transport.
 * @param t The BLE transport; it must outlive the sequencer.
 */
void cali_ctl_run_init(const cali_transport_t *t);

/**
 * @brief Submit a command to the sequencer.
 * @param fn Function name.
 * @param what Command name.
 * @param value calictl's string form of the JSON value.
 * @param local_now The page's wall clock read as UTC, in seconds; below 0 = none (the console).
 * @param done Called exactly once when the command ends, unless the call answers at once.
 * @param reason Receives the static refusal text of an immediate REFUSED or ELSEWHERE; else NULL.
 * @param src Who asks ("web", "auto_camper" or "console"), for the `cmd` diagnostics event.
 * @return CALI_CTL_PENDING when accepted, else the immediate refusal: BUSY, NOT_READY, REFUSED,
 *         ELSEWHERE, BAD_VALUE or NONE.
 */
int cali_ctl_submit(const char *fn, const char *what, const char *value, int64_t local_now,
                    cali_ctl_done_t done, const char **reason, const char *src);

/**
 * @brief Send the next frame when it is due.
 * @param now_ms Uptime clock in ms; the session passes its own.
 */
void cali_ctl_tick(uint64_t now_ms);

/** @brief Whether a command runs or a write awaits its ACK (submit would say BUSY). @return 1 or 0. */
int cali_ctl_busy(void);

/**
 * @brief Feed a CALI_TEV_WRITTEN event to the sequencer.
 * @param e The transport event.
 */
void cali_ctl_on_written(const cali_tevent_t *e);

#ifdef __cplusplus
}
#endif

#endif /* CALI_CONTROL_H */
