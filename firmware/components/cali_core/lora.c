/* lora.c — LoRaWAN telemetry, the platform-free part (R_FW_LORA). See cali_lora.h. */
#include "cali_lora.h"

#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_platform.h"
#include "cali_session.h"
#include "cali_time.h"

/* ---- the AT line parser ---- */

static int starts(const char *s, const char *p) { return strncmp(s, p, strlen(p)) == 0; }

cali_rak_event_t cali_rak_parse(const char *in) {
    cali_rak_event_t e;
    char line[160];
    memset(&e, 0, sizeof e);
    snprintf(line, sizeof line, "%s", in);
    line[strcspn(line, "\r\n")] = 0;
    if (!strcmp(line, "OK")) e.kind = CALI_RAK_OK;
    else if (!strcmp(line, "AT_BUSY_ERROR")) e.kind = CALI_RAK_BUSY;
    else if (!strcmp(line, "AT_NO_NETWORK_JOINED")) e.kind = CALI_RAK_NOT_JOINED;
    else if (starts(line, "AT_") && strstr(line, "ERROR")) e.kind = CALI_RAK_ERROR;
    else if (!strcmp(line, "+EVT:JOINED")) e.kind = CALI_RAK_JOINED;
    else if (starts(line, "+EVT:JOIN_FAILED") || starts(line, "+EVT:JOIN FAILED")) e.kind = CALI_RAK_JOIN_FAILED;
    else if (!strcmp(line, "+EVT:TX_DONE")) e.kind = CALI_RAK_TX_DONE;
    /* the RUI3 manual shows commas, RUI 4.2.4 sends colons */
    else if (sscanf(line, "+EVT:LINKCHECK:%d%*[,:]%d%*[,:]%d%*[,:]%d%*[,:]%d", &e.lc_status, &e.lc_margin,
                    &e.lc_gateways, &e.rssi, &e.snr) == 5)
        e.kind = CALI_RAK_LINKCHECK;
    else if (sscanf(line, "+EVT:RX_%*c:%d:%d", &e.rssi, &e.snr) == 2) {
        const char *cast = strstr(line, "CAST:");   /* UNICAST / MULTICAST, then <port>:<hex> */
        e.kind = CALI_RAK_RX;
        if (cast && sscanf(cast + 5, "%d", &e.port) == 1) {
            const char *hex = strchr(cast + 5, ':');
            for (hex = hex ? hex + 1 : ""; hex[0] && hex[1] && e.payload_len < sizeof e.payload; hex += 2) {
                char b[3] = {hex[0], hex[1], 0};
                e.payload[e.payload_len++] = (uint8_t)strtoul(b, NULL, 16);
            }
        }
    } else if (starts(line, "AT+") && strchr(line, '=')) {
        e.kind = CALI_RAK_VALUE;
        snprintf(e.value, sizeof e.value, "%s", strchr(line, '=') + 1);
    }
    return e;
}

const char *cali_rak_kind_name(cali_rak_kind_t k) {
    static const char *const n[] = {"NONE", "OK", "ERROR", "BUSY", "NOT_JOINED", "JOINED", "JOIN_FAILED",
                                    "TX_DONE", "LINKCHECK", "RX", "VALUE"};
    return (unsigned)k < sizeof n / sizeof n[0] ? n[k] : "?";
}

cali_lora_outcome_t cali_lora_outcome(cali_rak_kind_t status, int tx_done) {
    /* only a refusal proves nothing was sent; no answer at all may still be a lost OK */
    int may = !(status == CALI_RAK_BUSY || status == CALI_RAK_ERROR || status == CALI_RAK_NOT_JOINED);
    cali_lora_outcome_t o = {may, may && tx_done};
    return o;
}

/* ---- airtime and budgets ---- */

uint32_t cali_lora_airtime_ms(unsigned phy_len, unsigned sf) {
    /* Semtech AN1200.13: 125 kHz, CR 4/5, preamble 8, explicit header, CRC on, LDRO for SF >= 11 */
    double ts = (double)(1u << sf) / 125.0;
    int de = sf >= 11;
    double n = ceil((8.0 * phy_len - 4.0 * sf + 28 + 16) / (4.0 * ((double)sf - 2 * de))) * 5;
    if (n < 0) n = 0;
    return (uint32_t)((8 + 4.25 + 8 + n) * ts);
}

