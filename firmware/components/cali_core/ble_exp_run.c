/* ble_exp_run.c — runs the BLE experiment scheduler (ble_exp.c) on the owner task's tick and acts on
 * its output through the session (R_FW_BLE_EXP). Contract: include/cali_ble_exp.h. Proven by
 * tests/firmware/test_web_handlers.py (/api/ble) and test_session_fake.py (console "ble exp").
 */
#include "cali_auto_camper.h"
#include "cali_ble_exp.h"
#include "cali_diag.h"
#include "cali_json.h"
#include "cali_platform.h"
#include "cali_session.h"
#include "cali_time.h"

static cali_bx_t s_bx;
static unsigned s_min;
static uint64_t s_phase_at;   /* now_ms the running phase started (evt ble_phase dur_ms) */
static uint64_t s_tick_at;    /* the last tick's now_ms: the clock of a stop between ticks */

static void phase_evt(int mode, int aborted, uint64_t now_ms) {
    cali_diag_evt("ble_phase", "\"mode\":\"%s\",\"aborted\":%s,\"dur_ms\":%llu", cali_session_mode_name(mode),
                  aborted ? "true" : "false", (unsigned long long)(now_ms - s_phase_at));
}

/* a stop or a restart while a phase runs: log that phase (aborted) first, so the report keeps it */
static void end_running_phase(void) {
    if (s_bx.on && s_bx.want >= 0) phase_evt(s_bx.want, 1, s_tick_at);
}

int cali_ble_exp_start(unsigned phase_min, int from_h, int to_h) {
    int64_t ts;
    if (phase_min < 1 || phase_min > 720 || from_h < 0 || from_h > 23 || to_h < 0 || to_h > 23) return -2;
    if (cali_time_ts(&ts) == CALI_TSRC_NONE) return -1;
    end_running_phase();
    cali_bx_start(&s_bx, phase_min, from_h, to_h);
    s_min = phase_min;
    cali_log("ble-exp: on, %u min phases, %02d-%02d", phase_min, from_h, to_h);
    cali_diag_evt("ble_exp", "\"on\":true,\"phase_min\":%u,\"from\":%d,\"to\":%d", phase_min, from_h, to_h);
    return 0;
}

void cali_ble_exp_stop(void) {
    if (!s_bx.on) return;
    end_running_phase();
    cali_bx_stop(&s_bx);
    cali_log("ble-exp: off");
    (void)cali_session_set_mode(CALI_BLE_HOLD, "exp_off");
    cali_diag_evt("ble_exp", "\"on\":false");
}

void cali_ble_exp_activity(uint64_t now_ms) { cali_bx_activity(&s_bx, now_ms); }

void cali_ble_exp_tick(uint64_t now_ms) {
    int64_t ts;
    s_tick_at = now_ms;
    if (!s_bx.on) return;
    int hour = cali_time_ts(&ts) == CALI_TSRC_NONE ? -1 : cali_time_berlin_hour(ts);
    /* the van's ignition (terminal 15) on an up link: like a viewer, every tick while it is on, so a
     * night drive never reaches the next phase (off: the unit's advert, Task 4) */
    if (cali_session_link_up() && cali_ac_ignition() == 1) cali_bx_activity(&s_bx, now_ms);
    cali_bx_out_t o = cali_bx_step(&s_bx, now_ms, hour, cali_session_mode(), cali_session_probing());
    if (o.done_mode >= 0) phase_evt(o.done_mode, o.aborted, now_ms);
    if (o.started >= 0) s_phase_at = now_ms;
    if (o.set_mode >= 0) (void)cali_session_set_mode(o.set_mode, o.aborted ? "exp_abort" : "exp");
    if (o.probe && cali_session_probe() != 0) {
        cali_log("ble-exp: probe refused");
        cali_diag_evt("probe", "\"ok\":false,\"mode\":\"%s\",\"reason\":\"refused\"",
                      cali_session_mode_name(cali_session_mode()));
    }
}

void cali_ble_exp_exp_json(cali_json_t *j) {
    if (!s_bx.on) {
        cali_json_null(j);
        return;
    }
    uint64_t now = cali_uptime_ms();
    cali_json_obj_begin(j);
    cali_json_key(j, "on");
    cali_json_bool(j, 1);
    cali_json_key(j, "phase_min");
    cali_json_int(j, s_min);
    cali_json_key(j, "from");
    cali_json_int(j, s_bx.from_h);
    cali_json_key(j, "to");
    cali_json_int(j, s_bx.to_h);
    cali_json_key(j, "phase");   /* null between phases (outside the window, after activity, at start) */
    if (s_bx.want < 0) cali_json_null(j); else cali_json_str(j, cali_session_mode_name(s_bx.want));
    cali_json_key(j, "remaining_ms");
    if (s_bx.want < 0) cali_json_null(j);
    else cali_json_int(j, (long long)(s_bx.phase_end > now ? s_bx.phase_end - now : 0));
    cali_json_obj_end(j);
}

void cali_ble_exp_json(cali_json_t *j) {
    cali_json_obj_begin(j);
    cali_json_key(j, "mode");
    cali_json_str(j, cali_session_mode_name(cali_session_mode()));
    cali_json_key(j, "probing");
    cali_json_bool(j, cali_session_probing());
    cali_json_key(j, "exp");
    cali_ble_exp_exp_json(j);
    cali_json_obj_end(j);
}
