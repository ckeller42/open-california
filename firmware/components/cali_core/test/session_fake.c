/* session_fake.c — a scripted fake transport driving console + runner + session, for
 * tests/firmware/test_session_fake.py ONLY; not part of the ESP build. Compiled with console.c,
 * session.c, runner.c, pairing_sm.c and csrc/codec.c by the host `cc` (no NimBLE, runs on macOS).
 *
 * (Also links snapshot.c, which console.c's SNAP uses, and the WiFi runner — wifi_run.c + wifi_sm.c +
 * captive_dns.c — over the fake cali_net below, for console "wifi …" and the WiFi-vs-session tests.)
 *
 * stdin, one line each:
 *   transport events (through the sink the runner registered; the runner forwards to the session):
 *     FOUND | CONNECTED | CONNECT_FAIL | PASSKEY_REQ | ENC_OK | ENC_FAIL
 *     DISCONNECTED [reason]                  (default 8, a generic loss; 19 or 531 = the unit's
 *                                             remote-terminate, the parked kick — cali_session.h)
 *     DISCOVERED [status] | HEARTBEAT <status>
 *     READ <hex char> <status> [hex data]    (default data 0102)
 *     NOTIFY <hex char> <hex data>
 *     WRITTEN <hex char> <status>            (a control write() completed; 0 = ACKed)
 *   harness:
 *     > <console line>     cali_console_line(<console line>)
 *     tick <now_ms>        cali_runner_tick + cali_session_tick
 *     boot                 cali_session_boot + console "status" (what host_main does on sync)
 *     bond 0|1             what has_bond() answers (default 0)
 *     ident <addr>         the bonded identity identity() answers while bonded (default FAKE_IDENTITY)
 *     submit <local_now|-> <fn> <what> [value…]
 *                          cali_ctl_submit with that page clock ("-" = none): prints "SUBMIT <rc>
 *                          <reason|->" and, when the command ends, "DONE <rc> <reason|->"
 *     fail <call>          the next call of that transport function returns -1
 *     syncwritten <status> the next write() delivers its WRITTEN <status> inside the call (as the
 *                          NimBLE transport does for an ATT request it cannot start)
 *     lastupd              print "LASTUPD <cali_session_last_update_ms()>"
 *     wifi_boot            cali_wifi_run_init(fake net) + cali_wifi_run_boot() (what host_main
 *                          does with --http); before it the WiFi runtime is off, as without --http
 *     kv <key>             print "KV <key> <value>" or "KV <key> missing" (the in-memory kv store)
 *     kvset <key> <value>  store a kv value (e.g. saved WiFi credentials before wifi_boot)
 *     kvsethex <key> <hex>  store hex-decoded bytes (e.g. a saved frame: the persisted water_good)
 *     kvhex <key>          print "KV <key> <hex>" of a binary value (or "KV <key> missing")
 *     reinit               re-run cali_session_init (re-reads NVS, e.g. the persisted water_good)
 *     kverasefail <n>      the next n cali_kv_erase calls fail (-1, nothing erased)
 *     rssi <dBm>           what the fake net's sta_rssi() answers (default 0)
 *     lastfail             print "LASTFAIL <cali_wifi_run_last_fail() or ->"
 *     webscan              cali_wifi_run_scan_auto() (what a setup-mode GET /api/wifi asks for)
 *   fake-net events (through the sink the WiFi runner registered):
 *     NET_GOT_IP <a.b.c.d> | NET_LOST | NET_FAILED <reason> | NET_AP_STARTED | NET_AP_STOPPED
 *     NET_SCAN_DONE [ssid ...]   (rssi -40 - 10*i, secure)
 *     NET_SCAN_FAILED      a SCAN_DONE with nscan -1 (the scan was refused, aborted or timed out)
 * stdout: CALL <name> [arg] per transport action (decimal args; queries not printed; a control
 * write prints "CALL write <hex char> <hex frame>"), NET <op>
 * [args] per cali_net WiFi/UDP call, the console's STATE/SNAP lines, and LOG lines (cali_log).
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_console.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_wifi_run.h"
#include "codec.h"

#define FAKE_IDENTITY "C0:FF:EE:CA:11:F0"

static cali_tsink_t s_sink;
static void *s_ctx;
static char s_fail[32];
static int s_bond;
static char s_ident[32] = FAKE_IDENTITY;
static int s_sync_written = -1;   /* "syncwritten <status>": the next write() completes inside the call */

