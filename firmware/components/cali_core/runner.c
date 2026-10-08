/* runner.c — the pairing flow: transport events -> pairing SM -> transport calls (#154).
 * Contract and mapping: include/cali_runner.h. Proven by tests/firmware/test_runner_fake.py.
 */
#include "cali_runner.h"

#include <stddef.h>

#include "codec_chars.h"
#include "pairing_consts.h"

void (*cali_runner_on_state)(const cali_pair_state_t *s, const char *address);
void (*cali_runner_on_other)(const cali_tevent_t *e);

static const cali_transport_t *s_t;
static cali_pair_state_t s_ps;
static uint64_t s_now_ms;      /* now_ms of the latest tick */
static uint64_t s_entered_ms;  /* when the current state was entered (a tick's now_ms) */

/* SM events waiting to be stepped. A transport call that fails, or a sink called from inside a
 * transport call, feeds an event while a step is running: queue it and step it afterwards, so
 * cali_pair_step is never re-entered and actions stay in SM order. One step feeds at most one
 * event per action, so a handful of slots is plenty; overflow drops (and the timeout recovers). */
#define QLEN 8
static struct { uint8_t ev; uint32_t arg; } s_q[QLEN];
static unsigned s_qhead, s_qlen;
static int s_draining;

static void feed(uint8_t ev, uint32_t arg);

static int same(const cali_pair_state_t *a, const cali_pair_state_t *b) {
    return a->st == b->st && a->attempts == b->attempts && a->error == b->error;
}

static void run_action(const cali_pair_action_t *a) {
    switch (a->act) {
    case PAIR_ACT_START_SCAN:
        (void)s_t->start_scan(CODEC_DEVICE_NAME);     /* can't start: the scanning timeout ends it */
        break;
    case PAIR_ACT_STOP_SCAN:
        (void)s_t->stop_scan();
        break;
    case PAIR_ACT_CONNECT:
        if (s_t->connect_found() != 0) feed(PAIR_EV_CONNECT_FAIL, 0);
        break;
    case PAIR_ACT_PAIR:
        if (s_t->pair() != 0) feed(PAIR_EV_PAIR_FAIL, 0);
        break;
    case PAIR_ACT_SEND_PASSKEY:
        if (s_t->inject_passkey(a->arg) != 0) feed(PAIR_EV_PAIR_FAIL, 0);
        break;
    case PAIR_ACT_VERIFY:
        if (s_t->read(CODEC_CHAR_AUTH) != 0) feed(PAIR_EV_VERIFY_FAIL, 0);
        break;
    case PAIR_ACT_PERSIST_BOND:
        break;                                        /* NimBLE's store callbacks already did it */
    case PAIR_ACT_DISCONNECT:
        (void)s_t->disconnect();                      /* may be down already: nothing to do then */
        break;
    case PAIR_ACT_REMOVE_BOND:
        if (s_t->remove_bond() == 0) feed(PAIR_EV_RESET_DONE, 0);  /* else: resetting times out */
        break;
    default:
        break;
    }
}

static void step(uint8_t ev, uint32_t arg) {
    cali_pair_state_t before = s_ps;
    cali_pair_action_t acts[CALI_PAIR_MAX_ACTIONS];
    int n = cali_pair_step(&s_ps, ev, arg, acts);
    int changed = !same(&before, &s_ps);
    if (changed) s_entered_ms = s_now_ms;
    for (int i = 0; i < n; i++) run_action(&acts[i]);
    if (changed && cali_runner_on_state)
        cali_runner_on_state(&s_ps, s_ps.st == PAIR_BONDED ? s_t->identity() : NULL);
}

