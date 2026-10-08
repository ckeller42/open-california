/* control_run.c — one control command at a time onto the session's link (#154 B). Contract:
 * include/cali_control.h. Ticked and fed WRITTEN events by session.c. Proven by
 * tests/firmware/test_session_fake.py (test_control_*).
 */
#include "cali_control.h"

#include <stdio.h>
#include <string.h>

#include "cali_platform.h"
#include "cali_session.h"
#include "codec.h"
#include "codec_chars.h"

static const cali_transport_t *s_t;
static uint64_t s_now;
static struct {
    int active;            /* a command is running */
    int inflight;          /* a write is out and its WRITTEN has not come back (outlives a timeout) */
    size_t i;              /* the next frame */
    uint64_t next_at, deadline;
    cali_ctl_done_t done;
    char label[64];        /* "<fn>/<what>" for the log */
    cali_ctl_plan_t plan;
    int pull;              /* 1: the REQUEST_CONFIG pull is going out; 2: waiting for the unit's reply */
    uint64_t pull_until;
    char fn[24], what[32], value[CALI_CTL_VALUE_MAX];   /* the command, planned again after the pull */
    int64_t local_now;     /* the page's clock at the submit (s_now = submitted_at) */
    uint64_t submitted_at;
} O;

static int index_of(const char *fn) {
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        if (strcmp(CODEC_CHARS[i].function, fn) == 0) return (int)i;
    return -1;
}

/* cali_ctl_get_t over the session's stored frames (codec_decode on demand). */
static int get_field(const char *fn, const char *field, uint32_t *out) {
    static codec_kv_t kv[CODEC_KV_MAX];   /* static: the 8 KB host-task stack */
    const uint8_t *frame;
    size_t len;
    int i = index_of(fn), n;
    const codec_func_t *f = codec_func_by_name(fn);
    if (strcmp(fn, "lighting") == 0) {   /* this link's config latch answers like a field (serve._last) */
        codec_kv_t cfg[CALI_LCFG_N];
        int nc = cali_session_light_cfg(1, cfg);
        for (int k = 0; k < nc; k++)
            if (strcmp(cfg[k].name, field) == 0) {
                *out = cfg[k].value;
                return 1;
            }
    }
    if (i < 0 || !f || !cali_session_frame_live((size_t)i, &frame, &len)) return 0;
    n = codec_decode(f, frame, len, kv);
    for (int k = 0; k < n; k++)
        if (strcmp(kv[k].name, field) == 0) {
            *out = kv[k].value;
            return 1;
        }
    return 0;
}

static int have_frame(const char *fn) {
    const uint8_t *frame;
    size_t len;
    int i = index_of(fn);
    return i >= 0 && cali_session_frame_live((size_t)i, &frame, &len);
}

void cali_ctl_run_init(const cali_transport_t *t) {
    s_t = t;
    memset(&O, 0, sizeof O);
}

/* reason: a refusal's text (logged as calictl's "refused: …"), else why describes the result */
static void finish(int result, const char *why, const char *reason) {
    cali_ctl_done_t done = O.done;
    O.active = 0;
    O.pull = 0;
    O.done = NULL;
    if (result == CALI_CTL_OK) cali_log("control: %s sent", O.label);
    else if (reason) cali_log("control: %s refused: %s", O.label, reason);
    else cali_log("control: %s %s", O.label, why);
    if (done) done(result, reason);
}

int cali_ctl_submit(const char *fn, const char *what, const char *value, int64_t local_now,
                    cali_ctl_done_t done, const char **reason) {
    static cali_ctl_plan_t plan;   /* static: the 8 KB host-task stack */
    int pulling;
    *reason = NULL;
    if (O.active || O.inflight) {
        cali_log("control: %s/%s busy", fn, what);
        return CALI_CTL_BUSY;
    }
    cali_ctl_plan(fn, what, value, local_now, get_field, &plan);
    /* Not on the ESP, no such control, a malformed value: answered without state (the builders'
     * only state-dependent BAD, night_* with no cooler State, is refused by its gate first). A
     * frame or a gate's answer (a pull refusal too) waits for an armed link and this link's frame. */
    if ((plan.rc == CALI_CTL_OK || plan.rc == CALI_CTL_REFUSED) && (!cali_session_ready() || !have_frame(fn))) {
        cali_log("control: %s/%s not ready (no armed link or no state yet)", fn, what);
        return CALI_CTL_NOT_READY;
    }
    pulling = plan.rc == CALI_CTL_REFUSED && plan.pull;
    switch (plan.rc) {
    case CALI_CTL_OK:
        break;
    case CALI_CTL_REFUSED:
        if (pulling) {   /* R5: ask the unit for its config first, like serve and the app */
            cali_ctl_pull_plan(&plan);
            break;
        }
        /* fall through */
    case CALI_CTL_ELSEWHERE:
        *reason = plan.reason;
        cali_log("control: %s/%s refused: %s", fn, what, plan.reason);
        return plan.rc;
    case CALI_CTL_BAD_VALUE:
        cali_log("control: %s/%s bad value", fn, what);
        return plan.rc;
    default:
        cali_log("control: %s/%s no such control", fn, what);
        return CALI_CTL_NONE;
    }
    O.plan = plan;
    O.pull = pulling;
    O.active = 1;
    O.i = 0;
    O.next_at = s_now;
    O.deadline = s_now + CALI_CTL_DEADLINE_MS + (pulling ? CODEC_CONFIG_PULL_MS : 0u);
    O.done = done;
    O.local_now = local_now;
    O.submitted_at = s_now;
    snprintf(O.fn, sizeof O.fn, "%s", fn);
    snprintf(O.what, sizeof O.what, "%s", what);
    snprintf(O.value, sizeof O.value, "%s", value);
    snprintf(O.label, sizeof O.label, "%s/%s", fn, what);
    cali_log(pulling ? "control: %s pulling the lighting config" : "control: %s sending", O.label);
    return CALI_CTL_PENDING;
}

