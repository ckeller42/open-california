/* thermostat_run.c — the cabin thermostat on the satellite (R_FW_THERMOSTAT_RUN). Contract: the "Run"
 * part of include/cali_thermostat.h. Proven end to end by tests/firmware/test_thermostat_e2e.py.
 *
 * One cali_thermo_step() per period_ms (and at once after a request, a switch of the feature or a
 * finished command batch), fed from this ESP: the stored 1702 frame + its age, this link's 1202/1602/
 * 1004 frames, the configured Govees by alias. Its commands go out one at a time through the control
 * sequencer (the next from the done callback); the last result goes into the next step's cmd input.
 */
#include "cali_thermostat.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "cali_auto_camper.h"
#include "cali_control.h"
#include "cali_diag.h"
#include "cali_platform.h"
#include "cali_sensors.h"
#include "cali_session.h"
#include "codec.h"
#include "codec_chars.h"

#define SENSOR_TEMP 0   /* cali_sensors_value field: a govee's temp_c */
/* Plausibility of a thermometer reading (controller ruling): the range, and the most change per minute
 * against the last accepted reading of the same alias; anything else reaches the step as invalid. */
#define T_MIN (-4000)
#define T_MAX 6000
#define T_RATE_PER_MIN 300

static cali_thermo_run_cfg_t C = {.s = CALI_THERMO_DEFAULTS, .target = CALI_THERMO_TARGET_DEFAULT};
static cali_thermo_state_t S;
static cali_thermo_out_t s_out;     /* the last step's output (status) */
static const char *s_reason;        /* the last reason that was not NULL */
static int s_on, s_req, s_due;
static uint64_t s_next;
/* the batch going out: s_cmds[s_i] is in flight while s_wait */
static cali_thermo_cmd_t s_cmds[CALI_THERMO_MAX_CMDS];
static int s_n, s_i, s_wait;
static int s_batch_on;              /* the feature switch when the batch started */
/* the last accepted reading per alias (ref, backup, out): the rate check's baseline */
static struct {
    char alias[CALI_THERMO_ALIAS_LEN];
    int32_t v;
    uint64_t at;
} s_last[3];
/* the batch's last attempted command and its result: the next step's cmd input (once) */
static const char *s_cmd_what, *s_cmd_res;

static int fn_index(const char *fn) {
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        if (strcmp(CODEC_CHARS[i].function, fn) == 0) return (int)i;
    return -1;
}

/* fields of fn's frame (live = this link's only): the count, 0 when none */
static int frame_fields(const char *fn, int live, codec_kv_t kv[CODEC_KV_MAX]) {
    const uint8_t *frame;
    size_t len;
    int i = fn_index(fn);
    if (i < 0 || !(live ? cali_session_frame_live((size_t)i, &frame, &len) : cali_session_frame((size_t)i, &frame, &len)))
        return 0;
    return codec_decode(codec_func_by_name(fn), frame, len, kv);
}

static int32_t field(const codec_kv_t *kv, int n, const char *name) {
    for (int k = 0; k < n; k++)
        if (strcmp(kv[k].name, name) == 0) return (int32_t)kv[k].value;
    return CALI_THERMO_NONE;
}

/* 1 when v (read at `at`) is in range and, against slot k's last accepted reading of this alias, changes
 * at most T_RATE_PER_MIN per minute; an accepted reading becomes the baseline. The first has no rate check. */
static int plausible(int k, const char *alias, int32_t v, uint64_t at) {
    if (v < T_MIN || v > T_MAX) return 0;
    if (strcmp(s_last[k].alias, alias) == 0 && at >= s_last[k].at) {
        int64_t dv = (int64_t)v - s_last[k].v;
        if (dv < 0) dv = -dv;
        if (dv * 60000 > (int64_t)T_RATE_PER_MIN * (int64_t)(at - s_last[k].at)) return 0;
    }
    snprintf(s_last[k].alias, sizeof s_last[k].alias, "%s", alias);
    s_last[k].v = v;
    s_last[k].at = at;
    return 1;
}

