/* cali_ble_exp.h — the BLE power experiment's schedule (R_FW_BLE_EXP): inside a nightly window run
 * the session modes hold -> quiet -> off, each for a phase, with a probe at each phase end. Activity
 * (cali_bx_activity: a viewer; the ignition on an up link, ble_exp_run.c) or an outside mode change (a
 * command in quiet/off -> hold, the console, /api/ble; also during the probe) cuts the phase short
 * (aborted), returns to hold and holds CALI_BX_IDLE_MS before the next phase. A wake in off (adverts)
 * is not wired yet. Pure: step() returns what to do, the caller acts (ble_exp_run.c).
 * The cali_bx_* half is C99, no platform, no session calls (the mode numbers are CALI_BLE_* from
 * cali_session.h); the cali_ble_exp_* half below is its glue (ble_exp_run.c).
 */
#ifndef CALI_BLE_EXP_H
#define CALI_BLE_EXP_H

#include <stdint.h>

#include "cali_json.h"

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
/* One tick. hour = Berlin local hour or -1; cur_mode = the session's mode now; probe_busy = a probe runs.
 * Contract: the caller applies set_mode and starts the probe before the next step, so the next call's
 * cur_mode / probe_busy reflect them (a mode it did not set reads as an outside change = abort).
 * ble_exp_run.c meets it: cali_session_set_mode / cali_session_probe change the session synchronously. */
cali_bx_out_t cali_bx_step(cali_bx_t *x, uint64_t now_ms, int hour, int cur_mode, int probe_busy);

/* ---- the run side (ble_exp_run.c, owner task): the one scheduler driving the session ---- */
/* 0 started, -1 no wall clock (cali_time_ts NONE), -2 bad args (phase 1..720 min, hours 0..23). */
int cali_ble_exp_start(unsigned phase_min, int from_h, int to_h);
void cali_ble_exp_stop(void);                 /* no-op when off; else back to hold ("exp_off") */
void cali_ble_exp_activity(uint64_t now_ms);  /* a viewer (GET /api/state): cut the phase, hold 10 min */
void cali_ble_exp_tick(uint64_t now_ms);      /* the owner tick: step + act (set_mode, probe, evts) */
/* device.ble = {"mode","probing","exp"} (a value: the caller writes the key first) */
void cali_ble_exp_json(cali_json_t *j);
/* the "exp" value alone: null, or {"on","phase_min","from","to","phase","remaining_ms"} */
void cali_ble_exp_exp_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_BLE_EXP_H */
