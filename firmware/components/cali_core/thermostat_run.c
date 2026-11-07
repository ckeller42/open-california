/* thermostat_run.c — the cabin thermostat on the satellite (R_FW_THERMOSTAT_RUN). Contract: the "Run"
 * part of include/cali_thermostat.h. Proven end to end by tests/firmware/test_thermostat_e2e.py.
 *
 * One cali_thermo_step() per period_ms (and at once after a request, a switch of the feature, a
 * finished command batch or a new 1702 frame with a fault), fed from this ESP: the stored 1702 frame + its age, this link's 1202/1602/
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
static uint64_t s_link_wait;        /* an AUTO tap waits for the link it woke until this uptime; 0 = not yet */
static uint64_t s_heater_seen;      /* the time of the last 1702 frame the tick looked at */
static int s_heater_fault;          /* the last ErrorCode seen, until the heater runs again; 0 = none */
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
 * at most T_RATE_PER_MIN per minute, the time difference floored at one minute; an accepted reading becomes
 * the baseline. The first has no rate check. */
static int plausible(int k, const char *alias, int32_t v, uint64_t at, int commit) {
    if (v < T_MIN || v > T_MAX) return 0;
    if (strcmp(s_last[k].alias, alias) == 0 && at >= s_last[k].at) {
        int64_t dv = (int64_t)v - s_last[k].v;
        if (dv < 0) dv = -dv;
        int64_t dt = (int64_t)(at - s_last[k].at);
        if (dt < 60000) dt = 60000;   /* floored at a minute: two adverts 100 ms apart in one scan window */
        if (dv * 60000 > (int64_t)T_RATE_PER_MIN * dt) return 0;
    }
    if (!commit) return 1;   /* a GET's look: the step's baseline stays */
    snprintf(s_last[k].alias, sizeof s_last[k].alias, "%s", alias);
    s_last[k].v = v;
    s_last[k].at = at;
    return 1;
}

