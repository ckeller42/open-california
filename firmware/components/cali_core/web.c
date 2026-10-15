/* web.c — the firmware's HTTP endpoints, the web UI bundle and the /device recovery page (#154). Contract: include/cali_web.h.
 * Proven by tests/firmware/test_web_handlers.py (test/web_cli.c).
 */
#include "cali_web.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_auto_camper.h"
#include "cali_battery.h"
#include "cali_captive.h"
#include "cali_control.h"
#include "cali_display_model.h"
#include "cali_json.h"
#include "cali_ota.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_sdlog.h"
#include "cali_sensors.h"
#include "cali_session.h"
#include "cali_time.h"
#include "cali_snapshot.h"
#include "cali_status.h"
#include "cali_wifi_run.h"
#include "cali_wifi_sm.h"
#include "pairing_consts.h"
#include "strings_gen.h"   /* WEB_INDEX_HTML: the /device recovery page (index_gen.html), generated */
#include "app_bundle_gen.h"   /* WEB_APP_HTML_GZ: the calictl web UI bundle (satellite UI), generated */
#include "pwa_assets_gen.h"   /* PWA_ASSETS: the manifest + icons the bundle links (home screen), generated */

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
static const char ERR_PM[] = "{\"ok\":false,\"error\":\"pm\"}";   /* #346 knob: DFS did not apply */
static const char ERR_METHOD[] = "{\"ok\":false,\"error\":\"method\"}";
static const char ERR_BAD_JSON[] = "{\"ok\":false,\"error\":\"bad_json\"}";
static const char ERR_MISSING[] = "{\"ok\":false,\"error\":\"missing_function_or_what\"}";
static const char ERR_CONFIRM[] = "{\"ok\":false,\"error\":\"confirm_required\"}";
static const char ERR_SETUP_MODE[] = "{\"ok\":false,\"error\":\"setup_mode\"}";
static const char ERR_BAD_VALUE[] = "{\"ok\":false,\"error\":\"bad_value\"}";
static const char ERR_BAD_ACTION[] = "{\"ok\":false,\"error\":\"bad_action\"}";
static const char ERR_BUSY[] = "{\"ok\":false,\"error\":\"busy\"}";
static const char ERR_OTA_UNAVAILABLE[] = "{\"ok\":false,\"error\":\"ota_unavailable\"}";
static const char ERR_DISABLED[] = "{\"ok\":false,\"error\":\"disabled\"}";   /* /api/sensors, flag off */
static const char OVERFLOW_BODY[] = "response too large";
/* /api/pairing errors: calictl's own bodies (web.py), no "ok" member */
static const char ERR_P_BAD_JSON[] = "{\"error\":\"bad_json\"}";
static const char ERR_P_BAD_ACTION[] = "{\"error\":\"bad_action\"}";
static const char ERR_P_CONFIRM[] = "{\"error\":\"confirm_required\"}";
static const char ERR_P_BAD_PASSKEY[] = "{\"error\":\"bad_passkey\"}";
static const char ERR_P_BUSY[] = "{\"error\":\"busy\"}";
/* /api/ux errors: calictl's own bodies (web.py), no "ok" member */
static const char ERR_UX_BAD[] = "{\"error\":\"bad_ux\"}";
static const char ERR_UX_TOO_LARGE[] = "{\"error\":\"too_large\"}";

/* See cali_web.h / cali_wifi_run.h for the mapping and why a setup-flow join reports "off". */
static int wifi_mode(void) { return cali_wifi_mode(cali_wifi_run_state()); }

#define HTML_TYPE "text/html; charset=utf-8"
#define NO_CACHE "no-cache"   /* HTML + manifest: always revalidate (a 304 when unchanged), so an OTA shows at once */

/* "Send usage data" (R_UX_TELEMETRY): NVS ux_on, default on, read once */
static int s_ux = -1;
static int ux_on(void) {
    if (s_ux < 0) {
        uint8_t b = 1;
        size_t n = 1;
        s_ux = cali_kv_get(CALI_UX_ON_KEY, &b, &n) == CALI_KV_OK && n == 1 ? b != 0 : 1;
    }
    return s_ux;
}

/* R_FW_EXPERIMENTAL: one NVS byte gates everything in the sensor track; the stable paths never read it. */
static int s_exp = -1;
int cali_exp_sensors(void) {
    if (s_exp < 0) {
        uint8_t b = 0;
        size_t n = 1;
        s_exp = cali_kv_get(CALI_EXP_SENSORS_KEY, &b, &n) == CALI_KV_OK && n == 1 ? b != 0 : 0;
    }
    return s_exp;
}

int cali_exp_sensors_set(int on) {
    uint8_t b = (uint8_t)(on != 0);
    if (cali_kv_set(CALI_EXP_SENSORS_KEY, &b, 1) != 0) return -1;
    s_exp = b;
    cali_log("exp: sensors %s", b ? "on" : "off");
    return 0;
}

static void set_body(cali_http_resp_t *resp, int status, const char *type, const char *body, size_t len) {
    resp->status = status;
    resp->content_type = type;
    resp->body = body;
    resp->body_len = len;
}