unsigned cali_lora_max_payload(unsigned sf) { return sf >= 10 ? 51 : sf == 9 ? 115 : 222; }

void cali_lora_budget_init(cali_lora_budget_t *b) { memset(b, 0, sizeof *b); }

static void up_expire(cali_lora_budget_t *b, uint32_t now_s) {
    while (b->up_n && now_s - b->up[b->up_head].t_s >= CALI_LORA_DAY_S) {
        b->up_head = (b->up_head + 1) % CALI_LORA_UP_RING;
        b->up_n--;
    }
}

void cali_lora_uplink_add(cali_lora_budget_t *b, uint32_t now_s, uint32_t ms, int confirmed) {
    up_expire(b, now_s);
    if (b->up_n == CALI_LORA_UP_RING) {   /* full: drop the oldest */
        b->up_head = (b->up_head + 1) % CALI_LORA_UP_RING;
        b->up_n--;
    }
    unsigned i = (b->up_head + b->up_n++) % CALI_LORA_UP_RING;
    b->up[i].t_s = now_s;
    b->up[i].ms = ms;
    b->up[i].confirmed = (uint8_t)(confirmed != 0);
}

static uint32_t up_sum(cali_lora_budget_t *b, uint32_t now_s, int only_confirmed) {
    uint32_t sum = 0;
    up_expire(b, now_s);
    for (unsigned k = 0; k < b->up_n; k++) {
        unsigned i = (b->up_head + k) % CALI_LORA_UP_RING;
        if (!only_confirmed || b->up[i].confirmed) sum += b->up[i].ms;
    }
    return sum;
}

uint32_t cali_lora_uplink_used_ms(cali_lora_budget_t *b, uint32_t now_s) { return up_sum(b, now_s, 0); }
uint32_t cali_lora_uplink_confirmed_ms(cali_lora_budget_t *b, uint32_t now_s) { return up_sum(b, now_s, 1); }

uint32_t cali_lora_uplink_room_ms(cali_lora_budget_t *b, uint32_t now_s) {
    uint32_t used = up_sum(b, now_s, 0);
    if (b->up_n == CALI_LORA_UP_RING) return 0;   /* full: one more would drop an unexpired uplink's airtime */
    return used >= CALI_LORA_UPLINK_MS ? 0 : CALI_LORA_UPLINK_MS - used;
}

static void dl_expire(cali_lora_budget_t *b, uint32_t now_s) {
    while (b->dl_n && now_s - b->dl[b->dl_head] >= CALI_LORA_DAY_S) {
        b->dl_head = (b->dl_head + 1) % CALI_LORA_DL_RING;
        b->dl_n--;
    }
}

int cali_lora_downlink_take(cali_lora_budget_t *b, uint32_t now_s) {
    dl_expire(b, now_s);
    if (b->dl_n >= CALI_LORA_DOWNLINKS) return 0;
    b->dl[(b->dl_head + b->dl_n++) % CALI_LORA_DL_RING] = now_s;
    return 1;
}

unsigned cali_lora_downlinks_used(cali_lora_budget_t *b, uint32_t now_s) {
    dl_expire(b, now_s);
    return b->dl_n;
}

uint32_t cali_lora_next_interval_s(cali_lora_budget_t *b, uint32_t now_s, unsigned sf, unsigned len, unsigned share) {
    uint32_t air = cali_lora_airtime_ms(len + CALI_LORA_OVERHEAD, sf);
    uint32_t share_ms = (uint32_t)((uint64_t)CALI_LORA_UPLINK_MS * share / 100);
    /* steady state: at most per_day = floor(share / air) packets in any rolling day. An uplink ages out
     * when now - t >= CALI_LORA_DAY_S, so with spacing I a window holds floor((DAY - 1) / I) + 1 of
     * them, at most per_day when I = ceil(DAY / per_day). */
    uint32_t per_day = air ? share_ms / air : 1;
    if (!per_day) per_day = 1;
    uint32_t wait = (CALI_LORA_DAY_S + per_day - 1) / per_day;
    if (wait < CALI_LORA_BASE_S) wait = CALI_LORA_BASE_S;
    /* and never past the rolling budget: wait until enough old uplinks age out */
    uint32_t used = cali_lora_uplink_used_ms(b, now_s);
    for (unsigned k = 0; k < b->up_n && used + air > CALI_LORA_UPLINK_MS; k++) {
        unsigned i = (b->up_head + k) % CALI_LORA_UP_RING;
        used -= b->up[i].ms;
        uint32_t until = b->up[i].t_s + CALI_LORA_DAY_S - now_s;
        if (until > wait) wait = until;
    }
    return wait;
}

