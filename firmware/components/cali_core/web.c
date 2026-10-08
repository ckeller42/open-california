/* web.c — the firmware's HTTP endpoints + status/setup page (#154). Contract: include/cali_web.h.
 * Proven by tests/firmware/test_web_handlers.py (test/web_cli.c).
 */
#include "cali_web.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_captive.h"
#include "cali_control.h"
#include "cali_json.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_snapshot.h"
#include "cali_status.h"
#include "cali_wifi_run.h"
#include "cali_wifi_sm.h"
#include "pairing_consts.h"
#include "strings_gen.h"   /* WEB_INDEX_HTML: firmware/web/index_gen.html, generated */
#include "app_bundle_gen.h"   /* WEB_APP_HTML_GZ: the calictl web UI bundle (satellite UI), generated */

/* The JSON response buffer; a test build may shrink it (-DCALI_WEB_JSON_MAX=64) to force overflow. */
#ifndef CALI_WEB_JSON_MAX
#define CALI_WEB_JSON_MAX NET_JSON_MAX
#endif

#define N_OF(a) (sizeof (a) / sizeof *(a))
#define JSON_TYPE "application/json"
#define PROBE_TARGET "http://" NET_AP_ADDR "/"

static const cali_transport_t *s_t;
/* Response bodies: the core is single-connection and never calls the handler again before it closed
 * the connection, so one static buffer per kind outlives every response that points into it. */
static char s_json[CALI_WEB_JSON_MAX];

static const char OK_BODY[] = "{\"ok\":true}";
static const char ERR_JSON[] = "{\"ok\":false,\"error\":\"json\"}";
static const char ERR_SSID[] = "{\"ok\":false,\"error\":\"ssid\"}";
static const char ERR_PSK[] = "{\"ok\":false,\"error\":\"psk\"}";
static const char ERR_STORE[] = "{\"ok\":false,\"error\":\"store\"}";
static const char ERR_METHOD[] = "{\"ok\":false,\"error\":\"method\"}";
static const char ERR_BAD_JSON[] = "{\"ok\":false,\"error\":\"bad_json\"}";
static const char ERR_MISSING[] = "{\"ok\":false,\"error\":\"missing_function_or_what\"}";
static const char ERR_CONFIRM[] = "{\"ok\":false,\"error\":\"confirm_required\"}";
static const char ERR_SETUP_MODE[] = "{\"ok\":false,\"error\":\"setup_mode\"}";
static const char ERR_BAD_VALUE[] = "{\"ok\":false,\"error\":\"bad_value\"}";
static const char OVERFLOW_BODY[] = "response too large";
/* /api/pairing errors: calictl's own bodies (web.py), no "ok" member */
static const char ERR_P_BAD_JSON[] = "{\"error\":\"bad_json\"}";
static const char ERR_P_BAD_ACTION[] = "{\"error\":\"bad_action\"}";
static const char ERR_P_CONFIRM[] = "{\"error\":\"confirm_required\"}";
static const char ERR_P_BAD_PASSKEY[] = "{\"error\":\"bad_passkey\"}";
static const char ERR_P_BUSY[] = "{\"error\":\"busy\"}";

/* See cali_web.h / cali_wifi_run.h for the mapping and why a setup-flow join reports "off". */
static int wifi_mode(void) { return cali_wifi_mode(cali_wifi_run_state()); }

static void set_body(cali_http_resp_t *resp, int status, const char *type, const char *body, size_t len) {
    resp->status = status;
    resp->content_type = type;
    resp->body = body;
    resp->body_len = len;
}

#define SET_CONST(resp, status, body) set_body((resp), (status), JSON_TYPE, (body), sizeof (body) - 1)

