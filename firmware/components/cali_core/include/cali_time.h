/* cali_time.h — the firmware's clock for the SD log (R_FW_TIME, #154 standalone, Part 2).
 *
 * Every log record carries three stamps; buspi back-stamps a whole boot from any record of that boot
 * that has a ts (up is monotonic within a boot):
 *   boot  cali_time_boot(): a kv counter (key CALI_TIME_BOOT_KEY, NVS on the ESP), +1 per boot;
 *   up    cali_uptime_ms() (cali_platform.h): ms since this boot;
 *   ts    cali_time_ts_ms(): epoch ms, or -1 (JSON null) before this boot's first SNTP sync.
 * The time source is the platform's (components/cali_sntp on the ESP: SNTP whenever the WiFi
 * station is up); without one (host, QEMU, no WiFi driver) ts stays -1. No fallback clock: buspi
 * stamps such a boot from the unit RTC in its 1004 frames.
 *
 * device.time (cali_time_json): {"source":"sntp"|"none","synced":<bool>,"boot":<int>}.
 *
 * Threads: cali_time_synced may run on any task (the SNTP callback); the rest on the owner task.
 * C99, no ESP-IDF headers. Proven by tests/firmware/test_time.py (test/time_cli.c).
 */
#ifndef CALI_TIME_H
#define CALI_TIME_H

#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_TIME_BOOT_KEY "boot_count"   /* 4-byte LE; its own key, never a bond/WiFi/water one */

/* Epoch ms of the platform's wall clock (SNTP sets it). */
typedef int64_t (*cali_time_epoch_fn)(void);

/* Owner task, once at boot after cali_platform_init: the stored counter + 1, written back. A missing
 * or corrupt counter starts at 1 (logged when corrupt); a failed write still counts this boot.
 * Returns this boot's number. */
uint32_t cali_time_init(void);
/* Owner task: SNTP runs from now on and epoch_ms reads the clock it sets (source "sntp"). */
void cali_time_source(cali_time_epoch_fn epoch_ms);
/* Any task: the clock holds real time from now on (every later sync just keeps it right). */
void cali_time_synced(void);
uint32_t cali_time_boot(void);
/* 0 and *out = epoch ms once synced; -1 before (no source, or no sync yet on this boot). */
int cali_time_ts_ms(int64_t *out);
/* The device.time object (a value: call cali_json_key first). */
void cali_time_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_TIME_H */
