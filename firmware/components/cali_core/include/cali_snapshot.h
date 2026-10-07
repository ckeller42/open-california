/* cali_snapshot.h — the status pieces the console (SNAP/STATE lines) and the web page (/api/state)
 * both report, emitted in one place so the two can never disagree (#154).
 *
 * C99, no malloc, no platform deps beyond cali_core's own session/runner.
 */
#ifndef CALI_SNAPSHOT_H
#define CALI_SNAPSHOT_H

#include "cali_json.h"
#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Writes the MEMBERS of the "fn" object — one "<function>":{"<Field>":<int>,...} per CODEC_CHARS
 * function the session holds a frame for (cali_session_frame), in CODEC_CHARS order, decoded with
 * codec_decode; the lighting object also carries the session's config latch (cali_session_light_cfg(0))
 * after the frame's fields — calictl's serve._last['lighting'] shape. The caller writes the key and the braces:
 *     cali_json_key(j, "fn"); cali_json_obj_begin(j); cali_snapshot_fn(j); cali_json_obj_end(j);
 * The frames are read synchronously in one call, so the output is one consistent snapshot. */
void cali_snapshot_fn(cali_json_t *j);

/* The pairing address calictl's /api/pairing reports: the transport's identity() while the pairing
 * SM is bonded, or idle with a stored bond (has_bond()); NULL otherwise (or when t is NULL). */
const char *cali_snapshot_pair_address(const cali_transport_t *t);

#ifdef __cplusplus
}
#endif

#endif /* CALI_SNAPSHOT_H */
