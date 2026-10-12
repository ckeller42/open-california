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
static uint32_t s_dropped;   /* any task (text records): __atomic */
static uint64_t s_next_snap;

static int logging(void) { return s_put && __atomic_load_n(&s_state, __ATOMIC_ACQUIRE) == CALI_SD_OK; }

/* Stamp r and queue it: 0, or -1 (dropped and counted). */
static int stamp_put(cali_sdlog_rec_t *r, int ev, uint16_t c, const void *data, size_t len) {
    r->up = cali_uptime_ms();
    r->ts = 0;
    r->tsrc = (uint8_t)cali_time_ts(&r->ts);   /* ponytail: another task may read a unit anchor mid-update (a ts off by one 1004 frame) */
    r->ev = (uint8_t)ev;
    r->chr = c;
    r->len = (uint16_t)len;
    if (len) memcpy(r->data, data, len);
    if (s_put(r) == 0) return 0;
    __atomic_add_fetch(&s_dropped, 1, __ATOMIC_RELAXED);
    return -1;
}

static void enqueue(int ev, uint16_t c, const uint8_t *data, size_t len) {
    cali_sdlog_rec_t r;
    if (!logging()) return;
    (void)stamp_put(&r, ev, c, data, len > CODEC_FRAME_MAX ? CODEC_FRAME_MAX : len);
}

