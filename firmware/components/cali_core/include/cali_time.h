/* cali_time.h — the firmware's clock for the SD log (R_FW_TIME, #154 standalone, Part 2).
 *
 * Every log record carries four stamps; buspi back-stamps a whole boot from any record of that boot
 * that has a ts (up is monotonic within a boot):
 *   boot  cali_time_boot(): a kv counter (key CALI_TIME_BOOT_KEY, NVS on the ESP), +1 per boot;
 *   up    cali_uptime_ms() (cali_platform.h): ms since this boot;
 *   ts    cali_time_ts(): epoch ms UTC from the best source at that moment, or none (JSON null);
 *   tsrc  which source: "ntp" | "unit" | null.
 * Source order (spec decision A): ntp > unit > none.
 *   ntp   SNTP (components/cali_sntp on the ESP, whenever the WiFi station is up), once THIS boot's
 *         first sync completed.
 *   unit  the camper unit's own clock from the last usable 1004 frame (CarTime*, local
 *         Europe/Berlin; year + 1900, month + 1 like calictl.semantics.vehicle), converted to UTC by
 *         the POSIX rule CALI_TIME_UNIT_TZ and advanced by uptime since that frame. A frame with day
 *         0 (the unit's all-zero clock) or in the DST-ambiguous hour (last Sunday of October,
 *         02:00-02:59 local) is skipped: the previous anchor, if any, keeps counting.
 *
 * device.time (cali_time_json): {"source":"sntp"|"unit"|"none","synced":<bool>,"boot":<int>,"up":<ms>}.
 * source = what the next record would use: "sntp" once synced, else "unit" while a unit anchor is
 * held, else "sntp" while SNTP runs but has not synced yet (synced false), else "none".
 * synced comes only from the SNTP callback of THIS boot (RAM flag, never a "clock > 2020" check):
 * a clock that survived a reset or was restored is not synced.
 *
 * Threads: cali_time_synced may run on any task (the SNTP callback); the rest on the owner task.
 * C99, no ESP-IDF headers. Proven by tests/firmware/test_time.py (test/time_cli.c).
 */
#ifndef CALI_TIME_H
#define CALI_TIME_H

#include <stddef.h>
#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_TIME_BOOT_KEY "boot_count"   /* 4-byte LE; its own key, never a bond/WiFi/water one */
#define CALI_TIME_UNIT_TZ "CET-1CEST,M3.5.0,M10.5.0/3"   /* the rule cali_time_unit_utc implements */

enum { CALI_TSRC_NONE, CALI_TSRC_NTP, CALI_TSRC_UNIT };

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
/* Owner task: a 1004 (vehicle) state frame from the unit; a usable CarTime* becomes the unit anchor. */
void cali_time_on_vehicle(const uint8_t *frame, size_t len);
/* Europe/Berlin local calendar time (year e.g. 2026, month 1-12) -> epoch ms UTC: 0, or -1 when it
 * is no valid date (day 0 included) or falls in the ambiguous hour. The March gap hour (02:xx, no
 * such local time) maps as CET, the same instant as 03:xx CEST. */
int cali_time_unit_utc(int year, int month, int day, int hour, int min, int sec, int64_t *out_ms);
/* The best timestamp now: CALI_TSRC_NTP / CALI_TSRC_UNIT and *out = epoch ms, or CALI_TSRC_NONE. */
int cali_time_ts(int64_t *out);
/* The device.time object (a value: call cali_json_key first). */
void cali_time_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_TIME_H */
