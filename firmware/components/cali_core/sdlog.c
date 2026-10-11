/* sdlog.c — the SD log's portable core (R_FW_SD_LOG): stamping + queueing on the owner task, record
 * format, segment rolling and seq recovery on the SD task. Contract: include/cali_sdlog.h. */
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L   /* fileno, fsync, truncate, opendir under -std=c99 (host tests) */
#endif
#include "cali_sdlog.h"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "cali_platform.h"
#include "cali_session.h"
#include "cali_time.h"
#include "codec_chars.h"

#define VEHICLE_CHAR 0x1004u   /* 1004: CarTime*, the unit clock (cali_time_on_vehicle) */
#define PATH_MAX_LEN 200   /* the dir; ESP: "/sdcard/log" (host temp dirs are long) */

/* ---- shared with the SD task (__atomic: two cores) ---- */
static int s_state = CALI_SD_ABSENT;
static uint32_t s_total_mb, s_free_mb;
static uint32_t s_last_seq;   /* 0 = none yet */

/* ---- owner task ---- */
static cali_sdlog_put_fn s_put;
static uint32_t s_dropped;
static uint64_t s_next_snap;

static void enqueue(int ev, uint16_t c, const uint8_t *data, size_t len) {
    cali_sdlog_rec_t r;
    if (!s_put || __atomic_load_n(&s_state, __ATOMIC_ACQUIRE) != CALI_SD_OK) return;
    if (len > sizeof r.data) len = sizeof r.data;
    r.up = cali_uptime_ms();
    r.ts = 0;
    r.tsrc = (uint8_t)cali_time_ts(&r.ts);
    r.ev = (uint8_t)ev;
    r.chr = c;
    r.len = (uint8_t)len;
    if (len) memcpy(r.data, data, len);
    if (s_put(&r) != 0) s_dropped++;
}

void cali_sdlog_init(cali_sdlog_put_fn put) {
    s_put = put;
    s_dropped = 0;
    s_next_snap = 0;
}

void cali_sdlog_io(int what, uint16_t c, const uint8_t *data, size_t len) {
    /* the session's IO codes are the first four CALI_SDLOG_* codes (cali_sdlog.h) */
    if ((what == CALI_SESSION_IO_NOTIFY || what == CALI_SESSION_IO_READ) && c == VEHICLE_CHAR)
        cali_time_on_vehicle(data, len);   /* first: this very frame is stamped by its own clock */
    enqueue(what, c, data, len);
}

void cali_sdlog_tick(uint64_t now_ms) {
    if (!s_put || now_ms < s_next_snap) return;
    s_next_snap = now_ms + CALI_SDLOG_SNAP_MS;
    if (!cali_session_link_up()) return;   /* a stale held state is no snapshot of the unit */
    for (size_t i = 0; i < CODEC_NCHARS; i++) {
        const uint8_t *f;
        size_t n;
        if (cali_session_frame(i, &f, &n)) enqueue(CALI_SDLOG_SNAP, CODEC_CHARS[i].state_short, f, n);
    }
}

void cali_sdlog_json(cali_json_t *j) {
    static const char *const STATE[] = {"absent", "error", "ok"};
    int st = __atomic_load_n(&s_state, __ATOMIC_ACQUIRE);
    uint32_t last = __atomic_load_n(&s_last_seq, __ATOMIC_RELAXED);
    cali_json_obj_begin(j);
    cali_json_key(j, "state");
    cali_json_str(j, STATE[st]);
    cali_json_key(j, "total_mb");
    if (st == CALI_SD_OK) cali_json_int(j, __atomic_load_n(&s_total_mb, __ATOMIC_RELAXED)); else cali_json_null(j);
    cali_json_key(j, "free_mb");
    if (st == CALI_SD_OK) cali_json_int(j, __atomic_load_n(&s_free_mb, __ATOMIC_RELAXED)); else cali_json_null(j);
    cali_json_key(j, "last_seq");
    if (last) cali_json_int(j, last); else cali_json_null(j);
    cali_json_key(j, "dropped");
    cali_json_int(j, s_dropped);
    cali_json_obj_end(j);
}

