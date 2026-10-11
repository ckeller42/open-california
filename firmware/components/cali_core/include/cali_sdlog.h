/* cali_sdlog.h — the SD log (R_FW_SD_LOG, #154 standalone, Part 3): every unit frame and link change,
 * stamped at log time, as JSONL on the card, so buspi can fill the gaps of its own history.
 *
 * One record per line, calictl's CALICTL_BLE_TRACE shape (calictl/trace.py: ev/char/fn/hex, or
 * ev/addr for a link event) plus seq/boot/up/ts/tsrc (cali_time.h):
 *   {"seq":1042,"boot":17,"up":523110,"ts":1789639127412,"tsrc":"unit","ev":"notify","char":"1602","fn":"energy","hex":"00d0…"}
 *   {"seq":1043,"boot":17,"up":530002,"ts":null,"tsrc":null,"ev":"disconnect","addr":null}
 * ev: "notify" | "read" (as the unit sent it) | "snap" (the session's held frame, written for every
 * function every CALI_SDLOG_SNAP_MS while the link is up, so one segment always holds the whole
 * state) | "connect" | "disconnect". seq counts up by one per written record, across boots.
 *
 * Files: <dir>/<first seq>.jsonl, a new segment once the next line would pass
 * CALI_SDLOG_SEGMENT_BYTES. cali_sdlog_open resumes seq from the newest segment's last complete line
 * (a line cut by a power loss is truncated away first) — no per-record NVS writes.
 *
 * Two sides, two tasks:
 *   owner task  cali_sdlog_init(put), cali_sdlog_io (the session hook, cali_session.h; also feeds a
 *               1004 frame to the unit clock first, so that record is stamped by it), cali_sdlog_tick,
 *               cali_sdlog_json. It stamps each record and hands it to put (the platform's bounded
 *               queue); a full queue drops the record and counts it. Nothing is queued while the card
 *               is not "ok" (no card = no log, not a growing drop count).
 *   SD task     cali_sdlog_open / _write / _sync / _delete_oldest / _close (plain stdio + dirent:
 *               ESP FATFS on the card, a temp dir on the host) and cali_sdlog_card (device.sd).
 * Shared fields are __atomic (two cores). C99, no malloc, no ESP-IDF headers. Proven by
 * tests/firmware/test_sdlog.py (test/sdlog_cli.c) against a temp directory.
 */
#ifndef CALI_SDLOG_H
#define CALI_SDLOG_H

#include <stddef.h>
#include <stdint.h>

#include "cali_json.h"
#include "codec.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_SDLOG_SEGMENT_BYTES (1024L * 1024L)   /* roll at 1 MB */
#define CALI_SDLOG_SNAP_MS (5u * 60u * 1000u)       /* full snapshot every 5 min while linked */
#define CALI_SDLOG_LINE_MAX 256                     /* the longest record is ~190 B */

/* ev codes: cali_session.h's CALI_SESSION_IO_* (notify, read, connect, disconnect) + snap */
enum { CALI_SDLOG_NOTIFY, CALI_SDLOG_READ, CALI_SDLOG_CONNECT, CALI_SDLOG_DISCONNECT, CALI_SDLOG_SNAP };
/* device.sd.state */
enum { CALI_SD_ABSENT, CALI_SD_ERROR, CALI_SD_OK };

typedef struct {
    uint64_t up;      /* cali_uptime_ms() at log time */
    int64_t ts;       /* epoch ms, valid when tsrc != CALI_TSRC_NONE */
    uint8_t tsrc;     /* CALI_TSRC_* */
    uint8_t ev;       /* CALI_SDLOG_* */
    uint16_t chr;     /* the char's short id (0 for a link event) */
    uint8_t len;
    uint8_t data[CODEC_FRAME_MAX];
} cali_sdlog_rec_t;

/* The platform's queue to the SD task: 0 queued, -1 full (never blocks). */
typedef int (*cali_sdlog_put_fn)(const cali_sdlog_rec_t *r);

