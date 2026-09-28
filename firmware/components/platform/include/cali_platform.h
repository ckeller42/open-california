/* cali_platform.h — the firmware's platform layer: clock, persistent key-value store, log (#154).
 *
 * Two implementations behind one header: host/platform_host.c (the Linux/macOS host build and the
 * unit tests; one file per key under a store directory) and platform_esp.c (ESP-IDF; NVS). Both
 * wrap every value the same way — a 4-byte little-endian length, the bytes, a 4-byte little-endian
 * CRC32 (reflected polynomial 0xEDB88320) over length + bytes — so a torn or bit-rotted record
 * reads back as corrupt (-2) instead of as a wrong value.
 */
#ifndef CALI_PLATFORM_H
#define CALI_PLATFORM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_KV_OK        0
#define CALI_KV_MISSING (-1)
#define CALI_KV_CORRUPT (-2) /* short file, CRC mismatch, or the value does not fit the buffer */

/* Longest key accepted (ESP NVS limits keys to 15 characters; the host enforces the same). */
#define CALI_KV_KEY_MAX 15

/* Milliseconds since cali_platform_init() (monotonic). */
uint64_t cali_uptime_ms(void);

/* Read `key` into buf. On entry *len is the buffer size; on success (0) it is the value's length.
 * Returns 0 ok, -1 missing, -2 corrupt or larger than *len (buf contents unspecified then). */
int cali_kv_get(const char *key, void *buf, size_t *len);

/* Store `len` bytes (len may be 0) under `key`, replacing any old value atomically.
 * Returns 0 ok, -1 on failure (bad key, I/O error). */
int cali_kv_set(const char *key, const void *buf, size_t len);

/* Remove every key. Returns 0 ok, -1 on failure. */
int cali_kv_erase_all(void);

/* Write "LOG <text>\n" to the console (printf-style), flushed. */
void cali_log(const char *fmt, ...)
#if defined(__GNUC__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;

/* host: store_dir is the directory for the kv files (created if missing; NULL = "."); esp: ignored
 * (NVS). Starts the uptime clock. Returns 0 ok, -1 if the store directory is unusable. */
int cali_platform_init(const char *store_dir);

/* CRC32 (reflected 0xEDB88320, init/xorout 0xFFFFFFFF — zlib's crc32) of `len` bytes, continuing
 * from `crc` (pass 0 to start). Bitwise, no table (records are tiny; flash matters more than
 * cycles). Inline here so both platform implementations wrap records identically. */
static inline uint32_t cali_crc32(uint32_t crc, const void *buf, size_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    crc = ~crc;
    while (len--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

#ifdef __cplusplus
}
#endif

#endif /* CALI_PLATFORM_H */
