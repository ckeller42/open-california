/* time.c — boot counter + SNTP sync state for the log stamps (R_FW_TIME). Contract: cali_time.h. */
#include "cali_time.h"

#include <stddef.h>

#include "cali_platform.h"

static uint32_t s_boot;
static cali_time_epoch_fn s_epoch;
static int s_synced;   /* written by the SNTP callback's task, read on the owner (__atomic: two cores) */

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

int cali_time_ts_ms(int64_t *out) {
    if (!s_epoch || !__atomic_load_n(&s_synced, __ATOMIC_ACQUIRE)) return -1;
    *out = s_epoch();
    return 0;
}

void cali_time_json(cali_json_t *j) {
    cali_json_obj_begin(j);
    cali_json_key(j, "source");
    cali_json_str(j, s_epoch ? "sntp" : "none");
    cali_json_key(j, "synced");
    cali_json_bool(j, s_epoch && __atomic_load_n(&s_synced, __ATOMIC_ACQUIRE));
    cali_json_key(j, "boot");
    cali_json_int(j, (long long)s_boot);
    cali_json_key(j, "up");   /* the log's `up` now: buspi anchors this boot from its own clock */
    cali_json_int(j, (long long)cali_uptime_ms());
    cali_json_obj_end(j);
}
