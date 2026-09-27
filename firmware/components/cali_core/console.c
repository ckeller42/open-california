/* console.c — the line protocol (#154). Contract: include/cali_console.h. */
#include "cali_console.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "codec.h"
#include "codec_chars.h"
#include "pairing_consts.h"

void (*cali_console_on_quit)(void);

static const cali_transport_t *s_t;

#define N_OF(a) (sizeof (a) / sizeof *(a))

/* A SNAP of every function (14 functions, ~160 fields) is ~4.5 KB; built whole, then written with
 * one fputs so no other output can land inside it. */
#define SNAP_MAX 8192
static char s_buf[SNAP_MAX];
static size_t s_pos;
static int s_over;

static void put(const char *fmt, ...)
#if defined(__GNUC__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;

static void put(const char *fmt, ...) {
    if (s_over) return;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(s_buf + s_pos, SNAP_MAX - s_pos, fmt, ap);
    va_end(ap);
    if (n < 0 || (size_t)n >= SNAP_MAX - s_pos) s_over = 1;
    else s_pos += (size_t)n;
}

static void out_line(void) {
    if (s_over) {
        cali_log("console: output line longer than %u bytes dropped", (unsigned)SNAP_MAX);
        return;
    }
    fputs(s_buf, stdout);
    fflush(stdout);
}

void cali_console_state(const cali_pair_state_t *s, const char *address) {
    /* bounds = the generated tables' own lengths (pairing_consts.h), never hand-typed counts */
    const char *st = (size_t)s->st < N_OF(PAIR_STATE_NAMES) ? PAIR_STATE_NAMES[s->st] : "unknown";
    const char *err = (size_t)s->error < N_OF(PAIR_ERR_NAMES) ? PAIR_ERR_NAMES[s->error] : NULL;
    s_pos = 0;
    s_over = 0;
    put("STATE {\"state\":\"%s\",\"attempts\":%u,\"error\":", st, (unsigned)s->attempts);
    if (err) put("\"%s\"", err); else put("null");
    if (address) put(",\"address\":\"%s\"}\n", address); else put(",\"address\":null}\n");
    out_line();
}

void cali_console_snapshot(uint64_t t_ms) {
    codec_kv_t kv[CODEC_KV_MAX];
    int first = 1;
    s_pos = 0;
    s_over = 0;
    put("SNAP {\"t\":%llu,\"fn\":{", (unsigned long long)t_ms);
    for (size_t i = 0; i < CODEC_NCHARS; i++) {
        const uint8_t *frame;
        size_t len;
        const codec_func_t *f = codec_func_by_name(CODEC_CHARS[i].function);
        if (!f || !cali_session_frame(i, &frame, &len)) continue;
        int n = codec_decode(f, frame, len, kv);
        put("%s\"%s\":{", first ? "" : ",", CODEC_CHARS[i].function);
        first = 0;
        for (int k = 0; k < n; k++) put("%s\"%s\":%lu", k ? "," : "", kv[k].name, (unsigned long)kv[k].value);
        put("}");
    }
    put("}}\n");
    out_line();
}

/* calictl /api/pairing: with no pairing flow in this process it reports idle + the stored bond's
 * address (calictl/serve.py). The pairing SM is idle then; whether the link is up is the session's
 * business, not a pairing state. */
static void status(void) {
    const cali_pair_state_t *s = cali_runner_state();
    const char *addr = NULL;
    if (s->st == PAIR_BONDED || (s->st == PAIR_IDLE && s_t->has_bond())) addr = s_t->identity();
    cali_console_state(s, addr);
}

static void on_state(const cali_pair_state_t *s, const char *address) {
    cali_console_state(s, address);
    if (s->st == PAIR_BONDED) cali_session_on_bonded();
    else cali_session_stop();
}

void cali_console_init(const cali_transport_t *t) {
    s_t = t;
    cali_runner_init(t);
    cali_session_init(t);
    cali_runner_on_state = on_state;
}

/* "N" of "passkey N": 1-6 decimal digits and nothing else, else -1. */
static long parse_passkey(const char *p) {
    long v = 0;
    int n = 0;
    while (*p == ' ' || *p == '\t') p++;
    for (; isdigit((unsigned char)*p); p++, n++) {
        if (n == 6) return -1;
        v = v * 10 + (*p - '0');
    }
    return (n == 0 || *p) ? -1 : v;
}

void cali_console_line(const char *line) {
    char cmd[80];
    size_t n = 0;
    while (*line && isspace((unsigned char)*line)) line++;
    while (line[n] && n < sizeof cmd - 1) {
        cmd[n] = line[n];
        n++;
    }
    while (n && isspace((unsigned char)cmd[n - 1])) n--;
    cmd[n] = 0;
    if (!n) return;

    if (strcmp(cmd, "pair") == 0) {
        cali_runner_start();
    } else if (strncmp(cmd, "passkey", 7) == 0 && (cmd[7] == ' ' || cmd[7] == '\t')) {
        long pk = parse_passkey(cmd + 7);
        if (pk < 0) cali_log("console: bad passkey");
        else cali_runner_passkey((uint32_t)pk);       /* ignored unless waiting_passkey */
    } else if (strcmp(cmd, "forget") == 0) {
        cali_runner_forget();
    } else if (strcmp(cmd, "status") == 0) {
        status();
    } else if (strcmp(cmd, "quit") == 0) {
        if (cali_console_on_quit) cali_console_on_quit();
    } else {
        cali_log("unknown command: %s", cmd);
    }
}
