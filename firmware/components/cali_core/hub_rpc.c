/* Remote hub RPC adapter (cali_hub.h): one request plaintext -> cali_web_handle -> one response plaintext. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_hub.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#define RPC_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
#define RPC_FREE heap_caps_free
#else
#define RPC_ALLOC malloc
#define RPC_FREE free
#endif
#include "cali_http.h"
#include "cali_platform.h"
#include "cali_time.h"
#include "cali_web.h"

#define PATH_MAX_LEN 128

typedef struct {
    const char *p, *end;
} cur_t;

static void ws(cur_t *c) {
    while (c->p < c->end && (*c->p == ' ' || *c->p == '\t' || *c->p == '\n' || *c->p == '\r')) c->p++;
}
static int eat(cur_t *c, char ch) {
    ws(c);
    if (c->p < c->end && *c->p == ch) {
        c->p++;
        return 0;
    }
    return -1;
}
static int hex4(cur_t *c, unsigned *v) {
    *v = 0;
    if (c->end - c->p < 4) return -1;
    for (int i = 0; i < 4; i++, c->p++) {
        char h = *c->p;
        if (h >= '0' && h <= '9') *v = *v << 4 | (unsigned)(h - '0');
        else if (h >= 'a' && h <= 'f') *v = *v << 4 | (unsigned)(h - 'a' + 10);
        else if (h >= 'A' && h <= 'F') *v = *v << 4 | (unsigned)(h - 'A' + 10);
        else return -1;
    }
    return 0;
}
/* A JSON string into out (NUL-terminated, UTF-8). *len = the decoded length. 0 ok, -1 malformed, -2 too long.
 * ponytail: \u surrogate pairs are rejected (the app's compact JSON has none), add if a body needs them. */
static int str(cur_t *c, char *out, size_t cap, size_t *len) {
    size_t n = 0;
    if (eat(c, '"') != 0) return -1;
    for (;;) {
        unsigned ch, cp;
        char u[4];
        size_t k = 1;
        if (c->p == c->end) return -1;
        ch = (unsigned char)*c->p++;
        if (ch == '"') break;
        if (ch < 0x20) return -1;
        if (ch == '\\') {
            if (c->p == c->end) return -1;
            ch = (unsigned char)*c->p++;
            switch (ch) {
            case '"': case '\\': case '/': break;
            case 'b': ch = '\b'; break;
            case 'f': ch = '\f'; break;
            case 'n': ch = '\n'; break;
            case 'r': ch = '\r'; break;
            case 't': ch = '\t'; break;
            case 'u':
                if (hex4(c, &cp) != 0 || cp == 0 || (cp >= 0xD800 && cp <= 0xDFFF)) return -1; /* NUL would cut the C string */
                if (cp < 0x80) u[0] = (char)cp;
                else if (cp < 0x800) {
                    u[0] = (char)(0xC0 | cp >> 6), u[1] = (char)(0x80 | (cp & 63)), k = 2;
                } else {
                    u[0] = (char)(0xE0 | cp >> 12), u[1] = (char)(0x80 | (cp >> 6 & 63)), u[2] = (char)(0x80 | (cp & 63)), k = 3;
                }
                goto put;
            default: return -1;
            }
        }
        u[0] = (char)ch;
    put:
        if (n + k >= cap) return -2;
        memcpy(out + n, u, k);
        n += k;
    }
    out[n] = 0;
    *len = n;
    return 0;
}
/* Skips one JSON value of any type (an unknown key's), nested at most 8 deep. 0 ok, -1 malformed. A bare
 * number or literal is skipped by its characters, not validated: the router never sees it. */