uint32_t cali_lora_join_backoff_s(unsigned attempt) { return attempt >= 6 ? 3600 : 60u << attempt; }

/* ---- snapshot and packers ---- */

static int char_index(const char *fn) {
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        if (strcmp(CODEC_CHARS[i].function, fn) == 0) return (int)i;
    return -1;
}

void cali_lora_snapshot(cali_lora_snap_t *s, uint64_t now_ms) {
    memset(s, 0, sizeof *s);
    for (size_t i = 0; i < CODEC_NCHARS; i++) {
        const uint8_t *f;
        size_t len;
        if (!cali_session_frame(i, &f, &len)) continue;
        if (len > CODEC_FRAME_MAX) len = CODEC_FRAME_MAX;
        memcpy(s->frame[i], f, len);
        s->len[i] = (uint8_t)len;
    }
    /* a reading older than the energy counters' 60 s is "none": an offline sensor never looks current */
    if (cali_sensors_first("victron_mppt", &s->mppt) == 0 && now_ms - s->mppt.at_ms > CALI_ENERGY_MAX_AGE_MS)
        s->mppt.n = 0;
    if (cali_sensors_first("victron_battery", &s->bsense) == 0 && now_ms - s->bsense.at_ms > CALI_ENERGY_MAX_AGE_MS)
        s->bsense.n = 0;
    s->ctr = *cali_energy_counters();
    s->boot = cali_time_boot();
    s->uptime_s = (uint32_t)(now_ms / 1000);
    s->enabled = cali_exp_lora() != 0;
}

static void be(uint8_t *p, uint32_t v, int n) {
    for (int i = n - 1; i >= 0; i--, v >>= 8) p[i] = (uint8_t)v;
}

/* a raw frame into a fixed slot: its bytes, 0xFF past its end (and for no frame at all) */
static void put_frame(uint8_t *out, size_t slot, const cali_lora_snap_t *s, const char *fn) {
    int i = char_index(fn);
    size_t n = i >= 0 ? s->len[i] : 0;
    memset(out, 0xFF, slot);
    memcpy(out, s->frame[i < 0 ? 0 : i], n < slot ? n : slot);
}

/* a reading's field times k, rounded, or the all-ones "none" */
static uint32_t scaled(const cali_sensor_reading_t *r, int field, double k, uint32_t none) {
    if (field >= r->n || !isfinite(r->f[field])) return none;
    return (uint32_t)(int32_t)lround(r->f[field] * k);
}

size_t cali_lora_pack_energy(uint8_t *out, const cali_lora_snap_t *s) {
    out[0] = 1;
    be(out + 1, s->boot & 0xFFFF, 2);
    put_frame(out + 3, 22, s, "energy");
    put_frame(out + 25, 6, s, "water");
    /* victron_mppt fields: 0 battery_current, 2 charge_state, 3 error_code, 5 pv_power, 6 yield_today_kwh */
    be(out + 31, scaled(&s->mppt, 0, 10, 0xFFFF), 2);
    be(out + 33, scaled(&s->mppt, 5, 1, 0xFFFF), 2);
    be(out + 35, scaled(&s->mppt, 6, 100, 0xFFFF), 2);
    out[37] = (uint8_t)scaled(&s->mppt, 2, 1, 0xFF);
    out[38] = (uint8_t)scaled(&s->mppt, 3, 1, 0xFF);
    be(out + 39, scaled(&s->bsense, 0, 100, 0xFFFF), 2);   /* victron_battery 0: battery_voltage */
    be(out + 41, s->ctr.solar_dwh & CALI_ENERGY_MASK, 3);
    be(out + 44, s->ctr.load_dwh & CALI_ENERGY_MASK, 3);
    be(out + 47, (uint32_t)s->ctr.batt_net_dwh & CALI_ENERGY_MASK, 3);
    return CALI_LORA_ENERGY_LEN;
}

