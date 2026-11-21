/* hub_run_cli.c — host driver for hub_run.c (cali_hub_tick) with the real hub.c / hub_rpc.c, the real PSA
 * AES-GCM, an in-memory KV, a fake router and a fake port. Driven by tests/firmware/test_hub_run.py.
 *
 * Lines: cfg (store a hub config) | changed (cali_hub_cfg_changed) | online 0|1 | busy 0|1 (cali_http_busy) |
 *   keyfail 0|1 (storing hub_key fails) | tick <ms> (clock = ms, cali_hub_tick) | req <json> (the app seals it,
 *   dir 2, into the port's take slot) | retained <json> (the same, delivered as a retained message) |
 *   replay (the last request frame again) | newkey (cali_hub_new_key) |
 *   mark (prints MARK) | status (prints "STATUS <online> <last_pub_s != 0> <key_error>").
 * The port prints START / STOP and "POST state|res <plaintext>" (opened by the app's context with the stored
 * key, dir 1), or "POST <topic> unopened". The router answers GET /api/state 200 {"st":<n>} (n counts),
 * POST /api/command 200 {"ok":true}, else not found. cali_log prints "LOG <text>". */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_http.h"
#include "cali_hub.h"
#include "cali_lora_aead.h"
#include "cali_platform.h"
#include "cali_time.h"
#include "cali_web.h"

/* ---- fakes: KV, clock, log, random, time ---- */
static uint8_t s_kv[4][2048];
static size_t s_kv_len[4];
static const char *const s_names[3] = {CALI_HUB_KEY_KEY, CALI_HUB_CFG_KEY, CALI_LORA_CMD_CTR_KEY};
static int s_keyfail, s_online, s_busy, s_st, s_why;
static uint64_t s_now;

static int slot(const char *key) {
    for (int i = 0; i < 3; i++)
        if (!strcmp(key, s_names[i])) return i;
    return -1;
}
int cali_kv_get(const char *key, void *buf, size_t *len) {
    int i = slot(key);
    if (i < 0 || !s_kv_len[i]) return CALI_KV_MISSING;
    if (*len < s_kv_len[i]) return CALI_KV_CORRUPT;
    memcpy(buf, s_kv[i], s_kv_len[i]);
    *len = s_kv_len[i];
    return CALI_KV_OK;
}
int cali_kv_set(const char *key, const void *buf, size_t len) {
    int i = slot(key);
    if (i < 0 || len > sizeof s_kv[i] || (s_keyfail && i == 0)) return -1;
    memcpy(s_kv[i], buf, len);
    s_kv_len[i] = len;
    return 0;
}
void cali_random(void *buf, size_t len) {
    FILE *f = fopen("/dev/urandom", "rb");
    if (!f || fread(buf, 1, len, f) != len) exit(2);
    fclose(f);
}
uint64_t cali_uptime_ms(void) { return s_now; }
int cali_time_ts(int64_t *out) {   /* SNTP time, fixed: a POST over the hub runs only with it */
    *out = 1700000000000LL;
    return CALI_TSRC_NTP;
}
void cali_log(const char *fmt, ...) {
    va_list ap;
    fputs("LOG ", stdout);
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    putchar('\n');
}
int cali_http_busy(void) { return s_busy; }
void cali_lora_cmds_drop(const char *why) { printf("DROP %s\n", why); }   /* lora.c is not linked here */

/* ---- the fake router ---- */
int cali_web_handle(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx) {
    static char body[32];
    (void)ctx;
    if (!strcmp(req->method, "GET") && !strcmp(req->path, "/api/state")) {
        resp->body_len = (size_t)snprintf(body, sizeof body, "{\"st\":%d}", ++s_st);
        resp->body = body;
        return 1;
    }
    if (!strcmp(req->method, "POST") && !strcmp(req->path, "/api/command")) {
        resp->body = "{\"ok\":true}";
        resp->body_len = strlen(resp->body);
        return 1;
    }
    return 0;
}

