/* web_cli.c — line-protocol driver for tests/firmware/test_web_handlers.py ONLY; not part of the ESP
 * build. Links web.c + http_core.c + captive_dns.c (for cali_captive_is_probe) + json.c + snapshot.c
 * + csrc/codec.c, and fakes everything else web.c reaches: a scripted cali_net socket, the session's
 * frames, the runner state, a transport (has_bond/identity), an in-memory kv store, the clock, the
 * log, cali_fw_version ("test") and the WiFi runtime (cali_wifi_run.h).
 *
 * The session holds two frames: cooler (Installed=1, Level=3; "flip" toggles Level 3 <-> 4) and roof
 * (Position=1, Installed=1).
 *
 * stdin, one line each:
 *   req <text>       one connection sending <text> (escapes \r \n \0 \\) as one fragment; polls the
 *                    core every 10 ms until it closes the connection, then prints
 *                    "RESP <n>\n<the n response bytes>\n"
 *   sendmax <n>      tcp_send takes at most <n> bytes per poll (default unlimited)
 *   flip             toggle the cooler frame's Level 3 <-> 4
 *   flipping 0|1     1: flip after every poll while a response is being sent
 *   nofn             the session holds no frames
 *   wifi <state> [ssid|- ip|- rssi]   the fake WiFi runtime's SM state (unprovisioned, setup_ap,
 *                    connecting, online, retrying, setup_ap_retrying) + station ssid/ip/rssi
 *   joined 0|1       the SM state's joined_once
 *   ap <ssid> <rssi> <secure>   append one scan result; "apclear" empties the list
 *   pair <name>      the runner's state, by PAIR_STATE_NAMES name
 *   bond 0|1         has_bond() (identity() = C0:FF:EE:CA:11:F0 while 1)
 *   active 0|1       cali_session_active()
 *   stamp <ms>       cali_session_last_update_ms()
 *   now <ms>         cali_uptime_ms()
 *   kv <key>         print "KV <value>" or "KV <missing>"
 *   kvfail 0|1       cali_kv_set fails
 * Calls into the fake WiFi runtime print "CALL set_creds [<ssid>] [<psk>]", "CALL forget",
 * "CALL scan"; cali_log prints "LOG <text>". cali_web_init's result goes to stderr as "init=<rc>".
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_web.h"
#include "cali_wifi_run.h"
#include "codec_chars.h"
#include "pairing_consts.h"

#define LFD 3
#define CFD 7
#define LINE_MAX 8192
#define OUT_MAX 65536
#define N_OF(a) (sizeof (a) / sizeof *(a))

/* ---- the scripted socket ---- */
static char s_req[LINE_MAX];
static size_t s_req_len, s_req_off;
static int s_pending, s_open;
static long s_send_max = -1;
static size_t s_sent_poll;
static char s_out[OUT_MAX];
static size_t s_out_len;

static int f_listen(uint16_t port) { (void)port; return LFD; }
static int f_accept(int lfd) {
    if (lfd != LFD || !s_pending) return -1;
    s_pending = 0;
    s_open = 1;
    return CFD;
}
static int f_recv(int fd, void *buf, size_t n) {
    if (fd != CFD) return -2;
    if (s_req_off == s_req_len) return -1;
    size_t k = s_req_len - s_req_off;
    if (k > n) k = n;
    memcpy(buf, s_req + s_req_off, k);
    s_req_off += k;
    return (int)k;
}
static int f_send(int fd, const void *buf, size_t n) {
    if (fd != CFD) return -2;
    if (s_send_max >= 0 && s_sent_poll + n > (size_t)s_send_max) n = (size_t)s_send_max - s_sent_poll;
    if (n == 0) return -1;
    if (s_out_len + n > OUT_MAX) return -2;
    memcpy(s_out + s_out_len, buf, n);
    s_out_len += n;
    s_sent_poll += n;
    return (int)n;
}
static void f_close(int fd) { if (fd == CFD) s_open = 0; }

static const cali_net_t fake_net = {
    .tcp_listen = f_listen, .tcp_accept = f_accept, .tcp_recv = f_recv, .tcp_send = f_send,
    .tcp_close = f_close,
};

/* ---- the fake session ---- */
static uint8_t s_cooler[6] = {0x08, 0x03, 0, 0, 0, 0};   /* Installed=1 (bit 4), Level=3 (bits 12-15) */
static const uint8_t s_roof[5] = {0x12, 0, 0, 0, 0};     /* Position=1 (bits 0-3), Installed=1 (bit 6) */
static int s_nofn, s_active;
static uint64_t s_stamp, s_now = 1000;