size_t cali_lora_pack_daily(uint8_t *out, const cali_lora_snap_t *s, const char *fw) {
    memset(out, 0, CALI_LORA_DAILY_LEN);
    out[0] = 1;
    be(out + 1, s->boot & 0xFFFF, 2);
    be(out + 3, s->uptime_s, 4);
    put_frame(out + 7, 9, s, "general");
    for (int k = 0; k < 8 && fw[k]; k++) out[16 + k] = (uint8_t)fw[k];
    return CALI_LORA_DAILY_LEN;
}

static int untracked(size_t i) {
    const char *fn = CODEC_CHARS[i].function;
    return !strcmp(fn, "energy") || !strcmp(fn, "water") || !strcmp(fn, "general");
}

void cali_lora_tracker_init(cali_lora_tracker_t *t) { memset(t, 0, sizeof *t); }

void cali_lora_tracker_update(cali_lora_tracker_t *t, const cali_lora_snap_t *s, uint32_t now_s) {
    for (size_t i = 0; i < CODEC_NCHARS; i++) {
        if (untracked(i) || !s->len[i]) continue;   /* a function the unit never sent stays silent */
        if (t->last_len[i] != s->len[i] || memcmp(t->last[i], s->frame[i], s->len[i]) ||
            now_s - t->sent_at[i] >= CALI_LORA_REFRESH_S)
            t->pending[i] = 1;
    }
}

size_t cali_lora_pack_changed(const cali_lora_tracker_t *t, const cali_lora_snap_t *s, uint8_t *out, size_t max,
                              uint32_t *picked) {
    size_t n = 0;
    uint8_t tried[CODEC_NCHARS] = {0};
    *picked = 0;
    for (;;) {   /* oldest sent first; whole entries only, the rest stays pending */
        int pick = -1;
        for (size_t i = 0; i < CODEC_NCHARS; i++)
            if (t->pending[i] && !tried[i] && s->len[i] && (pick < 0 || t->sent_at[i] < t->sent_at[pick])) pick = (int)i;
        if (pick < 0) break;
        tried[pick] = 1;
        if (n + 2 + s->len[pick] > max) continue;
        out[n++] = (uint8_t)pick;
        out[n++] = s->len[pick];
        memcpy(out + n, s->frame[pick], s->len[pick]);
        n += s->len[pick];
        *picked |= 1u << pick;
    }
    return n;
}

void cali_lora_tracker_commit(cali_lora_tracker_t *t, const cali_lora_snap_t *s, uint32_t picked, uint32_t now_s) {
    for (size_t i = 0; i < CODEC_NCHARS; i++) {
        if (!(picked >> i & 1)) continue;
        memcpy(t->last[i], s->frame[i], s->len[i]);
        t->last_len[i] = s->len[i];
        t->sent_at[i] = now_s;
        t->pending[i] = 0;
    }
}

/* ---- status, switch, owner tick, console ---- */

void cali_lora_status_json(cali_json_t *j, const cali_lora_status_t *st, uint32_t now_s) {
    cali_json_obj_begin(j);
    cali_json_key(j, "module");
    cali_json_str(j, st->present < 0 ? "checking" : st->present ? "present" : "absent");
    cali_json_key(j, "joined");   /* the module's word: with at_unanswered > 0 it is stale */
    cali_json_bool(j, st->joined);
    cali_json_key(j, "at_unanswered");
    cali_json_int(j, st->at_unanswered);
    cali_json_key(j, "sf");
    cali_json_int(j, st->sf);
    cali_json_key(j, "last_s");   /* s since the last CONFIRMED uplink (ticks: R_FW_STATE_ETAG skips it) */
    if (st->last_confirmed_s && now_s >= st->last_confirmed_s) cali_json_int(j, now_s - st->last_confirmed_s);
    else cali_json_null(j);
    cali_json_key(j, "airtime_ms_24h");   /* confirmed by +EVT:TX_DONE only */
    cali_json_int(j, st->airtime_ms);
    cali_json_key(j, "airtime_charged_ms_24h");
    cali_json_int(j, st->charged_ms);
    cali_json_key(j, "unconfirmed_sends");
    cali_json_int(j, st->unconfirmed);
    cali_json_key(j, "downlinks_24h");
    cali_json_int(j, st->downlinks);
    cali_json_key(j, "uplinks");
    cali_json_arr_begin(j);
    for (int k = 0; k < 3; k++) cali_json_int(j, st->uplinks[k]);
    cali_json_arr_end(j);
    cali_json_key(j, "note");
    if (st->note) cali_json_str(j, st->note);
    else cali_json_null(j);
    cali_json_obj_end(j);
}