static int skip(cur_t *c, int depth) {
    ws(c);
    if (c->p == c->end || depth > 8) return -1;
    if (*c->p == '"') {
        c->p++;
        while (c->p < c->end && *c->p != '"') c->p += *c->p == '\\' ? 2 : 1;
        if (c->p >= c->end) return -1;
        c->p++;
        return 0;
    }
    if (*c->p == '{' || *c->p == '[') {
        char close = *c->p++ == '{' ? '}' : ']';
        if (eat(c, close) == 0) return 0;
        for (;;) {
            if (close == '}' && (skip(c, depth + 1) != 0 || eat(c, ':') != 0)) return -1;
            if (skip(c, depth + 1) != 0) return -1;
            if (eat(c, ',') != 0) return eat(c, close);
        }
    }
    {
        const char *s = c->p;
        while (c->p < c->end && !strchr(",}] \t\r\n", *c->p)) c->p++;
        return c->p == s ? -1 : 0;
    }
}

/* A JSON integer (optional '-', digits only) that fits int64. */
static int num(cur_t *c, int64_t *v) {
    int neg = 0, any = 0;
    uint64_t u = 0;
    ws(c);
    if (c->p < c->end && *c->p == '-') neg = 1, c->p++;
    while (c->p < c->end && *c->p >= '0' && *c->p <= '9') {
        if (u > (UINT64_MAX - 9) / 10) return -1;
        u = u * 10 + (uint64_t)(*c->p++ - '0');
        any = 1;
    }
    if (!any || u > (uint64_t)INT64_MAX) return -1;
    *v = neg ? -(int64_t)u : (int64_t)u;
    return 0;
}

/* {"id":..,"s":..,"b":"<b escaped>"} into out. @return the length, or 0 if it does not fit max. */
static size_t answer(char *out, size_t max, uint32_t id, int status, const char *b, size_t blen) {
    int h = snprintf(out, max, "{\"id\":%lu,\"s\":%d,\"b\":\"", (unsigned long)id, status);
    size_t n;
    if (h < 0 || (size_t)h >= max) return 0;
    n = (size_t)h;
    for (size_t i = 0; i < blen; i++) {
        unsigned char ch = (unsigned char)b[i];
        char e[7];
        size_t k = 1;
        e[0] = (char)ch;
        if (ch == '"' || ch == '\\') e[0] = '\\', e[1] = (char)ch, k = 2;
        else if (ch < 0x20) k = (size_t)snprintf(e, sizeof e, "\\u%04x", ch);
        if (n + k + 3 > max) return 0; /* room for the closing "} and the NUL */
        memcpy(out + n, e, k);
        n += k;
    }
    memcpy(out + n, "\"}", 3);
    return n + 2;
}

/* a short constant answer; the last resort when even this does not fit is 0 */
static size_t err(char *out, size_t max, uint32_t id, int status, const char *msg) {
    return answer(out, max, id, status, msg, strlen(msg));
}