/* The "mode","ssid","ip","rssi" members shared by /api/state's device.wifi and /api/wifi. */
static void wifi_members(cali_json_t *j, const cali_status_t *st) {
    cali_json_key(j, "mode");
    cali_json_str(j, cali_wifi_mode_name(st->wifi_mode));
    cali_json_key(j, "ssid");
    if (st->ssid[0]) cali_json_str(j, st->ssid); else cali_json_null(j);
    cali_json_key(j, "ip");
    if (st->ip) {
        char a[16];
        cali_status_ip_str(st->ip, a);
        cali_json_str(j, a);
    } else {
        cali_json_null(j);
    }
    cali_json_key(j, "rssi");
    if (st->rssi) cali_json_int(j, st->rssi); else cali_json_null(j);
}

/* Closes j into s_json as the response, or answers 500 + logs on overflow. */
static void finish_json(cali_json_t *j, cali_http_resp_t *resp) {
    int n = cali_json_end(j);
    if (n < 0) {
        cali_log("http: overflow");
        set_body(resp, 500, "text/plain", OVERFLOW_BODY, sizeof OVERFLOW_BODY - 1);
        return;
    }
    set_body(resp, 200, JSON_TYPE, s_json, (size_t)n);
}

static void api_state(cali_http_resp_t *resp) {
    cali_json_t j;
    cali_status_t st;
    cali_status_get(&st, s_t, cali_uptime_ms());

    cali_json_begin(&j, s_json, sizeof s_json);
    cali_json_key(&j, "t");
    cali_json_int(&j, (long long)st.uptime_ms);
    cali_json_key(&j, "fn");
    cali_json_obj_begin(&j);
    cali_snapshot_fn(&j);
    cali_json_obj_end(&j);
    cali_json_key(&j, "device");
    cali_json_obj_begin(&j);

    cali_json_key(&j, "pairing");
    cali_json_obj_begin(&j);
    cali_json_key(&j, "state");
    cali_json_str(&j, (size_t)st.pair_state < N_OF(PAIR_STATE_NAMES) && PAIR_STATE_NAMES[st.pair_state]
                          ? PAIR_STATE_NAMES[st.pair_state]
                          : "unknown");
    cali_json_key(&j, "address");
    if (st.address[0]) cali_json_str(&j, st.address); else cali_json_null(&j);
    cali_json_obj_end(&j);

    cali_json_key(&j, "link");
    cali_json_obj_begin(&j);
    cali_json_key(&j, "up");
    cali_json_bool(&j, st.link_up);
    cali_json_key(&j, "last_snap_age_ms");
    if (st.snap_age_ms >= 0) cali_json_int(&j, (long long)st.snap_age_ms); else cali_json_null(&j);
    cali_json_obj_end(&j);

    cali_json_key(&j, "wifi");
    cali_json_obj_begin(&j);
    wifi_members(&j, &st);
    cali_json_obj_end(&j);

    cali_json_key(&j, "control");   /* POST /api/command is accepted: station mode only (ruling B) */
    cali_json_obj_begin(&j);
    cali_json_key(&j, "writes");
    cali_json_bool(&j, st.wifi_mode == CALI_WIFI_MODE_STATION);
    cali_json_obj_end(&j);

    cali_json_key(&j, "uptime_ms");
    cali_json_int(&j, (long long)st.uptime_ms);
    cali_json_key(&j, "fw");
    cali_json_str(&j, st.fw);
    cali_json_obj_end(&j);
    finish_json(&j, resp);
}

