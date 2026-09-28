/* platform_host.c — cali_platform.h for the host build (Linux NimBLE host + unit tests on macOS).
 *
 * kv-store: one file "<store_dir>/<key>.kv" per key, content = 4-byte little-endian length, the
 * value bytes, 4-byte little-endian CRC32 over length + bytes. Writes go to "<key>.kv.tmp" first
 * and are renamed over the old file, so a crash mid-write leaves the old record (or none), never a
 * torn one; anything else that damages a file is caught by the length/CRC check on read (-2).
 */
#ifndef _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif

#include "cali_platform.h"

#include <dirent.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define KV_EXT ".kv"
#define KV_TMP_EXT ".kv.tmp"
#define KV_FILE_MAX (64u * 1024u) /* no record is anywhere near this; bigger = not ours = corrupt */

static char s_dir[4096] = ".";
static uint64_t s_t0_ms;
static int s_clock_started;

static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

const char *cali_fw_version(void) { return "host"; }

uint64_t cali_uptime_ms(void) {
    if (!s_clock_started) {
        s_t0_ms = now_ms();
        s_clock_started = 1;
    }
    return now_ms() - s_t0_ms;
}

void cali_log(const char *fmt, ...) {
    va_list ap;
    fputs("LOG ", stdout);
    va_start(ap, fmt);
    vfprintf(stdout, fmt, ap);
    va_end(ap);
    fputc('\n', stdout);
    fflush(stdout);
}

int cali_platform_init(const char *store_dir) {
    struct stat st;
    if (store_dir == NULL) store_dir = ".";
    if (strlen(store_dir) >= sizeof s_dir - (CALI_KV_KEY_MAX + sizeof KV_TMP_EXT + 1)) return -1;
    strcpy(s_dir, store_dir);
    if (mkdir(s_dir, 0700) != 0 && errno != EEXIST) return -1;
    if (stat(s_dir, &st) != 0 || !S_ISDIR(st.st_mode)) return -1;
    s_t0_ms = now_ms();
    s_clock_started = 1;
    return 0;
}

/* Keys become file names: 1..15 chars of [A-Za-z0-9_-] (also a valid NVS key). */
static int key_ok(const char *key) {
    size_t n = key ? strlen(key) : 0;
    if (n == 0 || n > CALI_KV_KEY_MAX) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = key[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '_' || c == '-'))
            return 0;
    }
    return 1;
}

static void key_path(char *out, size_t cap, const char *key, const char *ext) {
    snprintf(out, cap, "%s/%s%s", s_dir, key, ext);
}

static void put_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t get_le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int cali_kv_get(const char *key, void *buf, size_t *len) {
    char path[sizeof s_dir + 32];
    struct stat st;
    uint8_t *raw;
    uint32_t n;
    int rc = CALI_KV_CORRUPT;
    FILE *f;

    if (!key_ok(key) || len == NULL) return CALI_KV_MISSING;
    key_path(path, sizeof path, key, KV_EXT);
    f = fopen(path, "rb");
    if (f == NULL) return errno == ENOENT ? CALI_KV_MISSING : CALI_KV_CORRUPT;
    if (fstat(fileno(f), &st) != 0 || st.st_size < 8 || (uint64_t)st.st_size > KV_FILE_MAX) {
        fclose(f);
        return CALI_KV_CORRUPT;
    }
    raw = malloc((size_t)st.st_size);
    if (raw != NULL && fread(raw, 1, (size_t)st.st_size, f) == (size_t)st.st_size) {
        n = get_le32(raw);
        if ((uint64_t)n + 8u == (uint64_t)st.st_size &&
            cali_crc32(0, raw, 4u + n) == get_le32(raw + 4 + n) && n <= *len) {
            if (n) memcpy(buf, raw + 4, n);
            *len = n;
            rc = CALI_KV_OK;
        }
    }
    free(raw);
    fclose(f);
    return rc;
}

int cali_kv_set(const char *key, const void *buf, size_t len) {
    char tmp[sizeof s_dir + 32], path[sizeof s_dir + 32];
    uint8_t hdr[4], crc_le[4];
    uint32_t crc;
    int ok;
    FILE *f;

    if (!key_ok(key) || len > KV_FILE_MAX - 8 || (len && buf == NULL)) return -1;
    key_path(tmp, sizeof tmp, key, KV_TMP_EXT);
    key_path(path, sizeof path, key, KV_EXT);
    put_le32(hdr, (uint32_t)len);
    crc = cali_crc32(0, hdr, sizeof hdr);
    crc = cali_crc32(crc, buf, len);
    put_le32(crc_le, crc);

    f = fopen(tmp, "wb");
    if (f == NULL) return -1;
    ok = fwrite(hdr, 1, sizeof hdr, f) == sizeof hdr && (len == 0 || fwrite(buf, 1, len, f) == len) &&
         fwrite(crc_le, 1, sizeof crc_le, f) == sizeof crc_le && fflush(f) == 0 &&
         fsync(fileno(f)) == 0;
    if (fclose(f) != 0) ok = 0;
    if (!ok || rename(tmp, path) != 0) {
        remove(tmp);
        return -1;
    }
    return 0;
}

static int ends_with(const char *s, const char *suffix) {
    size_t n = strlen(s), m = strlen(suffix);
    return n >= m && strcmp(s + n - m, suffix) == 0;
}

int cali_kv_erase_all(void) {
    char path[sizeof s_dir + 300];
    struct dirent *e;
    int rc = 0;
    DIR *d = opendir(s_dir);

    if (d == NULL) return -1;
    while ((e = readdir(d)) != NULL) {
        if (!ends_with(e->d_name, KV_EXT) && !ends_with(e->d_name, KV_TMP_EXT)) continue;
        snprintf(path, sizeof path, "%s/%s", s_dir, e->d_name);
        if (remove(path) != 0) rc = -1;
    }
    closedir(d);
    return rc;
}
