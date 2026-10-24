/* cali_diag.h — diagnostics on the SD log (R_FW_DIAG_LOG): health every minute, events on change,
 * request/BLE timings and the firmware's own warnings, as extra `ev` types in the R_FW_SD_LOG JSONL
 * stream (the same seq/boot/up/ts/tsrc stamp; trace readers skip an `ev` they do not know).
 *
 * This module only builds the record body (`"ev":…` without the braces, at most CALI_DIAG_BODY_MAX
 * bytes) and hands it to cali_diag_sink — app_main sets it to cali_sdlog_text (any task, never
 * blocks; no card or a full queue = not logged). NULL (host, QEMU, unit tests): nothing is recorded.
 * Records (field names are the contract for the ingest; docs/firmware.md has the table):
 *   {"ev":"diag","heap_free":…,"heap_min":…,"psram_free":…,"cpu_pct":…,"stack_min":{"owner":…,…},
 *    "wifi_rssi":…,"ble_rssi":…,"link_up":true,"uptime_s":…}                 (app_main, every 60 s)
 *   {"ev":"evt","kind":"<kind>",…}   boot | link_up | link_down | wifi_up | wifi_down | net | cmd |
 *                                    ota | auto_camper | sd | ble_mode | probe | ble_exp | ble_phase | unit_adv | panic | touch (cali_diag_touch, #346) | display (the display-wake PR:
 *                                    cali_diag_evt("display", "\"state\":\"on\",\"src\":\"tap\""))
 *   {"ev":"perf","route":"/api/state","status":200,"bytes":4711,"ms":100}   every HTTP response
 *   {"ev":"perf","char":"1602","op":"read","ms":84}                         BLE ops (read/write/hb/disc/sub), sampled
 *   {"ev":"log","lvl":"W","tag":"wifi","msg":"…"} / {"ev":"log","dropped":n} ESP_LOGW/E, rate-limited
 *   {"ev":"ext","dev":"<alias>","type":"govee","temp_c":…,…}  a configured external sensor's reading
 *                                    (sensors.c, R_FW_SENSORS), at most once a minute per sensor
 * No client MAC or IP and no user-typed value is ever recorded (cmd: the fn + control name only).
 *
 * C99, no malloc, no ESP-IDF headers, no clock of its own (callers pass now_ms). Thread-safe where
 * noted; everything else runs on the owner task. Proven by tests/firmware/test_diag.py
 * (test/diag_cli.c).
 */
#ifndef CALI_DIAG_H
#define CALI_DIAG_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_DIAG_BODY_MAX 288        /* = CALI_SDLOG_DATA_MAX: the longest body that fits a record */
#define CALI_DIAG_PERIOD_MS 60000u    /* the diag record */
#define CALI_DIAG_LOG_PER_MIN 20      /* mirrored log lines per minute, then one "dropped" record */
#define CALI_DIAG_BLE_EVERY_MS 1000u  /* a char asked at most this often: every op timed */
#define CALI_DIAG_BLE_SAMPLE 10       /* else 1 op in this many */
#define CALI_DIAG_HB_EVERY_MS 60000u  /* heartbeat ("hb") ops: at most one a minute */

/* The body's destination: 0 queued, -1 not (no card, queue full, too long). Any task. */
typedef int (*cali_diag_sink_fn)(const char *body);
extern cali_diag_sink_fn cali_diag_sink;

#if defined(__GNUC__)
#define CALI_DIAG_FMT(a, b) __attribute__((format(printf, a, b)))
#else
#define CALI_DIAG_FMT(a, b)
#endif

/* {"ev":"<ev>",<fmt…>} -> the sink (fmt "" = no more fields). Any task. 0 queued, -1 not. */
int cali_diag_rec(const char *ev, const char *fmt, ...) CALI_DIAG_FMT(2, 3);
/* {"ev":"evt","kind":"<kind>",<fmt…>}. Any task. 0 queued, -1 not. */
int cali_diag_evt(const char *kind, const char *fmt, ...) CALI_DIAG_FMT(2, 3);
/* s as a quoted JSON string of at most max source bytes (NULL -> null; '"' and '\' escaped, control
 * and non-ASCII bytes -> '?') into buf: returns buf. */
const char *cali_diag_q(char *buf, size_t cap, const char *s, size_t max);

/* The diag record's values (the platform measures them; app_main on the ESP). */
#define CALI_DIAG_STACKS 8
typedef struct {
    long heap_free, heap_min, psram_free;   /* bytes (internal heap free / lowest; PSRAM free); < 0 -> null */
    int cpu_pct;                            /* both cores busy % since the previous record; < 0 -> null */
    int nstack;                             /* stack_min: the lowest free stack of each named task, bytes */
    const char *stack_name[CALI_DIAG_STACKS];
    unsigned stack_min[CALI_DIAG_STACKS];
    int wifi_rssi, ble_rssi;                /* dBm; 0 (none: offline, no link) -> null */
    int link_up;
    unsigned long uptime_s;
} cali_diag_health_t;
int cali_diag_health(const cali_diag_health_t *h);   /* {"ev":"diag",…} */
/* {"ev":"evt","kind":"boot","reset_reason":…,"fw":…,"boot":n,"prev_up_s":n|null}: prev_up_s = the
 * previous boot's last diag uptime (< 0: unknown, e.g. after a power-on). */