/* A static asset baked into the image: 200 + body, the given Cache-Control, and a strong ETag — the
 * FNV-1a 64 of the exact bytes served, so it changes with them on any build (dirty ones included),
 * and a matching If-None-Match gets a body-less 304 from the core. Hashed per request (~65 KB at
 * most, ~1 ms on the ESP); the tag lives in a static buffer: one connection, valid until its close.
 * ponytail: rehashed per request, memoize per asset if it ever shows in a profile. */
static void set_static(cali_http_resp_t *resp, const char *type, const unsigned char *body, size_t len,
                       const char *cache_control) {
    static char etag[19];   /* "\"" + 16 hex + "\"" + NUL */
    unsigned long long h = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < len; i++) h = (h ^ body[i]) * 0x100000001b3ull;
    snprintf(etag, sizeof etag, "\"%016llx\"", h);
    set_body(resp, 200, type, (const char *)body, len);
    resp->cache_control = cache_control;
    resp->etag = etag;
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
    resp->cache_control = "no-store";   /* live state: never a cached copy, no validator */
}

__attribute__((weak)) void cali_res_stats(cali_res_t *r) {
    r->heap_free_kb = r->heap_min_kb = r->psram_free_kb = r->cpu_pct = -1;
}

static void res_member(cali_json_t *j, const char *key, int v) {
    cali_json_key(j, key);
    if (v >= 0) cali_json_int(j, v); else cali_json_null(j);
}

static void res_json(cali_json_t *j) {
    cali_res_t r;
    cali_res_stats(&r);
    cali_json_obj_begin(j);
    res_member(j, "heap_free_kb", r.heap_free_kb);
    res_member(j, "heap_min_kb", r.heap_min_kb);
    res_member(j, "psram_free_kb", r.psram_free_kb);
    res_member(j, "cpu_pct", r.cpu_pct);
    cali_json_obj_end(j);
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
    cali_json_key(&j, "ota");   /* POST /api/ota's progress (R_FW_OTA) */
    cali_ota_json(&j);
    cali_json_key(&j, "time");   /* the SD log's clock (R_FW_TIME) */
    cali_time_json(&j);
    cali_json_key(&j, "sd");   /* the SD log (R_FW_SD_LOG) */
    cali_sdlog_json(&j);
    cali_json_key(&j, "res");
    res_json(&j);
    cali_json_key(&j, "battery");   /* the CoreS3's own battery (R_FW_BATTERY) */
    cali_battery_json(&j);
    cali_json_key(&j, "auto_camper");   /* restore camping after park (R_FW_AUTO_CAMPER) */
    cali_ac_json(&j);
    cali_json_key(&j, "display");   /* screen state + settings (R_FW_DISPLAY_WAKE) */
    cali_display_json(&j);
    cali_json_key(&j, "ux");   /* the web UI's usage beacons (R_UX_TELEMETRY) */
    cali_json_obj_begin(&j);
    cali_json_key(&j, "enabled");
    cali_json_bool(&j, ux_on());
    cali_json_obj_end(&j);
    cali_json_key(&j, "experimental");   /* the sensor track switch (R_FW_EXPERIMENTAL) */
    cali_json_obj_begin(&j);
    cali_json_key(&j, "sensors");
    cali_json_bool(&j, cali_exp_sensors());
    cali_json_obj_end(&j);
    cali_json_key(&j, "water_held");   /* fn.water is the held last-plausible reading, not the parked latch */
    cali_json_bool(&j, cali_session_water_held());
    uint64_t wg = cali_session_water_good_ms();   /* adopted on this boot; NVS restore has no time */
    if (cali_session_water_held() && wg) {
        cali_json_key(&j, "water_held_age_ms");   /* age of the held measurement (UI: "measured 2 h ago") */
        cali_json_int(&j, (long long)(st.uptime_ms >= wg ? st.uptime_ms - wg : 0));
    }
    cali_json_obj_end(&j);
    if (cali_exp_sensors()) {   /* external sensors (R_FW_SENSORS): absent while the flag is off */
        cali_json_key(&j, "ext");
        cali_json_obj_begin(&j);
        cali_sensors_state_json(&j, st.uptime_ms);
        cali_json_obj_end(&j);
    }
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
    cali_json_key(&j, "reverted");
    cali_json_bool(&j, cali_wifi_run_reverted() != 0);
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
     * hotspot's/station's channel), held back while a BLE pairing flow is active (the coex gate,
     * ruling R15). Over the setup hotspot and online (G1: Change network… lists networks); never
     * while a join runs (CONNECTING/RETRYING: a scan would disturb it). */
    if (st.wifi_mode == CALI_WIFI_MODE_SETUP || st.wifi_state == WIFI_ONLINE) cali_wifi_run_scan_auto();
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

/* An unsigned integer 0..65535 (digits only, no leading zero): 0 ok, -1 anything else. */
static int parse_uint(cursor_t *c, unsigned *out) {
    const char *p0;
    unsigned long v = 0;
    skip_ws(c);
    p0 = c->p;
    while (c->p < c->end && *c->p >= '0' && *c->p <= '9') {
        v = v * 10 + (unsigned long)(*c->p++ - '0');
        if (v > 65535) return -1;
    }
    if (c->p == p0 || (*p0 == '0' && c->p - p0 > 1)) return -1;
    *out = (unsigned)v;
    return 0;
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
    case CALI_CTL_OK: case CALI_CTL_REFUSED: case CALI_CTL_ELSEWHERE: case CALI_CTL_UNCONFIRMED: break;
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
        if (rc == CALI_CTL_UNCONFIRMED) {   /* sent, its ACK lost to a link drop (#264) */
            cali_json_key(&j, "unconfirmed");
            cali_json_bool(&j, 1);
        }
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
                             cmd_done, &reason, "web");
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

