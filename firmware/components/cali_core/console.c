/* console.c — the line protocol (#154). Contract: include/cali_console.h. */
#include "cali_console.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "cali_json.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_snapshot.h"
#include "pairing_consts.h"

void (*cali_console_on_quit)(void);

static const cali_transport_t *s_t;

#define N_OF(a) (sizeof (a) / sizeof *(a))

/* A SNAP of every function (14 functions, ~160 fields) is ~4.5 KB; built whole (behind the "STATE
 * "/"SNAP " line prefix, via cali_json), then written with one fputs so no other output can land
 * inside it. */
#define SNAP_MAX 8192
static char s_buf[SNAP_MAX];
static int s_over;

#define PREFIX_STATE "STATE "
#define PREFIX_SNAP "SNAP "

/* Writes prefix into s_buf and starts a cali_json_t right after it. */
static void begin_line(cali_json_t *j, const char *prefix) {
    size_t n = strlen(prefix);
    memcpy(s_buf, prefix, n);
    cali_json_begin(j, s_buf + n, SNAP_MAX - n);
}

/* Closes j's document, appends the trailing newline, and sets s_over on any overflow (of the
 * JSON itself, or of the one extra byte the newline needs). */
static void finish_line(cali_json_t *j, const char *prefix) {
    int n = cali_json_end(j);
    if (n < 0) {
        s_over = 1;
        return;
    }
    size_t total = strlen(prefix) + (size_t)n;
    if (total + 1 >= SNAP_MAX) {
        s_over = 1;
        return;
    }
    s_buf[total] = '\n';
    s_buf[total + 1] = '\0';
    s_over = 0;
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
    cali_json_t j;
    begin_line(&j, PREFIX_STATE);
    cali_json_key(&j, "state");
    cali_json_str(&j, st);
    cali_json_key(&j, "attempts");
    cali_json_int(&j, (long long)(unsigned)s->attempts);
    cali_json_key(&j, "error");
    if (err) cali_json_str(&j, err); else cali_json_null(&j);
    cali_json_key(&j, "address");
    if (address) cali_json_str(&j, address); else cali_json_null(&j);
    finish_line(&j, PREFIX_STATE);
    out_line();
}

void cali_console_snapshot(uint64_t t_ms) {
    cali_json_t j;
    begin_line(&j, PREFIX_SNAP);
    cali_json_key(&j, "t");
    cali_json_int(&j, (long long)t_ms);
    cali_json_key(&j, "fn");
    cali_json_obj_begin(&j);
    cali_snapshot_fn(&j);
    cali_json_obj_end(&j);
    finish_line(&j, PREFIX_SNAP);
    out_line();
}

/* calictl /api/pairing: with no pairing flow in this process it reports idle + the stored bond's
 * address (calictl/serve.py). The pairing SM is idle then; whether the link is up is the session's
 * business, not a pairing state. */
static void status(void) {
    cali_console_state(cali_runner_state(), cali_snapshot_pair_address(s_t));
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