static size_t rpc_run(const char *req_json, char *res_json, size_t max, int (*pump)(void), char *body, char *path) {
    char method[8];
    cur_t c = {req_json, req_json + strlen(req_json)};
    uint32_t id = 0;
    int64_t t = 0, v;
    unsigned seen = 0;
    int have_id = 0, have_t = 0, have_m = 0, have_p = 0, have_b = 0, rc, too_long = 0;
    size_t len = 0;
    if (max) res_json[0] = 0;
    if (eat(&c, '{') != 0) return err(res_json, max, 0, 400, "bad_json");
    for (int first = 1;; first = 0) {
        char key[8];
        size_t kl;
        int r;
        ws(&c);
        if (first && c.p < c.end && *c.p == '}') break;
        if (str(&c, key, sizeof key, &kl) != 0 || eat(&c, ':') != 0) return err(res_json, max, id, 400, "bad_json");
        {   /* a known key twice fails closed; an unknown key's value is skipped (forward compatible) */
            static const char *const K[] = {"id", "t", "m", "p", "b"};
            for (unsigned i = 0; i < 5; i++)
                if (strcmp(key, K[i]) == 0) {
                    if (seen & 1u << i) return err(res_json, max, id, 400, "bad_json");
                    seen |= 1u << i;
                }
        }
        if (strcmp(key, "id") == 0) {
            if (num(&c, &v) != 0 || v < 0 || v > UINT32_MAX) return err(res_json, max, 0, 400, "bad_json");
            id = (uint32_t)v, have_id = 1;
        } else if (strcmp(key, "t") == 0) {
            if (num(&c, &t) != 0) return err(res_json, max, id, 400, "bad_json");
            have_t = 1;
        } else if (strcmp(key, "m") == 0) {
            r = str(&c, method, sizeof method, &kl);
            if (r != 0) return err(res_json, max, id, 400, "bad_json");
            have_m = 1;
        } else if (strcmp(key, "p") == 0) {
            r = str(&c, path, PATH_MAX_LEN, &kl);
            if (r != 0) return err(res_json, max, id, 400, "bad_json");
            have_p = 1;
        } else if (strcmp(key, "b") == 0) {
            ws(&c);
            if (c.end - c.p >= 4 && memcmp(c.p, "null", 4) == 0) {
                c.p += 4;
                len = 0, body[0] = 0, have_b = 1;
            } else {
                r = str(&c, body, CALI_HUB_RPC_BODY_MAX, &len);
                if (r == -2) too_long = 1;
                else if (r != 0) return err(res_json, max, id, 400, "bad_json");
                if (r == -2) { /* skip the rest of the string */
                    while (c.p < c.end && *c.p != '"') c.p += *c.p == '\\' ? 2 : 1;
                    if (c.p >= c.end) return err(res_json, max, id, 400, "bad_json");
                    c.p++;
                }
                have_b = 1;
            }
        } else if (skip(&c, 0) != 0) {
            return err(res_json, max, id, 400, "bad_json");
        }
        if (eat(&c, ',') == 0) continue;
        if (eat(&c, '}') != 0) return err(res_json, max, id, 400, "bad_json");
        break;
    }
    ws(&c);
    if (c.p != c.end || !have_id || !have_m || !have_p || !have_b ||
        (strcmp(method, "GET") && strcmp(method, "POST") && strcmp(method, "DELETE")) || path[0] != '/')
        return err(res_json, max, id, 400, "bad_json");
    if (!have_t) return err(res_json, max, id, 400, "missing_t");
    {
        int64_t now;
        /* t <= 0 is stale at once: it keeps now - t (both then >= 0) from overflowing */
        if (cali_time_ts(&now) == CALI_TSRC_NTP &&
            (t <= 0 || (now > t ? now - t : t - now) > CALI_HUB_RPC_SKEW_MS))
            return err(res_json, max, id, 408, "stale");
    }
    if (too_long) return err(res_json, max, id, 413, "too_long");
    {
        cali_http_req_t rq = {method, path, "", body, len, 0, NULL};
        cali_http_resp_t rs = {200, "text/plain", NULL, 0, NULL, NULL, NULL, NULL};
        char *q = strchr(path, '?');
        uint64_t t0 = cali_uptime_ms();
        if (q) *q = 0, rq.query = q + 1;
        while ((rc = cali_web_handle(&rq, &rs, NULL)) == CALI_HTTP_PENDING) {
            rq.resume = 1;
            if (pump() != 0 || cali_uptime_ms() - t0 > CALI_HUB_RPC_PENDING_MS)
                return err(res_json, max, id, 504, "timeout");
        }
        if (rc == 0) return err(res_json, max, id, 404, "");
        len = answer(res_json, max, id, rs.status, rs.body ? rs.body : "", rs.body ? rs.body_len : 0);
        return len ? len : err(res_json, max, id, 500, "too_big");
    }
}

size_t cali_hub_rpc(const char *req_json, char *res_json, size_t max, int (*pump)(void)) {
    size_t n;
    char *buf;
    if (!max) return 0;
    res_json[0] = 0;
    if (!pump) return err(res_json, max, 0, 500, "no_pump"); /* without it a pending route would spin 60 s */
    buf = RPC_ALLOC(CALI_HUB_RPC_BODY_MAX + PATH_MAX_LEN);
    if (!buf) return err(res_json, max, 0, 500, "no_memory");
    n = rpc_run(req_json, res_json, max, pump, buf, buf + CALI_HUB_RPC_BODY_MAX);
    RPC_FREE(buf);
    return n;
}