void cali_sdlog_card(int state, uint32_t total_mb, uint32_t free_mb) {
    __atomic_store_n(&s_total_mb, total_mb, __ATOMIC_RELAXED);
    __atomic_store_n(&s_free_mb, free_mb, __ATOMIC_RELAXED);
    __atomic_store_n(&s_state, state, __ATOMIC_RELEASE);
}

/* ---- SD task ---- */
static char s_dir[PATH_MAX_LEN];
static FILE *s_f;
static uint32_t s_next = 1;   /* the seq the next record gets */
static uint32_t s_seg_first;  /* the open segment's name */
static long s_seg_bytes;
static char s_iobuf[4096];    /* stdio buffer: whole sectors to the card, not 128 B pieces */

static const char *fn_of(uint16_t c) {
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        if (CODEC_CHARS[i].state_short == c) return CODEC_CHARS[i].function;
    return NULL;
}

int cali_sdlog_format(char *buf, size_t cap, uint32_t seq, uint32_t boot, const cali_sdlog_rec_t *r) {
    static const char *const EV[] = {"notify", "read", "connect", "disconnect", "snap"};
    char ts[24] = "null";
    const char *src = r->tsrc == CALI_TSRC_NTP ? "\"ntp\"" : r->tsrc == CALI_TSRC_UNIT ? "\"unit\"" : "null";
    if (r->tsrc != CALI_TSRC_NONE) snprintf(ts, sizeof ts, "%lld", (long long)r->ts);
    if (r->ev >= sizeof EV / sizeof EV[0]) return -1;
    int n = snprintf(buf, cap, "{\"seq\":%lu,\"boot\":%lu,\"up\":%llu,\"ts\":%s,\"tsrc\":%s,\"ev\":\"%s\"",
                     (unsigned long)seq, (unsigned long)boot, (unsigned long long)r->up, ts, src, EV[r->ev]);
    if (n < 0 || (size_t)n >= cap) return -1;
    if (r->ev == CALI_SDLOG_CONNECT || r->ev == CALI_SDLOG_DISCONNECT) {
        /* calictl's link record carries the address; the card never holds the unit's MAC */
        n += snprintf(buf + n, cap - (size_t)n, ",\"addr\":null}\n");
        return (size_t)n < cap ? n : -1;
    }
    const char *fn = fn_of(r->chr);
    n += snprintf(buf + n, cap - (size_t)n, ",\"char\":\"%04x\",\"fn\":%s%s%s,\"hex\":\"", (unsigned)r->chr,
                  fn ? "\"" : "", fn ? fn : "null", fn ? "\"" : "");
    for (size_t i = 0; i < r->len && (size_t)n < cap; i++)
        n += snprintf(buf + n, cap - (size_t)n, "%02x", r->data[i]);
    if ((size_t)n < cap) n += snprintf(buf + n, cap - (size_t)n, "\"}\n");
    return (size_t)n < cap ? n : -1;
}

/* "<digits>.jsonl" -> 1 and *seq; else 0 */
static int seg_name(const char *name, uint32_t *seq) {
    char *end;
    if (name[0] < '0' || name[0] > '9') return 0;
    unsigned long long v = strtoull(name, &end, 10);
    if (strcmp(end, ".jsonl") != 0 || v == 0 || v > UINT32_MAX) return 0;
    *seq = (uint32_t)v;
    return 1;
}

/* The oldest (want_oldest) or newest segment's first seq, skipping `skip`; 0 = none. */
static uint32_t find_seg(int want_oldest, uint32_t skip) {
    DIR *d = opendir(s_dir);
    struct dirent *e;
    uint32_t best = 0, v;
    if (!d) return 0;
    while ((e = readdir(d)) != NULL)
        if (seg_name(e->d_name, &v) && v != skip && (!best || (want_oldest ? v < best : v > best))) best = v;
    closedir(d);
    return best;
}

static void seg_path(char *out, size_t cap, uint32_t first) {
    snprintf(out, cap, "%s/%lu.jsonl", s_dir, (unsigned long)first);
}

static int open_seg(uint32_t first, long bytes) {
    char p[PATH_MAX_LEN + 16];
    seg_path(p, sizeof p, first);
    s_f = fopen(p, "a");
    if (!s_f) return -1;
    setvbuf(s_f, s_iobuf, _IOFBF, sizeof s_iobuf);
    s_seg_first = first;
    s_seg_bytes = bytes;
    return 0;
}

