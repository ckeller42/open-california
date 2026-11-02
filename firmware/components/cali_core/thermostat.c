/* thermostat.c — calictl.thermostat (heater_state, control, track, step) in C (R_FW_THERMOSTAT_DECIDE).
 * Contract: include/cali_thermostat.h. Line for line with the Python, comments there; held equal by
 * tests/firmware/test_thermostat_parity.py. Integer arithmetic only; products in int64_t.
 */
#include "cali_thermostat.h"

#include <stddef.h>
#include <string.h>

#define NONE CALI_THERMO_NONE
#define NONE_MS CALI_THERMO_NONE_MS

const char *const cali_thermo_heater_names[8] = {"OFF",   "RUNNING", "CONTINUOUS", "TIMER_ARMED",
                                                 "FAULT", "LOCKED",  "BLOCKED",    "UNKNOWN"};
const char *const cali_thermo_mode_names[4] = {"OFF", "AUTO", "DEGRADED", "STOPPING"};

static const char *const FAULT_REASON[16] = {
    NULL,       "FAULT_1",  "FAULT_2",  "FAULT_3",  "FAULT_4",  "FAULT_5",  "FAULT_6",  "FAULT_7",
    "FAULT_8",  "FAULT_9",  "FAULT_10", "FAULT_11", "FAULT_12", "FAULT_13", "FAULT_14", "FAULT_15"};

/* Python _rdiv: division rounding half away from zero. */
static int64_t rdiv(int64_t a, int64_t b) {
    int64_t aa = a < 0 ? -a : a, bb = b < 0 ? -b : b;
    int64_t q = aa / bb, r = aa % bb;
    if (2 * r >= bb) q++;
    return (a < 0) == (b < 0) ? q : -q;
}

static int64_t mn(int64_t a, int64_t b) { return a < b ? a : b; }
static int64_t mx(int64_t a, int64_t b) { return a > b ? a : b; }
static int truthy(int32_t v) { return v != 0 && v != NONE; } /* Python d.get(k) truthiness */
static int is(const char *a, const char *b) { return a && strcmp(a, b) == 0; }

int cali_thermo_heater_state(const cali_thermo_heater_t *h, int64_t age_ms, const cali_thermo_cfg_t *cfg,
                             int *level, int *code) {
    int32_t err;
    *level = NONE;
    *code = 0;
    if (!h || age_ms == NONE_MS || age_ms > cfg->heater_stale_ms) return CALI_THERMO_H_UNKNOWN;
    if (h->installed == 0) {
        *code = -1;
        return CALI_THERMO_H_FAULT;
    }
    if (h->level != NONE && h->level >= 1 && h->level <= 10) *level = h->level;
    err = h->error_code == NONE ? 0 : h->error_code;
    *code = err;
    if (err == 4) return CALI_THERMO_H_LOCKED;
    if (err == 5) return CALI_THERMO_H_BLOCKED;
    if (err || truthy(h->fault_trigger)) return CALI_THERMO_H_FAULT;
    if (truthy(h->permanent)) return CALI_THERMO_H_CONTINUOUS;
    if (truthy(h->normal)) return CALI_THERMO_H_RUNNING;
    if (h->op_mode == 3) return CALI_THERMO_H_TIMER_ARMED;
    return CALI_THERMO_H_OFF;
}