void cali_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    printf("LOG ");
    vprintf(fmt, ap);
    printf("\n");
    va_end(ap);
}

static int call(const char *name, const char *arg) {
    if (arg) printf("CALL %s %s\n", name, arg);
    else printf("CALL %s\n", name);
    if (s_fail[0] && strcmp(s_fail, name) == 0) {
        s_fail[0] = 0;
        return -1;
    }
    return 0;
}

static int call_u(const char *name, unsigned long v) {
    char b[24];
    snprintf(b, sizeof b, "%lu", v);
    return call(name, b);
}

static void f_set_sink(cali_tsink_t sink, void *ctx) { s_sink = sink; s_ctx = ctx; }
static int f_start_scan(const char *name) { return call("start_scan", name); }
static int f_stop_scan(void) { return call("stop_scan", NULL); }
static int f_connect_found(void) { return call("connect_found", NULL); }
static int f_connect_bonded(void) { return call("connect_bonded", NULL); }
static int f_pair(void) { return call("pair", NULL); }
static int f_inject_passkey(uint32_t pk) { return call_u("inject_passkey", pk); }
static int f_discover(void) { return call("discover", NULL); }
static int f_read(uint16_t c) { return call_u("read", c); }
static int f_subscribe(uint16_t c) { return call_u("subscribe", c); }
static int f_write_heartbeat(uint32_t n) { return call_u("write_heartbeat", n); }
static int f_disconnect(void) { return call("disconnect", NULL); }
static int f_remove_bond(void) { s_bond = 0; return call("remove_bond", NULL); }
static int f_has_bond(void) { return s_bond; }
static const char *f_identity(void) { return s_bond ? s_ident : NULL; }

static void deliver(cali_tev_t ev, int status, uint16_t c, const uint8_t *data, size_t len);

static int f_write(uint16_t c, const uint8_t *d, size_t n) {
    char arg[8 + 2 * CODEC_FRAME_MAX + 1];
    int k = snprintf(arg, sizeof arg, "%04x ", c), rc;
    for (size_t i = 0; i < n && i < CODEC_FRAME_MAX; i++) k += snprintf(arg + k, sizeof arg - (size_t)k, "%02x", d[i]);
    rc = call("write", arg);
    if (rc == 0 && s_sync_written >= 0) {
        int st = s_sync_written;
        s_sync_written = -1;
        deliver(CALI_TEV_WRITTEN, st, c, NULL, 0);
    }
    return rc;
}

static const cali_transport_t FAKE = {
    f_set_sink, f_start_scan, f_stop_scan, f_connect_found, f_connect_bonded, f_pair,
    f_inject_passkey, f_discover, f_read, f_subscribe, f_write_heartbeat, f_disconnect,
    f_remove_bond, f_has_bond, f_identity, f_write,
};

static size_t unhex(const char *h, uint8_t *out, size_t max) {
    size_t n = 0;
    while (h[0] && h[1] && n < max) {
        char b[3] = {h[0], h[1], 0};
        out[n++] = (uint8_t)strtoul(b, NULL, 16);
        h += 2;
    }
    return n;
}

static void deliver(cali_tev_t ev, int status, uint16_t c, const uint8_t *data, size_t len) {
    cali_tevent_t e;
    memset(&e, 0, sizeof e);
    e.ev = ev;
    e.status = status;
    e.char_short = c;
    e.data = data;
    e.len = len;
    if (ev == CALI_TEV_FOUND) strcpy(e.addr, "5A:11:22:33:44:55");
    if (s_sink) s_sink(&e, s_ctx);
}

static void quit(void) { printf("QUIT\n"); }

static void on_done(int rc, const char *reason) { printf("DONE %d %s\n", rc, reason ? reason : "-"); }

/* ---- an in-memory kv store (cali_platform.h's kv calls) ---- */

#define KV_N 8
static struct {
    char key[CALI_KV_KEY_MAX + 1];
    char val[128];
    size_t len;
    int used;
} s_kv[KV_N];

static int kv_find(const char *key) {
    for (int i = 0; i < KV_N; i++)
        if (s_kv[i].used && strcmp(s_kv[i].key, key) == 0) return i;
    return -1;
}

int cali_kv_get(const char *key, void *buf, size_t *len) {
    int i = kv_find(key);
    if (i < 0) return CALI_KV_MISSING;
    if (s_kv[i].len > *len) return CALI_KV_CORRUPT;
    memcpy(buf, s_kv[i].val, s_kv[i].len);
    *len = s_kv[i].len;
    return CALI_KV_OK;
}

