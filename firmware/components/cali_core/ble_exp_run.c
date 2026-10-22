/* ble_exp_run.c — runs the BLE experiment scheduler (ble_exp.c) on the owner task's tick and acts on
 * its output through the session (R_FW_BLE_EXP), and the idle policy (ble_exp.c cali_bi_*, R_FW_BLE_IDLE)
 * after it. Contract: include/cali_ble_exp.h. Proven by tests/firmware/test_web_handlers.py (/api/ble)
 * and test_session_fake.py (console "ble exp").
 */
#include "cali_auto_camper.h"
#include "cali_ble_exp.h"
#include "cali_diag.h"
#include "cali_json.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_time.h"

static cali_bx_t s_bx;
static cali_bi_t s_bi;   /* the idle policy (R_FW_BLE_IDLE) */
static unsigned s_min;
static uint64_t s_phase_at;   /* now_ms the running phase started (evt ble_phase dur_ms) */
static uint64_t s_tick_at;    /* the last tick's now_ms: the clock of a stop between ticks */
static int s_adv_any;         /* the unit's advert was seen since boot (device.ble.unit_adv_seen_ms_ago) */
static uint64_t s_adv_at;     /* ... at this now_ms */
static int s_adv_mode = -1;   /* the session mode the gone below was armed in */
static int s_adv_armed;       /* a gone was logged in s_adv_mode: a reappearance there is a wake */

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
    s_adv_mode = -1;   /* the advert state starts unknown: no wake armed from an earlier run */
    s_adv_armed = 0;
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

void cali_ble_exp_activity(uint64_t now_ms) {
    cali_bx_activity(&s_bx, now_ms);
    cali_bi_activity(&s_bi);
}

unsigned cali_ble_idle_min(void) { return s_bi.probe_min; }

int cali_ble_idle_set(unsigned min) {
    uint8_t b = (uint8_t)min;
    if (min > CALI_BI_MAX_MIN || cali_kv_set(CALI_BI_KEY, &b, 1) != 0) return -1;
    cali_bi_set(&s_bi, min, cali_uptime_ms());
    cali_log("ble-idle: %s", min ? "probe" : "hold");
    cali_diag_evt("ble_idle", "\"probe_min\":%u", min);
    return 0;
}

/* The unit's advert sighting (1 seen / -1 gone from cali_bx_seen): log it; a reappearance in quiet/off
 * after a gone logged in that same mode WITH THE LINK DOWN = the unit woke (door, ignition) = activity.
 * A gone with the link up is not sleep: the unit is assumed (not yet checked on the van) not to advertise
 * while connected, so that gone is our held link's silence, and the adverts after the link drops (our
 * hold -> off disconnect, the unit dropping a quiet link) are no wake. A mode change disarms. */
static void adv_transition(int t, uint64_t now_ms) {
    int mode = cali_session_mode();
    if (mode != s_adv_mode) {
        s_adv_mode = mode;
        s_adv_armed = 0;
    }
    if (t < 0) {
        s_adv_armed = !cali_session_link_up();
        cali_diag_evt("unit_adv", "\"seen\":false");
    } else if (t > 0) {
        cali_diag_evt("unit_adv", "\"seen\":true");
        if (s_adv_armed && (mode == CALI_BLE_QUIET || mode == CALI_BLE_OFF)) {
            cali_log("ble-exp: the unit's advert is back: wake");
            cali_bx_activity(&s_bx, now_ms);
        }
        s_adv_armed = 0;
    }
}

static void on_unit_adv(uint64_t now_ms) {
    s_adv_any = 1;
    s_adv_at = now_ms;
    adv_transition(cali_bx_seen(&s_bx, now_ms, 1), now_ms);
}

void cali_ble_exp_init(void) {
    uint8_t b;
    size_t n = 1;
    unsigned min = 0;   /* missing = hold */
    if (cali_kv_get(CALI_BI_KEY, &b, &n) == CALI_KV_OK && n == 1 && b <= CALI_BI_MAX_MIN) min = b;
    cali_bi_set(&s_bi, min, cali_uptime_ms());
    if (min) cali_log("ble-idle: probe every %u min", min);   /* hold (the default) logs nothing */
    cali_runner_on_unit_adv = on_unit_adv;
}

static void exp_tick(uint64_t now_ms, int ign);

void cali_ble_exp_tick(uint64_t now_ms) {
    s_tick_at = now_ms;
    /* the van's ignition (terminal 15) on an up link (also a probe's): activity, every tick while on */
    int ign = cali_session_link_up() && cali_ac_ignition() == 1;
    if (ign) cali_bi_activity(&s_bi);
    if (s_bx.on) exp_tick(now_ms, ign);
    cali_bi_out_t io = cali_bi_step(&s_bi, now_ms, cali_session_mode(), cali_session_probing(), s_bx.on);
    if (io.set_mode >= 0) (void)cali_session_set_mode(io.set_mode, "idle");
    if (io.probe && cali_session_active() && cali_session_probe() != 0) {
        cali_log("ble-idle: probe refused");
        cali_diag_evt("probe", "\"ok\":false,\"mode\":\"%s\",\"reason\":\"refused\"",
                      cali_session_mode_name(cali_session_mode()));
    }
}

static void exp_tick(uint64_t now_ms, int ign) {
    int64_t ts;
    int hour = cali_time_ts(&ts) == CALI_TSRC_NONE ? -1 : cali_time_berlin_hour(ts);
    /* the ignition: like a viewer, every tick while it is on, so a night drive never reaches the next
     * phase (off: the unit's advert, adv_transition) */
    if (ign) cali_bx_activity(&s_bx, now_ms);
    adv_transition(cali_bx_seen(&s_bx, now_ms, 0), now_ms);   /* the gone check; off: the advert's wake */
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

/* the "exp" value: null, or {"on","phase_min","from","to","phase","remaining_ms"} */
static void exp_json(cali_json_t *j) {
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

void cali_ble_exp_fields(cali_json_t *j) {
    cali_json_key(j, "mode");
    cali_json_str(j, cali_session_mode_name(cali_session_mode()));
    cali_json_key(j, "probing");
    cali_json_bool(j, cali_session_probing());
    cali_json_key(j, "exp");
    exp_json(j);
    cali_json_key(j, "idle_min");   /* the idle policy: 0 = hold, else a probe every n min (R_FW_BLE_IDLE) */
    cali_json_int(j, s_bi.probe_min);
    cali_json_key(j, "unit_adv_seen_ms_ago");   /* the tick clock: the sighting's stamp is the runner's */
    if (!s_adv_any) cali_json_null(j);
    else cali_json_int(j, (long long)(s_tick_at > s_adv_at ? s_tick_at - s_adv_at : 0));
}

void cali_ble_exp_json(cali_json_t *j) {
    cali_json_obj_begin(j);
    cali_ble_exp_fields(j);
    cali_json_obj_end(j);
}
