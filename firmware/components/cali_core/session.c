/* session.c — the bonded session (#154). Contract: include/cali_session.h.
 * Proven by tests/firmware/test_session_fake.py (scripted transport) and, over NimBLE against the
 * Bumble fake unit, tests/firmware/test_host_e2e.py.
 */
#include "cali_session.h"

#include <string.h>

#include "cali_console.h"
#include "cali_control.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "codec.h"
#include "codec_chars.h"

enum { LINK_DOWN, LINK_CONNECTING, LINK_CONNECTED, LINK_UP };

static const cali_transport_t *s_t;
static int s_active;           /* keep (or re-establish) a link for the stored bond */
static int s_link;
static uint64_t s_now;         /* now_ms of the latest tick */
static uint64_t s_connected_at;
static uint64_t s_up_at;       /* now_ms the link came up (encrypted): the arm delay runs from here */

static int s_hb_on;
static uint32_t s_hb_ctr;
static uint64_t s_hb_next;

static int s_reconnect_pending;
static uint64_t s_reconnect_at;
static uint32_t s_backoff = CALI_SESSION_BACKOFF_MIN_MS;

static int s_warming;          /* discovered + subscribed, heartbeat running: reads start at s_warm_until */
static uint64_t s_warm_until;
static int s_reading;          /* read-all in progress: s_read_idx is outstanding */
static size_t s_read_idx;
static int s_snapped;          /* this link's first read-all completed */

static struct {
    uint8_t frame[CODEC_FRAME_MAX];
    size_t len;
    uint8_t have;
    uint8_t live;              /* stored on the current link (cleared by link_up) */
} s_fr[CODEC_NCHARS];
/* Pushed on this link (since its read-all started with the subscribe): the NOTIFY frame is fresher
 * than the unit's read latch, so the read-all neither reads nor overwrites it (calictl.device
 * read_all: "a fresh notification beats the stale latch"). */
static uint8_t s_pushed[CODEC_NCHARS];
/* now_ms (latest tick) of the latest stored frame; 0 = never (a store before the first tick is
 * stamped 1, so "stored" never reads as "never"). */
static uint64_t s_last_update;

static int index_of(uint16_t short_id) {
    for (size_t i = 0; i < CODEC_NCHARS; i++) {
        if (CODEC_CHARS[i].state_short == short_id) return (int)i;
    }
    return -1;
}

static void store(size_t i, const uint8_t *data, size_t len) {
    if (len > CODEC_FRAME_MAX) {
        cali_log("session: %s frame %u bytes, keeping the first %u", CODEC_CHARS[i].function,
                 (unsigned)len, (unsigned)CODEC_FRAME_MAX);
        len = CODEC_FRAME_MAX;
    }
    if (len) memcpy(s_fr[i].frame, data, len);
    s_fr[i].len = len;
    s_fr[i].have = 1;
    s_fr[i].live = 1;
    s_last_update = s_now ? s_now : 1;
}

static void link_clear(void) {
    s_link = LINK_DOWN;
    s_hb_on = 0;
    s_warming = 0;
    s_reading = 0;
    s_snapped = 0;
}

static void schedule_reconnect(void) {
    if (!s_active) return;
    if (!s_t->has_bond()) {                           /* bond gone (forget): nothing to reconnect to */
        s_active = 0;
        return;
    }
    s_reconnect_pending = 1;
    s_reconnect_at = s_now + s_backoff;
    cali_log("session: reconnect in %u ms", (unsigned)s_backoff);
    s_backoff = s_backoff * 2 > CALI_SESSION_BACKOFF_MAX_MS ? CALI_SESSION_BACKOFF_MAX_MS : s_backoff * 2;
}

/* The link is unusable (for a reason already logged): drop it and try again later. */
static void link_lost(void) {
    int was = s_link;
    link_clear();
    if (was != LINK_DOWN) (void)s_t->disconnect();   /* may be down already: silent either way */
    schedule_reconnect();
}

