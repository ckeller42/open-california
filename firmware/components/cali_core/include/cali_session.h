/**
 * @file cali_session.h
 * @brief The bonded session: discover, subscribe, read every state function, keep the link alive
 *        with the 1003 heartbeat, reconnect by bond, hold the latest frame per function.
 *
 * Lifecycle (all calls on the task that delivers the transport events, like the runner):
 * - cali_session_init(): once, after cali_runner_init(); hooks cali_runner_on_other.
 * - cali_session_boot(): once the stack is usable. A stored bond gives connect_bonded() (the
 *   transport re-encrypts with the stored keys). No bond gives nothing: the session never scans,
 *   pairing is the runner's, on "pair".
 * - cali_session_on_bonded(): the runner reached bonded; the link is up and encrypted.
 * - cali_session_stop(): the runner left bonded or starts a flow (forget, pair). No more heartbeat,
 *   reads or reconnects until the next on_bonded or boot.
 * - cali_session_tick(): every ~100 ms. Heartbeat, warm-up, reconnect and encryption timers. It also
 *   ticks the control sequencer (cali_ctl_tick), and WRITTEN events go to cali_ctl_on_written
 *   (cali_control.h).
 *
 * **Link up** (on_bonded, or ENC_OK on a reconnect): the heartbeat write_heartbeat(counter++) runs
 * every CODEC_HEARTBEAT_PERIOD_MS starting at CODEC_HEARTBEAT_START (first beat on the next tick),
 * and discover() runs. DISCOVERED subscribes every CODEC_CHARS entry (a char without NOTIFY is
 * refused by the transport and skipped). Then the heartbeat runs for the warm-up (default 0: the
 * first tick after DISCOVERED starts the reads, see cali_session_set_warm_ms) and every function is
 * read one at a time. Each READ (status 0) replaces that function's frame, and so does a NOTIFY at
 * any time: the last frame to arrive wins (the app subscribes, then reads, with one decoder for
 * both). After the last read the session prints one SNAP (cali_console_snapshot); once the link's
 * first read-all completed, a NOTIFY also prints a SNAP. While the link stays up, water (1302) is
 * re-read every CALI_SESSION_WATER_REREAD_MS after the read-all, so a stale latch served at connect
 * is corrected without a reconnect. The snapshot therefore always holds exactly one whole frame per
 * function, never a mix of two.
 *
 * **Link loss** is any of: DISCONNECTED, CONNECT_FAIL, ENC_FAIL, a failed discovery, a heartbeat
 * that cannot be written or completes with an error, no encryption within
 * CALI_SESSION_ENC_TIMEOUT_MS of CONNECTED, or no connect verdict within
 * CALI_SESSION_CONNECT_TIMEOUT_MS of connect_bonded() (a terminal event lost, for example a connect
 * silently cancelled under a scan). The session then calls disconnect() and connect_bonded() after
 * 1 s, doubling to a 60 s cap; the delay resets once a link has held for CALI_SESSION_STABLE_MS.
 * Without a stored bond the session goes inactive instead. A unit-side hang-up (HCI 0x13) takes the
 * same backoff, unless it starts the rest below. A control command meanwhile
 * (cali_session_connect_now) makes a waiting reconnect due at once.
 *
 * **Power-save rest**: when the unit itself ends the link (DISCONNECTED with HCI 0x13, raw 19 or
 * NimBLE's 531) while the stored frames say camping mode off (1202 State 0), no shore power (1602
 * StateLandAfs neither 1 active nor 2 standby), the ignition known off, no camping restore owed and
 * no control command in flight, the session does not reconnect. Every other loss, or an unknown
 * frame, takes the backoff above. The rest ends on a control command (cali_session_connect_now), on
 * cali_session_connect (the Connect button), on a rest probe whose read pass no longer meets the
 * conditions, on a viewer with recent input, on pairing, or on cali_session_stop. A mode change
 * alone never connects out of it. While resting, a probe runs every rest poll interval
 * (cali_session_set_rest_poll, 0 = none, not in mode off): connect, one read pass, then the session
 * drops its own link and rests again (or holds, when the read shows camping on or shore power).
 *
 * C99, no malloc, no NimBLE or ESP-IDF includes, no clock of its own.
 */
#ifndef CALI_SESSION_H
#define CALI_SESSION_H

#include <stddef.h>
#include <stdint.h>

#include "cali_control.h"
#include "cali_transport.h"
#include "codec.h"

