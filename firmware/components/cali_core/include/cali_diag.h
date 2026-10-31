/**
 * @file cali_diag.h
 * @brief Diagnostics on the SD log: health, events, timings, warnings and the panic capture.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite records its own health, events, timings and warnings on the SD log
 *    :id: R_FW_DIAG_LOG
 *    :status: implemented
 *    :tags: esp32, sd, log, diagnostics
 *
 *    The firmware shall append to the SD log (``R_FW_SD_LOG``), with the same stamp: a ``diag``
 *    record every 60 s (heap, PSRAM, CPU, per-task stack high-water marks, WiFi/BLE RSSI, link,
 *    uptime); ``evt`` records for boot (with the reset reason), BLE link up/down (reason, duration,
 *    reconnect time, attempts), WiFi up/down and join failures, every control command's result
 *    (function and control name, never the value; latency; source), OTA transitions, auto camper
 *    decisions and SD errors; a ``perf`` record per HTTP response (route, status, bytes, time) and per
 *    BLE operation, sampled per characteristic; and ``ESP_LOGW``/``ESP_LOGE`` lines as ``log``
 *    records, at most 20 a minute plus a dropped count. Records come from any task without blocking
 *    (dropped and counted when the queue is full) and never hold a client's MAC or IP address.
 *
 * .. req:: The satellite keeps the backtrace of a panic and logs it on the next boot
 *    :id: R_FW_PANIC_CAPTURE
 *    :status: implemented
 *    :tags: esp32, diagnostics, sd
 *
 *    On a panic, the firmware shall keep the task name, the panic reason (cut) and up to 16 backtrace
 *    PCs in RTC no-init memory, guarded by a magic and a checksum, without a coredump partition (an
 *    OTA-only change). The next boot shall print it on the console and log it as an ``evt`` ``panic``
 *    record after the boot record (``R_FW_DIAG_LOG``), then clear it; a capture that fails its magic,
 *    depth or checksum is never logged. CI shall keep each build's ``cali_fw.elf`` so the PCs can be
 *    symbolized.
 * @endverbatim
 *
 * The records are extra `ev` types in the JSONL stream of the SD log (cali_sdlog.h): they have the
 * same seq, boot, up, ts and tsrc stamp, and trace readers skip an `ev` they do not know.
 *
 * This module only builds the record body (`"ev":...` without the braces, at most
 * CALI_DIAG_BODY_MAX bytes) and hands it to cali_diag_sink. The app main function sets the sink to
 * cali_sdlog_text() (any task, never blocks; no card or a full queue means not logged). With the
 * sink NULL (host, QEMU, unit tests) nothing is recorded. The field names are the contract for the
 * ingest; `docs/firmware.md` has the table. The records are:
 * - `{"ev":"diag","heap_free":...,"heap_min":...,"psram_free":...,"cpu_pct":...,"stack_min":{"owner":...},"wifi_rssi":...,"ble_rssi":...,"link_up":true,"uptime_s":...}`
 *   every 60 s (see cali_diag_health()).
 * - `{"ev":"evt","kind":"KIND",...}` with a kind of boot, link_up, link_down, wifi_up, wifi_down,
 *   net, cmd, ota, auto_camper, sd, ble_mode, probe, ble_exp, ble_phase, unit_adv, panic, touch
 *   (cali_diag_touch()) or display (for example
 *   `cali_diag_evt("display", "\"state\":\"on\",\"src\":\"tap\"")`).
 * - `{"ev":"screen","lux":...,"prox":...,"motion":...}`, the screen's raw sensors, every 60 s once read.
 * - `{"ev":"perf","route":"/api/state","status":200,"bytes":4711,"ms":100}` for every HTTP
 *   response (cali_diag_http()).
 * - `{"ev":"perf","char":"1602","op":"read","ms":84}` for BLE ops (read, write, hb, disc, sub),
 *   sampled (cali_diag_ble()).
 * - `{"ev":"log","lvl":"W","tag":"wifi","msg":"..."}` for ESP_LOGW and ESP_LOGE, rate-limited, and
 *   `{"ev":"log","dropped":n}` (cali_diag_log()).
 * - `{"ev":"ext","dev":"ALIAS","type":"govee","temp_c":...}`, a configured external sensor's
 *   reading (sensors.c, see cali_sensors.h), at most once a minute per sensor.
 *
 * No client MAC or IP and no user-typed value is ever recorded (for cmd: the function and control
 * name only).
 *
 * C99, no malloc, no ESP-IDF headers, no clock of its own (callers pass `now_ms`). Thread-safe where
 * noted; everything else runs on the owner task. Proven by `tests/firmware/test_diag.py`
 * (`test/diag_cli.c`).
 */
