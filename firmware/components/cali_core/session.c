/* session.c — the bonded session (#154). Contract: include/cali_session.h.
 * Proven by tests/firmware/test_session_fake.py (scripted transport) and, over NimBLE against the
 * Bumble fake unit, tests/firmware/test_host_e2e.py.
 */
#include "cali_session.h"

#include <stdio.h>
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
static uint64_t s_connect_started;   /* now_ms connect_bonded() was accepted (watchdog, #264) */
static uint64_t s_connected_at;
static uint64_t s_up_at;       /* now_ms the link came up (encrypted): the arm delay runs from here */

static int s_hb_on;
static uint32_t s_hb_ctr;
static uint64_t s_hb_next;

static int s_reconnect_pending;
static uint64_t s_reconnect_at;
static uint32_t s_backoff = CALI_SESSION_BACKOFF_MIN_MS;
static int s_kicked;           /* this loss: the unit hung up (0x13) on a read-all-complete link */
static uint64_t s_web_seen;    /* last /api/state served (cali_session_web_seen); 0 = never */

static int s_warming;          /* discovered + subscribed, heartbeat running: reads start at s_warm_until */
static uint64_t s_warm_until;
static int s_reading;          /* read-all in progress: s_read_idx is outstanding */
static size_t s_read_idx;
static int s_snapped;          /* this link's first read-all completed */
static int s_rereading;        /* the periodic water re-read is outstanding */
static uint64_t s_water_next;  /* now_ms of the next water re-read */

#define WATER_CHAR 0x1302u
#define WATER_GOOD_KEY "water_good"   /* persisted last-plausible 1302 frame (<=15 chars, NVS) */

/* The last PLAUSIBLE water (1302) frame — the baseline the stale-latch guard holds onto, persisted
 * so a reboot while parked keeps the real level instead of re-accepting the latch (serve.py's
 * _water_good). Cleared only when the bond changes to a different unit. */
static struct {
    uint8_t frame[CODEC_FRAME_MAX];
    size_t len;
    uint8_t have;
} s_wg;
static int s_water_held;       /* the served 1302 frame is the held baseline, not the latch read */

static struct {
    uint8_t frame[CODEC_FRAME_MAX];
    size_t len;
    uint8_t have;
    uint8_t live;              /* stored on the current link (cleared by link_up) */
} s_fr[CODEC_NCHARS];
/* READ completions and NOTIFYs both replace a function's frame: the last frame to arrive wins. The
 * app subscribes, then reads, and one decoder takes both (decompile 2026-10-07; calictl.device
 * R_READ_LAST_FRAME_WINS). No push is privileged over a read. */
/* now_ms (latest tick) of the latest stored frame; 0 = never (a store before the first tick is
 * stamped 1, so "stored" never reads as "never"). */
static uint64_t s_last_update;

/* [0] across links (shown), [1] this link only (gated on; cleared by link_up) */
static cali_light_cfg_t s_lcfg[2];
static const cali_light_cfg_t *s_lprev;
static codec_kv_t s_lkv[CODEC_KV_MAX];   /* static: the 8 KB host-task stack */
static int s_lnkv;

/* cali_ctl_get_t over (the previous latch, then the new frame) = lighting_config(prev, frame) */
static int lcfg_get(const char *fn, const char *field, uint32_t *out) {
    (void)fn;
    for (int k = 0; k < CALI_LCFG_N; k++)
        if ((s_lprev->have >> k & 1u) && strcmp(field, CALI_LCFG_KEYS[k]) == 0) {
            *out = s_lprev->v[k];
            return 1;
        }
    for (int k = 0; k < s_lnkv; k++)
        if (strcmp(s_lkv[k].name, field) == 0) {
            *out = s_lkv[k].value;
            return 1;
        }
    return 0;
}

/* cali_ctl_get_t over the new frame alone */
static int lkv_get(const char *fn, const char *field, uint32_t *out) {
    (void)fn;
    for (int k = 0; k < s_lnkv; k++)
        if (strcmp(s_lkv[k].name, field) == 0) {
            *out = s_lkv[k].value;
            return 1;
        }
    return 0;
}