/* Python control(): updates ctl in place (every caller stores the new ctl); returns the level or NONE. */
static int32_t control(int32_t t_ref, int32_t t_out, int32_t target, int64_t dt_ms, cali_thermo_ctl_t *ctl,
                       const cali_thermo_cfg_t *cfg, int32_t cur_level, int64_t now, const char **why) {
    int64_t e = (int64_t)target - t_ref;
    int64_t p = rdiv(cfg->kp * e, 100);
    int64_t u_out = t_out == NONE ? 0 : mn(mx(rdiv(cfg->ka * mx(0, (int64_t)target - t_out), 100), 0), cfg->u_out_max);
    int64_t i = ctl->i, u_old = p + i + u_out, u, level, dist, last_change = ctl->last_change_ms;
    int frozen = dt_ms > 3 * (int64_t)cfg->period_ms || (u_old < 1000 && e < 0) || (u_old > 10000 && e > 0);
    int hot;
    if (!frozen) i += rdiv(cfg->ki * e * dt_ms, 100 * 60000);
    u = p + i + u_out;
    hot = cur_level == cfg->min_level && t_ref > (int64_t)target + cfg->band_hi;
    ctl->i = (int32_t)i;
    ctl->u = (int32_t)u;
    ctl->u_out = (int32_t)u_out;
    ctl->hot_since_ms = hot ? (ctl->hot_since_ms != NONE_MS ? ctl->hot_since_ms : now) : NONE_MS;
    if (t_ref > (int64_t)target + cfg->overheat) {
        *why = "OVERHEAT";
        return NONE;
    }
    if (ctl->hot_since_ms != NONE_MS && now - ctl->hot_since_ms >= cfg->hot_hold_ms) {
        *why = "TARGET_EXCEEDED_AT_MIN_LEVEL";
        return NONE;
    }
    level = mn(mx(rdiv(u, 1000), cfg->min_level), 10);
    if (cur_level == NONE) {
        *why = "LEVEL_UP";
        return (int32_t)level;
    }
    dist = u - (int64_t)cur_level * 1000;
    if (dist < 0) dist = -dist;
    if (level == cur_level || dist < 500 + cfg->hyst || now - last_change < cfg->min_dwell_ms) {
        *why = "HOLD";
        return NONE;
    }
    ctl->last_change_ms = now;
    *why = level > cur_level ? "LEVEL_UP" : "LEVEL_DOWN";
    return (int32_t)level;
}

/* Python track(): the next demand equals the current level. */
static void track(cali_thermo_ctl_t *ctl, int32_t cur_level, int32_t t_ref, int32_t target, const cali_thermo_cfg_t *cfg) {
    int64_t p = rdiv(cfg->kp * ((int64_t)target - t_ref), 100);
    ctl->i = (int32_t)((int64_t)cur_level * 1000 - p - ctl->u_out);
}

static int valid(const cali_thermo_sensor_t *r, const cali_thermo_cfg_t *cfg) {
    return r->present && r->value != NONE && r->age_ms != NONE_MS && r->age_ms <= cfg->stale_after_ms;
}

static int same(const cali_thermo_snap_t *a, const cali_thermo_snap_t *b) {
    return a->normal == b->normal && a->permanent == b->permanent &&
           (a->level == NONE || b->level == NONE || a->level == b->level);
}

/* Python p["value"] == "off": a level value never is. */
static int is_off(const cali_thermo_pending_t *p) { return !is(p->what, "level") && p->value == 0; }

static int applied(const cali_thermo_pending_t *p, const cali_thermo_snap_t *s) {
    if (is(p->what, "level")) return s->level == p->value;
    if (is(p->what, "permanent")) return s->permanent == 0;
    return s->normal == (p->value == 1 ? 1 : 0);
}

static void add(cali_thermo_out_t *o, const char *what, int32_t value) {
    o->cmds[o->n].what = what;
    o->cmds[o->n].value = value;
    o->n++;
}

static void resend(const cali_thermo_pending_t *p, const cali_thermo_state_t *st, const cali_thermo_in_t *in, int full,
                   cali_thermo_out_t *o) {
    o->n = 0;
    if (full && is(p->what, "power") && p->value == 1) {
        add(o, "runtime", in->runtime_min);
        add(o, "level", st->expect.level);
        add(o, "power", 1);
        return;
    }
    add(o, p->what, p->value);
}

