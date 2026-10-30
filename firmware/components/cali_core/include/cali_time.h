/**
 * @file cali_time.h
 * @brief The firmware's clock for the SD log: boot counter, SNTP time and the unit's own clock.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite counts its boots and keeps SNTP or unit-clock time for the log stamps
 *    :id: R_FW_TIME
 *    :status: implemented
 *    :tags: esp32, time
 *
 *    The firmware shall count every boot in NVS (``boot``, +1 per boot, under its own key) and, whenever
 *    the WiFi station has a lease, sync its clock over SNTP (``pool.ntp.org`` plus the DHCP-offered
 *    server), so a log record can carry ``boot``, ``up`` (uptime ms), ``ts`` (epoch ms UTC) and
 *    ``tsrc``. Source order: ``ntp`` once this boot's first sync completed; else ``unit``, the camper
 *    unit's clock from the last usable ``1004`` frame (local Europe/Berlin, converted with
 *    ``CET-1CEST,M3.5.0,M10.5.0/3``, advanced by uptime; a day-0 frame or one in the DST-ambiguous
 *    hour is skipped); else none. ``/api/state`` reports ``device.time``
 *    ``{source: sntp|unit|none, synced, boot, up}`` (``synced`` only from this boot's SNTP sync, never a
 *    plausible-clock check) and the shared UI's Device status shows it.
 * @endverbatim
 *
 * Every log record carries four stamps. The log reader (buspi) back-stamps a whole boot from any
 * record of that boot that has a `ts`, because `up` is monotonic within a boot:
 * - `boot`: cali_time_boot(), a kv counter (key CALI_TIME_BOOT_KEY, NVS on the ESP), +1 per boot.
 * - `up`: cali_uptime_ms() (cali_platform.h), ms since this boot.
 * - `ts`: cali_time_ts(), epoch ms UTC from the best source at that moment, or none (JSON null).
 * - `tsrc`: the source, `ntp`, `unit` or null.
 *
 * The source order is ntp, then unit, then none:
 * - ntp is SNTP (components/cali_sntp on the ESP, whenever the WiFi station is up), once the first
 *   sync of THIS boot completed.
 * - unit is the camper unit's own clock from the last usable 1004 frame (the CarTime fields, local
 *   Europe/Berlin; year + 1900 and month + 1 like `calictl.semantics.vehicle`). It is converted to
 *   UTC by the POSIX rule CALI_TIME_UNIT_TZ and advanced by the uptime since that frame. A frame
 *   with day 0 (the unit's all-zero clock) or in the ambiguous hour of the DST change (last Sunday
 *   of October, 02:00 to 02:59 local) is skipped; the previous anchor, if any, keeps counting.
 *
 * The `device.time` object (cali_time_json()) reads `{"source":"sntp"|"unit"|"none",
 * "synced":bool,"boot":int}`, and with diag (`/api/state?diag=1`) also `"up":ms`. The source is
 * what the next record would use: `sntp` once synced, else `unit` while a unit anchor is held, else
 * `sntp` while SNTP runs but has not synced yet (synced false), else `none`. The synced flag comes
 * only from the SNTP callback of THIS boot (a RAM flag, never a "clock after 2020" check): a clock
 * that survived a reset or was restored is not synced.
 *
 * Threads: cali_time_synced() may run on any task (the SNTP callback); the rest run on the owner
 * task. C99, no ESP-IDF headers. Proven by `tests/firmware/test_time.py` (`test/time_cli.c`).
 */
#ifndef CALI_TIME_H
#define CALI_TIME_H

#include <stddef.h>
#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The kv key of the boot counter: 4 bytes little-endian, its own key, never a bond, WiFi or water one. */
#define CALI_TIME_BOOT_KEY "boot_count"
/** @brief The POSIX rule that cali_time_unit_utc() implements (Europe/Berlin). */
#define CALI_TIME_UNIT_TZ "CET-1CEST,M3.5.0,M10.5.0/3"

/** @brief The timestamp sources; the `tsrc` of a record. */
enum cali_tsrc_e {
    CALI_TSRC_NONE,   /**< No usable time. */
    CALI_TSRC_NTP,    /**< SNTP. */
    CALI_TSRC_UNIT    /**< The camper unit's clock. */
};

/** @brief Read the platform's wall clock (SNTP sets it). @return Epoch ms. */
typedef int64_t (*cali_time_epoch_fn)(void);

/**
 * @brief Count this boot. Owner task, once at boot after cali_platform_init.
 * It stores the counter + 1. A missing or corrupt counter starts at 1 (logged when corrupt); a
 * failed write still counts this boot.
 * @return This boot's number.
 */
uint32_t cali_time_init(void);
/**
 * @brief Say that SNTP runs from now on. Owner task.
 * @param epoch_ms Reads the clock SNTP sets (the source `sntp`).
 */
void cali_time_source(cali_time_epoch_fn epoch_ms);
/** @brief Say that the clock holds real time from now on. Any task; later syncs just keep it right. */
void cali_time_synced(void);
/** @brief This boot's number. @return The value cali_time_init() returned. */
uint32_t cali_time_boot(void);
/**
 * @brief Feed a 1004 (vehicle) state frame from the unit. Owner task.
 * A usable CarTime value becomes the unit anchor.
 * @param frame The frame.
 * @param len Bytes in frame.
 */
void cali_time_on_vehicle(const uint8_t *frame, size_t len);
/**
 * @brief Convert a Europe/Berlin local calendar time to epoch ms UTC.
 * The March gap hour (02:xx, no such local time) maps as CET, the same instant as 03:xx CEST.
 * @param year The year, for example 2026.
 * @param month 1 to 12.
 * @param day The day of the month (0 is no valid date).
 * @param hour 0 to 23.
 * @param min 0 to 59.
 * @param sec 0 to 59.
 * @param out_ms Set to the epoch ms UTC on success.
 * @return 0 ok, -1 no valid date (day 0 included) or in the ambiguous hour.
 */
int cali_time_unit_utc(int year, int month, int day, int hour, int min, int sec, int64_t *out_ms);
/**
 * @brief The Europe/Berlin local hour of an instant (the CALI_TIME_UNIT_TZ rule).
 * @param epoch_ms Epoch ms UTC.
 * @return 0 to 23.
 */
int cali_time_berlin_hour(int64_t epoch_ms);
/**
 * @brief The best timestamp now.
 * @param out Set to the epoch ms when the result is not CALI_TSRC_NONE.
 * @return CALI_TSRC_NTP, CALI_TSRC_UNIT or CALI_TSRC_NONE.
 */
int cali_time_ts(int64_t *out);
/**
 * @brief Write the `device.time` object. It is a value: call cali_json_key() first.
 * @param j The JSON writer.
 * @param diag Not 0 to add `up` (the diagnostics query).
 */
void cali_time_json(cali_json_t *j, int diag);

#ifdef __cplusplus
}
#endif

#endif /* CALI_TIME_H */