/* Only store() calls this — a READ or a NOTIFY of the unit's own 1502, never a write of ours (R4).
 * Returns 1 when the frame reports the lamps + active profile (semantics.lighting_reports_state). */
static int latch_lighting(const uint8_t *data, size_t len) {
    s_lnkv = codec_decode(codec_func_by_name("lighting"), data, len, s_lkv);
    for (int w = 0; w < 2; w++) {
        cali_light_cfg_t next;
        s_lprev = &s_lcfg[w];
        cali_light_cfg(lcfg_get, &next);
        s_lcfg[w] = next;
    }
    return cali_light_reports_state(lkv_get);
}

/* 1 when the stored lighting frame i is a lamp-state 1502 frame. Reuses s_lkv as scratch: only
 * latch_lighting reads it, and it re-decodes first. */
static int stored_reports_state(size_t i) {
    s_lnkv = codec_decode(codec_func_by_name("lighting"), s_fr[i].frame, s_fr[i].len, s_lkv);
    return cali_light_reports_state(lkv_get);
}

static int index_of(uint16_t short_id) {
    for (size_t i = 0; i < CODEC_NCHARS; i++) {
        if (CODEC_CHARS[i].state_short == short_id) return (int)i;
    }
    return -1;
}

/* Fresh/waste tank LEVEL from a raw water frame. Returns 1 if FreshWaterLevel is present (sets
 * *fresh); has_waste/waste report WasteWaterLevel. Comparing the raw Level fields is equivalent to
 * freshness.py's liters comparison here — the unit is stable and Level maps monotonically to
 * liters, and the guard only ever asks "did fresh drop?" / "did grey move?". */
static int water_levels(const uint8_t *frame, size_t len, uint32_t *fresh, int *has_waste,
                        uint32_t *waste) {
    static codec_kv_t kv[CODEC_KV_MAX];   /* static: keep the 1 KB off the 8 KB host-task stack */
    const codec_func_t *wf = codec_func_by_name("water");
    int gotf = 0;
    *has_waste = 0;
    if (!wf) return 0;
    int n = codec_decode(wf, frame, len, kv);
    for (int k = 0; k < n; k++) {
        if (strcmp(kv[k].name, "FreshWaterLevel") == 0) {
            *fresh = kv[k].value;
            gotf = 1;
        } else if (strcmp(kv[k].name, "WasteWaterLevel") == 0) {
            *waste = kv[k].value;
            *has_waste = 1;
        }
    }
    return gotf;
}

/* The ramp-debounce candidate: a NEW level first seen at since_ms (calictl.freshness.settle_water). */
static struct {
    uint32_t fresh, waste;
    int has_waste, have;
    uint64_t since_ms;
} s_wpend;

/* calictl.freshness.settle_water, on the session clock: 1 = adopt the new water frame as the
 * baseline, 0 = hold (serve the baseline s_wg, or nothing on a cold start). The unit reports fresh
 * 1 L whenever it is NOT measuring and ramps 1 -> real value in ~4 s when it starts (BLE trace
 * 2026-10-09), so:
 *  - no fresh level -> adopt (can't judge);
 *  - no baseline: <= CALI_SESSION_WATER_LATCH_MAX_L is held (show nothing, not a fake 1 L);
 *  - baseline: a fresh DROP to <= the latch with the grey tank EXACTLY frozen (or grey unknown) is
 *    held (freshness.implausible_water_drop); the baseline's own (fresh, grey) is adopted at once;
 *  - any other (fresh, grey) is adopted only once seen unchanged for CALI_SESSION_WATER_SETTLE_MS. */