#ifdef __cplusplus
extern "C" {
#endif

/** First reconnect delay after a link loss, in ms. */
#define CALI_SESSION_BACKOFF_MIN_MS 1000u
/** Cap of the doubling reconnect delay, in ms. */
#define CALI_SESSION_BACKOFF_MAX_MS 60000u
/**
 * A link must hold this long (ms) before its loss resets the backoff to the minimum. With camping
 * off the unit ends each link 1 to 31 s after it comes up; a reset at link-up kept those
 * reconnects at 1 s.
 */
#define CALI_SESSION_STABLE_MS 60000u
/**
 * No reconnect sooner than this (ms) after a link was lost, backoff or a command's nudge alike.
 * With camping off the unit cuts every link that starts less than 6 s after it ended the previous
 * one, about 1.2 s in.
 */
#define CALI_SESSION_UNIT_COOLDOWN_MS 7000u
/** Longest wait (ms) from CONNECTED to an encrypted link before the session counts a loss. */
#define CALI_SESSION_ENC_TIMEOUT_MS 15000u
/**
 * Longest wait (ms) from connect_bonded() to a connect verdict. Twice the NimBLE transport's own
 * 10 s connect timeout: this watchdog only fires when the transport's verdict (CONNECTED or
 * CONNECT_FAIL) got lost, so it never races it.
 */
#define CALI_SESSION_CONNECT_TIMEOUT_MS 20000u
/**
 * Water (1302) re-read period (ms) while the link is up: calictl's POLL_INTERVAL (30 s), at which
 * it reads 1302 on every poll. The app has no periodic water re-read (it reconnects instead). The
 * water guard's constants live with its one C implementation, csrc/ports.h.
 */
#define CALI_SESSION_WATER_REREAD_MS 30000u

/**
 * @brief What a cali_session_io_fn call reports.
 */
enum cali_session_io_e {
    CALI_SESSION_IO_NOTIFY, /**< A NOTIFY as it arrived (the raw frame, before the water guard). */
    CALI_SESSION_IO_READ,   /**< A successful READ as it arrived (the raw frame, before the water guard). */
    CALI_SESSION_IO_UP,     /**< The link came up (encrypted). */
    CALI_SESSION_IO_DOWN    /**< An up link went away (data NULL, len 0). */
};

/**
 * @brief Hook type for the SD log: every unit frame the session sees and every link change.
 *
 * Called on the session's task, from inside its event handling: the hook only copies and returns.
 *
 * @param what One of the CALI_SESSION_IO_* values.
 * @param char_short Short id of the characteristic the frame came from.
 * @param data The frame; NULL for UP and DOWN.
 * @param len Length of data in bytes; 0 for UP and DOWN.
 */
typedef void (*cali_session_io_fn)(int what, uint16_t char_short, const uint8_t *data, size_t len);

/** The SD log's listener for unit frames and link changes. NULL = nobody listens. */
extern cali_session_io_fn cali_session_on_io;

/**
 * @brief Set the session up on a transport.
 * @param t The BLE transport; it must outlive the session.
 */
void cali_session_init(const cali_transport_t *t);

/** @brief Start the session once the stack is usable: connect by bond when one is stored. */
void cali_session_boot(void);

/** @brief The runner reached bonded: discover, subscribe, start the read-all. */
void cali_session_on_bonded(void);

/** @brief Stop the heartbeat, the reads and the reconnects until the next on_bonded or boot. */
void cali_session_stop(void);

/**
 * @brief Advance the session's timers: heartbeat, warm-up, reconnect backoff, encryption timeout.
 *
 * Also ticks the control sequencer (cali_ctl_tick).
 *
 * @param now_ms Uptime clock in ms, from the caller.
 */
void cali_session_tick(uint64_t now_ms);

/**
 * @brief A control command found no armed link (control_run.c's NOT_READY).
 *
 * A mode other than hold switches to hold ("command"); a reconnect waiting out its backoff delay
 * becomes due on the next tick.
 */
void cali_session_connect_now(void);

/** Default rest poll interval in minutes. */
#define CALI_SESSION_REST_POLL_DEFAULT_MIN 30u
/** Largest rest poll interval in minutes. */
#define CALI_SESSION_REST_POLL_MAX_MIN 240u

/**
 * @brief The Connect button: leave the rest and hold, and make a reconnect due now (after the cooldown).
 * @return 0 on success, -1 when there is no bond.
 */
int cali_session_connect(void);

/** @brief Whether the session is in power-save rest (no auto-reconnect). @return 1 in rest, else 0. */
int cali_session_rest(void);

/**
 * How long (ms) a GET /api/state counts as a viewer. The page polls every 2 s, and only while
 * visible. While a viewer and recent input both hold, the unit's power-save hang-up does not start
 * the rest: the session redials after the cooldown with the backoff at its minimum (a planned
 * redial, see cali_session_redial), and input in rest leaves it at once (mode off excepted).
 * Without a viewer, or after CALI_SESSION_INPUT_IDLE_MS without input, the rest runs as usual. A
 * reconnect out of rest that never gets a link up goes back to rest at its first failure once no
 * viewer looks.
 */
#define CALI_SESSION_VIEWER_MS 10000u
/**
 * How long (ms) user input keeps a viewer's redial going. Input is POST /api/ble {"active":true},
 * a page load, the page's GET /api/state?seen=1, a command, or Connect.
 */
#define CALI_SESSION_INPUT_IDLE_MS 600000u

/**
 * @brief A viewer fetched GET /api/state (with ?seen=1 it also counts as input, see cali_session_input).
 * @param now_ms Uptime clock in ms.
 */
void cali_session_viewer(uint64_t now_ms);

/**
 * @brief User input on the page; it also counts as a viewer.
 * @param now_ms Uptime clock in ms.
 */
void cali_session_input(uint64_t now_ms);

/**
 * @brief Whether a planned redial runs.
 * @return 1 from a planned redial's hang-up until that link takes commands again (any other loss
 *         clears it), else 0.
 */
int cali_session_redial(void);

/**
 * @brief Time to the next rest probe.
 * @return Milliseconds to the next probe; -1 when not resting or the rest poll is off.
 */
int64_t cali_session_rest_next_ms(void);

/**
 * @brief Set the rest poll interval.
 *
 * The session does not store it; ble_exp_run.c does.
 *
 * @param min Interval in minutes; 0 = no probe.
 */
void cali_session_set_rest_poll(unsigned min);

/** @brief The rest poll interval in minutes (0 = none). @return The interval. */
unsigned cali_session_rest_poll_min(void);

/** @brief BLE link modes for cali_session_set_mode. */
enum cali_ble_mode_e {
    CALI_BLE_HOLD,  /**< Hold the link and the heartbeat. */
    CALI_BLE_QUIET, /**< Hold the link; the heartbeat only while a read-all warms up or reads, or a command is in flight. */
    CALI_BLE_OFF    /**< No link and no reconnect. */
};

/**
 * @brief Set the BLE link mode (owner task).
 *
 * hold is the default. quiet keeps the link, with the 1003 heartbeat only while a read-all warms
 * up or reads, or a control command is in flight (cali_ctl_busy). off has no link and no reconnect.
 *
 * @param mode CALI_BLE_HOLD, CALI_BLE_QUIET or CALI_BLE_OFF.
 * @param why Reason text for the `ble_mode` event.
 * @return 0 on success, -1 for a bad mode.
 */
int cali_session_set_mode(int mode, const char *why);

/** @brief The current BLE link mode. @return CALI_BLE_HOLD, CALI_BLE_QUIET or CALI_BLE_OFF. */
int cali_session_mode(void);

/**
 * @brief Start one fresh read-all now.
 *
 * Connects first when the mode is off; the heartbeat runs through the warm-up. Then it emits the
 * `probe` event and goes back to the mode (off: disconnect).
 *
 * @return 0 when started, -1 when there is no bond or a probe already runs.
 */
int cali_session_probe(void);

/**
 * @brief Set the heartbeat warm-up before the next read pass.
 *
 * Default 0: reads need none. A measurement knob (POST /api/ble warm_ms); not stored, a reboot
 * restores the default.
 *
 * @param ms Warm-up in ms.
 */
void cali_session_set_warm_ms(uint32_t ms);

/** @brief Whether a probe runs. @return 1 while a probe runs, else 0. */
int cali_session_probing(void);

/**
 * @brief Name of a link mode.
 * @param mode CALI_BLE_HOLD, CALI_BLE_QUIET or CALI_BLE_OFF.
 * @return "hold", "quiet" or "off"; "?" for any other value.
 */
const char *cali_session_mode_name(int mode);

/**
 * @brief Whether the session holds a bond it keeps a link for.
 *
 * True through reconnect backoff too, while no link exists. For the actual encrypted BLE link, see
 * cali_session_link_up().
 *
 * @return 1 when active, else 0.
 */
int cali_session_active(void);

/**
 * @brief Whether the link is up.
 *
 * 1 once the link is encrypted (past ENC_OK): 0 during CONNECTING and CONNECTED (before
 * encryption), during reconnect backoff, and whenever cali_session_active() is 0. Discovery may
 * still be pending when this returns 1, so do not use it as proof that discovery finished: a caller
 * that needs the link ready for commands uses cali_session_ready(). This is what /api/state
 * reports as device.link.up.
 *
 * @return 1 when up, else 0.
 */
int cali_session_link_up(void);

/**
 * @brief Whether a control write may go out.
 *
 * 1 when the mode is hold, the link is up, its first read-all finished, and the heartbeat has
 * ticked for CODEC_ARM_DELAY_MS since the link came up or quiet or off went back to hold. This is
 * calictl.device's ARM_DELAY_S, here once per link (or mode return) instead of per write.
 *
 * @return 1 when ready, else 0.
 */
int cali_session_ready(void);

/**
 * @brief The stored frame of CODEC_CHARS[i].
 * @param i Index into CODEC_CHARS; must be below CODEC_NCHARS.
 * @param frame Receives a pointer to the stored frame.
 * @param len Receives the frame length in bytes.
 * @return 1 with *frame and *len set, or 0 when that function has not been read yet (or its read
 *         failed and no notification came).
 */
int cali_session_frame(size_t i, const uint8_t **frame, size_t *len);

/**
 * @brief As cali_session_frame, but only a frame read or pushed on the current link.
 *
 * This is what the control gates run on (cali_control.h): a previous link's state is not live.
 * Two exceptions mark a frame live that still holds earlier data. A lighting frame that only
 * carries configuration (a config or ack frame) leaves the stored lamp state in place and marks it
 * live. A water frame the stale-latch guard rejects is replaced by the held baseline and marked
 * live. A gate that needs a fresh lamp or water value cannot rely on this flag alone.
 *
 * @param i Index into CODEC_CHARS; must be below CODEC_NCHARS.
 * @param frame Receives a pointer to the stored frame.
 * @param len Receives the frame length in bytes.
 * @return 1 with *frame and *len set, or 0 for no frame or one kept from an earlier link.
 */
int cali_session_frame_live(size_t i, const uint8_t **frame, size_t *len);

/**
 * @brief The lighting configuration the unit reported in its own 1502 frames.
 *
 * It is semantics.lighting_config over every stored frame (a READ or a NOTIFY, never a write of
 * ours), as name and value pairs.
 *
 * @param live 1 = only what this link's frames reported (the gates and the wake-up builder); 0 =
 *        across links (what SNAP and /api/state show, like the frames themselves).
 * @param out Receives the known keys; room for CALI_LCFG_N pairs.
 * @return The number of known keys.
 */
int cali_session_light_cfg(int live, codec_kv_t out[CALI_LCFG_N]);

/**
 * @brief When the session last stored a frame.
 * @return The now_ms of the latest cali_session_tick at that time; 0 = no frame stored since
 *         cali_session_init.
 */
uint64_t cali_session_last_update_ms(void);

/**
 * @brief Whether the served water (1302) frame is the held last-plausible reading, not the live one.
 *
 * The parked unit stops measuring and hands back the latched low (true 17 L read as 1 L), so the
 * guard keeps the last plausible frame (calictl.freshness.implausible_water_drop: a fresh drop to
 * FRESH_LATCH_MAX_L or less while the grey tank is exactly frozen), or a new level that has not yet
 * stayed unchanged for FRESH_SETTLE_MS (the unit's 1 to real-value measurement ramp, see
 * calictl.freshness.settle_water, run by csrc/ports.c freshness_settle). The baseline persists in
 * NVS, so a reboot while parked shows the real level; with no baseline nothing is shown until a
 * level of 2 or more settles. /api/state reports this as device.water_held; the shared UI flags
 * the tank stale.
 *
 * @return 1 when held, else 0.
 */
int cali_session_water_held(void);

/**
 * @brief When the water baseline was last confirmed by a real measurement on this boot.
 *
 * /api/state reports now minus this as device.water_held_age_ms while the water is held.
 *
 * @return The session tick clock in ms at the last adoption or confirmation; 0 = not this boot
 *         (an NVS-restored baseline carries no time).
 */
uint64_t cali_session_water_good_ms(void);

/**
 * @brief Whether a unit of a given car variant has a CODEC_CHARS function.
 *
 * The app's per-variant gate (isSupportedForVariant): 1004 CarVariant 0 = California T7, 1 = Grand
 * California, 2 = California Next, any other value = T7 (as the app).
 *
 * @param function Function name from CODEC_CHARS.
 * @param car_variant The 1004 CarVariant; below 0 = no 1004 frame, variant unknown.
 * @return 1 when the variant has the function; 1 for every function when car_variant is below 0
 *         (the session then subscribes and reads everything); else 0.
 */
int cali_variant_has(const char *function, int car_variant);

#ifdef __cplusplus
}
#endif

#endif /* CALI_SESSION_H */
