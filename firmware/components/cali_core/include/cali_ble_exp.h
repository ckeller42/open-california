/* cali_ble_exp.h — the BLE power experiment's schedule (R_FW_BLE_EXP): inside a nightly window run
 * the session modes hold -> quiet -> off, each for a phase, with a probe at each phase end. Activity
 * (cali_bx_activity: a viewer; the ignition on an up link, ble_exp_run.c) or an outside mode change (a
 * command in quiet/off -> hold, the console, /api/ble; also during the probe) cuts the phase short
 * (aborted), returns to hold and holds CALI_BX_IDLE_MS before the next phase. The unit's own advert
 * (cali_bx_seen, the sleep indicator): gone after CALI_BX_GONE_MS without a sighting; a reappearance in
 * quiet/off after a gone logged in that mode with the link down is a wake = activity (ble_exp_run.c).
 * Pure: step() returns what to do, the caller acts (ble_exp_run.c).
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
/* the unit's advert is gone after this long without a sighting: 3 x SENS_PERIOD_MS + SENS_WINDOW_MS
 * (sensors.c, 60 s + 5 s — the unit is only seen in a sensor scan window, so the third missed window has
 * ended before it counts; test_ble_exp.py checks the sum) */
#define CALI_BX_GONE_MS 185000u

typedef struct {
    int on, from_h, to_h;
    uint64_t phase_ms, phase_end, idle_until;
    int phase; /* index into hold, quiet, off */
    int want;  /* the running phase's mode, -1 = none */
    int probing, touched;
    int adv;          /* the unit's advert: 0 unknown (since start), 1 seen, -1 gone */
    int adv_clock;    /* adv_ms is set (the first check while unknown, else the last sighting) */
    uint64_t adv_ms;
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
/* The unit's advert: seen_now 1 = a sighting, 0 = a check (every tick). Returns 1 on a transition to
 * seen (from unknown: the first sighting, or from gone), -1 on a transition to gone (no sighting for
 * CALI_BX_GONE_MS since the last one, or since the first check while unknown), else 0; 0 while off. */
int cali_bx_seen(cali_bx_t *x, uint64_t now_ms, int seen_now);

/* ---- the idle policy (R_FW_BLE_IDLE): probe_min 0 = hold (the link always up); 1..CALI_BI_MAX_MIN =
 * hold while active (a viewer, a command, the ignition: CALI_BX_IDLE_MS after the last), else off with a
 * probe every probe_min. A running experiment owns the session (exp_on): the policy then waits and holds
 * CALI_BX_IDLE_MS after it. A mode it did not set (a command -> hold, the console, /api/ble) = activity. */
#define CALI_BI_MAX_MIN 60u
typedef struct {
    unsigned probe_min;
    uint64_t idle_until, next_probe;
    int want;   /* the mode the policy last chose, -1 = none (after a set / an experiment) */
    int touched;
} cali_bi_t;

typedef struct {
    int set_mode, probe; /* -1 / 0 = nothing */
} cali_bi_out_t;

/* Set the interval (0 = hold); the policy then starts active (holds CALI_BX_IDLE_MS). */
void cali_bi_set(cali_bi_t *x, unsigned probe_min, uint64_t now_ms);
void cali_bi_activity(cali_bi_t *x);
/* One tick; same contract as cali_bx_step (the caller applies set_mode / starts the probe). */
cali_bi_out_t cali_bi_step(cali_bi_t *x, uint64_t now_ms, int cur_mode, int probe_busy, int exp_on);

/* ---- the run side (ble_exp_run.c, owner task): the one scheduler driving the session ---- */
void cali_ble_exp_init(void);   /* owner task, next to cali_pw_init: sets cali_runner_on_unit_adv */
/* 0 started, -1 no wall clock (cali_time_ts NONE), -2 bad args (phase 1..720 min, hours 0..23). */
int cali_ble_exp_start(unsigned phase_min, int from_h, int to_h);
void cali_ble_exp_stop(void);                 /* no-op when off; else back to hold ("exp_off") */
void cali_ble_exp_activity(uint64_t now_ms);  /* a viewer (GET /, /app, /api/state): cut the phase, hold 10 min */
#define CALI_BI_KEY "ble_idle"   /* kv, 1 byte: the idle policy's probe_min 0..CALI_BI_MAX_MIN; missing = 0 (hold) */
/* Store + apply the idle policy (0 = hold); 0 ok, -1 out of range or the store failed (nothing changed). */
int cali_ble_idle_set(unsigned min);
#define CALI_BR_KEY "ble_rest"   /* kv, 1 byte: the rest poll's minutes 0..240 (R_FW_BLE_REST); missing = 30 */
/* Store + apply the rest poll (0 = none); 0 ok, -1 out of range or the store failed (nothing changed). */
int cali_ble_rest_set(unsigned min);
void cali_ble_exp_tick(uint64_t now_ms);      /* the owner tick: step + act (set_mode, probe, evts) */
/* device.ble = {"mode","probing","exp","idle_min","rest_poll_min","unit_adv_seen_ms_ago"} (a value: the caller writes the key
 * first); the last = ms since the unit's advert was last seen (any time, not only in the experiment;
 * null = never since boot) — the live check of the sighting, no address */
void cali_ble_exp_json(cali_json_t *j, int diag);
/* the same members without the braces: /api/ble's top level */
void cali_ble_exp_fields(cali_json_t *j, int diag);

#ifdef __cplusplus
}
#endif

#endif /* CALI_BLE_EXP_H */