/* {"channel":"<s>"} exactly: 0 (the value in out, NUL-terminated, *len its whole length), -1 not that shape. */
static int parse_channel(const char *body, size_t body_len, char *out, size_t cap, size_t *len) {
    cursor_t c = {body, body + body_len};
    char key[8];
    size_t klen;
    if (expect(&c, '{') != 0 || parse_str(&c, key, sizeof key, &klen) != 0 || klen != 7 ||
        strcmp(key, "channel") != 0 || expect(&c, ':') != 0 || parse_str(&c, out, cap, len) != 0 ||
        expect(&c, '}') != 0)
        return -1;
    skip_ws(&c);
    return c.p == c.end ? 0 : -1;
}

/* ---- POST /api/ota {"action":"check"|"install"} | {"channel":"stable"|"latest"} (R_FW_OTA): station
 * mode only, like /api/command. The channel is stored at once (the next check uses it), even on a
 * build without OTA I/O; it never starts the OTA task. ---- */
static void api_ota(const cali_http_req_t *req, cali_http_resp_t *resp) {
    static pair_req_t r;   /* the same {"action":…} shape as /api/pairing */
    char ch[8];
    size_t chlen;
    int rc;
    if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
    } else if (wifi_mode() != CALI_WIFI_MODE_STATION) {   /* never over the setup hotspot */
        SET_CONST(resp, 403, ERR_SETUP_MODE);
    } else if (parse_channel(req->body, req->body_len, ch, sizeof ch, &chlen) == 0) {
        int latest = chlen == 6 && strcmp(ch, "latest") == 0;
        if (!latest && !(chlen == 6 && strcmp(ch, "stable") == 0)) SET_CONST(resp, 400, ERR_BAD_VALUE);
        else if (cali_ota_set_channel(latest) != 0) SET_CONST(resp, 500, ERR_STORE);
        else SET_CONST(resp, 200, OK_BODY);   /* the choice: /api/state device.ota.channel */
    } else if (parse_pairing(req->body, req->body_len, &r) != 0) {
        SET_CONST(resp, 400, ERR_BAD_JSON);
    } else if (!is_action(&r, "check") && !is_action(&r, "install")) {
        SET_CONST(resp, 400, ERR_BAD_ACTION);
    } else if ((rc = cali_ota_request(is_action(&r, "install"))) == -1) {
        SET_CONST(resp, 503, ERR_OTA_UNAVAILABLE);
    } else if (rc == -2) {
        SET_CONST(resp, 409, ERR_BUSY);
    } else {
        SET_CONST(resp, 200, OK_BODY);   /* progress: /api/state device.ota */
    }
}

/* ---- POST /api/auto_camper {"enabled":<bool>} (R_FW_AUTO_CAMPER, serve's endpoint): station mode only ---- */
static void api_auto_camper(const cali_http_req_t *req, cali_http_resp_t *resp) {
    cursor_t c = {req->body, req->body + req->body_len};
    char key[8];
    size_t klen;
    int on;
    if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
    } else if (wifi_mode() != CALI_WIFI_MODE_STATION) {
        SET_CONST(resp, 403, ERR_SETUP_MODE);
    } else if (expect(&c, '{') != 0 || parse_str(&c, key, sizeof key, &klen) != 0 || klen != 7 || strcmp(key, "enabled") != 0 ||
               expect(&c, ':') != 0 || parse_bool(&c, &on) != 0 || expect(&c, '}') != 0 || (skip_ws(&c), c.p != c.end)) {
        SET_CONST(resp, 400, ERR_BAD_JSON);
    } else if (cali_ac_set_enabled(on) != 0) {
        SET_CONST(resp, 500, ERR_STORE);
    } else if (on) {
        SET_CONST(resp, 200, "{\"ok\":true,\"auto_camper\":true}");
    } else {
        SET_CONST(resp, 200, "{\"ok\":true,\"auto_camper\":false}");
    }
}

/* ---- POST /api/display {"auto"?,"prox_wake"?,"min"?,"max"?,"offset"?,"motion_wake"?,"dfs"?} (R_FW_DISPLAY_WAKE): station mode only ---- */

static int parse_pct(cursor_t *c, int *out, int sign) {   /* [-]1-3 digits; a 4th is then bad JSON */
    const char *s;
    int v = 0, neg = 0;
    skip_ws(c);
    if (sign && c->p < c->end && *c->p == '-') { neg = 1; c->p++; }
    for (s = c->p; c->p < c->end && c->p - s < 3 && *c->p >= '0' && *c->p <= '9'; c->p++) v = v * 10 + (*c->p - '0');
    if (v > 127) v = 127;   /* out of every range (bad_value), and still fits the int8 offset */
    *out = neg ? -v : v;
    return c->p == s ? -1 : 0;
}

