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
 *
 * Link up (on_bonded, or ENC_OK on a reconnect): heartbeat write_heartbeat(counter++) every
 * CODEC_HEARTBEAT_PERIOD_MS starting at CODEC_HEARTBEAT_START (first beat on the next tick), and
 * discover(). DISCOVERED -> subscribe() every CODEC_CHARS entry (a char without NOTIFY is refused
 * by the transport and skipped), then — like calictl.device.read_all — let the heartbeat run for
 * CODEC_HEARTBEAT_WARMUP_MS (the first cali_session_tick at or past DISCOVERED + warm-up starts the
 * reads) and read() the functions one at a time in CODEC_CHARS order; each READ (status 0)
 * replaces that function's frame. A function the unit pushed on this link (NOTIFY since the
 * subscribe) is not read, and a read already outstanding when its push arrives does not replace
 * the pushed frame: the notification is fresher than the read latch. After the last read the
 * session prints one SNAP (cali_console_snapshot). A NOTIFY replaces that function's frame at any
 * time; once the link's first read-all completed it also prints a SNAP. The snapshot therefore
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

#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_SESSION_BACKOFF_MIN_MS 1000u
#define CALI_SESSION_BACKOFF_MAX_MS 60000u
#define CALI_SESSION_ENC_TIMEOUT_MS 15000u

void cali_session_init(const cali_transport_t *t);
void cali_session_boot(void);
void cali_session_on_bonded(void);        /* runner -> session: discover, subscribe, start read_all */
void cali_session_stop(void);
void cali_session_tick(uint64_t now_ms);  /* heartbeat every CODEC_HEARTBEAT_PERIOD_MS, reconnect backoff */

/* 1 while the session holds a bond it keeps (or re-establishes) a link for. */
int cali_session_active(void);

/* The stored frame of CODEC_CHARS[i] (i < CODEC_NCHARS): 1 and *frame, *len set, or 0 when that
 * function has not been read yet (or its read failed and no notification came). */
int cali_session_frame(size_t i, const uint8_t **frame, size_t *len);

#ifdef __cplusplus
}
#endif

#endif /* CALI_SESSION_H */
