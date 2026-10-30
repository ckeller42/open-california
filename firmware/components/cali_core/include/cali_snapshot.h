/**
 * @file cali_snapshot.h
 * @brief Emitter for the status members of the console SNAP line and /api/state.
 *
 * The status pieces the console (SNAP/STATE lines) and the web page (/api/state) both report,
 * emitted in one place so the two can never disagree. C99, no malloc, no platform deps beyond
 * cali_core's own session/runner.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: Snapshot emitter
 *    :id: R_FW_SNAPSHOT
 *    :status: implemented
 *    :tags: esp32
 *
 *    The satellite emits the session's last frame per function, decoded, as the ``fn`` members
 *    of ``/api/state`` and of the console SNAP line, from one function, so the two never disagree.
 * @endverbatim
 */
#ifndef CALI_SNAPSHOT_H
#define CALI_SNAPSHOT_H

#include "cali_json.h"
#include "cali_pairing_sm.h"
#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Write the members of the "fn" object: one decoded frame per function.
 *
 * Writes one `"function":{"Field":int,...}` member per CODEC_CHARS function the session
 * holds a frame for (cali_session_frame), in CODEC_CHARS order, decoded with codec_decode. The
 * lighting object also carries the session's config latch (cali_session_light_cfg(0)) after the
 * frame's fields: calictl's serve._last['lighting'] shape. The caller writes the key and the
 * braces:
 * @code
 * cali_json_key(j, "fn"); cali_json_obj_begin(j); cali_snapshot_fn(j); cali_json_obj_end(j);
 * @endcode
 *
 * The frames are read synchronously in one call, so the output is one consistent snapshot.
 *
 * @param j JSON writer, positioned inside the "fn" object.
 */
void cali_snapshot_fn(cali_json_t *j);

/**
 * @brief The pairing address calictl's /api/pairing reports.
 *
 * This is the transport's identity() while the pairing SM is bonded, or idle/error with a stored
 * bond (has_bond(): a failed re-pair keeps the old bond until the transport's pair() drops it).
 *
 * @param t Transport to ask; may be NULL.
 * @return The address string, or NULL when there is none (or when t is NULL).
 */
const char *cali_snapshot_pair_address(const cali_transport_t *t);

/**
 * @brief Write the pairing snapshot members "state", "attempts", "error", "address".
 *
 * Mirrors calictl's pairing snapshot (calictl/serve.py pairing_snapshot, names from
 * pairing_consts.h): the console's STATE line and GET/POST /api/pairing, so the two can never
 * disagree.
 *
 * @param j JSON writer, positioned inside the snapshot object.
 * @param s Pairing state machine state to report.
 * @param address Pairing address (see cali_snapshot_pair_address); NULL is written as JSON null.
 */
void cali_snapshot_pairing(cali_json_t *j, const cali_pair_state_t *s, const char *address);

#ifdef __cplusplus
}
#endif

#endif /* CALI_SNAPSHOT_H */