static void api_wifi_get(cali_http_resp_t *resp) {
    cali_json_t j;
    const cali_net_ap_t *aps = NULL;
    int n = cali_wifi_run_scan_list(&aps);
    cali_status_t st;

    cali_status_get(&st, s_t, cali_uptime_ms());
    cali_json_begin(&j, s_json, sizeof s_json);
    wifi_members(&j, &st);
    cali_json_key(&j, "last_error");
    if (st.last_fail[0]) cali_json_str(&j, st.last_fail); else cali_json_null(&j);
    cali_json_key(&j, "scan");
    cali_json_arr_begin(&j);
    for (int i = 0; aps && i < n && i < NET_SCAN_MAX; i++) {
        cali_json_obj_begin(&j);
        cali_json_key(&j, "ssid");
        cali_json_str(&j, aps[i].ssid);
        cali_json_key(&j, "rssi");
        cali_json_int(&j, aps[i].rssi);
        cali_json_key(&j, "secure");
        cali_json_bool(&j, aps[i].secure != 0);
        cali_json_obj_end(&j);
    }
    cali_json_arr_end(&j);
    finish_json(&j, resp);
    /* the list above is the last SCAN_DONE; a fresh one for a later GET — at most one per
     * NET_SCAN_MIN_INTERVAL_MS (the page GETs twice on load, and each scan takes the radio off the
     * hotspot's channel), held back while a BLE pairing flow is active (the coex gate, ruling R15) */
    if (st.wifi_mode == CALI_WIFI_MODE_SETUP) cali_wifi_run_scan_auto();
}

/* ---- the fixed-shape {"ssid":"…","psk":"…"} parser ---- */

typedef struct {
    const char *p, *end;
} cursor_t;

static void skip_ws(cursor_t *c) {
    while (c->p < c->end && (*c->p == ' ' || *c->p == '\t' || *c->p == '\r' || *c->p == '\n')) c->p++;
}

static int expect(cursor_t *c, char ch) {
    skip_ws(c);
    if (c->p == c->end || *c->p != ch) return -1;
    c->p++;
    return 0;
}

/* A quoted string with only \" and \\ escapes and no byte < 0x20 (NUL included). Copies at most
 * cap - 1 decoded bytes into out (NUL-terminated); *len = the whole decoded length, which may exceed
 * cap - 1 (the caller rejects it by length, not as malformed). 0 ok, -1 malformed. */
static int parse_str(cursor_t *c, char *out, size_t cap, size_t *len) {
    size_t n = 0;
    if (expect(c, '"') != 0) return -1;
    for (;;) {
        if (c->p == c->end) return -1;
        unsigned char ch = (unsigned char)*c->p++;
        if (ch == '"') break;
        if (ch < 0x20) return -1;
        if (ch == '\\') {
            if (c->p == c->end || (*c->p != '"' && *c->p != '\\')) return -1;
            ch = (unsigned char)*c->p++;
        }
        if (n + 1 < cap) out[n] = (char)ch;
        n++;
    }
    out[n + 1 < cap ? n : cap - 1] = '\0';
    *len = n;
    return 0;
}

/* 0 ok (ssid/psk filled, lengths set), -1 not exactly {"ssid":"…","psk":"…"} in either order. */
static int parse_creds(const char *body, size_t body_len, char *ssid, size_t *ssid_len, char *psk,
                       size_t *psk_len) {
    cursor_t c = {body, body + body_len};
    int seen_ssid = 0, seen_psk = 0;
    if (expect(&c, '{') != 0) return -1;
    for (int m = 0; m < 2; m++) {
        char key[8];
        size_t klen;
        if (parse_str(&c, key, sizeof key, &klen) != 0 || expect(&c, ':') != 0) return -1;
        if (klen == 4 && strcmp(key, "ssid") == 0 && !seen_ssid) {
            seen_ssid = 1;
            if (parse_str(&c, ssid, NET_SSID_MAX + 1, ssid_len) != 0) return -1;
        } else if (klen == 3 && strcmp(key, "psk") == 0 && !seen_psk) {
            seen_psk = 1;
            if (parse_str(&c, psk, NET_PSK_MAX + 1, psk_len) != 0) return -1;
        } else {
            return -1;
        }
        if (expect(&c, m == 0 ? ',' : '}') != 0) return -1;
    }
    skip_ws(&c);
    return c.p == c.end ? 0 : -1;
}