/* The newest segment: the last complete line's seq -> s_next, and anything after that line (a
 * record cut by a power loss) truncated away. Returns the bytes kept. */
static long recover(uint32_t first) {
    char p[PATH_MAX_LEN + 16], line[CALI_SDLOG_LINE_MAX];
    long good = 0, pos = 0;
    int whole = 1;   /* the chunk being read starts a line */
    seg_path(p, sizeof p, first);
    s_next = first;   /* an empty segment is named for the record it never got */
    FILE *f = fopen(p, "r");
    if (!f) return 0;
    while (fgets(line, sizeof line, f)) {
        size_t n = strlen(line);
        int complete = n && line[n - 1] == '\n';
        pos += (long)n;
        if (complete && whole && strncmp(line, "{\"seq\":", 7) == 0) {
            unsigned long long seq = strtoull(line + 7, NULL, 10);
            if (seq >= first && seq < UINT32_MAX) {
                s_next = (uint32_t)seq + 1;
                good = pos;
            }
        }
        whole = complete;
    }
    fclose(f);
    if (pos > good) {
        cali_log("sdlog: %lu.jsonl ends in a cut record (%ld bytes dropped)", (unsigned long)first, pos - good);
        if (truncate(p, good) != 0) cali_log("sdlog: truncate failed");
    }
    return good;
}

int cali_sdlog_open(const char *dir) {
    cali_sdlog_close();
    if ((size_t)snprintf(s_dir, sizeof s_dir, "%s", dir) >= sizeof s_dir) return -1;
    (void)mkdir(s_dir, 0777);   /* exists already: fine; unusable: opendir/fopen say so */
    uint32_t newest = find_seg(0, 0);
    long kept = 0;
    if (newest) {
        kept = recover(newest);
    } else {
        s_next = 1;
        newest = 1;
    }
    if (kept >= CALI_SDLOG_SEGMENT_BYTES) {   /* full: start the next one */
        newest = s_next;
        kept = 0;
    }
    if (open_seg(newest, kept) != 0) return -1;
    __atomic_store_n(&s_last_seq, s_next - 1, __ATOMIC_RELAXED);
    cali_log("sdlog: %s/%lu.jsonl, next seq %lu", s_dir, (unsigned long)newest, (unsigned long)s_next);
    return 0;
}

int cali_sdlog_write(const cali_sdlog_rec_t *r) {
    char line[CALI_SDLOG_LINE_MAX];
    int rolled = 0;
    if (!s_f) return -1;
    int n = cali_sdlog_format(line, sizeof line, s_next, cali_time_boot(), r);
    if (n < 0) return 0;   /* cannot happen (the longest record fits): skip it, keep logging */
    if (s_seg_bytes > 0 && s_seg_bytes + n > CALI_SDLOG_SEGMENT_BYTES) {
        int bad = fclose(s_f) != 0;
        s_f = NULL;
        if (bad || open_seg(s_next, 0) != 0) return -1;
        rolled = 1;
    }
    if (fwrite(line, 1, (size_t)n, s_f) != (size_t)n) return -1;
    s_seg_bytes += n;
    __atomic_store_n(&s_last_seq, s_next, __ATOMIC_RELAXED);
    s_next++;
    return rolled;
}

int cali_sdlog_sync(void) {
    if (!s_f) return -1;
    return fflush(s_f) == 0 && fsync(fileno(s_f)) == 0 ? 0 : -1;
}

int cali_sdlog_delete_oldest(void) {
    char p[PATH_MAX_LEN + 16];
    uint32_t old = find_seg(1, s_seg_first);
    if (!old || old > s_seg_first) return -1;   /* only the open segment (or newer junk) is left */
    seg_path(p, sizeof p, old);
    if (remove(p) != 0) return -1;
    cali_log("sdlog: card above 90%%, deleted %lu.jsonl", (unsigned long)old);
    return 0;
}

void cali_sdlog_close(void) {
    if (s_f) (void)fclose(s_f);
    s_f = NULL;
}

uint32_t cali_sdlog_next_seq(void) { return s_next; }