static void sensor(cali_thermo_sensor_t *s, int k, const char *alias, uint64_t now) {
    uint64_t at;
    memset(s, 0, sizeof *s);
    if (!alias[0]) return;   /* none selected */
    s->present = 1;          /* selected: no reading (removed, renamed, not yet seen) is an invalid one */
    snprintf(s->alias, sizeof s->alias, "%s", alias);
    if (cali_sensors_value(alias, SENSOR_TEMP, &s->value, &at) == 0 && plausible(k, alias, s->value, at)) {
        s->age_ms = now >= at ? (int64_t)(now - at) : 0;
    } else {
        s->value = CALI_THERMO_NONE;
        s->age_ms = CALI_THERMO_NONE_MS;
    }
}

static const char *result_name(int rc) {
    switch (rc) {
    case CALI_CTL_OK: return "OK";
    case CALI_CTL_BUSY: return "BUSY";
    case CALI_CTL_NOT_READY: return "NOT_READY";
    case CALI_CTL_FAILED: return "FAILED";
    case CALI_CTL_TIMEOUT: return "TIMEOUT";
    case CALI_CTL_UNCONFIRMED: return "UNCONFIRMED";
    default: return "REFUSED";   /* REFUSED, and ELSEWHERE/BAD_VALUE/NONE: never sent, never retried */
    }
}

static void submit_next(void);

static void cmd_done(int rc, const char *reason) {
    const cali_thermo_cmd_t *c = &s_cmds[s_i];
    (void)reason;
    s_wait = 0;
    s_cmd_what = c->what;
    s_cmd_res = result_name(rc);
    cali_diag_evt("thermo", "\"e\":\"cmd\",\"what\":\"%s\",\"v\":%ld,\"res\":\"%s\"", c->what, (long)c->value, s_cmd_res);
    /* switched off or an OFF request since the batch started: the rest stays home, the next step stops */
    if (rc == CALI_CTL_OK && ++s_i < s_n && !(s_batch_on && !s_on) && s_req != CALI_THERMO_REQ_OFF) {
        submit_next();
        return;
    }
    s_n = s_i = 0;   /* done, or stopped at the first result that is not OK */
    /* a real outcome steps at once; an immediate BUSY/NOT_READY waits for the period (no tick-rate resend) */
    if (rc != CALI_CTL_BUSY && rc != CALI_CTL_NOT_READY) s_due = 1;
}

static void submit_next(void) {
    const cali_thermo_cmd_t *c = &s_cmds[s_i];
    const char *reason = NULL;
    char v[12];
    if (strcmp(c->what, "power") == 0 || strcmp(c->what, "permanent") == 0) snprintf(v, sizeof v, "%s", c->value ? "on" : "off");
    else snprintf(v, sizeof v, "%ld", (long)c->value);
    s_wait = 1;
    int rc = cali_ctl_submit("airheater", c->what, v, -1, cmd_done, &reason, "thermostat");
    if (rc != CALI_CTL_PENDING) cmd_done(rc, reason);
}

static const char *num(char b[16], int32_t v) {
    if (v == CALI_THERMO_NONE) return "null";
    snprintf(b, 16, "%ld", (long)v);
    return b;
}