#ifndef CALI_DIAG_H
#define CALI_DIAG_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The longest record body in bytes; equal to CALI_SDLOG_DATA_MAX, the longest body that fits a record. */
#define CALI_DIAG_BODY_MAX 288
/** @brief The interval of the diag record in ms. */
#define CALI_DIAG_PERIOD_MS 60000u
/** @brief The mirrored log lines per minute; then one dropped record. */
#define CALI_DIAG_LOG_PER_MIN 20
/** @brief A characteristic asked at most this often in ms has every op timed. */
#define CALI_DIAG_BLE_EVERY_MS 1000u
/** @brief Otherwise one op in this many is timed. */
#define CALI_DIAG_BLE_SAMPLE 10
/** @brief Heartbeat (hb) ops are timed at most once in this many ms. */
#define CALI_DIAG_HB_EVERY_MS 60000u

/**
 * @brief The destination of a record body.
 * @param body The record fields without the braces.
 * @return 0 queued, -1 not (no card, queue full, too long).
 */
typedef int (*cali_diag_sink_fn)(const char *body);
/** @brief The sink for all records. Any task may call it. NULL records nothing. */
extern cali_diag_sink_fn cali_diag_sink;

#if defined(__GNUC__)
/** @brief Marks a printf-style function so the compiler checks the format (GCC and Clang). */
#define CALI_DIAG_FMT(a, b) __attribute__((format(printf, a, b)))
#else
/** @brief Empty without GCC or Clang. */
#define CALI_DIAG_FMT(a, b)
#endif

/**
 * @brief Send the record `{"ev":"EV",FMT}` to the sink. Any task.
 * @param ev The record type.
 * @param fmt A printf format of further fields (empty for none).
 * @return 0 queued, -1 not.
 */
int cali_diag_rec(const char *ev, const char *fmt, ...) CALI_DIAG_FMT(2, 3);
/**
 * @brief Send the record `{"ev":"evt","kind":"KIND",FMT}` to the sink. Any task.
 * @param kind The event kind.
 * @param fmt A printf format of further fields (empty for none).
 * @return 0 queued, -1 not.
 */
int cali_diag_evt(const char *kind, const char *fmt, ...) CALI_DIAG_FMT(2, 3);
/**
 * @brief Quote a string as JSON.
 * It escapes the double quote and the backslash and turns control and non-ASCII bytes into `?`.
 * @param buf The destination.
 * @param cap The size of buf.
 * @param s The string (NULL gives `null`).
 * @param max The most source bytes to use.
 * @return buf.
 */
const char *cali_diag_q(char *buf, size_t cap, const char *s, size_t max);