static void api_wifi_post(const cali_http_req_t *req, cali_http_resp_t *resp) {
    static char ssid[NET_SSID_MAX + 1], psk[NET_PSK_MAX + 1];
    size_t ssid_len = 0, psk_len = 0;
    if (parse_creds(req->body, req->body_len, ssid, &ssid_len, psk, &psk_len) != 0) {
        SET_CONST(resp, 400, ERR_JSON);
    } else if (ssid_len < 1 || ssid_len > NET_SSID_MAX) {
        SET_CONST(resp, 400, ERR_SSID);
    } else if (psk_len < NET_PSK_MIN || psk_len > NET_PSK_MAX) {
        SET_CONST(resp, 400, ERR_PSK);
    } else if (cali_kv_set(CALI_WIFI_KEY_SSID, ssid, ssid_len) != 0 ||
               cali_kv_set(CALI_WIFI_KEY_PSK, psk, psk_len) != 0) {
        cali_log("http: storing wifi credentials failed");
        SET_CONST(resp, 500, ERR_STORE);
    } else {
        cali_wifi_run_set_creds(ssid, psk);
        SET_CONST(resp, 200, OK_BODY);
    }
    memset(psk, 0, sizeof psk);
}

/* ---- POST /api/command: {"function","what","value","confirm","local_now"} (any order, each at most once) ---- */

typedef struct {
    char fn[24], what[32], value[CALI_CTL_VALUE_MAX];
    int confirm;
    int has_local_now, local_now_bad;   /* given (not null); given but no clock (-> 400 bad_value) */
    long long local_now;
} cmd_t;

static int parse_bool(cursor_t *c, int *out) {
    skip_ws(c);
    if (c->end - c->p >= 4 && memcmp(c->p, "true", 4) == 0) { c->p += 4; *out = 1; return 0; }
    if (c->end - c->p >= 5 && memcmp(c->p, "false", 5) == 0) { c->p += 5; *out = 0; return 0; }
    return -1;
}

/* value: a string (decoded), an integer -?(0|[1-9][0-9]*) passed on as its text, or null (-> "");
 * -1 for anything else — true/false, an object/array, a fraction or exponent (its '.'/'e' is then
 * neither ',' nor '}' for the caller) — and for a value of CALI_CTL_VALUE_MAX bytes or more. */
static int parse_value(cursor_t *c, char *out, size_t cap) {
    size_t n = 0, len;
    skip_ws(c);
    if (c->p < c->end && *c->p == '"') return parse_str(c, out, cap, &len) == 0 && len < cap ? 0 : -1;
    if (c->end - c->p >= 4 && memcmp(c->p, "null", 4) == 0) {
        c->p += 4;
        out[0] = 0;
        return 0;
    }
    if (c->p < c->end && *c->p == '-') out[n++] = *c->p++;
    if (c->p == c->end || *c->p < '0' || *c->p > '9') return -1;
    if (*c->p == '0' && c->p + 1 < c->end && c->p[1] >= '0' && c->p[1] <= '9') return -1;
    while (c->p < c->end && *c->p >= '0' && *c->p <= '9') {
        if (n + 1 >= cap) return -1;
        out[n++] = *c->p++;
    }
    out[n] = 0;
    return 0;
}

#define LOCAL_NOW_MIN 1767225600LL   /* 2026-01-01T00:00Z: an earlier browser clock is wrong */

static int skip_digits(cursor_t *c) {   /* 0 if at least one digit was skipped, else -1 */
    const char *p0 = c->p;
    while (c->p < c->end && *c->p >= '0' && *c->p <= '9') c->p++;
    return c->p > p0 ? 0 : -1;
}

/* local_now: the page's wall clock read as UTC (s), the wake-up builder's only clock (spec 2026-10-07,
 * ruling R1: no skew check). null = absent (the builder refuses with the clock reason). An integer
 * >= LOCAL_NOW_MIN is the clock, read saturated at LLONG_MAX (past the 32-bit Timestamp the builder
 * answers BAD -> 400 bad_value, never a frame). Any other JSON scalar — a string, a fraction or
 * exponent, true/false, an earlier integer — sets local_now_bad. -1 malformed. */
