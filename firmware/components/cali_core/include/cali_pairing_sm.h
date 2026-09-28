/* cali_pairing_sm.h — C twin of calictl/pairing.py's platform-free pairing state machine.
 *
 * .. req:: C twin of the pairing SM
 *    :id: R_FW_PAIRING_SM
 *
 *    (sphinx-needs cannot parse this C comment; the actual req/test objects live in the Python
 *    shim docstring of tests/firmware/test_pairing_sm_parity.py, collected via docs/api.rst.
 *    See firmware/README.md's traceability section.)
 *
 * Same contract as the Python original: no strings, no clock, no BLE/addresses in the SM. Time
 * enters only as PAIR_EV_TIMEOUT from a platform timer; bond persistence is the opaque action
 * PAIR_ACT_PERSIST_BOND. Enum values are pinned in csrc/pairing_consts.h (GENERATED from
 * calictl/pairing.py by tools/gen_c_dict.py) — never renumber by hand.
 *
 * C99, no malloc, no platform deps: compiles on a host for the parity test
 * (tests/firmware/test_pairing_sm_parity.py, replaying tests/vectors/pairing.json) and under
 * ESP-IDF for the #154 satellite.
 */
#ifndef CALI_PAIRING_SM_H
#define CALI_PAIRING_SM_H
#include <stdint.h>

typedef struct {
    uint8_t st;
    uint8_t attempts;
    uint8_t error;
} cali_pair_state_t;

typedef struct {
    uint8_t act;
    uint32_t arg;
} cali_pair_action_t;

/* The transition table never emits more than 2 actions for one event (e.g. STOP_SCAN + CONNECT). */
#define CALI_PAIR_MAX_ACTIONS 2

/* Advance the SM by one event.
 *
 * :param ps: current state, updated in place.
 * :param ev: one of the ``PAIR_EV_*`` constants (csrc/pairing_consts.h).
 * :param arg: event payload (passkey digits, verify readable-char count); unused by most events.
 * :param out: receives up to ``CALI_PAIR_MAX_ACTIONS`` actions to run, in order.
 * :returns: the number of actions written to ``out``.
 */
int cali_pair_step(cali_pair_state_t *ps, uint8_t ev, uint32_t arg,
                   cali_pair_action_t out[CALI_PAIR_MAX_ACTIONS]);

#endif /* CALI_PAIRING_SM_H */