static int water_settle(const uint8_t *data, size_t len) {
    uint32_t fn_, fp_ = 0, wn_, wp_ = 0;
    int hwn, hwp = 0, hp;
    if (!water_levels(data, len, &fn_, &hwn, &wn_)) return 1;
    hp = s_wg.have && water_levels(s_wg.frame, s_wg.len, &fp_, &hwp, &wp_);
    if (!hp) {
        if (fn_ <= CALI_SESSION_WATER_LATCH_MAX_L) goto hold;
    } else if (fn_ < fp_ && fn_ <= CALI_SESSION_WATER_LATCH_MAX_L && (!hwp || !hwn || wn_ == wp_)) {
        goto hold;   /* the not-measuring latch; any grey movement is live */
    } else if (fn_ == fp_ && hwn == hwp && (!hwn || wn_ == wp_)) {
        s_wpend.have = 0;
        return 1;
    }
    if (!s_wpend.have || s_wpend.fresh != fn_ || s_wpend.has_waste != hwn ||
        (hwn && s_wpend.waste != wn_)) {
        s_wpend.fresh = fn_;
        s_wpend.waste = hwn ? wn_ : 0;
        s_wpend.has_waste = hwn;
        s_wpend.have = 1;
        s_wpend.since_ms = s_now;
    }
    if (s_now - s_wpend.since_ms >= CALI_SESSION_WATER_SETTLE_MS) {
        s_wpend.have = 0;
        return 1;
    }
    return 0;
hold:
    s_wpend.have = 0;
    return 0;
}

/* Show the persisted last-plausible water frame for char 1302 (nothing if there is no baseline). */
static void seed_water_frame(void) {
    int w = index_of(WATER_CHAR);
    if (w < 0 || !s_wg.have) return;
    memcpy(s_fr[w].frame, s_wg.frame, s_wg.len);
    s_fr[w].len = s_wg.len;
    s_fr[w].have = 1;
    s_fr[w].live = 0;   /* from an earlier link or NVS, never this link */
}

static int adopt_water(const uint8_t *data, size_t len) {
    s_water_held = 0;
    memcpy(s_wg.frame, data, len);
    s_wg.len = len;
    s_wg.have = 1;
    return cali_kv_set(WATER_GOOD_KEY, data, len);
}

int cali_session_water_seed(const uint8_t *frame, size_t len) {
    static const uint8_t zero[CODEC_FRAME_MAX];
    static codec_kv_t kv[CODEC_KV_MAX];
    const codec_func_t *wf = codec_func_by_name("water");
    if (!wf || len == 0 || len > CODEC_FRAME_MAX) return -1;
    /* Exactly the water frame length, from the codec: every field fits in len, not in len - 1. */
    int all = codec_decode(wf, zero, CODEC_FRAME_MAX, kv);
    if (codec_decode(wf, frame, len, kv) != all || codec_decode(wf, frame, len - 1, kv) == all)
        return -1;
    int persisted = adopt_water(frame, len);
    seed_water_frame();
    return persisted == CALI_KV_OK ? 0 : -2;   /* -2: shown now, but will not survive a reboot */
}

static void store(size_t i, const uint8_t *data, size_t len) {
    if (len > CODEC_FRAME_MAX) {
        cali_log("session: %s frame %u bytes, keeping the first %u", CODEC_CHARS[i].function,
                 (unsigned)len, (unsigned)CODEC_FRAME_MAX);
        len = CODEC_FRAME_MAX;
    }
    if (CODEC_CHARS[i].state_short == WATER_CHAR) {
        /* Not measuring (the 1 L latch) or mid-ramp: hold the last plausible reading instead of
         * serving it (serve.py's guard); with no baseline yet, store nothing (no fake 1 L). */
        if (!water_settle(data, len)) {
            s_water_held = 1;
            if (!s_wg.have) return;
            data = s_wg.frame;
            len = s_wg.len;
        } else {
            adopt_water(data, len);   /* a plausible read -> new baseline, persisted */
        }
    }
    /* A 1502 read returns the unit's LAST frame of any kind: a config/ack frame (door echo, save ack,
     * REQUEST_CONFIG reply, wake-up) only feeds the latch and keeps the stored lamps (#284 =
     * semantics.lighting_merge; with no lamp-state frame stored it is stored as it is). */
    int keep = strcmp(CODEC_CHARS[i].function, "lighting") == 0 && !latch_lighting(data, len) && s_fr[i].have &&
               stored_reports_state(i);
    if (!keep) {
        if (len) memcpy(s_fr[i].frame, data, len);
        s_fr[i].len = len;
        s_fr[i].have = 1;
    }
    s_fr[i].live = 1;
    s_last_update = s_now ? s_now : 1;
}