/* Merges the given members into *d; "dfs" (a bool, not a setting) into *dfs (-1 when absent);
 * *only_dfs = the body held nothing but "dfs" (then there is no setting to store).
 * 0 ok, -1 not an object of those keys (each at most once). */
static int parse_display(const char *body, size_t body_len, cali_display_cfg_t *d, int *dfs, int *only_dfs) {
    static const char *const KEYS[7] = {"auto", "prox_wake", "min", "max", "offset", "motion_wake", "dfs"};
    uint8_t *dst[6] = {&d->auto_on, &d->prox_wake, &d->min_pct, &d->max_pct, (uint8_t *)&d->offset, &d->motion_wake};
    cursor_t c = {body, body + body_len};
    int seen = 0;
    *dfs = -1;
    if (expect(&c, '{') != 0) return -1;
    skip_ws(&c);
    if (c.p < c.end && *c.p == '}') c.p++;
    else for (;;) {
        char key[12];
        size_t klen;
        int i, v;
        if (parse_str(&c, key, sizeof key, &klen) != 0 || klen >= sizeof key || expect(&c, ':') != 0) return -1;
        for (i = 0; i < 7 && strcmp(key, KEYS[i]) != 0; i++) {}
        if (i == 7 || (seen & (1 << i)) || (i < 2 || i >= 5 ? parse_bool(&c, &v) : parse_pct(&c, &v, i == 4)) != 0)
            return -1;
        seen |= 1 << i;
        if (i == 6) *dfs = v;
        else *dst[i] = (uint8_t)(int8_t)v;
        skip_ws(&c);
        if (c.p < c.end && *c.p == ',') { c.p++; continue; }
        if (expect(&c, '}') != 0) return -1;
        break;
    }
    skip_ws(&c);
    *only_dfs = seen == 1 << 6;
    return c.p == c.end ? 0 : -1;
}

static void api_display(const cali_http_req_t *req, cali_http_resp_t *resp) {
    cali_display_cfg_t d = *cali_display_cfg();
    int rc, dfs = -1, only_dfs = 0, pm;
    if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
    } else if (wifi_mode() != CALI_WIFI_MODE_STATION) {
        SET_CONST(resp, 403, ERR_SETUP_MODE);
    } else if (parse_display(req->body, req->body_len, &d, &dfs, &only_dfs) != 0) {
        SET_CONST(resp, 400, ERR_BAD_JSON);
    } else if ((rc = only_dfs ? 0 : cali_display_cfg_set(&d)) == -1) {   /* dfs alone: nothing to store */
        SET_CONST(resp, 400, ERR_BAD_VALUE);
    } else if (rc != 0) {
        SET_CONST(resp, 500, ERR_STORE);
    } else if (dfs >= 0 && (pm = cali_pm_dfs(dfs, NULL, 0)) != CALI_PM_NONE && pm != dfs) {
        /* ponytail: #346 debug knob — DFS on/off at runtime, not persisted (a boot = sdkconfig);
         * device.display.dfs / pm show the live result (null where there is no PM). Drop with #346. */
        SET_CONST(resp, 500, ERR_PM);   /* PM exists but the config did not take (or cannot be read) */
    } else {
        SET_CONST(resp, 200, OK_BODY);   /* the new settings: /api/state device.display */
    }
}

/* "since=<u32>&max=<u32>" (either order, each optional, nothing else): 0 ok, -1 malformed. */
static int parse_log_query(const char *q, uint32_t *since, uint32_t *max) {
    *since = 0;
    *max = 0;
    while (*q) {
        uint32_t *dst = strncmp(q, "since=", 6) == 0 ? since : strncmp(q, "max=", 4) == 0 ? max : NULL;
        unsigned long long v = 0;
        if (!dst) return -1;
        q = strchr(q, '=') + 1;
        if (*q < '0' || *q > '9') return -1;
        for (; *q >= '0' && *q <= '9'; q++)
            if ((v = v * 10 + (unsigned)(*q - '0')) > UINT32_MAX) return -1;
        *dst = (uint32_t)v;
        if (*q == '&') q++;
        else if (*q) return -1;
    }
    return 0;
}

/* ---- GET /api/log?since=&max= (R_FW_LOG_API): one page of the SD log, read by the SD task ---- */
static int api_log(const cali_http_req_t *req, cali_http_resp_t *resp) {
    static const char ERR_NO_CARD[] = "{\"ok\":false,\"error\":\"no_card\"}";
    static const char ERR_SD_READ[] = "{\"ok\":false,\"error\":\"sd_read\"}";
    const char *body;
    size_t len;
    uint32_t since, max;
    int rc;
    if (!req->resume) {
        if (strcmp(req->method, "GET") != 0) {
            SET_CONST(resp, 405, ERR_METHOD);
        } else if (parse_log_query(req->query, &since, &max) != 0) {
            SET_CONST(resp, 400, ERR_BAD_VALUE);
        } else if ((rc = cali_sdlog_page_request(since, max)) == -1) {
            SET_CONST(resp, 503, ERR_NO_CARD);
        } else if (rc == -2) {
            SET_CONST(resp, 409, ERR_BUSY);
        } else {
            return CALI_HTTP_PENDING;
        }
        return 1;
    }
    rc = cali_sdlog_page_result(&body, &len);
    if (rc == 0) return CALI_HTTP_PENDING;
    if (rc < 0) SET_CONST(resp, 503, ERR_SD_READ);
    else set_body(resp, 200, "application/x-ndjson", body, len);
    return 1;
}