int cali_kv_set(const char *key, const void *buf, size_t len) {
    int i = kv_find(key);
    if (strlen(key) > CALI_KV_KEY_MAX || len > sizeof s_kv[0].val) return -1;
    for (int k = 0; i < 0 && k < KV_N; k++)
        if (!s_kv[k].used) i = k;
    if (i < 0) return -1;
    s_kv[i].used = 1;
    strcpy(s_kv[i].key, key);
    memcpy(s_kv[i].val, buf, len);
    s_kv[i].len = len;
    return 0;
}

static int s_erase_fail;   /* "kverasefail n": the next n erases fail */

int cali_kv_erase(const char *key) {
    int i = kv_find(key);
    if (s_erase_fail > 0) {
        s_erase_fail--;
        return -1;
    }
    if (i >= 0) memset(&s_kv[i], 0, sizeof s_kv[i]);
    return 0;
}

/* ---- the fake cali_net: WiFi + UDP calls print "NET <op> [args]" ---- */

static cali_net_sink_t s_net_sink;
static void *s_net_ctx;
static int s_rssi;

static void n_set_sink(cali_net_sink_t sink, void *ctx) { s_net_sink = sink; s_net_ctx = ctx; }
static int n_sta_start(const char *ssid, const char *psk) { printf("NET sta_start %s %s\n", ssid, psk); return 0; }
static int n_sta_stop(void) { printf("NET sta_stop\n"); return 0; }
static int n_ap_start(const char *ssid, const char *psk) { printf("NET ap_start %s %s\n", ssid, psk); return 0; }
static int n_ap_stop(void) { printf("NET ap_stop\n"); return 0; }
static int n_scan(void) { printf("NET scan\n"); return 0; }
static int n_sta_rssi(void) { return s_rssi; }
static int n_mdns(const char *host, uint16_t port) { printf("NET mdns %s %u\n", host, (unsigned)port); return 0; }
static int n_tcp_listen(uint16_t port) { (void)port; return -2; }
static int n_tcp_accept(int lfd) { (void)lfd; return -1; }
static int n_tcp_recv(int fd, void *b, size_t n) { (void)fd; (void)b; (void)n; return -1; }
static int n_tcp_send(int fd, const void *b, size_t n) { (void)fd; (void)b; (void)n; return -1; }
static void n_close(int fd) { printf("NET close %d\n", fd); }
static int n_udp_bind(uint16_t port) { printf("NET udp_bind %u\n", (unsigned)port); return 5; }
static int n_udp_recvfrom(int fd, void *b, size_t n, uint32_t *ip, uint16_t *port) {
    (void)fd; (void)b; (void)n; (void)ip; (void)port;
    return -1;
}
static int n_udp_sendto(int fd, const void *b, size_t n, uint32_t ip, uint16_t port) {
    (void)fd; (void)b; (void)ip; (void)port;
    return (int)n;
}

static const cali_net_t FAKE_NET = {
    .set_sink = n_set_sink, .sta_start = n_sta_start, .sta_stop = n_sta_stop, .ap_start = n_ap_start,
    .ap_stop = n_ap_stop, .scan = n_scan, .sta_rssi = n_sta_rssi, .mdns_announce = n_mdns,
    .tcp_listen = n_tcp_listen, .tcp_accept = n_tcp_accept, .tcp_recv = n_tcp_recv,
    .tcp_send = n_tcp_send, .tcp_close = n_close, .udp_bind = n_udp_bind,
    .udp_recvfrom = n_udp_recvfrom, .udp_sendto = n_udp_sendto,
};

static void net_deliver(cali_net_ev_t ev, cali_net_reason_t reason, uint32_t ip, int nscan,
                        const cali_net_ap_t *scan) {
    cali_net_event_t e;
    memset(&e, 0, sizeof e);
    e.ev = ev;
    e.reason = reason;
    e.ip = ip;
    e.nscan = nscan;
    e.scan = scan;
    if (s_net_sink) s_net_sink(&e, s_net_ctx);
}

static uint32_t parse_ip(const char *s) {
    unsigned a = 0, b = 0, c = 0, d = 0;
    sscanf(s, "%u.%u.%u.%u", &a, &b, &c, &d);
    return (uint32_t)a << 24 | (uint32_t)b << 16 | (uint32_t)c << 8 | (uint32_t)d;
}

