/* diag.c — diagnostics record bodies, perf sampling and the log mirror (R_FW_DIAG_LOG).
 * Contract: include/cali_diag.h. */
#include "cali_diag.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

cali_diag_sink_fn cali_diag_sink;

static int vrec(const char *ev, const char *kind, const char *fmt, va_list ap) {
    char b[CALI_DIAG_BODY_MAX];
    int n, m;
    if (!cali_diag_sink) return -1;
    n = kind ? snprintf(b, sizeof b, "\"ev\":\"%s\",\"kind\":\"%s\"", ev, kind) : snprintf(b, sizeof b, "\"ev\":\"%s\"", ev);
    if (n < 0 || (size_t)n >= sizeof b - 1) return -1;
    b[n] = ',';
    m = vsnprintf(b + n + 1, sizeof b - (size_t)n - 1, fmt, ap);
    if (m < 0 || (size_t)m >= sizeof b - (size_t)n - 1) return -1;   /* never a cut (invalid) record */
    if (m == 0) b[n] = 0;                                             /* no fields: no comma */
    return cali_diag_sink(b);
}

int cali_diag_rec(const char *ev, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int rc = vrec(ev, NULL, fmt, ap);
    va_end(ap);
    return rc;
}

int cali_diag_evt(const char *kind, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int rc = vrec("evt", kind, fmt, ap);
    va_end(ap);
    return rc;
}

const char *cali_diag_q(char *buf, size_t cap, const char *s, size_t max) {
    size_t o = 0;
    if (cap < 7) return buf[0] = 0, buf;
    if (!s) return strcpy(buf, "null");
    buf[o++] = '"';
    for (size_t i = 0; i < max && s[i] && o + 3 < cap; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') buf[o++] = '\\';
        buf[o++] = c < 0x20 || c > 0x7e ? '?' : (char)c;
    }
    buf[o++] = '"';
    buf[o] = 0;
    return buf;
}

static const char *num(char *buf, size_t cap, long v, int known) {
    if (known) snprintf(buf, cap, "%ld", v);
    else snprintf(buf, cap, "null");
    return buf;
}

int cali_diag_health(const cali_diag_health_t *h) {
    char st[CALI_DIAG_STACKS * 20 + 1], v[6][24];
    size_t n = 0;
    st[0] = 0;
    for (int i = 0; i < h->nstack && i < CALI_DIAG_STACKS && n < sizeof st; i++)
        n += (size_t)snprintf(st + n, sizeof st - n, "%s\"%.10s\":%u", i ? "," : "", h->stack_name[i], h->stack_min[i]);
    return cali_diag_rec("diag",
                         "\"heap_free\":%s,\"heap_min\":%s,\"psram_free\":%s,\"cpu_pct\":%s,\"stack_min\":{%s},"
                         "\"wifi_rssi\":%s,\"ble_rssi\":%s,\"link_up\":%s,\"uptime_s\":%lu",
                         num(v[0], sizeof v[0], h->heap_free, h->heap_free >= 0),
                         num(v[1], sizeof v[1], h->heap_min, h->heap_min >= 0),
                         num(v[2], sizeof v[2], h->psram_free, h->psram_free >= 0),
                         num(v[3], sizeof v[3], h->cpu_pct, h->cpu_pct >= 0), n < sizeof st ? st : "",
                         num(v[4], sizeof v[4], h->wifi_rssi, h->wifi_rssi != 0),
                         num(v[5], sizeof v[5], h->ble_rssi, h->ble_rssi != 0), h->link_up ? "true" : "false",
                         h->uptime_s);
}

int cali_diag_boot(const char *reset_reason, const char *fw, unsigned long boot, long prev_up_s) {
    char f[44], p[24];
    return cali_diag_evt("boot", "\"reset_reason\":\"%s\",\"fw\":%s,\"boot\":%lu,\"prev_up_s\":%s", reset_reason,
                         cali_diag_q(f, sizeof f, fw, 40), boot, num(p, sizeof p, prev_up_s, prev_up_s >= 0));
}

int cali_diag_touch(const cali_diag_touch_t *t) {
    char l[48], v[5][24], s[8], b[8];
    return cali_diag_evt("touch",
                         "\"errors\":%lu,\"last\":%s,\"mhz\":%s,\"usb\":%s,\"chg\":%s,\"scr\":%s,\"bus\":%s,"
                         "\"bus_ms\":%s,\"pmode\":%s",
                         (unsigned long)t->errors, cali_diag_q(l, sizeof l, t->last, 40),
                         num(v[0], sizeof v[0], t->mhz, t->mhz >= 0), t->usb < 0 ? "null" : t->usb ? "true" : "false",
                         t->chg < 0 ? "null" : t->chg ? "true" : "false", cali_diag_q(s, sizeof s, t->scr, 3),
                         cali_diag_q(b, sizeof b, t->bus, 4), num(v[1], sizeof v[1], t->bus_ms, t->bus_ms >= 0),
                         num(v[2], sizeof v[2], t->pmode, t->pmode >= 0));
}

