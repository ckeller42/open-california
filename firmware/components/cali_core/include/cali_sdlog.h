/**
 * @file cali_sdlog.h
 * @brief The SD log: every unit frame and link change, stamped at log time, as JSONL on the card.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite logs every unit frame to its SD card, stamped, in 1 MB segments
 *    :id: R_FW_SD_LOG
 *    :status: implemented
 *    :tags: esp32, sd, log
 *
 *    The firmware shall append every unit frame the session sees (notify and read), every link up/down
 *    and, every 5 min while linked, a snapshot of every held frame to ``/sdcard/log/<first seq>.jsonl``
 *    as JSON lines in calictl's ``CALICTL_BLE_TRACE`` shape plus ``seq``/``boot``/``up``/``ts``/``tsrc``
 *    (stamped at log time, ``R_FW_TIME``); roll segments at 1 MB, resume ``seq`` from the newest
 *    segment's last complete line (truncating a line cut by a power loss), delete the oldest segment
 *    above 90 % full, ``fsync`` at least every 10 s, and never block the owner task (a bounded queue;
 *    overflow drops and counts). A missing or unreadable card never aborts or reboots the firmware: it
 *    retries the mount every 5 min. ``/api/state`` reports ``device.sd``
 *    ``{state: absent|error|ok, total_mb, free_mb, dropped}`` (``last_seq`` with ``?diag=1``), shown as a
 *    Storage row in the shared UI's Device status card, EN + DE.
 *
 * .. req:: The satellite serves its SD log page by page over HTTP
 *    :id: R_FW_LOG_API
 *    :status: implemented
 *    :tags: esp32, sd, log
 *
 *    ``GET /api/log?since=<seq>&max=<n>`` shall answer the complete log records with ``seq > since``
 *    in seq order across segments, at most ``max`` (default 2000) and what fits the page buffer, then
 *    a trailer ``{"next", "more", "boot", "lost"}`` (``lost`` = seqs in deleted segments, the page
 *    resuming from the oldest one); never a half-written line. The card is read only by the SD task;
 *    no card = 503 with a JSON error.
 * @endverbatim
 *
 * One record per line, in the shape of calictl's `CALICTL_BLE_TRACE` (`calictl/trace.py`: `ev`,
 * `char`, `fn`, `hex`, or `ev` and `addr` for a link event) plus `seq`, `boot`, `up`, `ts` and
 * `tsrc` (see cali_time.h):
 *
 *     {"seq":1042,"boot":17,"up":523110,"ts":1789639127412,"tsrc":"unit","ev":"notify","char":"1602","fn":"energy","hex":"00d0..."}
 *     {"seq":1043,"boot":17,"up":530002,"ts":null,"tsrc":null,"ev":"disconnect","addr":null}
 *
 * The `ev` field is `notify` or `read` (the frame as the unit sent it), `snap` (the session's held
 * frame, written for every function every CALI_SDLOG_SNAP_MS while the link is up, so one segment
 * always holds the whole state), `connect` or `disconnect`. The `seq` counts up by one per written
 * record, across boots. A text record (cali_sdlog_text(): the diagnostics of cali_diag.h) carries
 * its own fields after the stamp: `{"seq":...,"tsrc":...,"ev":"diag",...}`.
 *
 * The files are named `first seq`.jsonl in the log directory. A new segment starts once the next
 * line would pass CALI_SDLOG_SEGMENT_BYTES. cali_sdlog_open() resumes `seq` from the last complete
 * line of the newest segment (a line cut by a power loss is truncated away first), so there is no
 * per-record NVS write.
 *
 * Two sides run on two tasks:
 * - The owner task calls cali_sdlog_init(), cali_sdlog_io() (the session hook of cali_session.h; it
 *   also feeds a 1004 frame to the unit clock first, so that record is stamped by it),
 *   cali_sdlog_tick() and cali_sdlog_json(). It stamps each record and hands it to the put
 *   function (the platform's bounded queue); a full queue drops the record and counts it. Nothing
 *   is queued while the card is not `ok` (no card means no log, not a growing drop count).
 * - The SD task calls cali_sdlog_open(), cali_sdlog_write(), cali_sdlog_sync(),
 *   cali_sdlog_delete_oldest(), cali_sdlog_close() (plain stdio and dirent: ESP FATFS on the card,
 *   a temp dir on the host) and cali_sdlog_card() (`device.sd`).
 *
 * Shared fields are `__atomic` (two cores). C99, no malloc, no ESP-IDF headers. Proven by
 * `tests/firmware/test_sdlog.py` (`test/sdlog_cli.c`) against a temp directory.
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

/** @brief The segment size in bytes: a new segment starts at 1 MB. */
#define CALI_SDLOG_SEGMENT_BYTES (1024L * 1024L)
/** @brief The interval of the full snapshot while the link is up: 5 min. */
#define CALI_SDLOG_SNAP_MS (5u * 60u * 1000u)
/** @brief The longest record data in bytes: a frame (at most CODEC_FRAME_MAX) or a text body. */
#define CALI_SDLOG_DATA_MAX 288
/** @brief The longest line in bytes: the stamp (at most 110), a text body and the closing brace and newline. */
#define CALI_SDLOG_LINE_MAX 416