static int parse_local_now(cursor_t *c, cmd_t *cmd) {
    long long v = 0;
    int neg = 0, integer = 1, bool_v;
    skip_ws(c);
    if (c->end - c->p >= 4 && memcmp(c->p, "null", 4) == 0) {
        c->p += 4;
        return 0;
    }
    cmd->has_local_now = 1;
    cmd->local_now_bad = 1;
    if (c->p < c->end && *c->p == '"') {
        char s[2];
        size_t len;
        return parse_str(c, s, sizeof s, &len);
    }
    if (parse_bool(c, &bool_v) == 0) return 0;
    if (c->p < c->end && *c->p == '-') {
        neg = 1;
        c->p++;
    }
    if (c->p == c->end || *c->p < '0' || *c->p > '9') return -1;
    if (*c->p == '0' && c->p + 1 < c->end && c->p[1] >= '0' && c->p[1] <= '9') return -1;
    while (c->p < c->end && *c->p >= '0' && *c->p <= '9') {
        int d = *c->p++ - '0';
        v = v > (LLONG_MAX - d) / 10 ? LLONG_MAX : v * 10 + d;
    }
    if (c->p < c->end && *c->p == '.') {
        c->p++;
        integer = 0;
        if (skip_digits(c) != 0) return -1;
    }
    if (c->p < c->end && (*c->p == 'e' || *c->p == 'E')) {
        c->p++;
        integer = 0;
        if (c->p < c->end && (*c->p == '+' || *c->p == '-')) c->p++;
        if (skip_digits(c) != 0) return -1;
    }
    cmd->local_now = v;
    cmd->local_now_bad = !integer || neg || v < LOCAL_NOW_MIN;
    return 0;
}

/* 0 ok, -1 not one object of the five known keys (each at most once; an over-long function, what
 * or value is malformed too — calictl would answer "unknown function"/CommandError, both a 4xx). */
static int parse_command(const char *body, size_t body_len, cmd_t *cmd) {
    cursor_t c = {body, body + body_len};
    unsigned seen = 0;
    memset(cmd, 0, sizeof *cmd);
    if (expect(&c, '{') != 0) return -1;
    skip_ws(&c);
    if (c.p < c.end && *c.p == '}') {
        c.p++;
    } else {
        for (;;) {
            char key[12];
            size_t klen, vlen;
            unsigned bit;
            int bad;
            if (parse_str(&c, key, sizeof key, &klen) != 0 || klen >= sizeof key || expect(&c, ':') != 0) return -1;
            if (strcmp(key, "function") == 0) {
                bit = 1;
                bad = parse_str(&c, cmd->fn, sizeof cmd->fn, &vlen) != 0 || vlen >= sizeof cmd->fn;
            } else if (strcmp(key, "what") == 0) {
                bit = 2;
                bad = parse_str(&c, cmd->what, sizeof cmd->what, &vlen) != 0 || vlen >= sizeof cmd->what;
            } else if (strcmp(key, "value") == 0) {
                bit = 4;
                bad = parse_value(&c, cmd->value, sizeof cmd->value) != 0;
            } else if (strcmp(key, "confirm") == 0) {
                bit = 8;
                bad = parse_bool(&c, &cmd->confirm) != 0;
            } else if (strcmp(key, "local_now") == 0) {
                bit = 16;
                bad = parse_local_now(&c, cmd) != 0;
            } else {
                return -1;
            }
            if (bad || (seen & bit)) return -1;
            seen |= bit;
            skip_ws(&c);
            if (c.p < c.end && *c.p == ',') {
                c.p++;
                continue;
            }
            if (expect(&c, '}') != 0) return -1;
            break;
        }
    }
    skip_ws(&c);
    return c.p == c.end ? 0 : -1;
}

/* The one command the single-connection core can have open: its function (echoed in the answer)
 * and CALI_CTL_PENDING while the sequencer runs it, then the done callback's result. */