static void step(uint64_t now) {
    static codec_kv_t h[CODEC_KV_MAX], c[CODEC_KV_MAX], e[CODEC_KV_MAX];   /* static: the 8 KB host-task stack */
    static cali_thermo_heater_t ht;
    int nh = frame_fields("airheater", 0, h), nc = frame_fields("campingmode", 1, c), ne = frame_fields("energy", 1, e);
    int mode_before = S.mode;
    int32_t shore = field(e, ne, "StateLandAfs");
    cali_thermo_in_t in;
    memset(&in, 0, sizeof in);
    in.now_ms = (int64_t)now;
    in.enabled = s_on;
    in.request = s_req;
    in.target = C.target;
    in.runtime_min = C.s.runtime_min;
    sensor(&in.ref, 0, C.ref, now);
    sensor(&in.backup, 1, C.backup, now);
    sensor(&in.out, 2, C.out, now);
    in.outside_on = C.outside_on;
    in.heater = NULL;   /* no frame, or one with no fields: no heater (a present all-NONE one would read OFF) */
    in.heater_age_ms = CALI_THERMO_NONE_MS;
    if (nh > 0) {
        uint64_t at = cali_session_frame_ms((size_t)fn_index("airheater"));
        ht.installed = field(h, nh, "Installed");
        ht.level = field(h, nh, "HeatingLevel");
        ht.error_code = field(h, nh, "ErrorCode");
        ht.fault_trigger = field(h, nh, "FaultTriggerBit");
        ht.permanent = field(h, nh, "PermanentOperation");
        ht.normal = field(h, nh, "NormalOperation");
        ht.op_mode = field(h, nh, "OperationModeAirHeater");
        in.heater = &ht;
        in.heater_age_ms = now >= at ? (int64_t)(now - at) : 0;
    }
    in.link_ready = cali_session_ready();
    in.camping_on = field(c, nc, "State") == 1;
    in.shore_on = shore == 1 || shore == 2;
    in.ignition_on = cali_ac_ignition();
    in.batt2_dv = field(e, ne, "UTwoBattBemAfs");
    in.cmd_what = s_cmd_what;
    in.cmd_result = s_cmd_res;
    s_req = CALI_THERMO_REQ_NONE;   /* one-shot, as the cmd input */
    s_cmd_what = s_cmd_res = NULL;

    cali_thermo_step(&in, &C.s, &S, &s_out);

    if (s_out.reason && (!s_reason || strcmp(s_out.reason, s_reason) != 0 || S.mode != mode_before))
        cali_log("thermostat: %s (mode %s, heater %s)", s_out.reason, cali_thermo_mode_names[S.mode],
                 cali_thermo_heater_names[s_out.status.heater]);
    if (s_out.reason) s_reason = s_out.reason;
    int busy = S.mode != CALI_THERMO_OFF || S.has_pending;   /* a mode, or a stop still pending in OFF */
    if (s_on || mode_before != CALI_THERMO_OFF || busy) {   /* a disabled idle one logs nothing */
        char b[5][16], r[40] = "null";
        int32_t want = S.has_pending && strcmp(S.pending.what, "level") == 0 ? S.pending.value
                       : S.has_expect                                       ? S.expect.level
                                                                            : CALI_THERMO_NONE;
        if (s_out.reason) snprintf(r, sizeof r, "\"%s\"", s_out.reason);
        (void)cali_diag_rec("thermo",
                            "\"r\":%s,\"m\":\"%s\",\"h\":\"%s\",\"t\":%s,\"o\":%s,\"g\":%ld,\"u\":%ld,\"i\":%ld,"
                            "\"uo\":%ld,\"l\":%s,\"lc\":%s,\"rem\":%s",
                            r,
                            cali_thermo_mode_names[S.mode], cali_thermo_heater_names[s_out.status.heater],
                            num(b[0], s_out.status.t_ref), num(b[1], s_out.status.t_out), (long)C.target,
                            (long)S.ctl.u, (long)S.ctl.i, (long)S.ctl.u_out, num(b[2], want),
                            num(b[3], s_out.status.level), num(b[4], field(h, nh, "RunningTimeinAction")));
        if (S.mode != mode_before)
            cali_diag_evt("thermo", "\"e\":\"mode\",\"from\":\"%s\",\"to\":\"%s\",\"r\":\"%s\"",
                          cali_thermo_mode_names[mode_before], cali_thermo_mode_names[S.mode],
                          s_out.reason ? s_out.reason : "");
    }
    cali_session_hold(busy);
    memcpy(s_cmds, s_out.cmds, sizeof s_cmds);
    s_n = s_out.n;
    s_i = 0;
    s_batch_on = s_on;
}