/* ---- POST /api/ux: the web UI's usage beacon (R_UX_TELEMETRY), calictl's web.ux_batch rules ----
 * {"sid":<8-32 hex>,"ua_class":ios|android|mac|other,"standalone":<bool>,"origin":lan|busnet|buspi,
 *  "events":[1..16 of {"type":view|control|timing|error, + exactly that type's keys}]} (any member
 * order): ids [a-z0-9_.-]{1,32}, timings integers 0..600000, msg 1..200 printable ASCII, a control's optional value 1..32 printable ASCII. Parsed
 * twice: a first pass validates the whole body (and fills the header), the second writes one SD
 * record per event — a bad beacon never leaves half its events on the card. */
#define UX_EVENTS_MAX 16
#define UX_MSG_MAX 200
#define UX_INT_MAX 600000L
#define UX_VALUE_MAX 32
enum { UX_SCREEN = 1, UX_FN = 2, UX_CONTROL = 4, UX_TTFB = 8, UX_DCL = 16, UX_TILES = 32, UX_MSG = 64, UX_TYPE = 128,
       UX_VALUE = 256 };   /* UX_VALUE: optional, a control event's value (printable ASCII 1..UX_VALUE_MAX) */
static const char *const UX_KEYS[] = {"screen", "fn", "control", "ttfb", "dcl", "tiles", "msg", "type", "value"};
static const char *const UX_TYPES[] = {"view", "control", "timing", "error"};
static const unsigned UX_NEEDS[] = {UX_SCREEN, UX_FN | UX_CONTROL, UX_TTFB | UX_DCL | UX_TILES, UX_MSG};
static const char *const UX_UA[] = {"ios", "android", "mac", "other"};
static const char *const UX_ORIGIN[] = {"lan", "busnet", "buspi"};

typedef struct {
    char sid[33];
    int ua, origin, standalone;
} ux_hdr_t;

static int index_of(const char *s, const char *const *set, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (strcmp(s, set[i]) == 0) return (int)i;
    return -1;
}

/* a string member value that is one of set: its index, else -1 */
static int parse_enum(cursor_t *c, const char *const *set, size_t n) {
    char v[16];
    size_t len;
    if (parse_str(c, v, sizeof v, &len) != 0 || len >= sizeof v) return -1;
    return index_of(v, set, n);
}

