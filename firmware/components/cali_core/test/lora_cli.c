/* lora_cli.c — host driver for lora.c (R_FW_LORA), one command per stdin line. Snapshot input:
 *   frame <function> <hex>   mppt <7 floats|->   bsense <volts|->   sensat <at_ms>   ctr <solar> <load> <net>   boot <n>
 *   snap <now_ms>
 * Packers:   pack2 | pack4 <fw> | trk_init | trk_update <now_s> | trk_pack <max> | trk_commit <now_s>
 * Radio:     parse <line> | redact <line> | outcome <kind> <tx_done> | airtime <phy_len> <sf> | maxpl <sf>
 * Budget:    up <t_s> <ms> <confirmed> | used <t> | conf <t> | room <t> | dl <t> | interval <t> <sf> <len> <share> | backoff <n>
 * Status:    stjson <module> <joined> <unanswered> <sf> <last_s> <air> <charged> <unconf> <dl> <now_s>
 * Switch:    console <args> | exp | tick <now_ms> (prints "POST <enabled>" when a snapshot went to the port)
 * Driven by tests/firmware/test_lora.py. Fakes: session frames, sensors, counters, clock, NVS, log. */
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_lora.h"
#include "cali_platform.h"

static uint8_t s_frames[CODEC_NCHARS][CODEC_FRAME_MAX];
static size_t s_len[CODEC_NCHARS];
static cali_sensor_reading_t s_mppt, s_bs;
static int s_have_mppt, s_have_bs;
static cali_energy_ctr_t s_ctr;
static uint32_t s_boot = 1;
static uint8_t s_kv[64];
static size_t s_kv_len;
static char s_kv_key[16];

int cali_session_frame(size_t i, const uint8_t **frame, size_t *len) {
    if (i >= CODEC_NCHARS || !s_len[i]) return 0;
    *frame = s_frames[i];
    *len = s_len[i];
    return 1;
}
int cali_sensors_first(const char *type, cali_sensor_reading_t *out) {
    if (!strcmp(type, "victron_mppt") && s_have_mppt) return *out = s_mppt, 0;
    if (!strcmp(type, "victron_battery") && s_have_bs) return *out = s_bs, 0;
    return -1;
}
const cali_energy_ctr_t *cali_energy_counters(void) { return &s_ctr; }
uint32_t cali_time_boot(void) { return s_boot; }
uint64_t cali_uptime_ms(void) { return 0; }
int cali_kv_get(const char *key, void *buf, size_t *len) {
    if (strcmp(key, s_kv_key) || *len < s_kv_len) return -1;
    memcpy(buf, s_kv, s_kv_len);
    *len = s_kv_len;
    return CALI_KV_OK;
}
int cali_kv_set(const char *key, const void *buf, size_t len) {
    snprintf(s_kv_key, sizeof s_kv_key, "%s", key);
    memcpy(s_kv, buf, len);
    s_kv_len = len;
    return 0;
}
void cali_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    printf("LOG ");
    vprintf(fmt, ap);
    printf("\n");
    va_end(ap);
}
void cali_lora_port_post(const cali_lora_snap_t *s) { printf("POST %d\n", s->enabled); }
int cali_lora_port_status(cali_lora_status_t *out) { (void)out; return 0; }

static size_t unhex(const char *h, uint8_t *out, size_t cap) {
    size_t n = 0;
    while (h[0] && h[1] && n < cap) {
        char b[3] = {h[0], h[1], 0};
        out[n++] = (uint8_t)strtoul(b, NULL, 16);
        h += 2;
    }
    return n;
}

static void hex(const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) printf("%02x", b[i]);
    printf("\n");
}

static cali_rak_kind_t kind_of(const char *name) {
    for (int k = CALI_RAK_NONE; k <= CALI_RAK_VALUE; k++)
        if (!strcmp(name, cali_rak_kind_name((cali_rak_kind_t)k))) return (cali_rak_kind_t)k;
    return CALI_RAK_NONE;
}