/* ponytail: the next frame goes out on the CALI_CTL_TICK_MS tick and the ACK's own time is unknown
 * (somewhere after the last tick), so a delayed frame waits delay + one tick from that tick: its gap
 * after the ACK is CODEC_FOLLOW_DELAY_MS..+CALI_CTL_TICK_MS (the app streams at ~500 ms), never less;
 * a timer per frame if the unit ever minds. */
void cali_ctl_tick(uint64_t now_ms) {
    const cali_ctl_frame_t *f;
    s_now = now_ms;
    if (!cali_session_link_up()) {
        O.inflight = 0;                         /* the transport dropped its queue with the link */
        if (O.active) finish(CALI_CTL_FAILED, "failed: link lost", NULL);
        return;
    }
    if (!O.active) return;
    if (now_ms >= O.deadline) {
        finish(CALI_CTL_TIMEOUT, "timed out", NULL);
        return;
    }
    if (O.pull == 2) {   /* the unit's reply frames latch as they arrive (session.c store) */
        static cali_ctl_plan_t plan;   /* static: the 8 KB host-task stack */
        /* the page's clock moved on while we waited (serve builds after its pull with its clock then) */
        cali_ctl_plan(O.fn, O.what, O.value, O.local_now + (int64_t)((now_ms - O.submitted_at) / 1000u), get_field,
                      &plan);
        if (plan.pull && now_ms < O.pull_until) return;
        O.pull = 0;
        if (plan.rc != CALI_CTL_OK) {   /* still unknown: WAKEUP_UNKNOWN; or the reply's own gate */
            finish(plan.rc, "bad value", plan.reason);
            return;
        }
        O.plan = plan;
        O.i = 0;
        O.next_at = now_ms;
        cali_log("control: %s sending", O.label);
    }
    if (O.inflight || now_ms < O.next_at) return;
    f = &O.plan.f[O.i];
    if (!cali_ctl_write_ok(f->chr, f->len)) {   /* the choke point, again at the transport */
        finish(CALI_CTL_FAILED, "failed: write not issued", NULL);
        return;
    }
    /* In flight BEFORE the call: the transport may complete the write inside it (WRITTEN with a
     * stack error for a request it cannot start), and that completion must count. */
    O.inflight = 1;
    if (s_t->write(f->chr, f->data, f->len) != 0) {
        O.inflight = 0;                         /* nonzero: nothing sent, no WRITTEN follows */
        if (O.active) finish(CALI_CTL_FAILED, "failed: write not issued", NULL);
    }
}

void cali_ctl_on_written(const cali_tevent_t *e) {
    if (!O.inflight) return;                    /* only the sequencer writes: it is ours */
    O.inflight = 0;
    if (!O.active) return;                      /* a timed-out command's late ACK */
    if (e->status != 0) {
        finish(CALI_CTL_FAILED, "failed: the unit refused the write", NULL);
        return;
    }
    if (++O.i == O.plan.n) {
        if (O.pull == 1) {   /* the pull is written: wait for the unit's reply (serve: 2.0 s) */
            O.pull = 2;
            O.pull_until = s_now + CODEC_CONFIG_PULL_MS;
            return;
        }
        finish(CALI_CTL_OK, NULL, NULL);
        return;
    }
    if (O.plan.f[O.i].delay_ms) O.next_at = s_now + O.plan.f[O.i].delay_ms + CALI_CTL_TICK_MS;
    else O.next_at = s_now;
}