int cali_sdlog_text(const char *body) {
    static cali_sdlog_rec_t r;   /* static: a log line may come from a task with a small stack */
    static int busy;
    size_t n = strlen(body);
    int rc;
    if (!logging()) return -1;
    if (n >= sizeof r.data || __atomic_exchange_n(&busy, 1, __ATOMIC_ACQUIRE)) {
        __atomic_add_fetch(&s_dropped, 1, __ATOMIC_RELAXED);
        return -1;
    }
    rc = stamp_put(&r, CALI_SDLOG_TEXT, 0, body, n);
    __atomic_store_n(&busy, 0, __ATOMIC_RELEASE);
    return rc;
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
    cali_json_int(j, __atomic_load_n(&s_dropped, __ATOMIC_RELAXED));
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
static uint32_t s_written;    /* the last seq written (buffered); published as last_seq once synced */
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
    if (r->ev > CALI_SDLOG_TEXT) return -1;
    int n = snprintf(buf, cap, "{\"seq\":%lu,\"boot\":%lu,\"up\":%llu,\"ts\":%s,\"tsrc\":%s,",
                     (unsigned long)seq, (unsigned long)boot, (unsigned long long)r->up, ts, src);
    if (n < 0 || (size_t)n >= cap) return -1;
    if (r->ev == CALI_SDLOG_TEXT) {   /* the body brings its own "ev" */
        if ((size_t)n + r->len + 3 > cap) return -1;
        memcpy(buf + n, r->data, r->len);
        memcpy(buf + n + r->len, "}\n", 3);
        return n + r->len + 2;
    }
    n += snprintf(buf + n, cap - (size_t)n, "\"ev\":\"%s\"", EV[r->ev]);
    if ((size_t)n >= cap) return -1;
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

/* In dir: above = 0 -> the newest segment whose first seq is <= ref; above = 1 -> the oldest one
 * whose first seq is > ref. 0 = none. */
static uint32_t seg_near(const char *dir, uint32_t ref, int above) {
    DIR *d = opendir(dir);
    struct dirent *e;
    uint32_t best = 0, v;
    if (!d) return 0;
    while ((e = readdir(d)) != NULL)
        if (seg_name(e->d_name, &v) && (above ? v > ref && (!best || v < best) : v <= ref && v > best)) best = v;
    closedir(d);
    return best;
}

int cali_sdlog_page(const char *dir, uint32_t since, uint32_t max, uint32_t boot, char *buf, size_t cap) {
    char p[PATH_MAX_LEN + 16], line[CALI_SDLOG_LINE_MAX];
    size_t n = 0, room;
    uint32_t next = since, count = 0, lost = 0;
    int more = 0;
    if (cap < CALI_SDLOG_PAGE_MIN) return -1;
    room = cap - (CALI_SDLOG_PAGE_MIN - CALI_SDLOG_LINE_MAX);   /* the trailer always fits */
    if (!max) max = CALI_SDLOG_PAGE_DEFAULT;
    uint32_t seg = since < UINT32_MAX ? seg_near(dir, since + 1, 0) : 0;
    if (!seg) {   /* since+1 is older than every segment: resume from the oldest, count the gap */
        seg = seg_near(dir, 0, 1);
        if (seg > since + 1) lost = seg - since - 1;
    }
    while (seg && !more) {
        int whole = 1;   /* the chunk being read starts a line */
        snprintf(p, sizeof p, "%s/%lu.jsonl", dir, (unsigned long)seg);
        FILE *f = fopen(p, "r");
        if (!f) return -1;
        while (!more && fgets(line, sizeof line, f)) {
            size_t len = strlen(line);
            int complete = len && line[len - 1] == '\n';
            if (complete && whole && strncmp(line, "{\"seq\":", 7) == 0) {
                unsigned long long seq = strtoull(line + 7, NULL, 10);
                if (seq > next && seq <= UINT32_MAX) {
                    if (count >= max || n + len > room) {
                        more = 1;
                    } else {
                        memcpy(buf + n, line, len);
                        n += len;
                        next = (uint32_t)seq;
                        count++;
                    }
                }
            }
            whole = complete;
        }
        fclose(f);
        seg = seg_near(dir, seg, 1);
    }
    int t = snprintf(buf + n, cap - n, "{\"next\":%lu,\"more\":%s,\"boot\":%lu,\"lost\":%lu}\n", (unsigned long)next,
                     more ? "true" : "false", (unsigned long)boot, (unsigned long)lost);
    return t < 0 || (size_t)t >= cap - n ? -1 : (int)(n + (size_t)t);
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
    s_written = s_next - 1;
    __atomic_store_n(&s_last_seq, s_written, __ATOMIC_RELAXED);
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
    s_written = s_next++;
    return rolled;
}

int cali_sdlog_sync(void) {
    if (!s_f || fflush(s_f) != 0 || fsync(fileno(s_f)) != 0) return -1;
    __atomic_store_n(&s_last_seq, s_written, __ATOMIC_RELAXED);   /* now really on the card */
    return 0;
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

/* ---- /api/log hand-off: owner task requests, SD task reads (shared fields __atomic) ---- */
enum { PAGE_IDLE, PAGE_REQ, PAGE_DONE, PAGE_FAIL };
static int s_pg = PAGE_IDLE;
static char *s_pg_buf;
static size_t s_pg_cap, s_pg_len;
static uint32_t s_pg_since, s_pg_max;

void cali_sdlog_page_buffer(char *buf, size_t cap) {
    s_pg_buf = buf;
    s_pg_cap = cap;
}

int cali_sdlog_page_request(uint32_t since, uint32_t max) {
    if (!s_pg_buf || __atomic_load_n(&s_state, __ATOMIC_ACQUIRE) != CALI_SD_OK) return -1;
    if (__atomic_load_n(&s_pg, __ATOMIC_ACQUIRE) == PAGE_REQ) return -2;
    s_pg_since = since;
    s_pg_max = max;
    __atomic_store_n(&s_pg, PAGE_REQ, __ATOMIC_RELEASE);
    return 0;
}

int cali_sdlog_page_result(const char **body, size_t *len) {
    int st = __atomic_load_n(&s_pg, __ATOMIC_ACQUIRE);
    if (st == PAGE_FAIL) return -1;
    if (st != PAGE_DONE) return 0;
    *body = s_pg_buf;
    *len = s_pg_len;
    return 1;
}

int cali_sdlog_page_pending(void) { return __atomic_load_n(&s_pg, __ATOMIC_ACQUIRE) == PAGE_REQ; }

void cali_sdlog_page_serve(void) {
    int n;
    if (__atomic_load_n(&s_pg, __ATOMIC_ACQUIRE) != PAGE_REQ) return;
    /* the newest records sit in the stdio buffer, and FATFS publishes a file's size only on f_sync */
    if (!s_f || cali_sdlog_sync() != 0) n = -1;
    else n = cali_sdlog_page(s_dir, s_pg_since, s_pg_max, cali_time_boot(), s_pg_buf, s_pg_cap);
    s_pg_len = n > 0 ? (size_t)n : 0;
    __atomic_store_n(&s_pg, n > 0 ? PAGE_DONE : PAGE_FAIL, __ATOMIC_RELEASE);
}