void cali_thermo_run_init(void) {
    uint8_t b;
    size_t n = 1;
    s_on = cali_kv_get(CALI_THERMO_ON_KEY, &b, &n) == CALI_KV_OK && n == 1 && b != 0;   /* missing = off */
    cali_thermo_init(&S);
    memset(&s_out, 0, sizeof s_out);
    s_out.status.heater = CALI_THERMO_H_UNKNOWN;
    s_out.status.t_ref = s_out.status.t_out = s_out.status.level = CALI_THERMO_NONE;
    s_out.status.end = "none";
    cali_log("thermostat: %s", s_on ? "enabled" : "disabled");
}

void cali_thermo_run_tick(uint64_t now_ms) {
    if (s_wait || s_n) return;   /* a batch is going out: its done callback sends the next */
    if (!s_due && now_ms < s_next) return;
    s_due = 0;
    s_next = now_ms + (uint64_t)C.s.period_ms;
    step(now_ms);
    if (s_n) submit_next();
}

int cali_thermo_request(const char *mode) {
    if (strcmp(mode, "AUTO") == 0) s_req = CALI_THERMO_REQ_AUTO;
    else if (strcmp(mode, "OFF") == 0) s_req = CALI_THERMO_REQ_OFF;
    else return -1;
    s_due = 1;
    return 0;
}

int cali_thermo_set_enabled(int on) {
    uint8_t b = (uint8_t)(on != 0);
    s_on = b;
    s_due = 1;   /* off while active: the next step stops it */
    cali_log("thermostat: %s", s_on ? "ENABLED" : "disabled");
    return cali_kv_set(CALI_THERMO_ON_KEY, &b, 1) == 0 ? 0 : -1;
}

cali_thermo_run_cfg_t *cali_thermo_run_cfg(void) { return &C; }

int cali_thermo_cfg_set(const char *key, int32_t value) {
#define K(m) {#m, offsetof(cali_thermo_cfg_t, m)}
    static const struct { const char *name; size_t off; } KEYS[] = {
        K(period_ms), K(kp), K(ki), K(ka), K(u_out_max), K(hyst), K(min_dwell_ms), K(band_hi), K(hot_hold_ms),
        K(overheat), K(min_level), K(stale_after_ms), K(heater_stale_ms), K(batt_min_dv), K(batt_hold_ms),
        K(verify_ms), K(runtime_min)};
#undef K
    for (size_t i = 0; i < sizeof KEYS / sizeof KEYS[0]; i++)
        if (strcmp(KEYS[i].name, key) == 0) {
            memcpy((char *)&C.s + KEYS[i].off, &value, sizeof value);
            return 0;
        }
    return -1;
}

static void opt_num(cali_json_t *j, const char *key, int32_t v) {
    cali_json_key(j, key);
    if (v == CALI_THERMO_NONE) cali_json_null(j);
    else cali_json_int(j, v);
}

void cali_thermo_state_json(cali_json_t *j) {
    const cali_thermo_status_t *st = &s_out.status;
    cali_json_obj_begin(j);
    cali_json_key(j, "enabled");
    cali_json_bool(j, s_on);
    cali_json_key(j, "mode");
    cali_json_str(j, cali_thermo_mode_names[S.mode]);
    cali_json_key(j, "reason");
    if (s_reason) cali_json_str(j, s_reason);
    else cali_json_null(j);
    cali_json_key(j, "heater");
    cali_json_str(j, cali_thermo_heater_names[st->heater]);
    opt_num(j, "t_ref", st->t_ref);
    opt_num(j, "t_out", st->t_out);
    opt_num(j, "target", C.target);
    opt_num(j, "level", st->level);
    cali_json_key(j, "active");
    if (st->active_alias[0]) cali_json_str(j, st->active_alias);
    else cali_json_null(j);
    cali_json_key(j, "outside_active");
    cali_json_bool(j, st->outside_active);
    cali_json_key(j, "end");
    cali_json_str(j, st->end ? st->end : "none");
    cali_json_obj_end(j);
}
