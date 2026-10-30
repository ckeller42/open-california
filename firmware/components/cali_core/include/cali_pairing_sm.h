/**
 * @file cali_pairing_sm.h
 * @brief C twin of calictl's platform-free pairing state machine.
 *
 * Same contract as the Python original (`calictl/pairing.py`): no strings, no clock, no BLE or
 * addresses in the SM. Time enters only as PAIR_EV_TIMEOUT from a platform timer; bond persistence
 * is the opaque action PAIR_ACT_PERSIST_BOND. The enum values are pinned in `pairing_consts.h`,
 * generated from `calictl/pairing.py` by `tools/gen_c_dict.py`: never renumber them by hand.
 *
 * C99, no malloc, no platform deps: it compiles on a host for the parity test and under ESP-IDF
 * for the satellite.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: C twin of the pairing SM
 *    :id: R_FW_PAIRING_SM
 *
 *    The C port of ``calictl.pairing.step`` for the satellite firmware:
 *    ``firmware/components/cali_core/pairing_sm.c`` uses the same pinned state, event and action
 *    enums as the Python original (``csrc/pairing_consts.h``, generated from
 *    ``calictl/pairing.py`` by ``tools/gen_c_dict.py``), the same transition table, and no
 *    strings, clock or BLE calls in the SM itself. Proven equal to the Python reference by
 *    replaying ``tests/vectors/pairing.json`` through both.
 * @endverbatim
 */
#ifndef CALI_PAIRING_SM_H
#define CALI_PAIRING_SM_H
#include <stdint.h>

/** @brief The pairing SM's state. */
typedef struct {
    uint8_t st;        /**< Current state, a PAIR_* constant. */
    uint8_t attempts;  /**< Pairing attempts made in this flow. */
    uint8_t error;     /**< Error code of the last failure, a PAIR_ERR_* constant; 0 = none. */
} cali_pair_state_t;

/** @brief One action the SM asks its driver to run. */
typedef struct {
    uint8_t act;   /**< A PAIR_ACT_* constant. */
    uint32_t arg;  /**< The action's argument; unused by most actions. */
} cali_pair_action_t;

/** The most actions the transition table emits for one event (e.g. STOP_SCAN + CONNECT). */
#define CALI_PAIR_MAX_ACTIONS 2

/**
 * @brief Advance the SM by one event.
 *
 * @param ps  Current state, updated in place.
 * @param ev  One of the `PAIR_EV_*` constants (`pairing_consts.h`).
 * @param arg Event payload (passkey digits, verify readable-char count); unused by most events.
 * @param out Receives up to `CALI_PAIR_MAX_ACTIONS` actions to run, in order.
 * @return The number of actions written to `out`.
 */
int cali_pair_step(cali_pair_state_t *ps, uint8_t ev, uint32_t arg,
                   cali_pair_action_t out[CALI_PAIR_MAX_ACTIONS]);

#endif /* CALI_PAIRING_SM_H */