/** @brief The number of task stacks in a health record. */
#define CALI_DIAG_STACKS 8
/** @brief The diag record's values; the platform measures them (app main on the ESP). */
typedef struct {
    long heap_free;    /**< Internal heap free in bytes; below 0 gives null. */
    long heap_min;     /**< Lowest internal heap free in bytes; below 0 gives null. */
    long psram_free;   /**< PSRAM free in bytes; below 0 gives null. */
    int cpu_pct;       /**< Both cores busy in percent since the previous record; below 0 gives null. */
    int nstack;        /**< Entries used in stack_name and stack_min. */
    const char *stack_name[CALI_DIAG_STACKS];   /**< The task names. */
    unsigned stack_min[CALI_DIAG_STACKS];       /**< The lowest free stack of each named task in bytes. */
    int wifi_rssi;     /**< dBm; 0 (offline) gives null. */
    int ble_rssi;      /**< dBm; 0 (no link) gives null. */
    int link_up;       /**< Not 0 while the unit link is up. */
    unsigned long uptime_s;   /**< Seconds since boot. */
} cali_diag_health_t;
/**
 * @brief Record the health: `{"ev":"diag",...}`.
 * @param h The values.
 * @return 0 queued, -1 not.
 */
int cali_diag_health(const cali_diag_health_t *h);
/**
 * @brief Record the screen's raw sensors: `{"ev":"screen","lux":...,"prox":...,"motion":...}`.
 * These are the calibration values that `/api/state` shows only with the diagnostics query (R_FW_STATE_DIAG).
 * @param lux The light level; below 0 gives null.
 * @param prox The proximity; below 0 gives null.
 * @param motion The motion; below 0 gives null.
 * @return 0 queued, -1 not.
 */
int cali_diag_screen(int lux, int prox, int motion);
/**
 * @brief Record the boot: `{"ev":"evt","kind":"boot","reset_reason":...,"fw":...,"boot":n,"prev_up_s":n|null}`.
 * @param reset_reason Why the chip reset.
 * @param fw The firmware version.
 * @param boot The boot number.
 * @param prev_up_s The previous boot's last diag uptime in seconds (below 0: unknown, for example after a power-on).
 * @return 0 queued, -1 not.
 */
int cali_diag_boot(const char *reset_reason, const char *fw, unsigned long boot, long prev_up_s);

/**
 * @brief The touch record's values (display.c, R_FW_TOUCH_NO_ABORT).
 * It holds the failed-read count and the context of the last failure, captured in the touch task
 * right after the failed call. Each int below 0 gives null.
 */
typedef struct {
    uint32_t errors;   /**< Failed touch reads since boot. */
    const char *last;  /**< The last failure's esp_err_to_name. */
    int mhz;           /**< CPU clock right after the failed call (DFS: 40 idle, 240 busy). */
    int usb;           /**< The display tick's last battery reading: on USB (0 or 1). */
    int chg;           /**< The same reading: charging (0 or 1). */
    const char *scr;   /**< Screen state then: `on`, `dim` or `off`. */
    const char *bus;   /**< The owner task's last I2C device: `ltr`, `axp`, `bmi`, `bl` or `none`. */
    long bus_ms;       /**< Ms since that transfer finished (below 0: none yet). */
    int pmode;         /**< FT5x06 PWR_MODE (0xA5) re-read after the failure: 0 active, 1 monitor, 3 hibernate, -1 failed too. */
} cali_diag_touch_t;
/**
 * @brief Record the touch state: `{"ev":"evt","kind":"touch","errors":n,"last":"...","mhz":...,"usb":...,"chg":...,"scr":"...","bus":"...","bus_ms":...,"pmode":...}`.
 * Owner task.
 * @param t The values.
 * @return 0 queued, -1 not.
 */
int cali_diag_touch(const cali_diag_touch_t *t);

/*
 * Panic capture. The ESP's esp_panic_handler wrapper (app_main.c) fills a cali_panic_t in RTC_NOINIT
 * memory and seals it. The next boot hands it to cali_diag_panic(), which logs
 * {"ev":"evt","kind":"panic","task":"NAME","reason":"CUT","bt":"4200abcd 4037f00e ..."} (bt is the
 * PCs, innermost first, bare hex: addr2line's own argument format) and clears it. A struct that
 * fails the magic, depth or checksum (power-on garbage) is never logged. Only fixed-size fields are
 * in it, so the checksum covers bytes that are always written.
 */

