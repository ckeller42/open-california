/* thermostat_cli.c — line driver for tests/firmware/test_thermostat_parity.py ONLY; not in the ESP build.
 *
 * argv "defaults": print CALI_THERMO_DEFAULTS in the settings-table order.
 * stdin, one case per line, space-separated tokens, "-" = None (the flat lists of
 * tools/gen_thermostat_vectors.py):
 *   H <cfg x17> <heater x8> <age>          -> state level code
 *   S <cfg x17> <in ...> <state ...>       -> <out ...> | <state ...>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_thermostat.h"

static char tok[64];

static const char *next(void) {
    if (scanf("%63s", tok) != 1) exit(0);
    return tok;
}
static int none(void) { return strcmp(tok, "-") == 0; }
static int32_t i32(void) {
    next();
    return none() ? CALI_THERMO_NONE : (int32_t)strtol(tok, NULL, 10);
}
static int64_t i64(void) {
    next();
    return none() ? CALI_THERMO_NONE_MS : (int64_t)strtoll(tok, NULL, 10);
}
static int flag(void) { /* a bool; "-" (None) = -1 */
    next();
    return none() ? -1 : atoi(tok);
}
static int name(const char *const *names, int n) {
    int k;
    next();
    for (k = 0; k < n; k++)
        if (strcmp(tok, names[k]) == 0) return k;
    return -1;
}
static const char *const WHATS[4] = {"power", "permanent", "level", "runtime"};
static const char *what(void) {
    int k = name(WHATS, 4);
    return k < 0 ? NULL : WHATS[k];
}

static void cfg_read(cali_thermo_cfg_t *c) {
    int32_t *f[17] = {&c->period_ms,    &c->kp,          &c->ki,          &c->ka,          &c->u_out_max,
                      &c->hyst,         &c->min_dwell_ms, &c->band_hi,    &c->hot_hold_ms, &c->overheat,
                      &c->min_level,    &c->stale_after_ms, &c->heater_stale_ms, &c->batt_min_dv,
                      &c->batt_hold_ms, &c->verify_ms,   &c->runtime_min};
    int k;
    for (k = 0; k < 17; k++) *f[k] = i32();
}

static int heater_read(cali_thermo_heater_t *h) {
    int present = flag();
    h->installed = i32();
    h->level = i32();
    h->error_code = i32();
    h->fault_trigger = i32();
    h->permanent = i32();
    h->normal = i32();
    h->op_mode = i32();
    return present > 0;
}

static void sensor_read(cali_thermo_sensor_t *s) {
    s->present = flag() > 0;
    next();
    s->alias[0] = '\0';
    if (!none()) {
        strncpy(s->alias, tok, CALI_THERMO_ALIAS_LEN - 1);
        s->alias[CALI_THERMO_ALIAS_LEN - 1] = '\0';
    }
    s->value = i32();
    s->age_ms = i64();
}

static void snap_read(int *has, cali_thermo_snap_t *s) {
    *has = flag() > 0;
    s->normal = flag();
    s->permanent = flag();
    s->level = i32();
}

static void p32(int32_t v) {
    if (v == CALI_THERMO_NONE)
        printf(" -");
    else
        printf(" %ld", (long)v);
}
static void p64(int64_t v) {
    if (v == CALI_THERMO_NONE_MS)
        printf(" -");
    else
        printf(" %lld", (long long)v);
}
static void pstr(const char *s) { printf(" %s", s && *s ? s : "-"); }

static void snap_print(int has, const cali_thermo_snap_t *s) {
    printf(" %d", has);
    if (has) {
        printf(" %d %d", s->normal, s->permanent);
        p32(s->level);
    } else {
        printf(" - - -");
    }
}

static void state_print(const cali_thermo_state_t *st) {
    const cali_thermo_pending_t *p = &st->pending;
    printf(" %s %s", cali_thermo_mode_names[st->mode], st->active ? "backup" : "ref");
    p32(st->ctl.i);
    p64(st->ctl.last_change_ms);
    p64(st->ctl.hot_since_ms);
    p32(st->ctl.u);
    p32(st->ctl.u_out);
    printf(" %d", st->has_pending);
    if (st->has_pending) {
        printf(" %s", p->what);
        p32(p->value);
        p64(p->since_ms);
        p32(p->tries);
        p64(p->first_ms);
        p32(p->before);
    } else {
        printf(" - - - - - -");
    }
    snap_print(st->has_expect, &st->expect);
    p64(st->batt_low_since_ms);
    snap_print(st->has_stop_snapshot, &st->stop_snapshot);
    p64(st->last_ctl_ms);
}