static int s_exp = -1;

int cali_exp_lora(void) {
    if (s_exp < 0) {
        uint8_t b = 0;
        size_t n = 1;
        s_exp = cali_kv_get(CALI_EXP_LORA_KEY, &b, &n) == CALI_KV_OK && n == 1 ? b != 0 : 0;
    }
    return s_exp;
}

int cali_exp_lora_set(int on) {
    uint8_t b = (uint8_t)(on != 0);
    if (cali_kv_set(CALI_EXP_LORA_KEY, &b, 1) != 0) return -1;
    s_exp = b;
    cali_log("exp: lora %s", b ? "on" : "off");
    return 0;
}

static cali_lora_status_t s_st = {.present = -1, .sf = 12};
static uint64_t s_next_snap;
static int s_was_on;

void cali_lora_tick(uint64_t now_ms) {
    cali_lora_status_t st;
    if (cali_lora_port_status(&st)) s_st = st;
    int on = cali_exp_lora();
    if (now_ms < s_next_snap && on == s_was_on) return;   /* a switch change goes over at once */
    s_next_snap = now_ms + CALI_LORA_SNAP_MS;
    s_was_on = on;
    static cali_lora_snap_t snap;   /* static: ~600 B off the owner task's stack */
    cali_lora_snapshot(&snap, now_ms);
    cali_lora_port_post(&snap);
}

void cali_lora_json(cali_json_t *j) {
    cali_lora_status_t st;
    if (cali_lora_port_status(&st)) s_st = st;   /* the newest, also before the first tick */
    cali_lora_status_json(j, &s_st, (uint32_t)(cali_uptime_ms() / 1000));
}

static int unhex(const char *h, uint8_t *out, size_t n) {
    if (strlen(h) != 2 * n) return -1;
    for (size_t k = 0; k < n; k++) {
        char b[3] = {h[2 * k], h[2 * k + 1], 0}, *end;
        out[k] = (uint8_t)strtoul(b, &end, 16);
        if (*end) return -1;
    }
    return 0;
}

int cali_lora_console(const char *args) {
    char op[8] = "", a[24] = "", b[24] = "", c[40] = "";
    int n = sscanf(args, "%7s %23s %23s %39s", op, a, b, c);
    if (n >= 1 && (!strcmp(op, "on") || !strcmp(op, "off"))) {
        if (cali_exp_lora_set(!strcmp(op, "on")) != 0) cali_log("lora: store failed");
    } else if (n >= 1 && !strcmp(op, "status")) {
        cali_log("lora: %s, module %s, %s, at_unanswered %u, airtime %lu ms confirmed / %lu ms charged",
                 cali_exp_lora() ? "on" : "off", s_st.present < 0 ? "checking" : s_st.present ? "present" : "absent",
                 s_st.joined ? "joined" : "not joined", s_st.at_unanswered, (unsigned long)s_st.airtime_ms,
                 (unsigned long)s_st.charged_ms);
    } else if (n == 4 && !strcmp(op, "keys")) {
        uint8_t k[32];   /* DevEUI 8, JoinEUI 8, AppKey 16; never printed */
        if (unhex(a, k, 8) || unhex(b, k + 8, 8) || unhex(c, k + 16, 16)) cali_log("lora: keys: 16 + 16 + 32 hex digits");
        else if (cali_kv_set(CALI_LORA_KEYS_KEY, k, sizeof k) != 0) cali_log("lora: keys: store failed");
        else cali_log("lora: keys stored (used at the next join attempt)");
        memset(k, 0, sizeof k);
    } else {
        cali_log("lora: usage: lora on|off|status|keys <deveui> <joineui> <appkey>");
    }
    return 0;
}