/* The unit the stored frames + config latch came from: its bonded identity ("" = none). */
static char s_unit[32];   /* "AA:BB:CC:DD:EE:FF" with room to spare */

/* The bond's identity changed (forget, or a bond to another unit): drop everything the old unit
 * reported, so another unit's state or wake-up config is never shown for this one. */
static void unit_check(void) {
    const char *id = s_t->identity();
    if (!id) id = "";
    if (strncmp(id, s_unit, sizeof s_unit) == 0) return;
    int had = s_unit[0] != 0;   /* we knew a DIFFERENT unit before (re-pair / forget), not boot */
    snprintf(s_unit, sizeof s_unit, "%s", id);
    memset(s_fr, 0, sizeof s_fr);
    memset(s_lcfg, 0, sizeof s_lcfg);
    s_last_update = 0;
    if (had) {   /* another unit's water isn't ours; a plain boot ("" -> id) keeps the baseline */
        memset(&s_wg, 0, sizeof s_wg);
        memset(&s_wpend, 0, sizeof s_wpend);
        s_water_held = 0;
        cali_kv_erase(WATER_GOOD_KEY);
    }
    seed_water_frame();   /* same unit: keep showing its last-known water right away */
}

static void link_clear(void) {
    s_link = LINK_DOWN;
    s_hb_on = 0;
    s_warming = 0;
    s_reading = 0;
    s_snapped = 0;
    s_rereading = 0;
}

/* HCI 0x13 "remote user terminated connection": raw (the fake, btmon) or NimBLE host-encoded
 * (0x200 + 0x13 = 531, what ble_nimble.c forwards from disconnect.reason). */
static int remote_terminated(int status) { return status == 0x13 || status == 0x213; }

/* Somebody is watching the page: /api/state was served within the window (the shared UI polls it
 * while open). Then a kicked link reconnects on the fast backoff, keeping the page ~live. */
static int viewer_active(void) {
    return s_web_seen && s_now - s_web_seen < CALI_SESSION_VIEWER_ACTIVE_MS;
}

static void schedule_reconnect(void) {
    int kicked = s_kicked;
    s_kicked = 0;
    if (!s_active) return;
    if (!s_t->has_bond()) {                           /* bond gone (forget): nothing to reconnect to */
        s_active = 0;
        return;
    }
    s_reconnect_pending = 1;
    if (kicked && !viewer_active()) {
        /* The parked unit shed an idle held guest (#264, field 2026-10-09): reconnect on calictl's
         * poll cadence — a unit-approved duty cycle — and leave the backoff state untouched. */
        s_reconnect_at = s_now + CALI_SESSION_KICKED_RECONNECT_MS;
        cali_log("session: reconnect in %u ms", (unsigned)CALI_SESSION_KICKED_RECONNECT_MS);
        return;
    }
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
    s_rereading = 0;
    unit_check();
    for (size_t i = 0; i < CODEC_NCHARS; i++) s_fr[i].live = 0;   /* the last link's frames stay shown,
                                                                     never gated on */
    memset(&s_lcfg[1], 0, sizeof s_lcfg[1]);   /* the last link's config stays shown, never gated on */
    int rc = s_t->discover();
    if (rc != 0) {
        cali_log("session: discover failed %d", rc);
        link_lost();
    }
}