/** @brief The magic that marks a sealed capture. */
#define CALI_PANIC_MAGIC 0x9a71c0deu
/** @brief The most backtrace PCs in a capture. */
#define CALI_PANIC_DEPTH 16
/** @brief One panic capture, kept in RTC no-init memory across the reset. */
typedef struct {
    uint32_t magic;                   /**< CALI_PANIC_MAGIC once sealed. */
    uint32_t depth;                   /**< Valid entries in pc. */
    uint32_t pc[CALI_PANIC_DEPTH];    /**< The backtrace PCs, innermost first. */
    char task[16];                    /**< The task name, NUL-terminated. */
    char reason[48];                  /**< The panic reason, cut, NUL-terminated. */
    uint32_t sum;                     /**< cali_panic_sum() over everything before it. */
} cali_panic_t;

/**
 * @brief FNV-1a hash over the struct before `sum`.
 * @param p The capture.
 * @return The checksum.
 */
static inline uint32_t cali_panic_sum(const cali_panic_t *p) {
    const unsigned char *b = (const unsigned char *)p;
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < offsetof(cali_panic_t, sum); i++) h = (h ^ b[i]) * 16777619u;
    return h;
}
/**
 * @brief Seal a capture: set the magic and the checksum. Any context (the panic handler).
 * @param p The capture.
 */
static inline void cali_panic_seal(cali_panic_t *p) {
    p->magic = CALI_PANIC_MAGIC;
    p->sum = cali_panic_sum(p);
}
/**
 * @brief Format the record's fields (no braces, no `ev` and no `kind`).
 * @param p The capture.
 * @param buf The destination.
 * @param cap The size of buf.
 * @return buf, or NULL if p is not a valid capture.
 */
const char *cali_panic_fields(const cali_panic_t *p, char *buf, size_t cap);
/**
 * @brief Log a valid capture as the panic record at boot. Owner task.
 * The capture is cleared once the sink took it.
 * @param p The capture.
 * @return 1 logged, 0 nothing to log, -1 not taken (no card yet: the capture is kept, call again).
 */
int cali_diag_panic(cali_panic_t *p);

/**
 * @brief Tell when the diag record is due. Owner task.
 * @param now_ms The uptime in ms.
 * @return 1 once every CALI_DIAG_PERIOD_MS (the first one CALI_DIAG_PERIOD_MS after the first call), else 0.
 */
int cali_diag_due(uint64_t now_ms);
/**
 * @brief Record one HTTP response. Owner task.
 * @param path The route. NULL (an unparsed request) gives `-`; a 404 gives `other`, so scanners cannot grow the route set.
 * @param status The HTTP status.
 * @param bytes The response size.
 * @param ms The time taken in ms.
 */
void cali_diag_http(const char *path, int status, size_t bytes, uint64_t ms);
/**
 * @brief Record one completed BLE op. Owner task.
 * Ops are sampled per characteristic; heartbeats are recorded at most once per CALI_DIAG_HB_EVERY_MS.
 * @param chr The characteristic's short id.
 * @param op `read`, `write` or `hb`.
 * @param ms The time taken in ms.
 * @param now_ms The uptime in ms.
 */
void cali_diag_ble(uint16_t chr, const char *op, uint32_t ms, uint64_t now_ms);
/**
 * @brief Mirror one formatted ESP log line. Any task.
 * Lines of level W and E are recorded, CALI_DIAG_LOG_PER_MIN a minute; the rest are counted and
 * reported as one `{"ev":"log","dropped":n}` when the minute ends. A concurrent caller is counted.
 * @param now_ms The uptime in ms.
 * @param line One line such as `W (123) tag: msg` (colour codes allowed). NULL only ends the minute
 *        if it is due (the diag tick calls it so a storm's count is not held until the next warning).
 */
void cali_diag_log(uint64_t now_ms, const char *line);

#ifdef __cplusplus
}
#endif

#endif /* CALI_DIAG_H */