/* ---- owner task ---- */
void cali_sdlog_init(cali_sdlog_put_fn put);   /* NULL (host, QEMU): nothing is logged */
void cali_sdlog_io(int what, uint16_t char_short, const uint8_t *data, size_t len);   /* cali_session_io_fn */
void cali_sdlog_tick(uint64_t now_ms);   /* the 5-min snapshot */
/* device.sd (a value: call cali_json_key first):
 * {"state":"absent"|"error"|"ok","total_mb":<int>|null,"free_mb":<int>|null,"last_seq":<int>|null,"dropped":<int>}
 * total/free only while ok; last_seq = the last record fsynced to the card (null: none yet). */
void cali_sdlog_json(cali_json_t *j);

/* ---- SD task ---- */
/* One record as a JSON line (with the '\n'): its length, or -1 when it does not fit cap. */
int cali_sdlog_format(char *buf, size_t cap, uint32_t seq, uint32_t boot, const cali_sdlog_rec_t *r);
/* Open the log in dir (created if missing): recover seq, append to the newest segment. 0 / -1. */
int cali_sdlog_open(const char *dir);
/* Append one record (the next seq, this boot): 0, 1 = it started a new segment, -1 = I/O error
 * (the caller closes and remounts). */
int cali_sdlog_write(const cali_sdlog_rec_t *r);
int cali_sdlog_sync(void);            /* fflush + fsync: 0 / -1 */
int cali_sdlog_delete_oldest(void);   /* the oldest segment but the open one: 0 deleted, -1 none */
void cali_sdlog_close(void);
uint32_t cali_sdlog_next_seq(void);   /* the seq the next record gets */
/* The card as the SD task sees it (device.sd). total/free in MB (ignored unless CALI_SD_OK). */
void cali_sdlog_card(int state, uint32_t total_mb, uint32_t free_mb);

/* ---- GET /api/log (R_FW_LOG_API): one page of the log for buspi's esp-ingest ----
 * Portable, any task: one page of the log in dir into buf — every complete record line with
 * seq > since, oldest first, at most max records and as many as fit cap less the trailer — then the
 * trailer line
 *   {"next":<the page's last seq, or since>,"more":<bool>,"boot":<boot>,"lost":<n>}
 * more = records were left for the next page; lost = seqs since+1 .. that are gone (their segment
 * was deleted at 90 % full): the page resumes from the oldest segment. A line without its '\n' (a
 * record being written / cut by a power loss) is never sent. Returns the bytes in buf, or -1 (a
 * segment unreadable, cap below CALI_SDLOG_PAGE_MIN). */
#define CALI_SDLOG_PAGE_DEFAULT 2000u   /* records per page when max is 0 */
#define CALI_SDLOG_PAGE_MIN (CALI_SDLOG_LINE_MAX + 96)   /* one record + the trailer */
int cali_sdlog_page(const char *dir, uint32_t since, uint32_t max, uint32_t boot, char *buf, size_t cap);

/* The owner task <-> SD task hand-off (the owner task never touches the card):
 *   platform, once      cali_sdlog_page_buffer(buf, cap): the page buffer (ESP: PSRAM). None = no /api/log.
 *   owner (web.c)       cali_sdlog_page_request(since, max): 0 queued, -1 no card / no buffer,
 *                       -2 busy (an earlier page is still being read); then cali_sdlog_page_result:
 *                       1 done (*body, *len: the page, valid until the next request), 0 not yet,
 *                       -1 the read failed.
 *   SD task             cali_sdlog_page_serve(): reads a requested page of the open log (after a
 *                       sync, so it ends at the newest record); a no-op otherwise. */
void cali_sdlog_page_buffer(char *buf, size_t cap);
int cali_sdlog_page_request(uint32_t since, uint32_t max);
int cali_sdlog_page_result(const char **body, size_t *len);
void cali_sdlog_page_serve(void);
int cali_sdlog_page_pending(void);   /* SD task: 1 while a page is requested (take the bus lock, serve) */

#ifdef __cplusplus
}
#endif

#endif /* CALI_SDLOG_H */