static int ux_chars(const char *s, size_t len, size_t max, int id) {
    if (len < 1 || len > max) return -1;
    for (size_t i = 0; i < len; i++) {
        unsigned char ch = (unsigned char)s[i];
        if (id ? !((ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '.' || ch == '-')
               : (ch < 0x20 || ch > 0x7e))
            return -1;
    }
    return 0;
}

/* an unsigned JSON integer 0..UX_INT_MAX (no sign, fraction, exponent or leading zero) */
static int parse_ux_int(cursor_t *c, long *out) {
    long v = 0;
    skip_ws(c);
    if (c->p == c->end || *c->p < '0' || *c->p > '9') return -1;
    if (*c->p == '0' && c->p + 1 < c->end && c->p[1] >= '0' && c->p[1] <= '9') return -1;
    while (c->p < c->end && *c->p >= '0' && *c->p <= '9') {
        v = v * 10 + (*c->p++ - '0');
        if (v > UX_INT_MAX) return -1;
    }
    if (c->p < c->end && (*c->p == '.' || *c->p == 'e' || *c->p == 'E')) return -1;
    *out = v;
    return 0;
}

/* One event object. emit: write its SD record (h complete). 0 ok, -1 not the shape. */
static int ux_event(cursor_t *c, const ux_hdr_t *h, int emit) {
    char id[3][33], msg[UX_MSG_MAX + 1], value[UX_VALUE_MAX + 1], key[8];
    long num[3] = {0, 0, 0};
    unsigned seen = 0;
    int type = -1;
    if (expect(c, '{') != 0) return -1;
    for (;;) {
        size_t klen, len;
        int k;
        if (parse_str(c, key, sizeof key, &klen) != 0 || klen >= sizeof key || expect(c, ':') != 0) return -1;
        k = index_of(key, UX_KEYS, N_OF(UX_KEYS));
        if (k < 0 || (seen & (1u << k))) return -1;
        seen |= 1u << k;
        if (k <= 2) {   /* screen / fn / control */
            if (parse_str(c, id[k], sizeof id[k], &len) != 0 || ux_chars(id[k], len, 32, 1) != 0) return -1;
        } else if (k <= 5) {   /* ttfb / dcl / tiles */
            if (parse_ux_int(c, &num[k - 3]) != 0) return -1;
        } else if (k == 6) {
            if (parse_str(c, msg, sizeof msg, &len) != 0 || ux_chars(msg, len, UX_MSG_MAX, 0) != 0) return -1;
        } else if (k == 7) {
            if ((type = parse_enum(c, UX_TYPES, N_OF(UX_TYPES))) < 0) return -1;
        } else if (parse_str(c, value, sizeof value, &len) != 0 || ux_chars(value, len, UX_VALUE_MAX, 0) != 0) {
            return -1;
        }
        skip_ws(c);
        if (c->p < c->end && *c->p == ',') {
            c->p++;
            continue;
        }
        if (expect(c, '}') != 0) return -1;
        break;
    }
    if (type < 0 || ((seen & UX_VALUE) && type != 1)) return -1;   /* a value is only a control's, and optional */
    if ((seen & ~UX_VALUE) != (UX_TYPE | UX_NEEDS[type])) return -1;
    if (emit) {
        /* the members without braces, < CALI_SDLOG_DATA_MAX (cali_sdlog_text): an error's msg is cut
         * (8 bytes a try) until the record fits — the SD record keeps ~150 of its 200 chars at worst */
        char buf[CALI_SDLOG_DATA_MAX + 1];
        size_t cut = strlen(msg);
        int n;
        for (;;) {
            cali_json_t j;
            msg[cut] = '\0';
            cali_json_begin(&j, buf, sizeof buf);
            cali_json_key(&j, "ev");
            cali_json_str(&j, "ux");
            cali_json_key(&j, "sid");
            cali_json_str(&j, h->sid);
            cali_json_key(&j, "ua_class");
            cali_json_str(&j, UX_UA[h->ua]);
            cali_json_key(&j, "standalone");
            cali_json_bool(&j, h->standalone);
            cali_json_key(&j, "origin");
            cali_json_str(&j, UX_ORIGIN[h->origin]);
            cali_json_key(&j, "type");
            cali_json_str(&j, UX_TYPES[type]);
            for (int k = 0; k <= 6; k++) {
                if (!(seen & (1u << k))) continue;
                cali_json_key(&j, UX_KEYS[k]);
                if (k <= 2) cali_json_str(&j, id[k]);
                else if (k <= 5) cali_json_int(&j, num[k - 3]);
                else cali_json_str(&j, msg);
            }
            if (seen & UX_VALUE) {
                cali_json_key(&j, "value");
                cali_json_str(&j, value);
            }
            n = cali_json_end(&j);   /* "{…}": the body is buf+1 up to the '}' */
            if ((n > 0 && (size_t)n - 2 < CALI_SDLOG_DATA_MAX) || cut == 0) break;
            cut = cut > 8 ? cut - 8 : 0;
        }
        if (n > 2 && (size_t)n - 2 < CALI_SDLOG_DATA_MAX) {
            buf[n - 1] = '\0';
            (void)cali_sdlog_text(buf + 1);   /* no card / queue full: not logged (counted) */
        }
    }
    return 0;
}

/* The whole beacon; emit: write each event's record. 0 ok, -1 not the shape. */
static int ux_parse(const char *body, size_t body_len, ux_hdr_t *h, int emit) {
    cursor_t c = {body, body + body_len};
    unsigned seen = 0;
    if (expect(&c, '{') != 0) return -1;
    for (;;) {
        char key[12];
        size_t klen, len;
        int k;
        static const char *const KEYS[] = {"sid", "ua_class", "standalone", "origin", "events"};
        if (parse_str(&c, key, sizeof key, &klen) != 0 || klen >= sizeof key || expect(&c, ':') != 0) return -1;
        k = index_of(key, KEYS, N_OF(KEYS));
        if (k < 0 || (seen & (1u << k))) return -1;
        seen |= 1u << k;
        if (k == 0) {
            if (parse_str(&c, h->sid, sizeof h->sid, &len) != 0 || len < 8 || len > 32) return -1;
            for (size_t i = 0; i < len; i++)
                if (!((h->sid[i] >= '0' && h->sid[i] <= '9') || (h->sid[i] >= 'a' && h->sid[i] <= 'f'))) return -1;
        } else if (k == 1) {
            if ((h->ua = parse_enum(&c, UX_UA, N_OF(UX_UA))) < 0) return -1;
        } else if (k == 2) {
            if (parse_bool(&c, &h->standalone) != 0) return -1;
        } else if (k == 3) {
            if ((h->origin = parse_enum(&c, UX_ORIGIN, N_OF(UX_ORIGIN))) < 0) return -1;
        } else {
            int n = 0;
            if (expect(&c, '[') != 0) return -1;
            for (;;) {
                if (++n > UX_EVENTS_MAX || ux_event(&c, h, emit) != 0) return -1;
                skip_ws(&c);
                if (c.p == c.end || *c.p != ',') break;
                c.p++;
            }
            if (expect(&c, ']') != 0) return -1;
        }
        skip_ws(&c);
        if (c.p < c.end && *c.p == ',') {
            c.p++;
            continue;
        }
        if (expect(&c, '}') != 0) return -1;
        break;
    }
    skip_ws(&c);
    return seen == 31u && c.p == c.end ? 0 : -1;
}

static void api_ux(const cali_http_req_t *req, cali_http_resp_t *resp) {
    ux_hdr_t h;
    if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
    } else if (wifi_mode() != CALI_WIFI_MODE_STATION) {
        SET_CONST(resp, 403, ERR_SETUP_MODE);
    } else if (req->body_len > CALI_UX_BODY_MAX) {   /* the core 413s most of these already */
        SET_CONST(resp, 413, ERR_UX_TOO_LARGE);
    } else if (!ux_on()) {
        set_body(resp, 204, NULL, NULL, 0);   /* switched off (Satellite settings): nothing recorded */
    } else if (ux_parse(req->body, req->body_len, &h, 0) != 0) {
        SET_CONST(resp, 400, ERR_UX_BAD);
    } else {
        (void)ux_parse(req->body, req->body_len, &h, 1);
        set_body(resp, 204, NULL, NULL, 0);
    }
}