static void link_up(void) {
    s_link = LINK_UP;
    s_up_at = s_now;
    s_reconnect_pending = 0;
    s_backoff = CALI_SESSION_BACKOFF_MIN_MS;
    s_hb_on = 1;
    s_hb_ctr = CODEC_HEARTBEAT_START;
    s_hb_next = s_now;                                /* first beat on the next tick */
    s_warming = 0;
    s_reading = 0;
    s_snapped = 0;
    memset(s_pushed, 0, sizeof s_pushed);
    for (size_t i = 0; i < CODEC_NCHARS; i++) s_fr[i].live = 0;   /* the last link's frames stay shown,
                                                                     never gated on */
    int rc = s_t->discover();
    if (rc != 0) {
        cali_log("session: discover failed %d", rc);
        link_lost();
    }
}

static void read_next(void) {
    while (s_read_idx < CODEC_NCHARS) {
        if (s_pushed[s_read_idx]) {                   /* a fresh push already holds this function */
            s_read_idx++;
            continue;
        }
        int rc = s_t->read(CODEC_CHARS[s_read_idx].state_short);
        if (rc == 0) return;                          /* READ comes back */
        cali_log("session: read %s failed %d", CODEC_CHARS[s_read_idx].function, rc);
        s_read_idx++;
    }
    s_reading = 0;
    s_snapped = 1;
    cali_console_snapshot(s_now);
}

static void on_discovered(int status) {
    if (status != 0) {
        cali_log("session: discovery failed %d", status);
        link_lost();
        return;
    }
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        (void)s_t->subscribe(CODEC_CHARS[i].state_short);   /* refused for a char without NOTIFY */
    /* calictl.device.read_all: the heartbeat runs CODEC_HEARTBEAT_WARMUP_MS before the read pass
     * ("let the liveness register + sensors refresh"); cali_session_tick starts the reads. */
    s_warming = 1;
    s_warm_until = s_now + CODEC_HEARTBEAT_WARMUP_MS;
}

static void start_reads(void) {
    s_warming = 0;
    s_reading = 1;
    s_read_idx = 0;
    read_next();
}

static void on_read(const cali_tevent_t *e) {
    if (!s_reading || e->char_short != CODEC_CHARS[s_read_idx].state_short) return;
    if (s_pushed[s_read_idx]) {
        /* pushed while this read was outstanding: keep the fresher NOTIFY frame */
    } else if (e->status == 0) {
        store(s_read_idx, e->data, e->len);
    } else {
        cali_log("session: read %s status %d", CODEC_CHARS[s_read_idx].function, e->status);
    }
    s_read_idx++;
    read_next();
}

static void on_event(const cali_tevent_t *e) {
    if (!s_active) return;
    int i;
    switch (e->ev) {
    case CALI_TEV_CONNECTED:
        if (s_link == LINK_CONNECTING) {
            s_link = LINK_CONNECTED;
            s_connected_at = s_now;
        }
        break;
    case CALI_TEV_ENC_OK:
        if (s_link == LINK_CONNECTED) link_up();
        break;
    case CALI_TEV_CONNECT_FAIL:
    case CALI_TEV_ENC_FAIL:
    case CALI_TEV_DISCONNECTED:
        if (s_link == LINK_DOWN) break;
        cali_log("session: link lost (event %d, status %d)", (int)e->ev, e->status);
        if (e->ev == CALI_TEV_DISCONNECTED || e->ev == CALI_TEV_CONNECT_FAIL) link_clear();
        link_lost();
        break;
    case CALI_TEV_DISCOVERED:
        if (s_link == LINK_UP) on_discovered(e->status);
        break;
    case CALI_TEV_READ:
        if (s_link == LINK_UP) on_read(e);
        break;
    case CALI_TEV_NOTIFY:
        if (s_link != LINK_UP || (i = index_of(e->char_short)) < 0) break;
        store((size_t)i, e->data, e->len);
        s_pushed[i] = 1;
        if (s_snapped) cali_console_snapshot(s_now);
        break;
    case CALI_TEV_HEARTBEAT:
        if (s_link == LINK_UP && e->status != 0) {
            cali_log("session: heartbeat failed %d", e->status);
            link_lost();
        }
        break;
    case CALI_TEV_WRITTEN:
        cali_ctl_on_written(e);
        break;
    default:
        break;
    }
}