int main(void) {
    static char line[512];
    static cali_lora_snap_t snap;
    static cali_lora_tracker_t trk;
    static cali_lora_budget_t bud;
    uint32_t picked = 0;
    while (fgets(line, sizeof line, stdin)) {
        char cmd[16], a[160], b[64];
        unsigned u1, u2, u3, u4;
        unsigned long long ull;
        line[strcspn(line, "\r\n")] = 0;
        if (sscanf(line, "%15s", cmd) != 1) continue;
        const char *rest = line + strlen(cmd) + (line[strlen(cmd)] ? 1 : 0);
        if (!strcmp(cmd, "frame") && sscanf(rest, "%63s %159s", b, a) == 2) {
            for (size_t i = 0; i < CODEC_NCHARS; i++)
                if (!strcmp(CODEC_CHARS[i].function, b)) s_len[i] = unhex(a, s_frames[i], CODEC_FRAME_MAX);
        } else if (!strcmp(cmd, "mppt")) {
            float *f = s_mppt.f;
            s_have_mppt = sscanf(rest, "%f %f %f %f %f %f %f", f, f + 1, f + 2, f + 3, f + 4, f + 5, f + 6) == 7;
            s_mppt.n = s_have_mppt ? 7 : 0;
        } else if (!strcmp(cmd, "bsense")) {
            s_have_bs = sscanf(rest, "%f", s_bs.f) == 1;
            s_bs.f[1] = NAN;
            s_bs.n = s_have_bs ? 2 : 0;
        } else if (!strcmp(cmd, "sensat") && sscanf(rest, "%llu", &ull) == 1) {   /* both readings' advert time */
            s_mppt.at_ms = s_bs.at_ms = ull;
        } else if (!strcmp(cmd, "ctr")) {
            long net;
            unsigned long sol, lo;
            if (sscanf(rest, "%lu %lu %ld", &sol, &lo, &net) == 3)
                s_ctr.solar_dwh = (uint32_t)sol, s_ctr.load_dwh = (uint32_t)lo, s_ctr.batt_net_dwh = (int32_t)net;
        } else if (!strcmp(cmd, "boot") && sscanf(rest, "%u", &u1) == 1) {
            s_boot = u1;
        } else if (!strcmp(cmd, "snap") && sscanf(rest, "%llu", &ull) == 1) {
            cali_lora_snapshot(&snap, ull);
        } else if (!strcmp(cmd, "pack2")) {
            uint8_t out[CALI_LORA_ENERGY_LEN];
            hex(out, cali_lora_pack_energy(out, &snap));
        } else if (!strcmp(cmd, "pack4") && sscanf(rest, "%63s", b) == 1) {
            uint8_t out[CALI_LORA_DAILY_LEN];
            hex(out, cali_lora_pack_daily(out, &snap, b));
        } else if (!strcmp(cmd, "trk_init")) {
            cali_lora_tracker_init(&trk);
        } else if (!strcmp(cmd, "trk_update") && sscanf(rest, "%u", &u1) == 1) {
            cali_lora_tracker_update(&trk, &snap, u1);
        } else if (!strcmp(cmd, "trk_pack") && sscanf(rest, "%u", &u1) == 1) {
            uint8_t out[256];
            hex(out, cali_lora_pack_changed(&trk, &snap, out, u1 < sizeof out ? u1 : sizeof out, &picked));
        } else if (!strcmp(cmd, "trk_commit") && sscanf(rest, "%u", &u1) == 1) {
            cali_lora_tracker_commit(&trk, &snap, picked, u1);
        } else if (!strcmp(cmd, "parse")) {
            cali_rak_event_t e = cali_rak_parse(rest);
            printf("%s %d %d %d %d %d %s %d ", cali_rak_kind_name(e.kind), e.lc_status, e.lc_margin, e.lc_gateways,
                   e.rssi, e.snr, e.value[0] ? e.value : "-", e.port);
            hex(e.payload, e.payload_len);
        } else if (!strcmp(cmd, "redact")) {
            char out[160];
            cali_rak_redact(rest, out, sizeof out);
            puts(out);
        } else if (!strcmp(cmd, "outcome") && sscanf(rest, "%63s %u", b, &u1) == 2) {
            cali_lora_outcome_t o = cali_lora_outcome(kind_of(b), (int)u1);
            printf("%d %d\n", o.charged, o.confirmed);
        } else if (!strcmp(cmd, "airtime") && sscanf(rest, "%u %u", &u1, &u2) == 2) {
            printf("%u\n", (unsigned)cali_lora_airtime_ms(u1, u2));
        } else if (!strcmp(cmd, "maxpl") && sscanf(rest, "%u", &u1) == 1) {
            printf("%u\n", cali_lora_max_payload(u1));
        } else if (!strcmp(cmd, "up") && sscanf(rest, "%u %u %u", &u1, &u2, &u3) == 3) {
            cali_lora_uplink_add(&bud, u1, u2, u3 != 0);
        } else if (!strcmp(cmd, "used") && sscanf(rest, "%u", &u1) == 1) {
            printf("%u\n", (unsigned)cali_lora_uplink_used_ms(&bud, u1));
        } else if (!strcmp(cmd, "conf") && sscanf(rest, "%u", &u1) == 1) {
            printf("%u\n", (unsigned)cali_lora_uplink_confirmed_ms(&bud, u1));
        } else if (!strcmp(cmd, "room") && sscanf(rest, "%u", &u1) == 1) {
            printf("%u\n", (unsigned)cali_lora_uplink_room_ms(&bud, u1));
        } else if (!strcmp(cmd, "dl") && sscanf(rest, "%u", &u1) == 1) {
            int took = cali_lora_downlink_take(&bud, u1);
            printf("%d %u\n", took, cali_lora_downlinks_used(&bud, u1));
        } else if (!strcmp(cmd, "interval") && sscanf(rest, "%u %u %u %u", &u1, &u2, &u3, &u4) == 4) {
            printf("%u\n", (unsigned)cali_lora_next_interval_s(&bud, u1, u2, u3, u4));
        } else if (!strcmp(cmd, "budget_reset")) {
            cali_lora_budget_init(&bud);
        } else if (!strcmp(cmd, "backoff") && sscanf(rest, "%u", &u1) == 1) {
            printf("%u\n", (unsigned)cali_lora_join_backoff_s(u1));
        } else if (!strcmp(cmd, "stjson")) {
            cali_lora_status_t st = {0};
            unsigned joined, last, air, ch, unc, dl, now;
            if (sscanf(rest, "%d %u %u %d %u %u %u %u %u %u", &st.present, &joined, &st.at_unanswered, &st.sf, &last,
                       &air, &ch, &unc, &dl, &now) != 10)
                continue;
            st.joined = joined, st.last_confirmed_s = last, st.airtime_ms = air, st.charged_ms = ch;
            st.unconfirmed = unc, st.downlinks = dl;
            char buf[512];
            cali_json_t j;
            cali_json_begin(&j, buf, sizeof buf);
            cali_json_key(&j, "lora");
            cali_lora_status_json(&j, &st, now);
            if (cali_json_end(&j) >= 0) puts(buf);
        } else if (!strcmp(cmd, "console")) {
            cali_lora_console(rest);
        } else if (!strcmp(cmd, "exp")) {
            printf("%d\n", cali_exp_lora());
        } else if (!strcmp(cmd, "kv")) {   /* what NVS holds, as hex (the keys test) */
            printf("%s ", s_kv_key);
            hex(s_kv, s_kv_len);
        } else if (!strcmp(cmd, "tick") && sscanf(rest, "%llu", &ull) == 1) {
            cali_lora_tick(ull);
        }
        fflush(stdout);
    }
    return 0;
}