static void read_next(void) {
    while (s_read_idx < CODEC_NCHARS) {
        int rc = s_t->read(CODEC_CHARS[s_read_idx].state_short);
        if (rc == 0) return;                          /* READ comes back */
        cali_log("session: read %s failed %d", CODEC_CHARS[s_read_idx].function, rc);
        s_read_idx++;
    }
    s_reading = 0;
    s_snapped = 1;
    s_water_next = s_now + CALI_SESSION_WATER_REREAD_MS;
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
    int w;
    if (s_rereading && e->char_short == WATER_CHAR && (w = index_of(WATER_CHAR)) >= 0) {
        s_rereading = 0;
        if (e->status == 0) {
            store((size_t)w, e->data, e->len);
            cali_console_snapshot(s_now);
        } else {
            cali_log("session: read water status %d", e->status);
        }
        return;
    }
    if (!s_reading || e->char_short != CODEC_CHARS[s_read_idx].state_short) return;
    if (e->status == 0) {
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
        if (e->ev == CALI_TEV_DISCONNECTED && s_snapped && remote_terminated(e->status)) s_kicked = 1;
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
    s_kicked = 0;
    s_web_seen = 0;
    memset(s_fr, 0, sizeof s_fr);
    memset(s_lcfg, 0, sizeof s_lcfg);
    s_last_update = 0;
    s_unit[0] = 0;
    /* Restore the last-plausible water from NVS so a reboot while parked shows the real level, not
     * the latch, and the guard has a baseline on the first read (serve.py's persisted _water_good). */
    memset(&s_wg, 0, sizeof s_wg);
    memset(&s_wpend, 0, sizeof s_wpend);
    s_water_held = 0;
    size_t wl = sizeof s_wg.frame;
    if (cali_kv_get(WATER_GOOD_KEY, s_wg.frame, &wl) == CALI_KV_OK) {
        s_wg.len = wl;
        s_wg.have = 1;
    }
    seed_water_frame();
    cali_ctl_run_init(t);
    cali_runner_on_other = on_event;
}

static void connect_now(void) {
    int rc = s_t->connect_bonded();
    if (rc == 0) {
        s_link = LINK_CONNECTING;
        s_connect_started = s_now;
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
    s_kicked = 0;
    if (s_link != LINK_DOWN) (void)s_t->disconnect();   /* the unit has one slot: free it */
    link_clear();
    unit_check();   /* a forget lands here with the bond gone */
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
    /* Re-read water on calictl's poll cadence: a latch served at connect is corrected on the link. */
    if (s_link == LINK_UP && s_snapped && !s_reading && !s_rereading && now_ms >= s_water_next) {
        s_water_next = now_ms + CALI_SESSION_WATER_REREAD_MS;
        int rc = s_t->read(WATER_CHAR);
        if (rc == 0)
            s_rereading = 1;
        else
            cali_log("session: read water failed %d", rc);
    }
    /* No verdict on the connect attempt at all — the transport's terminal event got lost (e.g. a
     * connect silently cancelled under a scan): drop and retry, never wedge in CONNECTING (#264,
     * field night 2026-10-08: 45 min link-down with the unit awake). */
    if (s_link == LINK_CONNECTING && now_ms - s_connect_started >= CALI_SESSION_CONNECT_TIMEOUT_MS) {
        cali_log("session: no connect verdict after %u ms", (unsigned)CALI_SESSION_CONNECT_TIMEOUT_MS);
        link_lost();
        return;
    }
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

void cali_session_connect_now(void) {
    if (s_active && s_link == LINK_DOWN && s_reconnect_pending) s_reconnect_at = s_now;
}

void cali_session_web_seen(void) { s_web_seen = s_now ? s_now : 1; }

int cali_session_water_held(void) { return s_water_held; }

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

int cali_session_light_cfg(int live, codec_kv_t out[CALI_LCFG_N]) {
    const cali_light_cfg_t *c = &s_lcfg[live ? 1 : 0];
    int n = 0;
    for (int k = 0; k < CALI_LCFG_N; k++)
        if (c->have >> k & 1u) {
            out[n].name = CALI_LCFG_KEYS[k];
            out[n].value = c->v[k];
            out[n].supplied = 1;
            n++;
        }
    return n;
}

uint64_t cali_session_last_update_ms(void) { return s_last_update; }