/* "NET_SCAN_DONE a b c": up to NET_SCAN_MAX networks named by the words after the event. */
static void net_scan_done(const char *line) {
    static cali_net_ap_t aps[NET_SCAN_MAX];
    char name[64];
    int n = 0, off = 0, used;
    line += strlen("NET_SCAN_DONE");
    while (n < NET_SCAN_MAX && sscanf(line + off, "%32s%n", name, &used) == 1) {
        memset(&aps[n], 0, sizeof aps[n]);
        strcpy(aps[n].ssid, name);
        aps[n].rssi = -40 - 10 * n;
        aps[n].secure = 1;
        n++;
        off += used;
    }
    net_deliver(CALI_NET_EV_SCAN_DONE, CALI_NET_REASON_NONE, 0, n, aps);
}

int main(void) {
    char line[512], word[32];
    setvbuf(stdout, NULL, _IOLBF, 0);
    cali_console_on_quit = quit;
    cali_console_init(&FAKE);

    while (fgets(line, sizeof line, stdin)) {
        char a1[32] = "", a2[32] = "", a3[256] = "";
        uint8_t data[128];
        size_t len;
        line[strcspn(line, "\n")] = 0;
        if (line[0] == '>') {
            cali_console_line(line + 1);
            continue;
        }
        if (strncmp(line, "submit ", 7) == 0) {   /* submit <local_now|-> <fn> <what> [value…] */
            const char *reason = NULL;
            char *ln = strtok(line + 7, " "), *fn = strtok(NULL, " "), *what = strtok(NULL, " ");
            char *value = strtok(NULL, "");
            int rc = cali_ctl_submit(fn ? fn : "", what ? what : "", value ? value : "",
                                     ln && strcmp(ln, "-") != 0 ? (int64_t)strtoll(ln, NULL, 10) : -1, on_done,
                                     &reason);
            printf("SUBMIT %d %s\n", rc, reason ? reason : "-");
            continue;
        }
        if (sscanf(line, "%31s %31s %31s %255s", word, a1, a2, a3) < 1) continue;
        int n1 = (int)strtol(a1, NULL, 10);
        if (strcmp(word, "FOUND") == 0) deliver(CALI_TEV_FOUND, 0, 0, NULL, 0);
        else if (strcmp(word, "CONNECTED") == 0) deliver(CALI_TEV_CONNECTED, 0, 0, NULL, 0);
        else if (strcmp(word, "CONNECT_FAIL") == 0) deliver(CALI_TEV_CONNECT_FAIL, 2, 0, NULL, 0);
        else if (strcmp(word, "PASSKEY_REQ") == 0) deliver(CALI_TEV_PASSKEY_REQ, 0, 0, NULL, 0);
        else if (strcmp(word, "ENC_OK") == 0) deliver(CALI_TEV_ENC_OK, 0, 0, NULL, 0);
        else if (strcmp(word, "ENC_FAIL") == 0) deliver(CALI_TEV_ENC_FAIL, 2, 0, NULL, 0);
        else if (strcmp(word, "DISCONNECTED") == 0)
            deliver(CALI_TEV_DISCONNECTED, a1[0] ? (int)strtol(a1, NULL, 0) : 8, 0, NULL, 0);
        else if (strcmp(word, "DISCOVERED") == 0) deliver(CALI_TEV_DISCOVERED, n1, 0, NULL, 0);
        else if (strcmp(word, "HEARTBEAT") == 0) deliver(CALI_TEV_HEARTBEAT, n1, 0x1003, NULL, 0);
        else if (strcmp(word, "READ") == 0) {
            len = unhex(a3[0] ? a3 : "0102", data, sizeof data);
            int st = (int)strtol(a2, NULL, 10);
            deliver(CALI_TEV_READ, st, (uint16_t)strtoul(a1, NULL, 16), st ? NULL : data, st ? 0 : len);
        } else if (strcmp(word, "WRITTEN") == 0) {
            deliver(CALI_TEV_WRITTEN, (int)strtol(a2, NULL, 10), (uint16_t)strtoul(a1, NULL, 16), NULL, 0);
        } else if (strcmp(word, "syncwritten") == 0) {
            s_sync_written = n1;
        } else if (strcmp(word, "NOTIFY") == 0) {
            len = unhex(a2, data, sizeof data);
            deliver(CALI_TEV_NOTIFY, 0, (uint16_t)strtoul(a1, NULL, 16), data, len);
        } else if (strcmp(word, "tick") == 0) {
            uint64_t now = strtoull(a1, NULL, 10);
            cali_runner_tick(now);
            cali_session_tick(now);
            cali_wifi_run_tick(now);
        } else if (strcmp(word, "wifi_boot") == 0) {
            cali_wifi_run_init(&FAKE_NET);
            cali_wifi_run_boot();
        } else if (strcmp(word, "kv") == 0) {
            char v[129];
            size_t vl = sizeof v - 1;
            if (cali_kv_get(a1, v, &vl) == CALI_KV_OK) {
                v[vl] = 0;
                printf("KV %s %s\n", a1, v);
            } else {
                printf("KV %s missing\n", a1);
            }
        } else if (strcmp(word, "kvhex") == 0) {   /* print a binary value as hex (e.g. water_good) */
            uint8_t v[129];
            size_t vl = sizeof v;
            if (cali_kv_get(a1, v, &vl) == CALI_KV_OK) {
                printf("KV %s ", a1);
                for (size_t k = 0; k < vl; k++) printf("%02x", v[k]);
                printf("\n");
            } else {
                printf("KV %s missing\n", a1);
            }
        } else if (strcmp(word, "kvset") == 0) {
            cali_kv_set(a1, a2, strlen(a2));
        } else if (strcmp(word, "kvsethex") == 0) {   /* store hex-decoded bytes (e.g. a saved frame) */
            uint8_t b[64];
            size_t bl = 0;
            for (const char *p = a2; p[0] && p[1] && bl < sizeof b; p += 2)
                b[bl++] = (uint8_t)strtol((char[3]){p[0], p[1], 0}, NULL, 16);
            cali_kv_set(a1, b, bl);
        } else if (strcmp(word, "reinit") == 0) {   /* re-boot the session: re-reads NVS (water_good) */
            cali_session_init(&FAKE);
        } else if (strcmp(word, "kverasefail") == 0) {
            s_erase_fail = n1;
        } else if (strcmp(word, "rssi") == 0) {
            s_rssi = n1;
        } else if (strcmp(word, "webscan") == 0) {
            cali_wifi_run_scan_auto();
        } else if (strcmp(word, "lastfail") == 0) {
            const char *f = cali_wifi_run_last_fail();
            printf("LASTFAIL %s\n", f ? f : "-");
        } else if (strcmp(word, "NET_GOT_IP") == 0) {
            net_deliver(CALI_NET_EV_STA_GOT_IP, CALI_NET_REASON_NONE, parse_ip(a1), 0, NULL);
        } else if (strcmp(word, "NET_LOST") == 0) {
            net_deliver(CALI_NET_EV_STA_LOST, CALI_NET_REASON_NONE, 0, 0, NULL);
        } else if (strcmp(word, "NET_FAILED") == 0) {
            net_deliver(CALI_NET_EV_STA_FAILED, (cali_net_reason_t)n1, 0, 0, NULL);
        } else if (strcmp(word, "NET_AP_STARTED") == 0) {
            net_deliver(CALI_NET_EV_AP_STARTED, CALI_NET_REASON_NONE, 0, 0, NULL);
        } else if (strcmp(word, "NET_AP_STOPPED") == 0) {
            net_deliver(CALI_NET_EV_AP_STOPPED, CALI_NET_REASON_NONE, 0, 0, NULL);
        } else if (strcmp(word, "NET_SCAN_DONE") == 0) {
            net_scan_done(line);
        } else if (strcmp(word, "NET_SCAN_FAILED") == 0) {
            net_deliver(CALI_NET_EV_SCAN_DONE, CALI_NET_REASON_NONE, 0, -1, NULL);
        } else if (strcmp(word, "boot") == 0) {
            cali_session_boot();
            cali_console_line("status");
        } else if (strcmp(word, "bond") == 0) s_bond = n1;
        else if (strcmp(word, "ident") == 0) snprintf(s_ident, sizeof s_ident, "%s", a1);
        else if (strcmp(word, "lastupd") == 0)
            printf("LASTUPD %llu\n", (unsigned long long)cali_session_last_update_ms());
        else if (strcmp(word, "fail") == 0) snprintf(s_fail, sizeof s_fail, "%s", a1);
        else printf("UNKNOWN %s\n", word);
    }
    return 0;
}