int cali_session_frame(size_t i, const uint8_t **frame, size_t *len) {
    if (s_nofn || i >= CODEC_NCHARS) return 0;
    if (strcmp(CODEC_CHARS[i].function, "cooler") == 0) {
        *frame = s_cooler;
        *len = sizeof s_cooler;
        return 1;
    }
    if (strcmp(CODEC_CHARS[i].function, "roof") == 0) {
        *frame = s_roof;
        *len = sizeof s_roof;
        return 1;
    }
    return 0;
}
int cali_session_active(void) { return s_active; }
uint64_t cali_session_last_update_ms(void) { return s_stamp; }

/* ---- the fake runner + transport ---- */
static cali_pair_state_t s_pair;
const cali_pair_state_t *cali_runner_state(void) { return &s_pair; }

static int s_bond;
static int f_has_bond(void) { return s_bond; }
static const char *f_identity(void) { return s_bond ? "C0:FF:EE:CA:11:F0" : NULL; }
static const cali_transport_t fake_t = {.has_bond = f_has_bond, .identity = f_identity};

/* ---- the fake platform ---- */
#define KV_N 8
static struct { char key[CALI_KV_KEY_MAX + 1]; char val[128]; size_t len; int used; } s_kv[KV_N];
static int s_kvfail;

int cali_kv_set(const char *key, const void *buf, size_t len) {
    if (s_kvfail || strlen(key) > CALI_KV_KEY_MAX || len > sizeof s_kv[0].val) return -1;
    size_t i, free_i = KV_N;
    for (i = 0; i < KV_N; i++) {
        if (s_kv[i].used && strcmp(s_kv[i].key, key) == 0) break;
        if (!s_kv[i].used && free_i == KV_N) free_i = i;
    }
    if (i == KV_N) i = free_i;
    if (i == KV_N) return -1;
    strcpy(s_kv[i].key, key);
    memcpy(s_kv[i].val, buf, len);
    s_kv[i].len = len;
    s_kv[i].used = 1;
    return 0;
}
int cali_kv_get(const char *key, void *buf, size_t *len) {
    for (size_t i = 0; i < KV_N; i++) {
        if (!s_kv[i].used || strcmp(s_kv[i].key, key) != 0) continue;
        if (s_kv[i].len > *len) return CALI_KV_CORRUPT;
        memcpy(buf, s_kv[i].val, s_kv[i].len);
        *len = s_kv[i].len;
        return CALI_KV_OK;
    }
    return CALI_KV_MISSING;
}
uint64_t cali_uptime_ms(void) { return s_now; }
const char *cali_fw_version(void) { return "test"; }
void cali_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    printf("LOG ");
    vprintf(fmt, ap);
    printf("\n");
    va_end(ap);
}

/* ---- the fake WiFi runtime ---- */
static cali_wifi_state_t s_wifi;
static char s_ssid[NET_SSID_MAX + 1];
static int s_has_ssid, s_rssi;
static uint32_t s_ip;
static cali_net_ap_t s_aps[NET_SCAN_MAX];
static int s_naps;

const cali_wifi_state_t *cali_wifi_run_state(void) { return &s_wifi; }
const char *cali_wifi_run_ssid(void) { return s_has_ssid ? s_ssid : NULL; }
uint32_t cali_wifi_run_ip(void) { return s_ip; }
int cali_wifi_run_rssi(void) { return s_rssi; }
int cali_wifi_run_scan_list(const cali_net_ap_t **out) {
    *out = s_aps;
    return s_naps;
}
void cali_wifi_run_set_creds(const char *ssid, const char *psk) { printf("CALL set_creds [%s] [%s]\n", ssid, psk); }
void cali_wifi_run_forget(void) { printf("CALL forget\n"); }
void cali_wifi_run_scan(void) { printf("CALL scan\n"); }

static const char *const WIFI_NAMES[] = {"unprovisioned", "setup_ap", "connecting", "online", "retrying",
                                         "setup_ap_retrying"};

static size_t unescape(const char *s, char *out, size_t cap) {
    size_t n = 0;
    for (; *s && *s != '\n' && n < cap; s++) {
        if (*s == '\\' && s[1]) {
            s++;
            out[n++] = *s == 'r' ? '\r' : *s == 'n' ? '\n' : *s == '0' ? '\0' : *s;
        } else {
            out[n++] = *s;
        }
    }
    return n;
}

static void flip(void) { s_cooler[1] = s_cooler[1] == 0x03 ? 0x04 : 0x03; }