int cali_diag_boot(const char *reset_reason, const char *fw, unsigned long boot, long prev_up_s);

/* The touch record (display.c, R_FW_TOUCH_NO_ABORT, #346): the failed-read count plus the context
 * of the LAST failure, captured in the touch task right after the failed call. Each < 0 int -> null. */
typedef struct {
    uint32_t errors;   /* failed touch reads since boot */
    const char *last;  /* the last one's esp_err_to_name */
    int mhz;           /* CPU clock right after the failed call (DFS: 40 idle / 240 busy) */
    int usb, chg;      /* the display tick's last battery reading: on USB / charging (0/1) */
    const char *scr;   /* screen state then: "on" | "dim" | "off" */
    const char *bus;   /* the owner task's last I2C device: "ltr" | "axp" | "bmi" | "bl" | "none" */
    long bus_ms;       /* ms since that transfer finished (< 0: none yet) */
    int pmode;         /* FT5x06 PWR_MODE (0xA5) re-read after the failure: 0 active, 1 monitor, 3 hibernate; -1 failed too */
} cali_diag_touch_t;
/* {"ev":"evt","kind":"touch","errors":n,"last":"…","mhz":…,"usb":…,"chg":…,"scr":"…","bus":"…",
 *  "bus_ms":…,"pmode":…}. Owner task. 0 queued, -1 not. */
int cali_diag_touch(const cali_diag_touch_t *t);

/* ---- panic capture (R_FW_PANIC_CAPTURE) ----
 * The ESP's esp_panic_handler wrapper (app_main.c) fills one of these in RTC_NOINIT memory and seals
 * it; the next boot hands it to cali_diag_panic, which logs
 *   {"ev":"evt","kind":"panic","task":"<name>","reason":"<cut>","bt":"4200abcd 4037f00e …"}
 * (bt = PCs, innermost first, bare hex: addr2line's own argument format) and clears it. A struct
 * that fails the magic, depth or checksum (power-on garbage) is never logged. Only fixed-size
 * fields, so the checksum covers bytes that are always written. */
#define CALI_PANIC_MAGIC 0x9a71c0deu
#define CALI_PANIC_DEPTH 16
typedef struct {
    uint32_t magic;
    uint32_t depth;                   /* valid entries in pc[] */
    uint32_t pc[CALI_PANIC_DEPTH];
    char task[16];                    /* NUL-terminated */
    char reason[48];                  /* NUL-terminated, cut */
    uint32_t sum;                     /* cali_panic_sum over everything before it */
} cali_panic_t;

/* FNV-1a over the struct before `sum`. */
static inline uint32_t cali_panic_sum(const cali_panic_t *p) {
    const unsigned char *b = (const unsigned char *)p;
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < offsetof(cali_panic_t, sum); i++) h = (h ^ b[i]) * 16777619u;
    return h;
}
/* Any context (the panic handler): magic + checksum. */
static inline void cali_panic_seal(cali_panic_t *p) {
    p->magic = CALI_PANIC_MAGIC;
    p->sum = cali_panic_sum(p);
}
/* The record's fields (no braces, no "ev"/"kind") into buf, or NULL if p is not a valid capture. */
const char *cali_panic_fields(const cali_panic_t *p, char *buf, size_t cap);
/* Boot (owner task): a valid capture -> the panic record; cleared once the sink took it.
 * 1 logged, 0 nothing to log, -1 not taken (no card yet: kept, call again). */
int cali_diag_panic(cali_panic_t *p);

/* Owner task: 1 once every CALI_DIAG_PERIOD_MS (the first CALI_DIAG_PERIOD_MS after the first call). */
int cali_diag_due(uint64_t now_ms);
/* Owner task: one HTTP response. path NULL (an unparsed request) -> "-"; a 404 -> "other" (scanners
 * must not grow the route set). */
void cali_diag_http(const char *path, int status, size_t bytes, uint64_t ms);
/* Owner task: one completed BLE op on chr ("read" | "write" | "hb"), sampled per char; heartbeats
 * at most once per CALI_DIAG_HB_EVERY_MS. */
void cali_diag_ble(uint16_t chr, const char *op, uint32_t ms, uint64_t now_ms);
/* Any task: one formatted ESP log line ("W (123) tag: msg", colour codes allowed). W and E are
 * recorded, CALI_DIAG_LOG_PER_MIN a minute, the rest counted and reported as one
 * {"ev":"log","dropped":n} when the minute ends. line NULL: only end the minute if due (the diag
 * tick, so a storm's count is not held until the next warning). A concurrent caller is counted. */
void cali_diag_log(uint64_t now_ms, const char *line);

#ifdef __cplusplus
}
#endif

#endif /* CALI_DIAG_H */