static void pend(cali_thermo_state_t *st, const char *what, int32_t value, int64_t now, int32_t before,
                 const char *why) {
    st->has_pending = 1;
    st->pending.what = what;
    st->pending.value = value;
    st->pending.since_ms = now;
    st->pending.tries = 1;
    st->pending.first_ms = now;
    st->pending.before = before;
    st->pending.why = why;
}

static int start_retry_ok(const cali_thermo_pending_t *p, const cali_thermo_in_t *in, const cali_thermo_cfg_t *cfg) {
    return in->now_ms - p->first_ms <= cfg->heater_stale_ms && in->enabled && (in->camping_on || in->shore_on) &&
           in->ignition_on == 0 && valid(&in->ref, cfg);
}

/* Python _stop(): replaces the commands. */
static void stop(cali_thermo_state_t *st, int can_send, int64_t now, const char *why, cali_thermo_out_t *o) {
    cali_thermo_snap_t e = {0, 0, NONE};
    const char *what;
    if (st->has_expect) e = st->expect;
    what = e.permanent ? "permanent" : e.normal ? "power" : NULL;
    st->has_pending = 0;
    st->mode = CALI_THERMO_OFF;
    o->n = 0;
    if (!what) return;
    if (can_send) {
        pend(st, what, 0, now, NONE, why);
        add(o, what, 0);
        return;
    }
    st->mode = CALI_THERMO_STOPPING;
    st->has_stop_snapshot = 1;
    st->stop_snapshot = e;
    st->stop_why = why;
}

/* Python _end(): replaces the commands with none. */
static void end(cali_thermo_state_t *st, cali_thermo_out_t *o) {
    st->mode = CALI_THERMO_OFF;
    st->has_pending = 0;
    o->n = 0;
}

static const char *refusal(const cali_thermo_in_t *in, const cali_thermo_cfg_t *cfg, int hs, int code) {
    if (!in->enabled) return "AUTO_REFUSED_DISABLED";
    if (!in->link_ready) return "AUTO_REFUSED_NO_LINK";
    if (hs == CALI_THERMO_H_UNKNOWN) return "AUTO_REFUSED_STALE_HEATER";
    if (code == -1) return "AUTO_REFUSED_NOT_INSTALLED";
    if (hs == CALI_THERMO_H_FAULT || hs == CALI_THERMO_H_LOCKED || hs == CALI_THERMO_H_BLOCKED)
        return "AUTO_REFUSED_FAULT";
    if (hs == CALI_THERMO_H_TIMER_ARMED) return "AUTO_REFUSED_TIMER_ARMED";
    if (!(in->camping_on || in->shore_on)) return "AUTO_REFUSED_NO_CAMPING";
    if (in->ignition_on != 0) return "AUTO_REFUSED_IGNITION";
    if (!valid(&in->ref, cfg)) return "AUTO_REFUSED_NO_REFERENCE";
    return NULL;
}

/* Python out(why): the reason, the mode and the status at the time of the return. */
static void finish(const cali_thermo_in_t *in, const cali_thermo_cfg_t *cfg, const cali_thermo_state_t *st, int hs,
                   int level, int32_t t_out, const char *why, cali_thermo_out_t *o) {
    const cali_thermo_sensor_t *r = st->active ? &in->backup : &in->ref;
    cali_thermo_status_t *s = &o->status;
    o->reason = why;
    o->mode = st->mode;
    s->heater = hs;
    s->t_ref = valid(r, cfg) ? r->value : NONE;
    s->t_out = t_out;
    s->target = in->target;
    s->level = level;
    s->active_alias[0] = '\0';
    if (r->present) {
        memcpy(s->active_alias, r->alias, sizeof s->active_alias);
        s->active_alias[CALI_THERMO_ALIAS_LEN - 1] = '\0';
    }
    s->outside_active = t_out != NONE;
    s->end = hs == CALI_THERMO_H_RUNNING ? "run" : "none";
}

