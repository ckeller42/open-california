/**
 * @file cali_runner.h
 * @brief The pairing flow: feeds transport events into the pairing SM (cali_pairing_sm.h) and
 *        executes the actions it returns through the transport (cali_transport.h).
 *
 * Event to SM mapping (docs/business-logic/guided-pairing.md, "ESP mapping"), applied only while a
 * pairing flow is active (scanning, connecting, waiting_passkey, pairing, verifying, resetting):
 * - FOUND gives EV_DEVICE_FOUND, CONNECTED gives EV_CONNECTED, CONNECT_FAIL gives EV_CONNECT_FAIL.
 * - PASSKEY_REQ gives EV_PASSKEY_REQUESTED, ENC_OK gives EV_PAIR_OK, ENC_FAIL gives EV_PAIR_FAIL.
 * - A READ of CODEC_CHAR_AUTH (1004) in verifying gives EV_VERIFY_OK (status 0) or EV_VERIFY_FAIL.
 * - DISCONNECTED gives EV_CONNECT_FAIL (connecting), EV_PAIR_FAIL (pairing: NimBLE refusing SMP
 *   itself emits no ENC_CHANGE, only a drop, or nothing, and then PAIR_TIMEOUT_S[pairing] ends it)
 *   or EV_VERIFY_FAIL (verifying).
 * - In waiting_passkey the SM has no EV_PAIR_FAIL transition (Python parity), so ENC_FAIL and
 *   DISCONNECTED there are consumed and the passkey timeout ends the flow.
 *
 * ADV (a sensor window's advert) goes to cali_sensors_on_adv at the latest tick's now_ms; UNIT_ADV
 * (the bonded unit's own advert) goes to cali_runner_on_unit_adv, likewise. Every other event
 * (NOTIFY, DISCOVERED, other READs, anything outside a flow) goes to cali_runner_on_other (the
 * session).
 *
 * Action to transport: START_SCAN gives start_scan(CODEC_DEVICE_NAME), STOP_SCAN gives stop_scan,
 * CONNECT gives connect_found, PAIR gives pair, SEND_PASSKEY gives inject_passkey, VERIFY gives
 * read(CODEC_CHAR_AUTH), DISCONNECT gives disconnect, REMOVE_BOND gives remove_bond (then
 * EV_RESET_DONE when it returned 0), PERSIST_BOND gives keep_only_bond (NimBLE already persisted
 * the bond through the store callbacks; the ESP serves one unit, so a verified pairing drops every
 * other bond). The bonded STATE carries address = identity(). A call that cannot start is fed back
 * as the matching failure event (connect gives EV_CONNECT_FAIL, pair or passkey gives
 * EV_PAIR_FAIL, verify gives EV_VERIFY_FAIL); a scan that cannot start is ended by the scanning
 * timeout.
 *
 * Time: cali_runner_tick() drives EV_TIMEOUT from PAIR_TIMEOUT_S (csrc/pairing_consts.h). A state
 * is entered at the now_ms of the latest tick, so the timeout fires within one tick period.
 *
 * C99, no malloc, no NimBLE or ESP-IDF includes, no clock of its own. Single-threaded: call
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

/**
 * @brief Register the runner's sink on the transport; the state is idle.
 * @param t The BLE transport; it must outlive the runner.
 */
void cali_runner_init(const cali_transport_t *t);

/** @brief Start a pairing flow (console "pair"). */
void cali_runner_start(void);

/**
 * @brief Give the passkey the unit shows (console "passkey N").
 *
 * Ignored unless the flow is in waiting_passkey.
 *
 * @param pk The six-digit passkey.
 */
void cali_runner_passkey(uint32_t pk);

/** @brief Forget the bond (console "forget"): EV_RESET. */
void cali_runner_forget(void);

/** @brief Cancel the flow (POST /api/pairing "cancel"): EV_CANCEL. */
void cali_runner_cancel(void);

/**
 * @brief Advance the runner's clock and fire EV_TIMEOUT from PAIR_TIMEOUT_S.
 * @param now_ms Uptime clock in ms, from the caller.
 */
void cali_runner_tick(uint64_t now_ms);

/** @brief The pairing SM state. @return A pointer to the live state; never NULL. */
const cali_pair_state_t *cali_runner_state(void);

/**
 * @brief Whether a pairing flow runs.
 *
 * WiFi scans and sensor windows wait while it does.
 *
 * @return 1 while a flow runs (state not idle, bonded or error), else 0.
 */
int cali_runner_in_flow(void);

/**
 * Called on every SM state change (state, attempts or error), after that step's actions ran. The
 * address is the transport's identity() in bonded, else NULL. NULL hook = silent. The console's
 * STATE writer is hooked here.
 */
extern void (*cali_runner_on_state)(const cali_pair_state_t *s, const char *address);

/**
 * Receives every transport event the runner does not consume (see the mapping above), with the
 * event's data still valid. NULL hook = dropped.
 */
extern void (*cali_runner_on_other)(const cali_tevent_t *e);

/**
 * Receives CALI_TEV_UNIT_ADV (the bonded unit's own advert) at the latest tick's now_ms; never the
 * SM, the session or the sensors. NULL hook = dropped. cali_ble_exp_init() sets it.
 */
extern void (*cali_runner_on_unit_adv)(uint64_t now_ms);

#ifdef __cplusplus
}
#endif

#endif /* CALI_RUNNER_H */
