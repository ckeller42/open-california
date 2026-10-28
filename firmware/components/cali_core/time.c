/* time.c — boot counter, SNTP sync state and the unit clock for the log stamps (R_FW_TIME). Contract: cali_time.h. */
#include "cali_time.h"

#include <stddef.h>
#include <string.h>

#include "cali_platform.h"
#include "codec.h"

static uint32_t s_boot;
static cali_time_epoch_fn s_epoch;
static int s_synced;   /* written by the SNTP callback's task, read on the owner (__atomic: two cores) */
static int s_unit;              /* a unit-clock anchor is held (owner task only) */
static int64_t s_unit_utc_ms;   /* the anchor: the 1004 frame's clock in UTC ... */
static uint64_t s_unit_up;      /* ... and the uptime it arrived at */

uint32_t cali_time_init(void) {
    uint8_t b[4];
    size_t n = sizeof b;
    uint32_t prev = 0;
    int rc = cali_kv_get(CALI_TIME_BOOT_KEY, b, &n);
    if (rc == CALI_KV_OK && n == sizeof b)
        prev = (uint32_t)b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
    else if (rc != CALI_KV_MISSING)
        cali_log("time: boot counter corrupt, restarting at 1");
    s_boot = prev + 1;
    for (int i = 0; i < 4; i++) b[i] = (uint8_t)(s_boot >> (8 * i));
    if (cali_kv_set(CALI_TIME_BOOT_KEY, b, sizeof b) != 0) cali_log("time: boot counter not saved");
    cali_log("time: boot %lu", (unsigned long)s_boot);
    return s_boot;
}

void cali_time_source(cali_time_epoch_fn epoch_ms) { s_epoch = epoch_ms; }

void cali_time_synced(void) { __atomic_store_n(&s_synced, 1, __ATOMIC_RELEASE); }

uint32_t cali_time_boot(void) { return s_boot; }

static int synced(void) { return s_epoch && __atomic_load_n(&s_synced, __ATOMIC_ACQUIRE); }

/* Days since 1970-01-01 of a proleptic Gregorian date (H. Hinnant's days_from_civil). */
static int64_t days_from_civil(int y, int m, int d) {
    y -= m <= 2;
    int64_t era = (y >= 0 ? y : y - 399) / 400;
    int64_t yoe = y - era * 400;
    int64_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    return era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468;
}

static int month_days(int y, int m) {
    static const int n[12] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    return m == 2 && ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0) ? 29 : n[m - 1];
}

/* Day of month of the last Sunday of y-m (1970-01-01 was a Thursday). */
static int last_sunday(int y, int m) {
    int last = month_days(y, m);
    int dow = (int)((days_from_civil(y, m, last) % 7 + 11) % 7);   /* 0 = Sunday */
    return last - dow;
}

/* CALI_TIME_UNIT_TZ by hand (no global TZ/tzset): CEST from the last Sunday of March 02:00 local
 * to the last Sunday of October 03:00 local, CET otherwise. */
int cali_time_unit_utc(int year, int month, int day, int hour, int min, int sec, int64_t *out_ms) {
    if (month < 1 || month > 12 || day < 1 || day > month_days(year, month) || hour < 0 || hour > 23 ||
        min < 0 || min > 59 || sec < 0 || sec > 59)
        return -1;
    int cest;
    if (month == 3 && day == last_sunday(year, 3)) cest = hour >= 3;          /* 02:xx gap -> CET */
    else if (month == 10 && day == last_sunday(year, 10)) {
        if (hour == 2) return -1;                                             /* 02:xx twice */
        cest = hour < 2;
    } else cest = (month > 3 && month < 10) || (month == 3 && day > last_sunday(year, 3)) ||
                  (month == 10 && day < last_sunday(year, 10));
    int64_t local = days_from_civil(year, month, day) * 86400 + hour * 3600 + min * 60 + sec;
    *out_ms = (local - (cest ? 7200 : 3600)) * 1000;
    return 0;
}

void cali_time_on_vehicle(const uint8_t *frame, size_t len) {
    static codec_kv_t kv[CODEC_KV_MAX];   /* static: keep the 1 KB off the owner task's stack */
    static const char *const NAMES[6] = {"CarTimeYear", "CarTimeMonth", "CarTimeDay",
                                         "CarTimeHour", "CarTimeMinute", "CarTimeSecond"};
    int v[6] = {0}, have = 0;
    int n = codec_decode(codec_func_by_name("vehicle"), frame, len, kv);
    for (int k = 0; k < n; k++)
        for (int f = 0; f < 6; f++)
            if (strcmp(kv[k].name, NAMES[f]) == 0) {
                v[f] = (int)kv[k].value;
                have |= 1 << f;
            }
    int64_t utc;
    if (have != 0x3f || cali_time_unit_utc(v[0] + 1900, v[1] + 1, v[2], v[3], v[4], v[5], &utc) != 0)
        return;   /* no clock in this frame (day 0, ambiguous hour, short frame): keep the anchor */
    s_unit = 1;
    s_unit_utc_ms = utc;
    s_unit_up = cali_uptime_ms();
}

int cali_time_ts(int64_t *out) {
    if (synced()) {
        *out = s_epoch();
        return CALI_TSRC_NTP;
    }
    if (s_unit) {
        *out = s_unit_utc_ms + (int64_t)(cali_uptime_ms() - s_unit_up);
        return CALI_TSRC_UNIT;
    }
    return CALI_TSRC_NONE;
}

void cali_time_json(cali_json_t *j, int diag) {
    cali_json_obj_begin(j);
    cali_json_key(j, "source");
    cali_json_str(j, synced() ? "sntp" : s_unit ? "unit" : s_epoch ? "sntp" : "none");
    cali_json_key(j, "synced");
    cali_json_bool(j, synced());
    cali_json_key(j, "boot");
    cali_json_int(j, (long long)s_boot);
    if (diag) {
        cali_json_key(j, "up");   /* the log's `up` now */
        cali_json_int(j, (long long)cali_uptime_ms());
    }
    cali_json_obj_end(j);
}

int cali_time_berlin_hour(int64_t epoch_ms) {
    int64_t s = epoch_ms / 1000;
    int y = (int)(1970 + s / 31556952);
    while (days_from_civil(y + 1, 1, 1) * 86400 <= s) y++;
    while (days_from_civil(y, 1, 1) * 86400 > s) y--;
    int64_t on = days_from_civil(y, 3, last_sunday(y, 3)) * 86400 + 3600;   /* 01:00 UTC */
    int64_t off = days_from_civil(y, 10, last_sunday(y, 10)) * 86400 + 3600;
    int64_t l = s + (s >= on && s < off ? 7200 : 3600);
    return (int)((l % 86400 + 86400) % 86400 / 3600);
}
