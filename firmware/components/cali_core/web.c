/* web.c — the firmware's HTTP endpoints + status/setup page (#154). Contract: include/cali_web.h.
 * Proven by tests/firmware/test_web_handlers.py (test/web_cli.c).
 */
#include "cali_web.h"

#include <stdio.h>
#include <string.h>

#include "cali_captive.h"
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
static const char OVERFLOW_BODY[] = "response too large";

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
    /* the list above is the last SCAN_DONE; a fresh one for the next GET (the runner holds it back
     * while a BLE pairing flow is active: the one coex gate, ruling R15) */
    if (st.wifi_mode == CALI_WIFI_MODE_SETUP) cali_wifi_run_scan();
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

int cali_web_handle(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx) {
    int get = strcmp(req->method, "GET") == 0;
    (void)ctx;
    if (strcmp(req->path, "/") == 0 && get) {
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
