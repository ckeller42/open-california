/* ble_exp.c — the BLE power experiment's schedule: hold -> quiet -> off, a probe at each phase end,
 * interrupted by activity. Pure C99; ble_exp_run.c feeds it and acts on its output.
 *
 * .. req:: BLE experiment scheduler
 *    :id: R_FW_BLE_EXP
 */
#include <string.h>

#include "cali_ble_exp.h"
#include "cali_session.h"

static const int ORDER[3] = {CALI_BLE_HOLD, CALI_BLE_QUIET, CALI_BLE_OFF};

int cali_bx_in_window(int hour, int from_h, int to_h) {
    if (hour < 0) return 0;
    if (from_h == to_h) return 1;
    return from_h < to_h ? hour >= from_h && hour < to_h : hour >= from_h || hour < to_h;
}

void cali_bx_start(cali_bx_t *x, unsigned phase_min, int from_h, int to_h) {
    memset(x, 0, sizeof *x);
    x->on = 1;
    x->want = -1;
    x->from_h = from_h;
    x->to_h = to_h;
    x->phase_ms = (uint64_t)phase_min * 60000u;
}

void cali_bx_stop(cali_bx_t *x) {
    x->on = 0;
    x->want = -1;
    x->probing = 0;
}

void cali_bx_activity(cali_bx_t *x, uint64_t now_ms) {
    if (!x->on) return;
    x->idle_until = now_ms + CALI_BX_IDLE_MS;
    x->touched = 1;
}

cali_bx_out_t cali_bx_step(cali_bx_t *x, uint64_t now, int hour, int cur, int busy) {
    cali_bx_out_t o = {-1, 0, -1, 0, -1};
    if (!x->on) return o;
    int win = cali_bx_in_window(hour, x->from_h, x->to_h);
    int touched = x->touched;
    x->touched = 0;
    if (x->want >= 0 && (touched || !win || (cur != x->want && !x->probing))) { /* phase cut short */
        o.done_mode = x->want;
        o.aborted = 1;
        x->want = -1;
        x->probing = 0;
        x->phase = (x->phase + 1) % 3;
        if (cur != CALI_BLE_HOLD) o.set_mode = CALI_BLE_HOLD;
        return o;
    }
    if (x->want < 0) {
        if (!win || now < x->idle_until) { /* outside the window / after activity */
            if (cur != CALI_BLE_HOLD) o.set_mode = CALI_BLE_HOLD;
            return o;
        }
        x->want = ORDER[x->phase];
        x->phase_end = now + x->phase_ms;
        o.started = x->want;
        if (cur != x->want) o.set_mode = x->want;
        return o;
    }
    if (!x->probing && now >= x->phase_end) {
        x->probing = 1;
        o.probe = 1;
        return o;
    }
    if (x->probing && !busy) {
        o.done_mode = x->want;
        x->want = -1;
        x->probing = 0;
        x->phase = (x->phase + 1) % 3;
    }
    return o;
}