/* ---- POST /api/ux_setting {"enabled":<bool>}: the Satellite settings "Send usage data" switch, station only ---- */
static void api_ux_setting(const cali_http_req_t *req, cali_http_resp_t *resp) {
    cursor_t c = {req->body, req->body + req->body_len};
    char key[8];
    size_t klen;
    int on;
    if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
    } else if (wifi_mode() != CALI_WIFI_MODE_STATION) {
        SET_CONST(resp, 403, ERR_SETUP_MODE);
    } else if (expect(&c, '{') != 0 || parse_str(&c, key, sizeof key, &klen) != 0 || klen != 7 || strcmp(key, "enabled") != 0 ||
               expect(&c, ':') != 0 || parse_bool(&c, &on) != 0 || expect(&c, '}') != 0 || (skip_ws(&c), c.p != c.end)) {
        SET_CONST(resp, 400, ERR_BAD_JSON);
    } else {
        uint8_t b = (uint8_t)on;
        if (cali_kv_set(CALI_UX_ON_KEY, &b, 1) != 0) {
            SET_CONST(resp, 500, ERR_STORE);
            return;
        }
        s_ux = on;
        cali_log("ux: usage data %s", on ? "on" : "off");
        if (on) SET_CONST(resp, 200, "{\"ok\":true,\"ux\":true}");
        else SET_CONST(resp, 200, "{\"ok\":true,\"ux\":false}");
    }
}

/* ---- POST /api/experimental {"sensors":<bool>}: the Satellite settings sensor-track switch, station only ---- */
static void api_experimental(const cali_http_req_t *req, cali_http_resp_t *resp) {
    cursor_t c = {req->body, req->body + req->body_len};
    char key[8];
    size_t klen;
    int on;
    if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
    } else if (wifi_mode() != CALI_WIFI_MODE_STATION) {
        SET_CONST(resp, 403, ERR_SETUP_MODE);
    } else if (expect(&c, '{') != 0 || parse_str(&c, key, sizeof key, &klen) != 0 || klen != 7 || strcmp(key, "sensors") != 0 ||
               expect(&c, ':') != 0 || parse_bool(&c, &on) != 0 || expect(&c, '}') != 0 || (skip_ws(&c), c.p != c.end)) {
        SET_CONST(resp, 400, ERR_BAD_JSON);
    } else if (cali_exp_sensors_set(on) != 0) {
        SET_CONST(resp, 500, ERR_STORE);
    } else if (on) {
        SET_CONST(resp, 200, "{\"ok\":true,\"sensors\":true}");
    } else {
        SET_CONST(resp, 200, "{\"ok\":true,\"sensors\":false}");
    }
}

/* ---- GET|POST /api/sensors: the Satellite settings Sensors box (R_FW_SENSORS), every mode, 404 while the flag is off ---- */

enum { S_ADD = 1, S_REMOVE = 2, S_ID = 4, S_ALIAS = 8, S_KEY = 16 };

/* {"add":<id>,"alias"?,"key"?} | {"remove":<id>} | {"id":<id>,"alias":…}, any order, each key once:
 * the keys present (S_*), or -1 not that shape. *alias_len / *key_len = the whole decoded lengths. */
static int sensors_parse(const char *body, size_t body_len, unsigned *id, char alias[17], size_t *alias_len,
                         char key[33], size_t *key_len) {
    static const char *const NAMES[] = {"add", "remove", "id", "alias", "key"};
    cursor_t c = {body, body + body_len};
    int seen = 0;
    if (expect(&c, '{') != 0) return -1;
    do {
        char name[8];
        size_t len, i;
        if (parse_str(&c, name, sizeof name, &len) != 0 || expect(&c, ':') != 0) return -1;
        for (i = 0; i < N_OF(NAMES) && (len != strlen(NAMES[i]) || strcmp(name, NAMES[i]) != 0); i++) {}
        if (i == N_OF(NAMES) || (seen & 1 << i)) return -1;
        seen |= 1 << i;
        if ((1 << i) == S_ALIAS ? parse_str(&c, alias, 17, alias_len)
            : (1 << i) == S_KEY ? parse_str(&c, key, 33, key_len)
                                : parse_uint(&c, id))
            return -1;
        skip_ws(&c);
    } while (c.p < c.end && *c.p == ',' && c.p++);
    if (expect(&c, '}') != 0 || (skip_ws(&c), c.p != c.end)) return -1;
    if ((seen & S_ADD) ? (seen & (S_REMOVE | S_ID)) != 0 : seen != S_REMOVE && seen != (S_ID | S_ALIAS)) return -1;
    return seen;
}