/** @brief The `ev` codes: cali_session.h's CALI_SESSION_IO_* (notify, read, connect, disconnect) plus snap and text. */
enum cali_sdlog_ev_e {
    CALI_SDLOG_NOTIFY,       /**< A notification as the unit sent it. */
    CALI_SDLOG_READ,         /**< A read reply as the unit sent it. */
    CALI_SDLOG_CONNECT,      /**< The link came up. */
    CALI_SDLOG_DISCONNECT,   /**< The link went down. */
    CALI_SDLOG_SNAP,         /**< The session's held frame of one function. */
    CALI_SDLOG_TEXT          /**< A text record (the diagnostics). */
};
/** @brief The card states; the values of `device.sd.state`. */
enum cali_sd_state_e {
    CALI_SD_ABSENT,   /**< No card. */
    CALI_SD_ERROR,    /**< A card that does not mount or fails. */
    CALI_SD_OK        /**< A mounted card. */
};

/** @brief One record on its way to the card. */
typedef struct {
    uint64_t up;      /**< cali_uptime_ms() at log time. */
    int64_t ts;       /**< Epoch ms, valid when tsrc is not CALI_TSRC_NONE. */
    uint8_t tsrc;     /**< One of CALI_TSRC_*. */
    uint8_t ev;       /**< One of #cali_sdlog_ev_e. */
    uint16_t chr;     /**< The characteristic's short id (0 for a link event). */
    uint16_t len;     /**< Bytes in data. */
    uint8_t data[CALI_SDLOG_DATA_MAX];   /**< The frame, or (CALI_SDLOG_TEXT) the body, no NUL. */
} cali_sdlog_rec_t;

/**
 * @brief The platform's queue to the SD task. It never blocks.
 * @param r The record to queue.
 * @return 0 queued, -1 full.
 */
typedef int (*cali_sdlog_put_fn)(const cali_sdlog_rec_t *r);

/* ---- owner task ---- */

/**
 * @brief Set the queue to the SD task. Owner task.
 * @param put The queue. NULL (host, QEMU): nothing is logged.
 */
void cali_sdlog_init(cali_sdlog_put_fn put);
/**
 * @brief The session hook (cali_session_io_fn): stamp and queue one frame or link event. Owner task.
 * @param what One of CALI_SESSION_IO_*.
 * @param char_short The characteristic's short id.
 * @param data The frame (NULL for a link event).
 * @param len Bytes in data.
 */
void cali_sdlog_io(int what, uint16_t char_short, const uint8_t *data, size_t len);
/**
 * @brief Write the snapshot every CALI_SDLOG_SNAP_MS while linked. Owner task.
 * @param now_ms The uptime in ms.
 */
void cali_sdlog_tick(uint64_t now_ms);
/**
 * @brief Queue a text record. Any task (this is cali_diag_sink).
 * It never blocks: a concurrent caller, a full queue or an overlong body is dropped and counted.
 * @param body The record fields as `"ev":"...",...` without the braces, under CALI_SDLOG_DATA_MAX bytes.
 * @return 0 queued, -1 not (also when there is no card).
 */
int cali_sdlog_text(const char *body);
/**
 * @brief Write the `device.sd` object. It is a value: call cali_json_key() first.
 * The object reads `{"state":"absent"|"error"|"ok","total_mb":int|null,"free_mb":int|null,
 * "last_seq":int|null,"dropped":int}`. The totals show only while the card is ok. The last_seq is
 * the last record synced to the card (null: none yet).
 * @param j The JSON writer.
 * @param diag Not 0 to add last_seq (the diagnostics query).
 */
void cali_sdlog_json(cali_json_t *j, int diag);

/* ---- SD task ---- */

