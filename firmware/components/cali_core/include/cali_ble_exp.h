/* cali_ble_exp.h — the BLE power experiment's schedule (R_FW_BLE_EXP): inside a nightly window run
 * the session modes hold -> quiet -> off, each for a phase, with a probe at each phase end; any
 * activity (viewer, command, wake) or an outside mode change cuts the phase short and returns to
 * hold for CALI_BX_IDLE_MS. Pure: step() returns what to do, the caller acts (ble_exp_run.c).
 * C99, no platform, no session calls (the mode numbers are CALI_BLE_* from cali_session.h).
 */
#ifndef CALI_BLE_EXP_H
#define CALI_BLE_EXP_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_BX_IDLE_MS 600000u /* after a viewer / command / wake: hold this long, then go on */

typedef struct {
    int on, from_h, to_h;
    uint64_t phase_ms, phase_end, idle_until;
    int phase; /* index into hold, quiet, off */
    int want;  /* the running phase's mode, -1 = none */
    int probing, touched;
} cali_bx_t;

/* -1 = none */
typedef struct {
    int set_mode, probe, done_mode, aborted, started;
} cali_bx_out_t;

/* hour -1 (no time) -> 0; from == to -> all day; from > to wraps midnight. */
int cali_bx_in_window(int hour, int from_h, int to_h);
void cali_bx_start(cali_bx_t *x, unsigned phase_min, int from_h, int to_h);
void cali_bx_stop(cali_bx_t *x);
/* A viewer, command or wake happened: cut the running phase, hold for CALI_BX_IDLE_MS. */
void cali_bx_activity(cali_bx_t *x, uint64_t now_ms);
/* One tick. hour = Berlin local hour or -1; cur_mode = the session's mode now; probe_busy = a probe runs. */
cali_bx_out_t cali_bx_step(cali_bx_t *x, uint64_t now_ms, int hour, int cur_mode, int probe_busy);

#ifdef __cplusplus
}
#endif

#endif /* CALI_BLE_EXP_H */