static char s_cmd_fn[24];
static int s_cmd = CALI_CTL_OK;
static const char *s_cmd_reason;   /* the done callback's refusal text (static), else NULL */

static void cmd_done(int result, const char *reason) {
    s_cmd = result;
    s_cmd_reason = reason;
}

/* calictl's /api/command answers (serve.ServeBackend.command / web.py), plus the ESP's own codes:
 * applied is never true (no readback check), a refusal is calictl's {"applied":false,"refused":…}. */
static void cmd_answer(cali_http_resp_t *resp, int rc, const char *reason) {
    cali_json_t j;
    const char *err = NULL;
    int status = 200;
    switch (rc) {
    case CALI_CTL_OK: case CALI_CTL_REFUSED: case CALI_CTL_ELSEWHERE: break;
    case CALI_CTL_BAD_VALUE: status = 400; err = "bad_value"; break;
    case CALI_CTL_NONE: status = 400; err = "unknown_control"; break;
    case CALI_CTL_BUSY: status = 409; err = "busy"; break;
    case CALI_CTL_NOT_READY: status = 503; err = "not_connected"; break;
    case CALI_CTL_TIMEOUT: status = 504; err = "write_timeout"; break;
    default: status = 502; err = "write_failed"; break;   /* FAILED (and PENDING: a bug) */
    }
    cali_json_begin(&j, s_json, sizeof s_json);
    cali_json_key(&j, "ok");
    cali_json_bool(&j, err == NULL);
    if (err) {
        cali_json_key(&j, "error");
        cali_json_str(&j, err);
    } else {
        cali_json_key(&j, "applied");
        if (reason) cali_json_bool(&j, 0); else cali_json_null(&j);
        if (reason) {
            cali_json_key(&j, "refused");
            cali_json_str(&j, reason);
        }
        cali_json_key(&j, "state");
        cali_json_null(&j);
        cali_json_key(&j, "error");
        cali_json_null(&j);
        cali_json_key(&j, "function");
        cali_json_str(&j, s_cmd_fn);
    }
    finish_json(&j, resp);   /* 200, or 500 on overflow */
    if (resp->status == 200) resp->status = status;
}

static int api_command(const cali_http_req_t *req, cali_http_resp_t *resp) {
    static cmd_t cmd;   /* static: the 8 KB host-task stack */
    const char *reason = NULL;
    int rc;
    if (req->resume) {   /* the core re-asks while we wait for the sequencer */
        if (s_cmd == CALI_CTL_PENDING) return CALI_HTTP_PENDING;
        cmd_answer(resp, s_cmd, s_cmd_reason);
        return 1;
    }
    if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
    } else if (wifi_mode() != CALI_WIFI_MODE_STATION) {   /* never over the setup hotspot (ruling B) */
        SET_CONST(resp, 403, ERR_SETUP_MODE);
    } else if (parse_command(req->body, req->body_len, &cmd) != 0) {
        SET_CONST(resp, 400, ERR_BAD_JSON);
    } else if (!cmd.fn[0] || !cmd.what[0]) {
        SET_CONST(resp, 400, ERR_MISSING);
    } else if ((strcmp(cmd.fn, "airheater") == 0 || strcmp(cmd.fn, "roof") == 0) && !cmd.confirm) {
        SET_CONST(resp, 400, ERR_CONFIRM);   /* web.py CONFIRM_REQUIRED */
    } else if (cmd.local_now_bad) {
        SET_CONST(resp, 400, ERR_BAD_VALUE);
    } else {
        snprintf(s_cmd_fn, sizeof s_cmd_fn, "%s", cmd.fn);
        s_cmd = CALI_CTL_PENDING;
        s_cmd_reason = NULL;
        rc = cali_ctl_submit(cmd.fn, cmd.what, cmd.value, cmd.has_local_now ? (int64_t)cmd.local_now : -1,
                             cmd_done, &reason);
        if (rc == CALI_CTL_PENDING) return CALI_HTTP_PENDING;
        s_cmd = rc;
        cmd_answer(resp, rc, reason);
    }
    return 1;
}