void cali_session_init(const cali_transport_t *t) {
    s_t = t;
    s_active = 0;
    link_clear();
    s_reconnect_pending = 0;
    s_backoff = CALI_SESSION_BACKOFF_MIN_MS;
    memset(s_fr, 0, sizeof s_fr);
    s_last_update = 0;
    cali_ctl_run_init(t);
    cali_runner_on_other = on_event;
}

static void connect_now(void) {
    int rc = s_t->connect_bonded();
    if (rc == 0) {
        s_link = LINK_CONNECTING;
    } else {
        cali_log("session: connect_bonded failed %d", rc);
        schedule_reconnect();
    }
}

void cali_session_boot(void) {
    if (!s_t->has_bond()) return;
    s_active = 1;
    connect_now();
}

void cali_session_on_bonded(void) {
    s_active = 1;
    link_up();
}

void cali_session_stop(void) {
    s_active = 0;
    s_reconnect_pending = 0;
    s_backoff = CALI_SESSION_BACKOFF_MIN_MS;
    if (s_link != LINK_DOWN) (void)s_t->disconnect();   /* the unit has one slot: free it */
    link_clear();
}

void cali_session_tick(uint64_t now_ms) {
    s_now = now_ms;
    cali_ctl_tick(now_ms);   /* the control sequencer runs on the session's tick, even with no link */
    if (!s_active) return;
    if (s_link == LINK_UP && s_hb_on && now_ms >= s_hb_next) {
        uint32_t n = s_hb_ctr++;
        s_hb_next = now_ms - s_hb_next >= CODEC_HEARTBEAT_PERIOD_MS ? now_ms + CODEC_HEARTBEAT_PERIOD_MS
                                                                     : s_hb_next + CODEC_HEARTBEAT_PERIOD_MS;
        int rc = s_t->write_heartbeat(n);
        if (rc != 0) {
            cali_log("session: heartbeat not written %d", rc);
            link_lost();
            return;
        }
    }
    if (s_link == LINK_UP && s_warming && now_ms >= s_warm_until) start_reads();
    if (s_link == LINK_CONNECTED && now_ms - s_connected_at >= CALI_SESSION_ENC_TIMEOUT_MS) {
        cali_log("session: link not encrypted after %u ms", (unsigned)CALI_SESSION_ENC_TIMEOUT_MS);
        link_lost();
        return;
    }
    if (s_link == LINK_DOWN && s_reconnect_pending && now_ms >= s_reconnect_at) {
        s_reconnect_pending = 0;
        if (!s_t->has_bond()) {
            s_active = 0;
            return;
        }
        connect_now();
    }
}

int cali_session_active(void) { return s_active; }

int cali_session_link_up(void) { return s_link == LINK_UP; }

int cali_session_ready(void) {
    return s_link == LINK_UP && s_snapped && s_now - s_up_at >= CODEC_ARM_DELAY_MS;
}

int cali_session_frame(size_t i, const uint8_t **frame, size_t *len) {
    if (i >= CODEC_NCHARS || !s_fr[i].have) return 0;
    *frame = s_fr[i].frame;
    *len = s_fr[i].len;
    return 1;
}

int cali_session_frame_live(size_t i, const uint8_t **frame, size_t *len) {
    if (i >= CODEC_NCHARS || !s_fr[i].live) return 0;
    return cali_session_frame(i, frame, len);
}

uint64_t cali_session_last_update_ms(void) { return s_last_update; }
