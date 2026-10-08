/* cali_session.h — the bonded session: discover, subscribe, read every state function, keep the
 * link alive with the 1003 heartbeat, reconnect by bond, hold the latest frame per function (#154).
 *
 * Lifecycle (all on the task that delivers the transport events, like the runner):
 *   cali_session_init(t)        once, after cali_runner_init(t); hooks cali_runner_on_other.
 *   cali_session_boot()         once the stack is usable: a stored bond -> connect_bonded() (the
 *                               transport re-encrypts with the stored keys); no bond -> nothing
 *                               (never scans: pairing is the runner's, on "pair").
 *   cali_session_on_bonded()    the runner reached bonded: the link is up and encrypted.
 *   cali_session_stop()         the runner left bonded or starts a flow (forget, pair): no more
 *                               heartbeat, reads or reconnects until the next on_bonded/boot.
 *   cali_session_tick(now_ms)   every ~100 ms: heartbeat + warm-up + reconnect + encryption timers.
 *                               It also ticks the control sequencer (cali_ctl_tick), and WRITTEN
 *                               events go to cali_ctl_on_written (cali_control.h).
 *
 * Link up (on_bonded, or ENC_OK on a reconnect): heartbeat write_heartbeat(counter++) every
 * CODEC_HEARTBEAT_PERIOD_MS starting at CODEC_HEARTBEAT_START (first beat on the next tick), and
 * discover(). DISCOVERED -> subscribe() every CODEC_CHARS entry (a char without NOTIFY is refused
 * by the transport and skipped), then — like calictl.device.read_all — let the heartbeat run for
 * CODEC_HEARTBEAT_WARMUP_MS (the first cali_session_tick at or past DISCOVERED + warm-up starts the
 * reads) and read() every function one at a time in CODEC_CHARS order; each READ (status 0)
 * replaces that function's frame, and so does a NOTIFY at any time: the LAST frame to arrive wins
 * (the app subscribes, then reads, one decoder for both — calictl R_READ_LAST_FRAME_WINS). After
 * the last read the session prints one SNAP (cali_console_snapshot); once the link's first read-all
 * completed a NOTIFY also prints a SNAP. While the link stays up, water (1302) is re-read every
 * CALI_SESSION_WATER_REREAD_MS after the read-all (its completion stores + SNAPs), so a stale latch
 * served at connect is corrected without a reconnect. The snapshot therefore
 * always holds exactly one whole frame per function (never a mix of two).
 *
 * Link loss (DISCONNECTED, CONNECT_FAIL, ENC_FAIL, a failed discovery, a heartbeat that cannot be
 * written or completes with an error, or no encryption within CALI_SESSION_ENC_TIMEOUT_MS of
 * CONNECTED) -> disconnect() and connect_bonded() after 1 s, doubling to a 60 s cap; the delay
 * resets once a link is encrypted again. Without a stored bond the session goes inactive instead.
 *
 * C99, no malloc, no NimBLE/ESP-IDF includes, no clock of its own.
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

#define CALI_SESSION_BACKOFF_MIN_MS 1000u
#define CALI_SESSION_BACKOFF_MAX_MS 60000u
#define CALI_SESSION_ENC_TIMEOUT_MS 15000u
/* Water (1302) re-read period while the link is up: calictl's POLL_INTERVAL (30 s), at which it
 * reads 1302 on every poll. The app has no periodic water re-read (it reconnects instead). */
#define CALI_SESSION_WATER_REREAD_MS 30000u

void cali_session_init(const cali_transport_t *t);
void cali_session_boot(void);
void cali_session_on_bonded(void);        /* runner -> session: discover, subscribe, start read_all */
void cali_session_stop(void);
void cali_session_tick(uint64_t now_ms);  /* heartbeat every CODEC_HEARTBEAT_PERIOD_MS, reconnect backoff */

/* 1 while the session holds a bond it keeps (or re-establishes) a link for — true through
 * reconnect backoff too, while no link exists. For the actual encrypted BLE link, see
 * cali_session_link_up(). */
int cali_session_active(void);

/* 1 only while the link is up (encrypted, past ENC_OK/discover) — 0 during CONNECTING/CONNECTED
 * (pre-encryption), reconnect backoff, and whenever cali_session_active() is 0. This is what
 * /api/state's device.link.up reports (T_FW_WEB_HANDLERS). */
int cali_session_link_up(void);

/* 1 when a control write may go out: the link is up, its first read-all finished, and it has been
 * up (heartbeat ticking) for CODEC_ARM_DELAY_MS — calictl.device's ARM_DELAY_S, here once per link
 * instead of per write. */
int cali_session_ready(void);

/* The stored frame of CODEC_CHARS[i] (i < CODEC_NCHARS): 1 and *frame, *len set, or 0 when that
 * function has not been read yet (or its read failed and no notification came). */
int cali_session_frame(size_t i, const uint8_t **frame, size_t *len);

/* As cali_session_frame, but only a frame read or pushed on the CURRENT link (0 for one kept from an
 * earlier link): what the control gates run on (cali_control.h), never a previous link's state. */
int cali_session_frame_live(size_t i, const uint8_t **frame, size_t *len);

/* The lighting configuration the unit reported in its own 1502 frames (semantics.lighting_config over
 * every stored frame — a READ or a NOTIFY, never a write of ours), as name/value pairs: live = 1 only
 * what this link's frames reported (the gates and the wake-up builder), live = 0 across links (what
 * SNAP and /api/state show, like the frames themselves). Returns the number of known keys. */
int cali_session_light_cfg(int live, codec_kv_t out[CALI_LCFG_N]);

/* The now_ms (of the latest cali_session_tick) at which the session last stored a frame — a READ or
 * a NOTIFY; 0 = no frame stored since cali_session_init. */
uint64_t cali_session_last_update_ms(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_SESSION_H */