/* ---- the app side and the fake port ---- */
static cali_hub_crypto_t s_app;
static uint8_t s_slot[CALI_HUB_REQ_MAX], s_last[CALI_HUB_REQ_MAX];
static size_t s_slot_n, s_last_n;
static int s_slot_retained;

static void app_key(void) {   /* the app scanned the QR: the stored key, its own session */
    size_t n = sizeof s_app.key;
    memset(&s_app, 0, sizeof s_app);
    if (cali_kv_get(CALI_HUB_KEY_KEY, s_app.key, &n) != CALI_KV_OK) return;
    cali_random(s_app.sess, sizeof s_app.sess);
}

int cali_hub_port_start(const cali_hub_cfg_t *cfg) {
    (void)cfg;
    puts("START");
    app_key();
    return 0;
}
void cali_hub_port_stop(void) { puts("STOP"); }
int cali_hub_port_online(void) { return s_online; }
int cali_hub_port_why(void) { return s_why; }
size_t cali_hub_port_take(uint8_t *buf, size_t max, int *retained) {
    size_t n = s_slot_n;
    *retained = s_slot_retained;
    if (!n || n > max) return 0;
    memcpy(buf, s_slot, n);
    s_slot_n = 0;
    return n;
}
int cali_hub_port_post(int topic, const uint8_t *frame, size_t n) {
    static uint8_t pt[CALI_HUB_RES_MAX + 1];
    int k = cali_hub_open(&s_app, frame, n, pt, CALI_HUB_RES_MAX, 1u << CALI_HUB_DIR_ESP, NULL);
    const char *t = topic == CALI_HUB_T_STATE ? "state" : "res";
    if (k < 0) {
        printf("POST %s unopened\n", t);
    } else {
        pt[k] = 0;
        printf("POST %s %s\n", t, (char *)pt);
    }
    if (topic == CALI_HUB_T_STATE) cali_hub_published();
    return 0;
}
void cali_hub_port_tick(void) {}

int main(void) {
    static char line[8192];
    cali_hub_set_aead(cali_aead_seal_psa, cali_aead_open_psa);
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long ms;
        int v;
        line[strcspn(line, "\n")] = 0;
        if (!strcmp(line, "cfg")) {
            cali_hub_cfg_t c = {"van1", "wss://h:8884/mqtt", "vu", "vp", "au", "ap"};
            cali_hub_cfg_save(&c);
        } else if (!strcmp(line, "changed")) {
            cali_hub_cfg_changed();
        } else if (sscanf(line, "online %d", &v) == 1) {
            s_online = v;
        } else if (sscanf(line, "why %d", &v) == 1) {
            s_why = v;
        } else if (sscanf(line, "busy %d", &v) == 1) {
            s_busy = v;
        } else if (sscanf(line, "keyfail %d", &v) == 1) {
            s_keyfail = v;
        } else if (sscanf(line, "tick %llu", &ms) == 1) {
            s_now = ms;
            cali_hub_tick(s_now);
        } else if (!strncmp(line, "req ", 4) || !strncmp(line, "retained ", 9)) {
            const char *j = strchr(line, ' ') + 1;
            s_slot_retained = !strncmp(line, "retained ", 9);
            s_slot_n = cali_hub_seal(&s_app, CALI_HUB_DIR_APP, (const uint8_t *)j, strlen(j), s_slot, sizeof s_slot);
            memcpy(s_last, s_slot, s_slot_n);
            s_last_n = s_slot_n;
        } else if (!strcmp(line, "replay")) {
            memcpy(s_slot, s_last, s_last_n);
            s_slot_n = s_last_n;
        } else if (!strcmp(line, "newkey")) {
            if (cali_hub_new_key() == 0) app_key();
        } else if (!strcmp(line, "mark")) {
            puts("MARK");
        } else if (!strcmp(line, "status")) {
            cali_hub_status_t st;
            cali_hub_status(&st);
            printf("STATUS %d %d %d %d\n", st.online, st.last_pub_s != 0, st.key_error, st.reason);
        }
        fflush(stdout);
    }
    return 0;
}