static int s_flipping;

static void request(const char *text) {
    s_req_len = unescape(text, s_req, sizeof s_req);
    s_req_off = 0;
    s_out_len = 0;
    s_pending = 1;
    for (int polls = 0; polls < 100000; polls++) {
        s_sent_poll = 0;
        s_now += 10;
        cali_web_poll(s_now);
        if (!s_pending && !s_open) break;
        if (s_flipping) flip();
    }
    printf("RESP %lu\n", (unsigned long)s_out_len);
    fwrite(s_out, 1, s_out_len, stdout);
    printf("\n");
}

static uint32_t parse_ip(const char *s) {
    unsigned a, b, c, d;
    if (sscanf(s, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) return 0;
    return (a << 24) | (b << 16) | (c << 8) | d;
}

int main(void) {
    static char line[LINE_MAX];
    setvbuf(stdout, NULL, _IOFBF, 1 << 16);
    fprintf(stderr, "init=%d\n", cali_web_init(&fake_net, &fake_t, NET_HTTP_PORT));
    while (fgets(line, sizeof line, stdin)) {
        char w[64] = "", a1[64] = "", a2[64] = "", a3[64] = "", a4[64] = "";
        long v;
        unsigned long long ms;
        if (strncmp(line, "req ", 4) == 0) {
            request(line + 4);
            continue;
        }
        line[strcspn(line, "\n")] = 0;
        int n = sscanf(line, "%63s %63s %63s %63s %63s", w, a1, a2, a3, a4);
        if (n < 1) continue;
        v = strtol(a1, NULL, 10);
        if (strcmp(w, "sendmax") == 0) s_send_max = v;
        else if (strcmp(w, "flip") == 0) flip();
        else if (strcmp(w, "flipping") == 0) s_flipping = (int)v;
        else if (strcmp(w, "nofn") == 0) s_nofn = 1;
        else if (strcmp(w, "joined") == 0) s_wifi.joined_once = (uint8_t)v;
        else if (strcmp(w, "bond") == 0) s_bond = (int)v;
        else if (strcmp(w, "active") == 0) s_active = (int)v;
        else if (strcmp(w, "kvfail") == 0) s_kvfail = (int)v;
        else if (strcmp(w, "apclear") == 0) s_naps = 0;
        else if (strcmp(w, "stamp") == 0 && sscanf(a1, "%llu", &ms) == 1) s_stamp = ms;
        else if (strcmp(w, "now") == 0 && sscanf(a1, "%llu", &ms) == 1) s_now = ms;
        else if (strcmp(w, "ap") == 0 && s_naps < NET_SCAN_MAX) {
            snprintf(s_aps[s_naps].ssid, sizeof s_aps[s_naps].ssid, "%s", a1);
            s_aps[s_naps].rssi = (int)strtol(a2, NULL, 10);
            s_aps[s_naps].secure = (int)strtol(a3, NULL, 10);
            s_naps++;
        } else if (strcmp(w, "wifi") == 0) {
            size_t i;
            for (i = 0; i < N_OF(WIFI_NAMES) && strcmp(WIFI_NAMES[i], a1) != 0; i++) {}
            if (i == N_OF(WIFI_NAMES)) { printf("UNKNOWN wifi %s\n", a1); continue; }
            s_wifi.st = (uint8_t)i;
            s_has_ssid = n >= 3 && strcmp(a2, "-") != 0;
            snprintf(s_ssid, sizeof s_ssid, "%s", s_has_ssid ? a2 : "");
            s_ip = n >= 4 ? parse_ip(a3) : 0;
            s_rssi = n >= 5 ? (int)strtol(a4, NULL, 10) : 0;
        } else if (strcmp(w, "pair") == 0) {
            size_t i;
            for (i = 0; i < N_OF(PAIR_STATE_NAMES) && !(PAIR_STATE_NAMES[i] && strcmp(PAIR_STATE_NAMES[i], a1) == 0); i++) {}
            if (i == N_OF(PAIR_STATE_NAMES)) { printf("UNKNOWN pair %s\n", a1); continue; }
            s_pair.st = (uint8_t)i;
        } else if (strcmp(w, "kv") == 0) {
            char buf[128];
            size_t len = sizeof buf;
            if (cali_kv_get(a1, buf, &len) == CALI_KV_OK) printf("KV %.*s\n", (int)len, buf);
            else printf("KV <missing>\n");
        } else {
            printf("UNKNOWN %s\n", w);
        }
    }
    fflush(stdout);
    return 0;
}
