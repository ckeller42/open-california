/* runner_fake.c — a scripted fake transport driving the pairing runner, for
 * tests/firmware/test_runner_fake.py ONLY; not part of the ESP build. Compiled with
 * runner.c + pairing_sm.c by the host `cc` (no NimBLE, runs on macOS too).
 *
 * stdin, one line each:
 *   transport events (delivered through the sink the runner registered):
 *     FOUND | CONNECTED | CONNECT_FAIL [status] | PASSKEY_REQ | ENC_OK | ENC_FAIL [status]
 *     DISCONNECTED [reason] | READ <hex char> <status> | NOTIFY <hex char> | DISCOVERED [status]
 *   console-side commands:
 *     pair | passkey N | forget | tick <now_ms>
 *     fail <call>        the next call of that transport function returns -1
 * stdout:
 *   CALL <name> [arg]    each transport ACTION the runner makes (numbers decimal); the queries
 *                        set_sink/has_bond/identity are not printed
 *   STATE <json>         each cali_runner_on_state, keys state/attempts/error/address
 *   OTHER <ev> <char> <status>   each event forwarded to cali_runner_on_other
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_runner.h"
#include "pairing_consts.h"

#define FAKE_IDENTITY "C0:FF:EE:CA:11:F0"

static cali_tsink_t s_sink;
static void *s_ctx;
static char s_fail[32];

static int call(const char *name, const char *fmt_arg) {
    if (fmt_arg) printf("CALL %s %s\n", name, fmt_arg);
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
static int f_remove_bond(void) { return call("remove_bond", NULL); }
static int f_has_bond(void) { return 0; }
static const char *f_identity(void) { return FAKE_IDENTITY; }

static const cali_transport_t FAKE = {
    f_set_sink, f_start_scan, f_stop_scan, f_connect_found, f_connect_bonded, f_pair,
    f_inject_passkey, f_discover, f_read, f_subscribe, f_write_heartbeat, f_disconnect,
    f_remove_bond, f_has_bond, f_identity,
};

static void on_state(const cali_pair_state_t *s, const char *address) {
    const char *err = s->error < 5 ? PAIR_ERR_NAMES[s->error] : NULL;
    printf("STATE {\"state\":\"%s\",\"attempts\":%u,\"error\":", PAIR_STATE_NAMES[s->st],
           (unsigned)s->attempts);
    if (err) printf("\"%s\"", err); else printf("null");
    printf(",\"address\":");
    if (address) printf("\"%s\"}\n", address); else printf("null}\n");
}

static const char *const EV_NAMES[] = {
    "FOUND", "CONNECTED", "CONNECT_FAIL", "PASSKEY_REQ", "ENC_OK", "ENC_FAIL", "DISCONNECTED",
    "READ", "NOTIFY", "DISCOVERED",
};

static void on_other(const cali_tevent_t *e) {
    printf("OTHER %s %u %d\n", EV_NAMES[e->ev], (unsigned)e->char_short, e->status);
}

static void deliver(cali_tev_t ev, int status, uint16_t c) {
    static const uint8_t payload[2] = {0x01, 0x02};
    cali_tevent_t e;
    memset(&e, 0, sizeof e);
    e.ev = ev;
    e.status = status;
    e.char_short = c;
    if ((ev == CALI_TEV_READ && status == 0) || ev == CALI_TEV_NOTIFY) {
        e.data = payload;
        e.len = sizeof payload;
    }
    if (ev == CALI_TEV_FOUND) strcpy(e.addr, "5A:11:22:33:44:55");
    if (s_sink) s_sink(&e, s_ctx);
}

int main(void) {
    char line[128], word[32];
    setvbuf(stdout, NULL, _IOLBF, 0);
    cali_runner_on_state = on_state;
    cali_runner_on_other = on_other;
    cali_runner_init(&FAKE);

    while (fgets(line, sizeof line, stdin)) {
        char a1[32] = "", a2[32] = "";
        if (sscanf(line, "%31s %31s %31s", word, a1, a2) < 1) continue;
        int n1 = (int)strtol(a1, NULL, 10);
        if (strcmp(word, "FOUND") == 0) deliver(CALI_TEV_FOUND, 0, 0);
        else if (strcmp(word, "CONNECTED") == 0) deliver(CALI_TEV_CONNECTED, 0, 0);
        else if (strcmp(word, "CONNECT_FAIL") == 0) deliver(CALI_TEV_CONNECT_FAIL, a1[0] ? n1 : 2, 0);
        else if (strcmp(word, "PASSKEY_REQ") == 0) deliver(CALI_TEV_PASSKEY_REQ, 0, 0);
        else if (strcmp(word, "ENC_OK") == 0) deliver(CALI_TEV_ENC_OK, 0, 0);
        else if (strcmp(word, "ENC_FAIL") == 0) deliver(CALI_TEV_ENC_FAIL, a1[0] ? n1 : 2, 0);
        else if (strcmp(word, "DISCONNECTED") == 0) deliver(CALI_TEV_DISCONNECTED, a1[0] ? n1 : 0x13, 0);
        else if (strcmp(word, "READ") == 0)
            deliver(CALI_TEV_READ, (int)strtol(a2, NULL, 10), (uint16_t)strtoul(a1, NULL, 16));
        else if (strcmp(word, "NOTIFY") == 0) deliver(CALI_TEV_NOTIFY, 0, (uint16_t)strtoul(a1, NULL, 16));
        else if (strcmp(word, "DISCOVERED") == 0) deliver(CALI_TEV_DISCOVERED, n1, 0);
        else if (strcmp(word, "pair") == 0) cali_runner_start();
        else if (strcmp(word, "passkey") == 0) cali_runner_passkey((uint32_t)strtoul(a1, NULL, 10));
        else if (strcmp(word, "forget") == 0) cali_runner_forget();
        else if (strcmp(word, "tick") == 0) cali_runner_tick(strtoull(a1, NULL, 10));
        else if (strcmp(word, "fail") == 0) snprintf(s_fail, sizeof s_fail, "%s", a1);
        else printf("UNKNOWN %s\n", word);
    }
    return 0;
}