/**
 * @brief Format one record as a JSON line.
 * @param buf The destination.
 * @param cap The size of buf.
 * @param seq The record's sequence number.
 * @param boot The boot counter.
 * @param r The record.
 * @return The line length including the newline, or -1 when it does not fit cap.
 */
int cali_sdlog_format(char *buf, size_t cap, uint32_t seq, uint32_t boot, const cali_sdlog_rec_t *r);
/**
 * @brief Open the log: create the directory if missing, recover seq and append to the newest segment.
 * @param dir The log directory.
 * @return 0 ok, -1 failed.
 */
int cali_sdlog_open(const char *dir);
/**
 * @brief Append one record with the next seq and this boot.
 * @param r The record.
 * @return 0 written, 1 written and it started a new segment, -1 I/O error (the caller closes and remounts).
 */
int cali_sdlog_write(const cali_sdlog_rec_t *r);
/** @brief Flush and fsync the open segment. @return 0 ok, -1 failed. */
int cali_sdlog_sync(void);
/** @brief Delete the oldest segment but the open one. @return 0 deleted, -1 none. */
int cali_sdlog_delete_oldest(void);
/** @brief Close the log. */
void cali_sdlog_close(void);
/** @brief The seq the next record gets. @return The sequence number. */
uint32_t cali_sdlog_next_seq(void);
/**
 * @brief Report the card as the SD task sees it (`device.sd`).
 * @param state One of #cali_sd_state_e.
 * @param total_mb The card size in MB (ignored unless CALI_SD_OK).
 * @param free_mb The free space in MB (ignored unless CALI_SD_OK).
 */
void cali_sdlog_card(int state, uint32_t total_mb, uint32_t free_mb);

/* ---- GET /api/log ---- */

/** @brief The records per page when max is 0. */
#define CALI_SDLOG_PAGE_DEFAULT 2000u
/** @brief The smallest page buffer in bytes: one record and the trailer. */
#define CALI_SDLOG_PAGE_MIN (CALI_SDLOG_LINE_MAX + 96)
/**
 * @brief Build one page of the log for buspi's esp-ingest. Portable, any task.
 * The page holds every complete record line with seq above since, oldest first, at most max
 * records and as many as fit cap less the trailer. Then comes the trailer line
 * `{"next":N,"more":bool,"boot":B,"lost":n}`. N is the page's last seq, or since. The more flag
 * says records were left for the next page. The lost count is the number of seqs after since that
 * are gone (their segment was deleted at 90 % full); the page then resumes from the oldest
 * segment. A line without its newline (a record being written, or cut by a power loss) is never
 * sent.
 * @param dir The log directory.
 * @param since The last seq the reader has.
 * @param max The most records (0: CALI_SDLOG_PAGE_DEFAULT).
 * @param boot The current boot number for the trailer.
 * @param buf The destination.
 * @param cap The size of buf.
 * @return The bytes in buf, or -1 (a segment is unreadable, or cap is below CALI_SDLOG_PAGE_MIN).
 */
int cali_sdlog_page(const char *dir, uint32_t since, uint32_t max, uint32_t boot, char *buf, size_t cap);

/*
 * The owner task and the SD task hand a page over; the owner task never touches the card.
 * The platform calls cali_sdlog_page_buffer() once. The owner task (web.c) calls
 * cali_sdlog_page_request() and then cali_sdlog_page_result(). The SD task calls
 * cali_sdlog_page_serve() when cali_sdlog_page_pending() says so.
 */

/**
 * @brief Give the page buffer (on the ESP: PSRAM). Platform, once. Without one there is no /api/log.
 * @param buf The buffer.
 * @param cap The size of buf.
 */
void cali_sdlog_page_buffer(char *buf, size_t cap);
/**
 * @brief Request a page. Owner task.
 * @param since The last seq the reader has.
 * @param max The most records.
 * @return 0 queued, -1 no card or no buffer, -2 busy (an earlier page is still being read).
 */
int cali_sdlog_page_request(uint32_t since, uint32_t max);
/**
 * @brief Poll for the requested page. Owner task.
 * @param body Set to the page (valid until the next request) when done.
 * @param len Set to the page length when done.
 * @return 1 done, 0 not yet, -1 the read failed.
 */
int cali_sdlog_page_result(const char **body, size_t *len);
/** @brief Read a requested page of the open log, after a sync so it ends at the newest record. SD task. A no-op with no request. */
void cali_sdlog_page_serve(void);
/** @brief SD task: whether a page is requested (then take the bus lock and serve). @return 1 while a page is requested. */
int cali_sdlog_page_pending(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_SDLOG_H */