/* ---- /api/pairing: calictl's wizard endpoint (web.py + serve.pairing_command) ---- */

typedef struct {
    char action[12], value[8];
    size_t action_len, value_len;
    int value_str, confirm;
} pair_req_t;

/* 0 ok, -1 not one object of the keys action (string), value (string, integer or null), confirm
 * (bool), each at most once. */
static int parse_pairing(const char *body, size_t body_len, pair_req_t *r) {
    cursor_t c = {body, body + body_len};
    unsigned seen = 0;
    memset(r, 0, sizeof *r);
    if (expect(&c, '{') != 0) return -1;
    skip_ws(&c);
    if (c.p < c.end && *c.p == '}') {
        c.p++;
    } else {
        for (;;) {
            char key[10], other[CALI_CTL_VALUE_MAX];
            size_t klen;
            unsigned bit;
            int bad;
            if (parse_str(&c, key, sizeof key, &klen) != 0 || klen >= sizeof key || expect(&c, ':') != 0) return -1;
            skip_ws(&c);
            if (strcmp(key, "action") == 0) {
                bit = 1;
                bad = parse_str(&c, r->action, sizeof r->action, &r->action_len) != 0;
            } else if (strcmp(key, "value") == 0) {
                bit = 2;
                r->value_str = c.p < c.end && *c.p == '"';
                bad = r->value_str ? parse_str(&c, r->value, sizeof r->value, &r->value_len) != 0
                                   : parse_value(&c, other, sizeof other) != 0;
            } else if (strcmp(key, "confirm") == 0) {
                bit = 4;
                bad = parse_bool(&c, &r->confirm) != 0;
            } else {
                return -1;
            }
            if (bad || (seen & bit)) return -1;
            seen |= bit;
            skip_ws(&c);
            if (c.p < c.end && *c.p == ',') {
                c.p++;
                continue;
            }
            if (expect(&c, '}') != 0) return -1;
            break;
        }
    }
    skip_ws(&c);
    return c.p == c.end ? 0 : -1;
}

/* calictl's pairing_snapshot(): the console's STATE members + radio_busy (always false here: no
 * co-resident BLE scanner on the ESP). */
static void pairing_snapshot(cali_http_resp_t *resp) {
    cali_json_t j;
    cali_json_begin(&j, s_json, sizeof s_json);
    cali_snapshot_pairing(&j, cali_runner_state(), cali_snapshot_pair_address(s_t));
    cali_json_key(&j, "radio_busy");
    cali_json_bool(&j, 0);
    finish_json(&j, resp);
}

static int is_action(const pair_req_t *r, const char *name) {
    return r->action_len < sizeof r->action && strcmp(r->action, name) == 0;
}

/* In every WiFi mode (connection management, not a control write). web.py's checks in its order;
 * then the runner call the console's pair / passkey N / forget (and cancel) make, and the
 * post-action snapshot. start/reset need the single link: 409 busy while a control command runs. */
