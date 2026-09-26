/* session_fake.c — a scripted fake transport driving console + runner + session, for
 * tests/firmware/test_session_fake.py ONLY; not part of the ESP build. Compiled with console.c,
 * session.c, runner.c, pairing_sm.c and csrc/codec.c by the host `cc` (no NimBLE, runs on macOS).
 *
 * stdin, one line each:
 *   transport events (through the sink the runner registered; the runner forwards to the session):
 *     FOUND | CONNECTED | CONNECT_FAIL | PASSKEY_REQ | ENC_OK | ENC_FAIL | DISCONNECTED
 *     DISCOVERED [status] | HEARTBEAT <status>
 *     READ <hex char> <status> [hex data]    (default data 0102)
 *     NOTIFY <hex char> <hex data>
 *   harness:
 *     > <console line>     cali_console_line(<console line>)
 *     tick <now_ms>        cali_runner_tick + cali_session_tick
 *     boot                 cali_session_boot + console "status" (what host_main does on sync)
 *     bond 0|1             what has_bond() answers (default 0)
 *     fail <call>          the next call of that transport function returns -1
 * stdout: CALL <name> [arg] per transport action (decimal args; queries not printed), the
 * console's STATE/SNAP lines, and LOG lines (cali_log).
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_console.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"

#define FAKE_IDENTITY "C0:FF:EE:CA:11:F0"

static cali_tsink_t s_sink;
static void *s_ctx;
static char s_fail[32];
static int s_bond;

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
static const char *f_identity(void) { return s_bond ? FAKE_IDENTITY : NULL; }

static const cali_transport_t FAKE = {
    f_set_sink, f_start_scan, f_stop_scan, f_connect_found, f_connect_bonded, f_pair,
    f_inject_passkey, f_discover, f_read, f_subscribe, f_write_heartbeat, f_disconnect,
    f_remove_bond, f_has_bond, f_identity,
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
        if (sscanf(line, "%31s %31s %31s %255s", word, a1, a2, a3) < 1) continue;
        int n1 = (int)strtol(a1, NULL, 10);
        if (strcmp(word, "FOUND") == 0) deliver(CALI_TEV_FOUND, 0, 0, NULL, 0);
        else if (strcmp(word, "CONNECTED") == 0) deliver(CALI_TEV_CONNECTED, 0, 0, NULL, 0);
        else if (strcmp(word, "CONNECT_FAIL") == 0) deliver(CALI_TEV_CONNECT_FAIL, 2, 0, NULL, 0);
        else if (strcmp(word, "PASSKEY_REQ") == 0) deliver(CALI_TEV_PASSKEY_REQ, 0, 0, NULL, 0);
        else if (strcmp(word, "ENC_OK") == 0) deliver(CALI_TEV_ENC_OK, 0, 0, NULL, 0);
        else if (strcmp(word, "ENC_FAIL") == 0) deliver(CALI_TEV_ENC_FAIL, 2, 0, NULL, 0);
        else if (strcmp(word, "DISCONNECTED") == 0) deliver(CALI_TEV_DISCONNECTED, 0x13, 0, NULL, 0);
        else if (strcmp(word, "DISCOVERED") == 0) deliver(CALI_TEV_DISCOVERED, n1, 0, NULL, 0);
        else if (strcmp(word, "HEARTBEAT") == 0) deliver(CALI_TEV_HEARTBEAT, n1, 0x1003, NULL, 0);
        else if (strcmp(word, "READ") == 0) {
            len = unhex(a3[0] ? a3 : "0102", data, sizeof data);
            int st = (int)strtol(a2, NULL, 10);
            deliver(CALI_TEV_READ, st, (uint16_t)strtoul(a1, NULL, 16), st ? NULL : data, st ? 0 : len);
        } else if (strcmp(word, "NOTIFY") == 0) {
            len = unhex(a2, data, sizeof data);
            deliver(CALI_TEV_NOTIFY, 0, (uint16_t)strtoul(a1, NULL, 16), data, len);
        } else if (strcmp(word, "tick") == 0) {
            uint64_t now = strtoull(a1, NULL, 10);
            cali_runner_tick(now);
            cali_session_tick(now);
        } else if (strcmp(word, "boot") == 0) {
            cali_session_boot();
            cali_console_line("status");
        } else if (strcmp(word, "bond") == 0) s_bond = n1;
        else if (strcmp(word, "fail") == 0) snprintf(s_fail, sizeof s_fail, "%s", a1);
        else printf("UNKNOWN %s\n", word);
    }
    return 0;
}
