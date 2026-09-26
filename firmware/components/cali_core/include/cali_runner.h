/* cali_runner.h — the pairing flow: feeds transport events into the pairing SM (cali_pairing_sm.h)
 * and executes the actions it returns through the transport (cali_transport.h) (#154).
 *
 * Event -> SM mapping (docs/business-logic/guided-pairing.md "ESP mapping"), applied only while a
 * pairing flow is active (scanning, connecting, waiting_passkey, pairing, verifying, resetting):
 *   FOUND -> EV_DEVICE_FOUND, CONNECTED -> EV_CONNECTED, CONNECT_FAIL -> EV_CONNECT_FAIL,
 *   PASSKEY_REQ -> EV_PASSKEY_REQUESTED, ENC_OK -> EV_PAIR_OK, ENC_FAIL -> EV_PAIR_FAIL,
 *   READ of CODEC_CHAR_AUTH (1004) in verifying -> EV_VERIFY_OK (status 0) / EV_VERIFY_FAIL,
 *   DISCONNECTED -> EV_CONNECT_FAIL (connecting), EV_PAIR_FAIL (pairing: NimBLE refusing SMP
 *   itself emits no ENC_CHANGE, only a drop, or nothing — then PAIR_TIMEOUT_S[pairing] ends it),
 *   EV_VERIFY_FAIL (verifying). In waiting_passkey the SM has no EV_PAIR_FAIL transition (Python
 *   parity), so ENC_FAIL/DISCONNECTED there are consumed and the passkey timeout ends the flow.
 * Every other event (NOTIFY, DISCOVERED, other READs, anything outside a flow) goes to
 * cali_runner_on_other (the session, Task 6).
 *
 * Action -> transport: START_SCAN start_scan(CODEC_DEVICE_NAME), STOP_SCAN stop_scan, CONNECT
 * connect_found, PAIR pair, SEND_PASSKEY inject_passkey, VERIFY read(CODEC_CHAR_AUTH), DISCONNECT
 * disconnect, REMOVE_BOND remove_bond (then EV_RESET_DONE when it returned 0), PERSIST_BOND nothing
 * (NimBLE already persisted the bond through the store callbacks) — the bonded STATE carries
 * address = identity(). A call that cannot start is fed back as the matching failure event
 * (connect -> EV_CONNECT_FAIL, pair/passkey -> EV_PAIR_FAIL, verify -> EV_VERIFY_FAIL); a scan
 * that cannot start is ended by the scanning timeout.
 *
 * Time: cali_runner_tick(now_ms) drives EV_TIMEOUT from PAIR_TIMEOUT_S (csrc/pairing_consts.h). A
 * state is entered at the now_ms of the latest tick, so the timeout fires within one tick period.
 *
 * C99, no malloc, no NimBLE/ESP-IDF includes, no clock of its own. Single-threaded: call
 * everything on the task that delivers the transport events.
 */
#ifndef CALI_RUNNER_H
#define CALI_RUNNER_H

#include <stdint.h>

#include "cali_pairing_sm.h"
#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

void cali_runner_init(const cali_transport_t *t);  /* registers the runner's sink; state idle */
void cali_runner_start(void);                      /* console "pair" */
void cali_runner_passkey(uint32_t pk);             /* console "passkey N" — ignored unless waiting_passkey */
void cali_runner_forget(void);                     /* console "forget" -> EV_RESET */
void cali_runner_tick(uint64_t now_ms);            /* drives EV_TIMEOUT from PAIR_TIMEOUT_S */
const cali_pair_state_t *cali_runner_state(void);

/* Called on every SM state change (state, attempts or error), after that step's actions ran.
 * address = the transport's identity() in bonded, else NULL. NULL hook = silent. Task 6 points it
 * at the console's STATE writer. */
extern void (*cali_runner_on_state)(const cali_pair_state_t *s, const char *address);

/* Receives every transport event the runner does not consume (see the mapping above), with the
 * event's data still valid. NULL hook = dropped. */
extern void (*cali_runner_on_other)(const cali_tevent_t *e);

#ifdef __cplusplus
}
#endif

#endif /* CALI_RUNNER_H */