const char *cali_panic_fields(const cali_panic_t *p, char *buf, size_t cap) {
    char t[40], r[56], bt[CALI_PANIC_DEPTH * 9 + 1];
    size_t n = 0;
    if (p->magic != CALI_PANIC_MAGIC || p->depth > CALI_PANIC_DEPTH || p->sum != cali_panic_sum(p)) return NULL;
    bt[0] = 0;
    for (uint32_t i = 0; i < p->depth; i++)
        n += (size_t)snprintf(bt + n, sizeof bt - n, "%s%08lx", i ? " " : "", (unsigned long)p->pc[i]);
    snprintf(buf, cap, "\"task\":%s,\"reason\":%s,\"bt\":\"%s\"",
             cali_diag_q(t, sizeof t, p->task, sizeof p->task - 1),
             cali_diag_q(r, sizeof r, p->reason, sizeof p->reason - 1), bt);
    return buf;
}

int cali_diag_panic(cali_panic_t *p) {
    char f[CALI_DIAG_BODY_MAX];
    if (!cali_panic_fields(p, f, sizeof f)) return 0;
    if (cali_diag_evt("panic", "%s", f) != 0) return -1;
    p->magic = 0;
    return 1;
}

int cali_diag_due(uint64_t now_ms) {
    static uint64_t next;
    if (!next) {
        next = now_ms + CALI_DIAG_PERIOD_MS;
        return 0;
    }
    if (now_ms < next) return 0;
    next = now_ms + CALI_DIAG_PERIOD_MS;
    return 1;
}

void cali_diag_http(const char *path, int status, size_t bytes, uint64_t ms) {
    char r[48];
    cali_diag_q(r, sizeof r, status == 404 ? "other" : path ? path : "-", 40);
    cali_diag_rec("perf", "\"route\":%s,\"status\":%d,\"bytes\":%lu,\"ms\":%llu", r, status, (unsigned long)bytes,
                  (unsigned long long)ms);
}

void cali_diag_ble(uint16_t chr, const char *op, uint32_t ms, uint64_t now_ms) {
    static struct {
        uint16_t chr;
        uint8_t n;       /* ops since the last one timed, while asked more often than 1/s */
        uint64_t last;   /* the previous op's now_ms */
    } t[24];
    static uint64_t hb_next;   /* heartbeats: one a minute (every 600 ms otherwise dominates the card) */
    size_t i;
    if (strcmp(op, "hb") == 0) {
        if (hb_next && now_ms < hb_next) return;
        hb_next = now_ms + CALI_DIAG_HB_EVERY_MS;
        cali_diag_rec("perf", "\"char\":\"%04x\",\"op\":\"hb\",\"ms\":%lu", (unsigned)chr, (unsigned long)ms);
        return;
    }
    for (i = 0; i < sizeof t / sizeof t[0] - 1 && t[i].chr && t[i].chr != chr; i++) {
    }   /* full table: the last slot is shared (ponytail: 24 > the unit's 16 chars + heartbeat) */
    int often = t[i].chr == chr && now_ms - t[i].last < CALI_DIAG_BLE_EVERY_MS;
    t[i].chr = chr;
    t[i].last = now_ms;
    if (often && ++t[i].n < CALI_DIAG_BLE_SAMPLE) return;
    t[i].n = 0;
    cali_diag_rec("perf", "\"char\":\"%04x\",\"op\":\"%s\",\"ms\":%lu", (unsigned)chr, op, (unsigned long)ms);
}

void cali_diag_log(uint64_t now_ms, const char *line) {
    static int busy, lost;              /* lost: lines a concurrent caller could not record */
    static int n, dropped, started;
    static uint64_t win;
    char tag[32], msg[120], lvl;
    const char *p = line, *colon;
    size_t len;
    if (__atomic_exchange_n(&busy, 1, __ATOMIC_ACQUIRE)) {
        __atomic_add_fetch(&lost, 1, __ATOMIC_RELAXED);
        return;
    }
    if (!started || now_ms - win >= 60000u) {   /* the minute ends: report what it dropped */
        int d = dropped + __atomic_exchange_n(&lost, 0, __ATOMIC_RELAXED);
        if (d) cali_diag_rec("log", "\"dropped\":%d", d);
        started = 1;
        win = now_ms;
        n = dropped = 0;
    }
    if (!p) goto out;
    if (*p == '\033' && (p = strchr(p, 'm')) != NULL) p++;   /* "\033[0;33m" colour */
    if (!p || (*p != 'W' && *p != 'E') || p[1] != ' ' || p[2] != '(') goto out;
    lvl = *p;
    if ((p = strchr(p, ')')) == NULL || p[1] != ' ' || (colon = strstr(p + 2, ": ")) == NULL) goto out;
    p += 2;
    if (n >= CALI_DIAG_LOG_PER_MIN) {
        dropped++;
        goto out;
    }
    n++;
    len = strcspn(colon + 2, "\033\r\n");   /* the line without its colour reset / newline */
    cali_diag_q(tag, sizeof tag, p, (size_t)(colon - p) < 24 ? (size_t)(colon - p) : 24);
    cali_diag_q(msg, sizeof msg, colon + 2, len);
    cali_diag_rec("log", "\"lvl\":\"%c\",\"tag\":%s,\"msg\":%s", lvl, tag, msg);
out:
    __atomic_store_n(&busy, 0, __ATOMIC_RELEASE);
}
