/* pairing_sm.c — line-for-line transcription of calictl/pairing.py's step() (Python lines 49-92).
 * Keep both in lock-step; the parity test (tests/firmware/test_pairing_sm_parity.py) replays the
 * same golden vectors (tests/vectors/pairing.json) through this and the Python original.
 */
#include "cali_pairing_sm.h"
#include "pairing_consts.h"

static int emit(cali_pair_action_t *o, int n, uint8_t act, uint32_t arg) {
    o[n].act = act;
    o[n].arg = arg;
    return n + 1;
}

static int cleanup(uint8_t st, cali_pair_action_t *o) {
    if (st == PAIR_SCANNING) return emit(o, 0, PAIR_ACT_STOP_SCAN, 0);
    if (st == PAIR_CONNECTING || st == PAIR_WAITING_PASSKEY || st == PAIR_PAIRING || st == PAIR_VERIFYING)
        return emit(o, 0, PAIR_ACT_DISCONNECT, 0);
    return 0;
}

static void set(cali_pair_state_t *ps, uint8_t st, uint8_t att, uint8_t err) {
    ps->st = st;
    ps->attempts = att;
    ps->error = err;
}

int cali_pair_step(cali_pair_state_t *ps, uint8_t ev, uint32_t arg, cali_pair_action_t o[CALI_PAIR_MAX_ACTIONS]) {
    uint8_t st = ps->st;
    if (ev == PAIR_EV_CANCEL) { int n = cleanup(st, o); set(ps, PAIR_IDLE, 0, PAIR_ERR_NONE); return n; }
    if (ev == PAIR_EV_TIMEOUT && PAIR_TIMEOUT_S[st]) { int n = cleanup(st, o); set(ps, PAIR_ERROR, ps->attempts, PAIR_ERR_TIMEOUT); return n; }
    if (ev == PAIR_EV_RESET && (st == PAIR_BONDED || st == PAIR_ERROR || st == PAIR_IDLE)) { set(ps, PAIR_RESETTING, 0, PAIR_ERR_NONE); return emit(o, 0, PAIR_ACT_REMOVE_BOND, 0); }
    if ((st == PAIR_IDLE || st == PAIR_ERROR) && ev == PAIR_EV_START) { set(ps, PAIR_SCANNING, 0, PAIR_ERR_NONE); return emit(o, 0, PAIR_ACT_START_SCAN, 0); }
    if (st == PAIR_SCANNING && ev == PAIR_EV_DEVICE_FOUND) { set(ps, PAIR_CONNECTING, ps->attempts, PAIR_ERR_NONE); return emit(o, emit(o, 0, PAIR_ACT_STOP_SCAN, 0), PAIR_ACT_CONNECT, 0); }
    if (st == PAIR_CONNECTING && ev == PAIR_EV_CONNECTED) { set(ps, PAIR_PAIRING, ps->attempts, PAIR_ERR_NONE); return emit(o, 0, PAIR_ACT_PAIR, 0); }
    if (st == PAIR_PAIRING && ev == PAIR_EV_PASSKEY_REQUESTED) { set(ps, PAIR_WAITING_PASSKEY, ps->attempts, PAIR_ERR_NONE); return 0; }
    if (st == PAIR_WAITING_PASSKEY && ev == PAIR_EV_PASSKEY_ENTERED) { set(ps, PAIR_PAIRING, ps->attempts, PAIR_ERR_NONE); return emit(o, 0, PAIR_ACT_SEND_PASSKEY, arg); }
    if (st == PAIR_PAIRING && ev == PAIR_EV_PAIR_OK) { set(ps, PAIR_VERIFYING, ps->attempts, PAIR_ERR_NONE); return emit(o, 0, PAIR_ACT_VERIFY, 0); }
    if ((st == PAIR_PAIRING && ev == PAIR_EV_PAIR_FAIL) || (st == PAIR_CONNECTING && ev == PAIR_EV_CONNECT_FAIL)) {
        uint8_t att = ps->attempts + 1, err = ev == PAIR_EV_PAIR_FAIL ? PAIR_ERR_PAIR : PAIR_ERR_CONNECT;
        if (att < PAIR_MAX_ATTEMPTS) { set(ps, PAIR_SCANNING, att, PAIR_ERR_NONE); return emit(o, emit(o, 0, PAIR_ACT_DISCONNECT, 0), PAIR_ACT_START_SCAN, 0); }
        set(ps, PAIR_ERROR, att, err); return emit(o, 0, PAIR_ACT_DISCONNECT, 0);
    }
    if (st == PAIR_VERIFYING && ev == PAIR_EV_VERIFY_OK) { set(ps, PAIR_BONDED, ps->attempts, PAIR_ERR_NONE); return emit(o, 0, PAIR_ACT_PERSIST_BOND, 0); }
    if (st == PAIR_VERIFYING && ev == PAIR_EV_VERIFY_FAIL) { set(ps, PAIR_ERROR, ps->attempts, PAIR_ERR_VERIFY); return emit(o, 0, PAIR_ACT_DISCONNECT, 0); }
    if (st == PAIR_RESETTING && ev == PAIR_EV_RESET_DONE) { set(ps, PAIR_IDLE, 0, PAIR_ERR_NONE); return 0; }
    return 0;
}