static void api_pairing(const cali_http_req_t *req, cali_http_resp_t *resp) {
    static pair_req_t r;
    int start, passkey, cancel, reset;
    if (strcmp(req->method, "GET") == 0) {
        pairing_snapshot(resp);
        return;
    }
    if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
        return;
    }
    if (parse_pairing(req->body, req->body_len, &r) != 0) {
        SET_CONST(resp, 400, ERR_P_BAD_JSON);
        return;
    }
    start = is_action(&r, "start");
    passkey = is_action(&r, "passkey");
    cancel = is_action(&r, "cancel");
    reset = is_action(&r, "reset");
    if (!start && !passkey && !cancel && !reset) {
        SET_CONST(resp, 400, ERR_P_BAD_ACTION);
    } else if (reset && !r.confirm) {
        SET_CONST(resp, 400, ERR_P_CONFIRM);
    } else if (passkey && !(r.value_str && r.value_len == 6 && strspn(r.value, "0123456789") == 6)) {
        SET_CONST(resp, 400, ERR_P_BAD_PASSKEY);
    } else if ((start || reset) && cali_ctl_busy()) {
        SET_CONST(resp, 409, ERR_P_BUSY);
    } else {
        if (start) cali_runner_start();
        else if (passkey) cali_runner_passkey((uint32_t)strtoul(r.value, NULL, 10));
        else if (cancel) {   /* only a running flow: a stale cancel never drops a bonded link */
            uint8_t st = cali_runner_state()->st;
            if (st == PAIR_SCANNING || st == PAIR_CONNECTING || st == PAIR_PAIRING ||
                st == PAIR_WAITING_PASSKEY || st == PAIR_VERIFYING)
                cali_runner_cancel();
        }
        else cali_runner_forget();
        pairing_snapshot(resp);
    }
}

int cali_web_handle(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx) {
    int get = strcmp(req->method, "GET") == 0;
    (void)ctx;
    if (strcmp(req->path, "/api/command") == 0) return api_command(req, resp);
    if (get && strcmp(req->path, "/") == 0) {
        if (wifi_mode() == CALI_WIFI_MODE_STATION) {   /* the calictl web UI (R_FW_SHARED_UI) */
            set_body(resp, 200, "text/html; charset=utf-8", (const char *)WEB_APP_HTML_GZ, WEB_APP_HTML_GZ_LEN);
            resp->content_encoding = "gzip";
        } else {                                       /* setup hotspot / setup-flow join: the setup page */
            set_body(resp, 200, "text/html; charset=utf-8", (const char *)WEB_INDEX_HTML, WEB_INDEX_HTML_LEN);
        }
        return 1;
    }
    if (get && strcmp(req->path, "/app") == 0) {       /* the calictl web UI in every mode: the wizard over the hotspot */
        set_body(resp, 200, "text/html; charset=utf-8", (const char *)WEB_APP_HTML_GZ, WEB_APP_HTML_GZ_LEN);
        resp->content_encoding = "gzip";
        return 1;
    }
    if (strcmp(req->path, "/api/pairing") == 0) {
        api_pairing(req, resp);
        return 1;
    }
    if (get && strcmp(req->path, "/device") == 0) {    /* the status/setup page, in every mode */
        set_body(resp, 200, "text/html; charset=utf-8", (const char *)WEB_INDEX_HTML, WEB_INDEX_HTML_LEN);
        return 1;
    }
    if (strcmp(req->path, "/api/state") == 0) {
        if (get) api_state(resp);
        else SET_CONST(resp, 405, ERR_METHOD);
        return 1;
    }
    if (strcmp(req->path, "/api/wifi") == 0) {
        if (get) {
            api_wifi_get(resp);
        } else if (strcmp(req->method, "POST") == 0) {
            api_wifi_post(req, resp);
        } else if (strcmp(req->method, "DELETE") == 0) {
            cali_wifi_run_forget();
            SET_CONST(resp, 200, OK_BODY);
        } else {
            SET_CONST(resp, 405, ERR_METHOD);
        }
        return 1;
    }
    if (wifi_mode() == CALI_WIFI_MODE_SETUP) {
        /* captive portal: an OS probe gets the absolute setup address (its Host is some probe domain);
         * anything else goes home */
        resp->status = 302;
        resp->location = cali_captive_is_probe(req->path) ? PROBE_TARGET : "/";
        return 1;
    }
    return 0;
}

int cali_web_init(const cali_net_t *net, const cali_transport_t *t, uint16_t port) {
    s_t = t;
    return cali_http_init(net, port, cali_web_handle, NULL);
}

void cali_web_poll(uint64_t now_ms) { cali_http_poll(now_ms); }