static void defaults(void) {
    cali_thermo_cfg_t c = CALI_THERMO_DEFAULTS;
    printf("%ld %ld %ld %ld %ld %ld %ld %ld %ld %ld %ld %ld %ld %ld %ld %ld %ld\n", (long)c.period_ms, (long)c.kp,
           (long)c.ki, (long)c.ka, (long)c.u_out_max, (long)c.hyst, (long)c.min_dwell_ms, (long)c.band_hi,
           (long)c.hot_hold_ms, (long)c.overheat, (long)c.min_level, (long)c.stale_after_ms,
           (long)c.heater_stale_ms, (long)c.batt_min_dv, (long)c.batt_hold_ms, (long)c.verify_ms,
           (long)c.runtime_min);
}

int main(int argc, char **argv) {
    static const char *const REQS[3] = {"-", "AUTO", "OFF"};
    static const char *const ACTIVE[2] = {"ref", "backup"};
    if (argc == 2 && strcmp(argv[1], "defaults") == 0) {
        defaults();
        return 0;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    for (;;) {
        cali_thermo_cfg_t cfg;
        cali_thermo_heater_t h;
        int kind = next()[0], hp, k;
        cfg_read(&cfg);
        if (kind == 'H') {
            int level, code, s;
            int64_t age;
            hp = heater_read(&h);
            age = i64();
            s = cali_thermo_heater_state(hp ? &h : NULL, age, &cfg, &level, &code);
            printf("%s", cali_thermo_heater_names[s]);
            p32(level);
            printf(" %d\n", code);
            continue;
        }
        {
            cali_thermo_in_t in;
            cali_thermo_state_t st;
            cali_thermo_out_t out;
            static char res[32];
            in.now_ms = i64();
            in.enabled = flag();
            in.request = name(REQS, 3);
            in.target = i32();
            in.runtime_min = i32();
            sensor_read(&in.ref);
            sensor_read(&in.backup);
            sensor_read(&in.out);
            in.outside_on = flag();
            hp = heater_read(&h);
            in.heater = hp ? &h : NULL;
            in.heater_age_ms = i64();
            in.link_ready = flag();
            in.camping_on = flag();
            in.shore_on = flag();
            in.ignition_on = flag();
            in.batt2_dv = i32();
            in.cmd_what = what();
            next();
            strncpy(res, tok, sizeof res - 1);
            in.cmd_result = none() ? NULL : res;

            st.mode = name(cali_thermo_mode_names, 4);
            st.active = name(ACTIVE, 2);
            st.ctl.i = i32();
            st.ctl.last_change_ms = i64();
            st.ctl.hot_since_ms = i64();
            st.ctl.u = i32();
            st.ctl.u_out = i32();
            st.has_pending = flag() > 0;
            st.pending.what = what();
            st.pending.value = i32();
            st.pending.since_ms = i64();
            st.pending.tries = i32();
            st.pending.first_ms = i64();
            st.pending.before = i32();
            snap_read(&st.has_expect, &st.expect);
            st.batt_low_since_ms = i64();
            snap_read(&st.has_stop_snapshot, &st.stop_snapshot);
            st.last_ctl_ms = i64();

            cali_thermo_step(&in, &cfg, &st, &out);

            printf("%s", out.reason ? out.reason : "-");
            printf(" %s %s", cali_thermo_mode_names[out.mode], cali_thermo_heater_names[out.status.heater]);
            p32(out.status.t_ref);
            p32(out.status.t_out);
            p32(out.status.target);
            p32(out.status.level);
            pstr(out.status.active_alias);
            printf(" %d %s %d", out.status.outside_active, out.status.end, out.n);
            for (k = 0; k < out.n; k++) {
                printf(" %s", out.cmds[k].what);
                p32(out.cmds[k].value);
            }
            printf(" |");
            state_print(&st);
            printf("\n");
        }
    }
}