static void api_sensors(const cali_http_req_t *req, cali_http_resp_t *resp) {
    static const char *const ERRS[] = {   /* -CALI_SENS_E_* - 1 */
        "{\"ok\":false,\"error\":\"bad_alias\"}", "{\"ok\":false,\"error\":\"bad_key\"}",
        "{\"ok\":false,\"error\":\"key_mismatch\"}", "{\"ok\":false,\"error\":\"unknown_id\"}",
        "{\"ok\":false,\"error\":\"full\"}"};
    char alias[17], key[33];
    size_t alias_len = 0, key_len = 0;
    unsigned id = 0;
    int keys, rc;
    if (!cali_exp_sensors()) {
        SET_CONST(resp, 404, ERR_DISABLED);
        return;
    }
    if (strcmp(req->method, "GET") == 0) {
        cali_sensors_kick();   /* the box is open: a fresh window soon */
    } else if (strcmp(req->method, "POST") != 0) {
        SET_CONST(resp, 405, ERR_METHOD);
        return;
    } else {
        keys = sensors_parse(req->body, req->body_len, &id, alias, &alias_len, key, &key_len);
        if (keys < 0) rc = 1;   /* bad_json */
        else if ((keys & S_ALIAS) && alias_len > 16) rc = CALI_SENS_E_ALIAS;
        else if ((keys & S_KEY) && key_len > 32) rc = CALI_SENS_E_KEY;
        else if (keys & S_ADD) rc = cali_sensors_add((uint16_t)id, keys & S_ALIAS ? alias : NULL, keys & S_KEY ? key : NULL, cali_uptime_ms());
        else if (keys & S_REMOVE) rc = cali_sensors_remove((uint16_t)id);
        else rc = cali_sensors_rename((uint16_t)id, alias);
        memset(key, 0, sizeof key);   /* no key copy left on the stack */
        if (rc == 1) {
            SET_CONST(resp, 400, ERR_BAD_JSON);
            return;
        }
        if (rc < 0 && -rc - 1 < (int)N_OF(ERRS)) {
            set_body(resp, 400, JSON_TYPE, ERRS[-rc - 1], strlen(ERRS[-rc - 1]));
            return;
        }
        if (rc < 0) {   /* CALI_SENS_E_STORE, or any code the table lacks: never a 200 */
            SET_CONST(resp, 500, ERR_STORE);
            return;
        }
    }
    cali_json_t j;
    cali_json_begin(&j, s_json, sizeof s_json);
    cali_sensors_api_json(&j, cali_uptime_ms());
    finish_json(&j, resp);
}

int cali_web_handle(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx) {
    int get = strcmp(req->method, "GET") == 0;
    (void)ctx;
    if (strcmp(req->path, "/api/command") == 0) return api_command(req, resp);
    if (strcmp(req->path, "/api/log") == 0) return api_log(req, resp);
    /* the calictl web UI (R_FW_SHARED_UI) in every mode: over the setup hotspot it opens on WiFi setup
     * by itself; /app = the same (the manifest's start_url, old links) */
    if (get && (strcmp(req->path, "/") == 0 || strcmp(req->path, "/app") == 0)) {
        set_static(resp, HTML_TYPE, WEB_APP_HTML_GZ, WEB_APP_HTML_GZ_LEN, NO_CACHE);
        resp->content_encoding = "gzip";
        return 1;
    }
    for (size_t i = 0; get && i < N_OF(PWA_ASSETS); i++) {   /* "Add to Home Screen", in every mode */
        if (strcmp(req->path, PWA_ASSETS[i].path) == 0) {
            /* the manifest + sw.js revalidate every load like the page (an OTA shows at once); an
             * icon is fresh a day (a reflash shows by tomorrow), then a cheap 304 — never immutable:
             * its URL never changes */
            set_static(resp, PWA_ASSETS[i].type, PWA_ASSETS[i].body, PWA_ASSETS[i].len,
                       strncmp(PWA_ASSETS[i].type, "image/", 6) == 0 ? "max-age=86400" : NO_CACHE);
            return 1;
        }
    }
    if (strcmp(req->path, "/api/pairing") == 0) {
        api_pairing(req, resp);
        return 1;
    }
    if (strcmp(req->path, "/api/ota") == 0) {
        api_ota(req, resp);
        return 1;
    }
    if (strcmp(req->path, "/api/auto_camper") == 0) {
        api_auto_camper(req, resp);
        return 1;
    }
    if (strcmp(req->path, "/api/display") == 0) {
        api_display(req, resp);
        return 1;
    }
    if (strcmp(req->path, "/api/ux") == 0) {
        api_ux(req, resp);
        return 1;
    }
    if (strcmp(req->path, "/api/ux_setting") == 0) {
        api_ux_setting(req, resp);
        return 1;
    }
    if (strcmp(req->path, "/api/experimental") == 0) {
        api_experimental(req, resp);
        return 1;
    }
    if (strcmp(req->path, "/api/sensors") == 0) {
        api_sensors(req, resp);
        return 1;
    }
    if (get && strcmp(req->path, "/device") == 0) {    /* the WiFi recovery page, in every mode */
        set_static(resp, HTML_TYPE, WEB_INDEX_HTML, WEB_INDEX_HTML_LEN, NO_CACHE);
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
        /* captive portal: an OS probe gets the absolute setup address (its Host is some probe domain),
         * so the OS's captive sheet opens the UI (WiFi setup); anything else goes home */
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