static void sensor(cali_thermo_sensor_t *s, int k, const char *alias, uint64_t now, int commit) {
    uint64_t at;
    memset(s, 0, sizeof *s);
    if (!alias[0]) return;   /* none selected */
    s->present = 1;          /* selected: no reading (removed, renamed, not yet seen) is an invalid one */
    snprintf(s->alias, sizeof s->alias, "%s", alias);
    if (cali_sensors_value(alias, SENSOR_TEMP, &s->value, &at) == 0 && plausible(k, alias, s->value, at, commit)) {
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

static int s_stepped;         /* a step has filled s_out since init (status.night in OFF is the stored one only then) */
static uint64_t s_tick_now;   /* the last tick's clock: the "now" of a GET (cali_thermo_state_json) */
static codec_kv_t s_h[CODEC_KV_MAX];   /* the last inputs' 1702 fields (the step's record reads them) */
static int s_nh;

/* The step's inputs from this ESP, but the request and the cmd result. commit = 1 for a step (the
 * readings become the plausibility baseline), 0 for a GET's cali_thermo_blocked(). */
static void inputs(cali_thermo_in_t *in, uint64_t now, int commit) {
    /* static: the 8 KB host-task stack; k* = the last-known frames, also of an earlier link (cali_thermo_blocked()) */
    static codec_kv_t c[CODEC_KV_MAX], e[CODEC_KV_MAX], kc[CODEC_KV_MAX], ke[CODEC_KV_MAX], kv[CODEC_KV_MAX];
    static cali_thermo_heater_t ht;
    codec_kv_t *h = s_h;
    int nh = s_nh = frame_fields("airheater", 0, h), nc = frame_fields("campingmode", 1, c);
    int ne = frame_fields("energy", 1, e), nkc = frame_fields("campingmode", 0, kc);
    int nke = frame_fields("energy", 0, ke), nkv = frame_fields("vehicle", 0, kv);
    int32_t shore = field(e, ne, "StateLandAfs"), kshore = field(ke, nke, "StateLandAfs");
    int32_t t15 = field(kv, nkv, "TerminalOneFive"), en = field(kc, nkc, "Enable");
    memset(in, 0, sizeof *in);
    in->now_ms = (int64_t)now;
    in->enabled = s_on;
    in->target = C.target;
    in->runtime_min = C.s.runtime_min;
    sensor(&in->ref, 0, C.ref, now, commit);
    sensor(&in->backup, 1, C.backup, now, commit);
    sensor(&in->out, 2, C.out, now, commit);
    in->outside_on = C.outside_on;
    in->heater = NULL;   /* no frame, or one with no fields: no heater (a present all-NONE one would read OFF) */
    in->heater_age_ms = CALI_THERMO_NONE_MS;
    if (nh > 0) {
        uint64_t at = cali_session_frame_ms((size_t)fn_index("airheater"));
        ht.installed = field(h, nh, "Installed");
        ht.level = field(h, nh, "HeatingLevel");
        ht.error_code = field(h, nh, "ErrorCode");
        ht.fault_trigger = field(h, nh, "FaultTriggerBit");
        ht.permanent = field(h, nh, "PermanentOperation");
        ht.normal = field(h, nh, "NormalOperation");
        ht.op_mode = field(h, nh, "OperationModeAirHeater");
        in->heater = &ht;
        in->heater_age_ms = now >= at ? (int64_t)(now - at) : 0;
    }
    in->link_ready = cali_session_ready();
    in->camping_on = field(c, nc, "State") == 1;
    in->shore_on = shore == 1 || shore == 2;
    in->ignition_on = cali_ac_ignition();
    in->batt2_dv = field(e, ne, "UTwoBattBemAfs");
    in->night = C.night;
    in->known_camping_on = field(kc, nkc, "State") == 1;
    in->known_shore_on = kshore == 1 || kshore == 2;
    /* cali_ac_ignition()'s rule on the last-known frames: 1004 TerminalOneFive OR 1202 Enable */
    in->known_ignition_on = t15 < 0 && en < 0 ? -1 : t15 > 0 || en > 0;
}

/* R_THERMOSTAT_ETA: one t_ref sample per minute while AUTO heats, each stamped with its reading's time; leaving
 * AUTO or a switch of the active thermometer clears them. The estimate runs from the oldest sample in the
 * window to this step's reading. */
#define ETA_N 12
static struct {
    uint64_t at;
    int32_t t;
} s_es[ETA_N];
static int s_en;
static int s_eta_src = -1;   /* S.active of the samples */
static int32_t s_eta = CALI_THERMO_NONE;
static int s_eta_first;

static void eta_drop_oldest(void) {
    if (s_en > 0) memmove(s_es, s_es + 1, (size_t)--s_en * sizeof *s_es);
}

static void eta_update(uint64_t now, const cali_thermo_in_t *in, int32_t rem_min) {
    const cali_thermo_status_t *st = &s_out.status;
    const cali_thermo_sensor_t *r = S.active ? &in->backup : &in->ref;
    s_eta = CALI_THERMO_NONE;
    s_eta_first = 0;
    int heating = S.mode == CALI_THERMO_AUTO && st->t_ref != CALI_THERMO_NONE &&
                  (st->heater == CALI_THERMO_H_RUNNING || st->heater == CALI_THERMO_H_CONTINUOUS);
    if (!heating || S.active != s_eta_src) s_en = 0;   /* another thermometer: its readings start a new series */
    s_eta_src = S.active;
    if (!heating) return;
    uint64_t age = r->age_ms > 0 && r->age_ms != CALI_THERMO_NONE_MS ? (uint64_t)r->age_ms : 0;
    uint64_t at = now > age ? now - age : 0;
    if (s_en > 0 && at < s_es[s_en - 1].at) at = s_es[s_en - 1].at;
    if (s_en <= 0 || at - s_es[s_en - 1].at >= 60000) {
        if (s_en == ETA_N) eta_drop_oldest();
        s_es[s_en].at = at;
        s_es[s_en++].t = st->t_ref;
    }
    while (s_en > 1 && at - s_es[0].at > CALI_THERMO_ETA_WINDOW_MS) eta_drop_oldest();
    int32_t rem_s = CALI_THERMO_NONE;
    if (!st->night && rem_min != CALI_THERMO_NONE) {   /* the frame's run time left, less the frame's age */
        int64_t left = (int64_t)rem_min * 60 -
                       (in->heater_age_ms == CALI_THERMO_NONE_MS ? 0 : in->heater_age_ms / 1000);
        rem_s = left < 0 ? 0 : (int32_t)left;
    }
    s_eta = cali_thermo_eta((int64_t)(at - s_es[0].at), st->t_ref - s_es[0].t, C.target - st->t_ref, rem_s,
                            &s_eta_first);
}

static void step(uint64_t now) {
    int mode_before = S.mode;
    cali_thermo_in_t in;
    inputs(&in, now, 1);
    const codec_kv_t *h = s_h;
    int nh = s_nh;
    in.request = s_req;
    in.cmd_what = s_cmd_what;
    in.cmd_result = s_cmd_res;
    s_req = CALI_THERMO_REQ_NONE;   /* one-shot, as the cmd input */
    s_cmd_what = s_cmd_res = NULL;

    cali_thermo_step(&in, &C.s, &S, &s_out);
    s_stepped = 1;
    eta_update(now, &in, field(h, nh, "RunningTimeinAction"));

    if (s_out.reason && (!s_reason || strcmp(s_out.reason, s_reason) != 0 || S.mode != mode_before))
        cali_log("thermostat: %s (mode %s, heater %s)", s_out.reason, cali_thermo_mode_names[S.mode],
                 cali_thermo_heater_names[s_out.status.heater]);
    if (s_out.reason) s_reason = s_out.reason;
    int busy = S.mode != CALI_THERMO_OFF || S.has_pending;   /* a mode, or a stop still pending in OFF */
    if (s_on || mode_before != CALI_THERMO_OFF || busy) {   /* a disabled idle one logs nothing */
        char b[6][16], r[40] = "null";
        int32_t want = S.has_pending && strcmp(S.pending.what, "level") == 0 ? S.pending.value
                       : S.has_expect                                       ? S.expect.level
                                                                            : CALI_THERMO_NONE;
        if (s_out.reason) snprintf(r, sizeof r, "\"%s\"", s_out.reason);
        (void)cali_diag_rec("thermo",
                            "\"r\":%s,\"m\":\"%s\",\"h\":\"%s\",\"t\":%s,\"o\":%s,\"g\":%ld,\"u\":%ld,\"i\":%ld,"
                            "\"uo\":%s,\"l\":%s,\"lc\":%s,\"rem\":%s",
                            r,
                            cali_thermo_mode_names[S.mode], cali_thermo_heater_names[s_out.status.heater],
                            num(b[0], s_out.status.t_ref), num(b[1], s_out.status.t_out), (long)C.target,
                            (long)S.ctl.u, (long)S.ctl.i, num(b[5], S.ctl.u_out), num(b[2], want),
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

/* ---- the settings table: name, place in cali_thermo_cfg_t, range (R_FW_THERMOSTAT_API). The range is
 * the API's: periods and times above 0, gains not below 0; cali_thermo_cfg_set() itself takes any value. ---- */
#define I32 INT32_MAX
#define CFG_TABLE(X)                                                                               \
    X(period_ms, 1, I32) X(kp, 0, I32) X(ki, 0, I32) X(ka, 0, I32) X(u_out_max, 0, I32)         \
    X(hyst, 0, I32) X(min_dwell_ms, 1, I32) X(band_hi, 1, I32) X(hot_hold_ms, 1, I32)            \
    X(overheat, 1, I32) X(min_level, 1, 10) X(stale_after_ms, 1, I32) X(heater_stale_ms, 1, I32) \
    X(batt_min_dv, 100, 150) X(batt_hold_ms, 1, I32) X(verify_ms, 1, I32) X(runtime_min, 10, 120)    \
    X(night_wait_ms, 1, I32) X(night_confirm_ms, 1, I32)
#define X(m, lo, hi) #m,
const char *const cali_thermo_cfg_keys[CALI_THERMO_CFG_N] = {CFG_TABLE(X)};
#undef X
static const struct { size_t off; int64_t lo, hi; } LIM[CALI_THERMO_CFG_N] = {
#define X(m, lo, hi) {offsetof(cali_thermo_cfg_t, m), lo, hi},
    CFG_TABLE(X)
#undef X
};
#define X(m, lo, hi) CFG_##m,
enum { CFG_TABLE(X) };   /* CFG_<member> = its place in the table */
#undef X
typedef char cfg_table_matches_struct[sizeof(cali_thermo_cfg_t) == CALI_THERMO_CFG_N * sizeof(int32_t) ? 1 : -1];

static int32_t *cfg_at(cali_thermo_cfg_t *s, int i) { return (int32_t *)((char *)s + LIM[i].off); }
static int cfg_ok(int i, int64_t v) { return v >= LIM[i].lo && v <= LIM[i].hi; }
static int target_ok(int64_t v) {
    return v >= CALI_THERMO_TARGET_MIN && v <= CALI_THERMO_TARGET_MAX && v % CALI_THERMO_TARGET_STEP == 0;
}
static int alias_chars_ok(const char *a, size_t cap) {   /* "" or [a-z0-9_-]{1,16}, NUL-terminated */
    size_t n = 0;
    while (n < cap && a[n]) n++;
    if (n == cap) return 0;
    for (size_t i = 0; i < n; i++)
        if (!((a[i] >= 'a' && a[i] <= 'z') || (a[i] >= '0' && a[i] <= '9') || a[i] == '_' || a[i] == '-')) return 0;
    return 1;
}

/* The stored configuration: a version byte, then the whole cali_thermo_run_cfg_t (all int32 and char, so
 * the layout is the same on every build). Anything that does not check is dropped for the defaults. */
typedef struct {
    uint8_t ver, pad[3];
    cali_thermo_run_cfg_t c;
} th_blob_t;
#define TH_BLOB_VER 2
/* Version 1 (no night_wait_ms, night_confirm_ms, night): loaded with those at their defaults, so the
 * settings of an older firmware survive the update. */
#define TH_V1_CFG_N 17   /* period_ms .. runtime_min: a prefix of cali_thermo_cfg_t */
typedef struct {
    uint8_t ver, pad[3];
    int32_t s[TH_V1_CFG_N];
    int32_t target;
    char ref[CALI_THERMO_ALIAS_LEN], backup[CALI_THERMO_ALIAS_LEN], out[CALI_THERMO_ALIAS_LEN];
    int outside_on;
} th_blob_v1_t;
typedef char th_blob_v1_size[sizeof(th_blob_v1_t) == 132 ? 1 : -1];   /* the layout on the van */

static int cfg_valid(const cali_thermo_run_cfg_t *c) {
    cali_thermo_cfg_t s = c->s;
    for (int i = 0; i < CALI_THERMO_CFG_N; i++)
        if (!cfg_ok(i, *cfg_at(&s, i))) return 0;
    return target_ok(c->target) && (c->outside_on == 0 || c->outside_on == 1) && (c->night == 0 || c->night == 1) &&
           alias_chars_ok(c->ref, sizeof c->ref) && alias_chars_ok(c->backup, sizeof c->backup) &&
           alias_chars_ok(c->out, sizeof c->out);
}

static int cfg_store(const cali_thermo_run_cfg_t *c) {
    th_blob_t b;
    memset(&b, 0, sizeof b);
    b.ver = TH_BLOB_VER;
    b.c = *c;
    return cali_kv_set(CALI_THERMO_CFG_KEY, &b, sizeof b) == 0 ? 0 : -1;
}

static unsigned s_rev;   /* counts the accepted POSTs; not stored */

/* a stored version-1 record into *c (the rest at the defaults already there); 0 when it is one */
static int cfg_from_v1(const void *raw, size_t len, cali_thermo_run_cfg_t *c) {
    th_blob_v1_t v;
    if (len != sizeof v) return -1;
    memcpy(&v, raw, sizeof v);
    if (v.ver != 1) return -1;
    memcpy(&c->s, v.s, sizeof v.s);
    c->target = v.target;
    memcpy(c->ref, v.ref, sizeof c->ref);
    memcpy(c->backup, v.backup, sizeof c->backup);
    memcpy(c->out, v.out, sizeof c->out);
    c->outside_on = v.outside_on;
    return 0;
}

void cali_thermo_run_init(void) {
    uint8_t b;
    size_t n = 1;
    th_blob_t blob;
    size_t bn = sizeof blob;
    cali_thermo_run_cfg_t v1;
    s_on = cali_kv_get(CALI_THERMO_ON_KEY, &b, &n) == CALI_KV_OK && n == 1 && b != 0;   /* missing = off */
    static const cali_thermo_run_cfg_t DEFAULTS = {.s = CALI_THERMO_DEFAULTS, .target = CALI_THERMO_TARGET_DEFAULT};
    C = DEFAULTS;
    v1 = DEFAULTS;
    if (cali_kv_get(CALI_THERMO_CFG_KEY, &blob, &bn) == CALI_KV_OK) {
        if (bn == sizeof blob && blob.ver == TH_BLOB_VER && cfg_valid(&blob.c)) C = blob.c;
        else if (cfg_from_v1(&blob, bn, &v1) == 0 && cfg_valid(&v1)) C = v1;
    }
    s_rev = 0;
    cali_thermo_init(&S);
    memset(&s_out, 0, sizeof s_out);
    s_stepped = 0;
    s_en = 0;
    s_eta_src = -1;
    s_eta = CALI_THERMO_NONE;
    s_eta_first = 0;
    s_out.status.heater = CALI_THERMO_H_UNKNOWN;
    s_out.status.t_ref = s_out.status.t_out = s_out.status.level = CALI_THERMO_NONE;
    s_out.status.end = "none";
    s_out.status.wait_left_s = CALI_THERMO_NONE;
    cali_log("thermostat: %s", s_on ? "enabled" : "disabled");
}

void cali_thermo_run_tick(uint64_t now_ms) {
    s_tick_now = now_ms;
    /* a new 1702 frame with a fault steps at once: the unit can clear the code (ErrorCode 4 after 36 s on the
     * van) before the next period, and the step would then see a heater off without a fault (RUN_ENDED) */
    uint64_t hat = cali_session_frame_ms((size_t)fn_index("airheater"));
    if (hat != s_heater_seen) {
        static codec_kv_t kv[CODEC_KV_MAX];   /* static as in inputs(): the owner task's stack is small */
        int n = frame_fields("airheater", 1, kv);
        s_heater_seen = hat;
        int32_t err = field(kv, n, "ErrorCode");
        if (field(kv, n, "FaultTriggerBit") > 0 || err > 0) s_due = 1;
        if (err > 0) s_heater_fault = (int)err;
        else if (field(kv, n, "NormalOperation") > 0 || field(kv, n, "PermanentOperation") > 0) s_heater_fault = 0;
    }
    if (s_wait || s_n) return;   /* a batch is going out: its done callback sends the next */
    /* an AUTO tap from OFF woke the link (cali_thermo_request): give it time to come up and, in OFF the session
     * does not re-read 1702, to read the heater (the hold reads it at once), then step */
    if (s_req == CALI_THERMO_REQ_AUTO && s_on && S.mode == CALI_THERMO_OFF) {
        uint64_t at = cali_session_frame_ms((size_t)fn_index("airheater"));
        if (!cali_session_ready() || !at || now_ms - at > (uint64_t)C.s.heater_stale_ms) {
            cali_session_hold(1);   /* the step's hold(busy) releases it when the request is refused */
            if (!s_link_wait) s_link_wait = now_ms + CALI_THERMO_LINK_WAIT_MS;
            if (now_ms < s_link_wait) return;
        }
    }
    s_link_wait = 0;
    if (!s_due && now_ms < s_next) return;
    s_due = 0;
    step(now_ms);
    /* WAITING: sample the confirm window every few seconds (the control law stays gated by last_ctl_ms) */
    s_next = now_ms + (uint64_t)(S.mode == CALI_THERMO_WAITING && C.s.period_ms > CALI_THERMO_WAIT_STEP_MS
                                     ? CALI_THERMO_WAIT_STEP_MS
                                     : C.s.period_ms);
    if (s_n) submit_next();
}

int cali_thermo_run_heater_fault(void) { return s_heater_fault; }

int cali_thermo_request(const char *mode) {
    if (strcmp(mode, "AUTO") == 0) {
        s_req = CALI_THERMO_REQ_AUTO;
        if (s_on) cali_session_connect_now();   /* the idle policy may have the link down: wake it for the tap */
    } else if (strcmp(mode, "OFF") == 0) s_req = CALI_THERMO_REQ_OFF;
    else return -1;
    s_link_wait = 0;
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
    for (int i = 0; i < CALI_THERMO_CFG_N; i++)
        if (strcmp(cali_thermo_cfg_keys[i], key) == 0) {
            *cfg_at(&C.s, i) = value;
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
    cali_thermo_in_t in;
    inputs(&in, s_tick_now, 0);   /* blocked from the current inputs: the card polls between steps */
    const char *blocked = cali_thermo_blocked(&in, &C.s);
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
    cali_json_key(j, "blocked");
    if (blocked) cali_json_str(j, blocked);
    else cali_json_null(j);
    if (S.mode == CALI_THERMO_WAITING && S.wait_until_ms != CALI_THERMO_NONE_MS) {
        /* counted down at each GET from the wait's end (the step runs only every few seconds) */
        int64_t left = S.wait_until_ms - (int64_t)s_tick_now;
        opt_num(j, "wait_left_s", left <= 0 ? 0 : (int32_t)((left + 999) / 1000));
    } else {
        opt_num(j, "wait_left_s", st->wait_left_s);
    }
    cali_json_key(j, "night");
    cali_json_bool(j, S.mode == CALI_THERMO_OFF && s_stepped ? C.night : st->night);   /* in OFF the stored one, at each GET */
    opt_num(j, "eta_s", s_eta);   /* as of the last step (R_THERMOSTAT_ETA) */
    cali_json_key(j, "eta_ends_first");
    cali_json_bool(j, s_eta_first);
    cali_json_obj_end(j);
}

static void alias_json(cali_json_t *j, const char *key, const char *alias) {
    cali_json_key(j, key);
    if (alias[0]) cali_json_str(j, alias);
    else cali_json_null(j);
}

void cali_thermo_api_json(cali_json_t *j) {
    cali_thermo_cfg_t s = C.s;
    cali_json_key(j, "rev");
    cali_json_int(j, s_rev);
    cali_json_key(j, "mode");
    cali_json_str(j, cali_thermo_mode_names[S.mode]);
    cali_json_key(j, "target");
    cali_json_int(j, C.target);
    cali_json_key(j, "runtime_min");
    cali_json_int(j, C.s.runtime_min);
    alias_json(j, "ref", C.ref);
    alias_json(j, "backup", C.backup);
    alias_json(j, "out", C.out);
    cali_json_key(j, "outside_on");
    cali_json_bool(j, C.outside_on);
    cali_json_key(j, "night");
    cali_json_bool(j, C.night);
    cali_json_key(j, "cfg");
    cali_json_obj_begin(j);
    for (int i = 0; i < CALI_THERMO_CFG_N; i++) {
        cali_json_key(j, cali_thermo_cfg_keys[i]);
        cali_json_int(j, *cfg_at(&s, i));
    }
    cali_json_obj_end(j);
    cali_json_key(j, "status");
    cali_thermo_state_json(j);
}

int cali_thermo_has_ref(void) { return C.ref[0] != 0; }

void cali_thermo_brief_json(cali_json_t *j) {
    cali_json_obj_begin(j);
    cali_json_key(j, "mode");
    cali_json_str(j, cali_thermo_mode_names[S.mode]);
    opt_num(j, "target", C.target);
    opt_num(j, "t_ref", s_out.status.t_ref);
    opt_num(j, "level", s_out.status.level);
    cali_json_key(j, "reason");
    if (s_reason) cali_json_str(j, s_reason);
    else cali_json_null(j);
    cali_json_obj_end(j);
}

static int sensor_ok(const char *alias) {   /* "" clears; else a configured Govee */
    const char *t = alias[0] ? cali_sensors_type(alias) : "govee";
    return t && strcmp(t, "govee") == 0;
}

int cali_thermo_apply(const cali_thermo_patch_t *p) {
    cali_thermo_run_cfg_t n = C;
    unsigned h = p->has;
    int auto_req = 0;
    /* a body that is only mode OFF (+ rev) is never blocked by a stale rev: a stop always goes through */
    int plain_off = (h & ~(unsigned)CALI_THERMO_P_REV) == CALI_THERMO_P_MODE && strcmp(p->mode, "OFF") == 0;
    if ((h & CALI_THERMO_P_REV) && !plain_off && p->rev != (int64_t)s_rev) return CALI_THERMO_E_STALE;
    if (h & CALI_THERMO_P_MODE) {
        if (strcmp(p->mode, "AUTO") == 0) auto_req = 1;
        else if (strcmp(p->mode, "OFF") != 0) return CALI_THERMO_E_VALUE;
    }
    if (((h & CALI_THERMO_P_TARGET) && !target_ok(p->target)) ||
        ((h & CALI_THERMO_P_RUNTIME) && !cfg_ok(CFG_runtime_min, p->runtime_min)))
        return CALI_THERMO_E_VALUE;
    for (int i = 0; (h & CALI_THERMO_P_CFG) && i < CALI_THERMO_CFG_N; i++)
        if ((p->cfg_has >> i & 1u) && !cfg_ok(i, p->cfg[i])) return CALI_THERMO_E_VALUE;
    if (((h & CALI_THERMO_P_REF) && !sensor_ok(p->ref)) || ((h & CALI_THERMO_P_BACKUP) && !sensor_ok(p->backup)) ||
        ((h & CALI_THERMO_P_OUT) && !sensor_ok(p->out)))
        return CALI_THERMO_E_SENSOR;
    if (((h & (CALI_THERMO_P_ENABLED | CALI_THERMO_P_CFG)) || auto_req) && !((h & CALI_THERMO_P_CONFIRM) && p->confirm))
        return CALI_THERMO_E_CONFIRM;

    if (h & CALI_THERMO_P_TARGET) n.target = (int32_t)p->target;
    if (h & CALI_THERMO_P_RUNTIME) n.s.runtime_min = (int32_t)p->runtime_min;
    if (h & CALI_THERMO_P_REF) snprintf(n.ref, sizeof n.ref, "%s", p->ref);
    if (h & CALI_THERMO_P_BACKUP) snprintf(n.backup, sizeof n.backup, "%s", p->backup);
    if (h & CALI_THERMO_P_OUT) snprintf(n.out, sizeof n.out, "%s", p->out);
    if (h & CALI_THERMO_P_OUTSIDE) n.outside_on = p->outside_on != 0;
    if (h & CALI_THERMO_P_NIGHT) n.night = p->night != 0;
    for (int i = 0; (h & CALI_THERMO_P_CFG) && i < CALI_THERMO_CFG_N; i++)
        if (p->cfg_has >> i & 1u) *cfg_at(&n.s, i) = (int32_t)p->cfg[i];
    if (memcmp(&n, &C, sizeof n) != 0) {
        if (cfg_store(&n) != 0) return CALI_THERMO_E_STORE;
        if (n.target != C.target) {   /* the estimate was for the old target: none until the next step */
            s_eta = CALI_THERMO_NONE;
            s_eta_first = 0;
        }
        C = n;
    }
    if ((h & CALI_THERMO_P_ENABLED) && cali_thermo_set_enabled(p->enabled) != 0) {
        if (p->enabled) (void)cali_thermo_set_enabled(0);   /* an enable that could not be stored stays off */
        return CALI_THERMO_E_STORE;
    }
    if (auto_req) (void)cali_thermo_request("AUTO");
    else if (h & CALI_THERMO_P_MODE) (void)cali_thermo_request("OFF");
    s_rev++;
    return 0;
}