static void feed(uint8_t ev, uint32_t arg) {
    if (s_qlen < QLEN) {
        unsigned tail = (s_qhead + s_qlen) % QLEN;
        s_q[tail].ev = ev;
        s_q[tail].arg = arg;
        s_qlen++;
    }
    if (s_draining) return;
    s_draining = 1;
    while (s_qlen) {
        uint8_t e = s_q[s_qhead].ev;
        uint32_t a = s_q[s_qhead].arg;
        s_qhead = (s_qhead + 1) % QLEN;
        s_qlen--;
        step(e, a);
    }
    s_draining = 0;
}

static int in_flow(uint8_t st) {
    return st == PAIR_SCANNING || st == PAIR_CONNECTING || st == PAIR_WAITING_PASSKEY ||
           st == PAIR_PAIRING || st == PAIR_VERIFYING || st == PAIR_RESETTING;
}

/* The SM event for a transport event, or -1 when the runner does not consume it. */
static int map_event(const cali_tevent_t *e, uint32_t *arg) {
    uint8_t st = s_ps.st;
    *arg = 0;
    if (!in_flow(st)) return -1;
    switch (e->ev) {
    case CALI_TEV_FOUND:        return PAIR_EV_DEVICE_FOUND;
    case CALI_TEV_CONNECTED:    return PAIR_EV_CONNECTED;
    case CALI_TEV_CONNECT_FAIL: return PAIR_EV_CONNECT_FAIL;
    case CALI_TEV_PASSKEY_REQ:  return PAIR_EV_PASSKEY_REQUESTED;
    case CALI_TEV_ENC_OK:       return PAIR_EV_PAIR_OK;
    case CALI_TEV_ENC_FAIL:     return PAIR_EV_PAIR_FAIL;
    case CALI_TEV_DISCONNECTED:
        if (st == PAIR_CONNECTING) return PAIR_EV_CONNECT_FAIL;
        /* waiting_passkey too, although the SM (Python parity) has no transition for it there:
         * consumed, not forwarded; the passkey timeout ends that flow. */
        if (st == PAIR_WAITING_PASSKEY || st == PAIR_PAIRING) return PAIR_EV_PAIR_FAIL;
        if (st == PAIR_VERIFYING) return PAIR_EV_VERIFY_FAIL;
        return -1;
    case CALI_TEV_READ:
        if (st != PAIR_VERIFYING || e->char_short != CODEC_CHAR_AUTH) return -1;
        if (e->status != 0) return PAIR_EV_VERIFY_FAIL;
        *arg = 1;                                     /* one readable char: the auth char */
        return PAIR_EV_VERIFY_OK;
    default:
        return -1;
    }
}

static void sink(const cali_tevent_t *e, void *ctx) {
    (void)ctx;
    uint32_t arg;
    int ev = map_event(e, &arg);
    if (ev >= 0) feed((uint8_t)ev, arg);
    else if (cali_runner_on_other) cali_runner_on_other(e);
}

void cali_runner_init(const cali_transport_t *t) {
    s_t = t;
    s_ps.st = PAIR_IDLE;
    s_ps.attempts = 0;
    s_ps.error = PAIR_ERR_NONE;
    s_qhead = s_qlen = 0;
    s_draining = 0;
    s_now_ms = s_entered_ms = 0;
    t->set_sink(sink, NULL);
}

void cali_runner_start(void) { feed(PAIR_EV_START, 0); }

void cali_runner_passkey(uint32_t pk) {
    if (s_ps.st == PAIR_WAITING_PASSKEY) feed(PAIR_EV_PASSKEY_ENTERED, pk);
}

void cali_runner_forget(void) { feed(PAIR_EV_RESET, 0); }

void cali_runner_cancel(void) { feed(PAIR_EV_CANCEL, 0); }

void cali_runner_tick(uint64_t now_ms) {
    s_now_ms = now_ms;
    uint16_t t = PAIR_TIMEOUT_S[s_ps.st];
    if (t && now_ms - s_entered_ms >= (uint64_t)t * 1000u) feed(PAIR_EV_TIMEOUT, 0);
}

const cali_pair_state_t *cali_runner_state(void) { return &s_ps; }