void cali_thermo_init(cali_thermo_state_t *st) {
    memset(st, 0, sizeof *st);
    st->mode = CALI_THERMO_OFF;
    st->ctl.hot_since_ms = NONE_MS;
    st->batt_low_since_ms = NONE_MS;
    st->last_ctl_ms = NONE_MS;
}

#define RET(why)                                                  \
    do {                                                          \
        finish(in, cfg, st, hs, level, t_out, (why), out);        \
        return;                                                   \
    } while (0)

void cali_thermo_step(const cali_thermo_in_t *in, const cali_thermo_cfg_t *cfg, cali_thermo_state_t *st,
                      cali_thermo_out_t *out) {
    int64_t now = in->now_ms;
    int link = in->link_ready, hs, level, code, has_snap, on, req, faulty, heating;
    int32_t target = in->target, t_out;
    const char *reason = NULL;
    cali_thermo_snap_t snap = {0, 0, NONE};
    const cali_thermo_sensor_t *act;

    out->n = 0;
    hs = cali_thermo_heater_state(in->heater, in->heater_age_ms, cfg, &level, &code);
    has_snap = hs != CALI_THERMO_H_UNKNOWN;
    if (has_snap) {
        snap.normal = truthy(in->heater->normal);
        snap.permanent = truthy(in->heater->permanent);
        snap.level = level;
    }
    t_out = in->outside_on && valid(&in->out, cfg) ? in->out.value : NONE;
    faulty = hs == CALI_THERMO_H_FAULT || hs == CALI_THERMO_H_LOCKED || hs == CALI_THERMO_H_BLOCKED;
    heating = hs == CALI_THERMO_H_RUNNING || hs == CALI_THERMO_H_CONTINUOUS;

    /* (1) pending command: readback, result, verify window */
    if (st->has_pending) {
        cali_thermo_pending_t *p = &st->pending;
        const char *res = in->cmd_result;
        int start = is(p->what, "power") && p->value == 1;
        int post = has_snap && now - in->heater_age_ms >= p->since_ms; /* read after the send */
        const char *fail = start ? "START_NOT_CONFIRMED" : "NOT_APPLIED";
        const cali_thermo_snap_t *e = &st->expect;
        int moved = is(p->what, "level") && has_snap && post && !faulty &&
                    ((level != NONE && level != p->value && level != p->before) || snap.normal != e->normal ||
                     snap.permanent != e->permanent);
        if (moved) {
            end(st, out);
            RET("FOREIGN_CHANGE");
        }
        if (has_snap && post && applied(p, &snap)) {
            st->has_pending = 0;
            st->has_expect = 1;
            st->expect = snap;
        } else if (faulty) {
            /* rule (3) ends AUTO with the fault's reason */
        } else if (is(res, "REFUSED")) {
            int off = is_off(p);
            st->has_pending = 0;
            if (!off) end(st, out);
            RET(fail); /* a refused stop: the heater still runs (status.heater) */
        } else if ((is(res, "BUSY") || is(res, "NOT_READY")) && link) {
            if (start && !start_retry_ok(p, in, cfg)) {
                end(st, out);
                RET(fail);
            }
            resend(p, st, in, !is(in->cmd_what, "power"), out);
            p->since_ms = now;
        } else if (has_snap && post && link && now - p->since_ms >= cfg->verify_ms) {
            if (is_off(p) && !same(&snap, &st->expect)) {
                st->has_pending = 0;
                RET("STOP_DROPPED");
            }
            if (p->tries >= 2 || (start && !start_retry_ok(p, in, cfg))) {
                if (!is_off(p)) end(st, out);
                st->has_pending = 0;
                RET(fail);
            }
            resend(p, st, in, 0, out);
            p->since_ms = now;
            p->tries = 2;
        }
        if (out->n) reason = is_off(p) ? "STOPPING" : "AUTO_ON";
    }

    /* (2) owner request (feature disabled while on = owner OFF) */
    on = st->mode == CALI_THERMO_AUTO || st->mode == CALI_THERMO_DEGRADED;
    req = on && !in->enabled ? CALI_THERMO_REQ_OFF : in->request;
    if (req == CALI_THERMO_REQ_OFF && on) {
        stop(st, link && has_snap, now, in->enabled ? "USER_OFF" : "DISABLED", out);
        RET(st->mode == CALI_THERMO_STOPPING ? "STOPPING" : "USER_OFF");
    }
    { /* in mode OFF only a stop can be pending */
        const char *held = st->has_pending ? st->pending.why : st->mode == CALI_THERMO_STOPPING ? st->stop_why : NULL;
        if (req == CALI_THERMO_REQ_AUTO && !on && held && !is(held, "USER_OFF")) {
            req = CALI_THERMO_REQ_NONE; /* a safety stop runs on: the AUTO request is ignored */
            reason = "STOPPING";
        }
    }
    if (req == CALI_THERMO_REQ_AUTO && !on) {
        const char *why = refusal(in, cfg, hs, code);
        int32_t t_ref, lvl;
        cali_thermo_ctl_t ctl0;
        if (why) RET(why); /* the owner's own stop (if any) runs on */
        if (st->has_pending) { /* the last owner request wins: drop the owner's stop */
            st->has_pending = 0;
            out->n = 0;
        }
        t_ref = in->ref.value;
        ctl0.i = 0;
        ctl0.last_change_ms = now;
        ctl0.hot_since_ms = NONE_MS;
        ctl0.u = 0;
        ctl0.u_out = 0;
        st->mode = CALI_THERMO_AUTO;
        st->active = 0;
        st->has_pending = 0;
        st->batt_low_since_ms = NONE_MS;
        st->has_stop_snapshot = 0;
        st->last_ctl_ms = now;
        if (hs == CALI_THERMO_H_OFF) {
            const char *w;
            lvl = control(t_ref, t_out, target, 0, &ctl0, cfg, NONE, now, &w);
            st->ctl = ctl0;
            if (lvl == NONE || lvl == 0) lvl = cfg->min_level; /* overheat at entry: start low, the next step stops */
            pend(st, "power", 1, now, NONE, NULL);
            st->has_expect = 1;
            st->expect.normal = 1;
            st->expect.permanent = 0;
            st->expect.level = lvl;
            out->n = 0;
            add(out, "runtime", in->runtime_min);
            add(out, "level", lvl);
            add(out, "power", 1);
            RET("START");
        } else {
            const char *w;
            control(t_ref, t_out, target, 0, &ctl0, cfg, level, now, &w);
            if (level != NONE) track(&ctl0, level, t_ref, target, cfg);
            st->ctl = ctl0;
            st->has_expect = 1;
            st->expect = snap;
            RET(hs == CALI_THERMO_H_CONTINUOUS ? "ADOPT_CONTINUOUS" : "ADOPT_RUN");
        }
    }
    if (req == CALI_THERMO_REQ_AUTO && on && st->active == 1 && valid(&in->ref, cfg)) {
        st->active = 0; /* back to the reference only by the owner */
        if (level != NONE) track(&st->ctl, level, in->ref.value, target, cfg);
    }

    /* STOPPING: wait for the link, then stop only what the ESP left */
    if (st->mode == CALI_THERMO_STOPPING) {
        cali_thermo_snap_t snap0 = st->stop_snapshot;
        if (!link || !has_snap) RET("STOPPING");
        st->mode = CALI_THERMO_OFF;
        st->has_stop_snapshot = 0;
        if (!heating) RET(snap0.permanent ? "CONTINUOUS_ENDED" : "RUN_ENDED");
        if (!same(&snap, &snap0)) RET("STOP_DROPPED");
        st->has_expect = 1;
        st->expect = snap0;
        stop(st, 1, now, st->stop_why, out);
        RET("STOPPING");
    }
    if (!on) RET(reason);

    /* (3) faults: AUTO ends, never retried; only ErrorCode 1 (low battery) stops the heater */
    if (faulty && code == 1) {
        stop(st, link, now, "BATTERY_LOW", out);
        RET("BATTERY_LOW");
    }
    if (faulty) {
        end(st, out);
        RET(code == 4 ? "LOCKED" : code >= 1 && code <= 15 ? FAULT_REASON[code] : "HEATER_UNKNOWN");
    }
    if (!has_snap) {
        out->n = 0; /* no writes until fresh; AUTO stays on */
        RET("HEATER_UNKNOWN");
    }

    /* (4) foreign change (only while no own command is pending) */
    if (!st->has_pending && heating) {
        if (!same(&snap, &st->expect)) {
            end(st, out);
            RET("FOREIGN_CHANGE");
        }
        if (st->expect.level == NONE) st->expect.level = level;
    }

    /* (5) second battery low for batt_hold_ms */
    if (in->batt2_dv != NONE && in->batt2_dv < cfg->batt_min_dv) {
        if (st->batt_low_since_ms == NONE_MS) st->batt_low_since_ms = now;
        if (now - st->batt_low_since_ms >= cfg->batt_hold_ms) {
            stop(st, link, now, "BATTERY_LOW", out);
            RET("BATTERY_LOW");
        }
    } else {
        st->batt_low_since_ms = NONE_MS;
    }

    /* (6) the heater ended by itself */
    if (!st->has_pending && !heating) {
        end(st, out);
        RET(st->expect.permanent ? "CONTINUOUS_ENDED" : "RUN_ENDED");
    }

    /* (7) reference validity: backup, else min_level (DEGRADED) */
    act = st->active ? &in->backup : &in->ref;
    if (!valid(act, cfg)) {
        if (st->active == 0 && valid(&in->backup, cfg)) {
            st->active = 1;
            st->mode = CALI_THERMO_AUTO;
            st->last_ctl_ms = now;
            if (level != NONE) track(&st->ctl, level, in->backup.value, target, cfg);
            RET("BACKUP_SELECTED");
        }
        st->mode = CALI_THERMO_DEGRADED;
        if (level != cfg->min_level && !st->has_pending && link) {
            out->n = 0;
            add(out, "level", cfg->min_level);
            pend(st, "level", cfg->min_level, now, level, NULL);
            st->expect.level = cfg->min_level;
            st->ctl.last_change_ms = now;
        }
        RET("REFERENCE_LOST");
    }
    if (st->mode == CALI_THERMO_DEGRADED) {
        st->mode = CALI_THERMO_AUTO;
        st->last_ctl_ms = now;
        if (level != NONE) track(&st->ctl, level, act->value, target, cfg);
        RET("AUTO_ON");
    }

    /* (8) control: once per period, frozen while a command is pending */
    if (st->has_pending || now - st->last_ctl_ms < cfg->period_ms) RET(reason ? reason : "AUTO_ON");
    {
        int64_t old_change = st->ctl.last_change_ms;
        const char *why;
        int32_t lvl = control(act->value, t_out, target, now - st->last_ctl_ms, &st->ctl, cfg, level, now, &why);
        st->last_ctl_ms = now;
        if (is(why, "OVERHEAT") || is(why, "TARGET_EXCEEDED_AT_MIN_LEVEL")) {
            stop(st, link, now, why, out);
            RET(why);
        }
        if (lvl != NONE && lvl != level && link) {
            out->n = 0;
            add(out, "level", lvl);
            pend(st, "level", lvl, now, level, NULL);
            st->expect.level = lvl;
            st->ctl.last_change_ms = now;
            RET(why);
        }
        st->ctl.last_change_ms = old_change; /* nothing sent: the dwell does not restart */
        RET(in->outside_on && t_out == NONE ? "OUTSIDE_INACTIVE" : "HOLD");
    }
}
