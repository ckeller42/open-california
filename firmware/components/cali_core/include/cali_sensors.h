/* cali_sensors.h — external BLE sensors (experimental): Govee thermo-hygrometers, Victron SmartSolar/Battery Sense.
 *
 * .. req:: External BLE sensors (experimental)
 *    :id: R_FW_SENSORS
 *    :status: open
 *
 *    Behind exp_sensors (R_FW_EXPERIMENTAL): a 5 s passive scan window every 60 s (skipped while a
 *    connect is pending or a pairing flow runs) delivers manufacturer-data adverts; configured
 *    addresses decode by type (govee, victron_mppt, victron_battery) into /api/state ext.<alias> and
 *    an ext SD record at most once a minute; unconfigured ones fill a 16-row seen table with
 *    boot-local ids. The list (<= 16, NVS sens_list) survives the flag; keys never leave the ESP.
 *
 * The transport opens the window (cali_transport_t.scan_window) and delivers CALI_TEV_ADV through
 * the runner's sink to cali_sensors_on_adv. A sensor is found by its manufacturer id (the type
 * table in sensors.c); a configured address decodes into a reading (fields per type, NaN = "not
 * available" -> JSON null / left out of the record), an unconfigured one fills the seen table
 * (type, model = the local name without its "_XXXX" suffix, rssi; never logged). Ids are
 * boot-local: (crc32 of the 6 address bytes & 0xffff) | 1, never 0; the MAC never leaves the ESP.
 *
 * C99, no malloc, no ESP-IDF/NimBLE headers, no clock of its own (callers pass now_ms). Everything
 * runs on the owner task (the one that delivers transport events). Proven by
 * tests/firmware/test_sensors.py (test/sensors_cli.c) and test_sensors_e2e.py (host NimBLE).
 */
#ifndef CALI_SENSORS_H
#define CALI_SENSORS_H

#include <stdint.h>

#include "cali_json.h"
#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_SENS_E_ALIAS (-1)          /* not [a-z0-9_-]{1,16}, taken, or omitted for a type already configured */
#define CALI_SENS_E_KEY (-2)            /* a key type without 32 hex digits, or a key that is not 32 hex digits */
#define CALI_SENS_E_KEY_MISMATCH (-3)   /* the key's check byte does not match the advert (Victron) */
#define CALI_SENS_E_UNKNOWN (-4)        /* no such id (seen < 10 min ago for add; configured for remove/rename) */
#define CALI_SENS_E_FULL (-5)           /* 16 configured */
#define CALI_SENS_E_STORE (-6)          /* the NVS write failed */

/* One decode: f[i] = the type's field i (NaN = not available). */
typedef struct {
    uint64_t at_ms;
    float f[8];
    uint8_t n;
} cali_sensor_reading_t;

void cali_sensors_init(void);   /* loads sens_list from kv; empties the seen table and readings */
/* The window cadence: one window of 5 s every 60 s (or 15 s after a kick) while enabled and no
 * pairing flow runs (pairing_active: "paused"); a busy transport is retried on the next tick (a kick
 * then stays pending until a window opens). */
void cali_sensors_tick(uint64_t now_ms, const cali_transport_t *t, int enabled, int pairing_active);
void cali_sensors_on_adv(const cali_tevent_t *e, uint64_t now_ms);   /* CALI_TEV_ADV */
void cali_sensors_kick(void);   /* GET /api/sensors: the next tick opens a window unless one ran < 15 s ago */
/* /api/state "ext": writes {"<alias>":{<field>:<num|null>,…,"age_s":<int|null>},…}'s MEMBERS into the
 * open object (the caller writes the braces). */
void cali_sensors_state_json(cali_json_t *j, uint64_t now_ms);
/* GET /api/sensors: writes the members "configured":[{id,type,alias,last_seen_s,value,has_key}],
 * "seen":[{id,type,model,rssi}], "scan":"ok"|"paused" into the open object. value = the fields
 * object while the last reading is < 10 min old, else null; seen rows older than 10 min are left out. */
void cali_sensors_api_json(cali_json_t *j, uint64_t now_ms);
/* alias NULL -> the type's default alias; key_hex NULL for a type without a key; now_ms ages the seen
 * row (one the API hides is CALI_SENS_E_UNKNOWN). 0 | CALI_SENS_E_*. */
int cali_sensors_add(uint16_t id, const char *alias, const char *key_hex, uint64_t now_ms);
int cali_sensors_remove(uint16_t id);
int cali_sensors_rename(uint16_t id, const char *alias);
const char *cali_sensors_scan_state(void);   /* "ok" | "paused" */
/* AES-128 ECB of one block (the Victron decoders' CTR keystream): ESP = ble_nimble.c's PSA
 * aes128_ecb, tests = a stub. Without one a Victron advert never decodes. 0 ok. Victron container:
 * company id, 0x10 0x00, model u16 LE, readout type, iv u16 LE, key-check byte (= key[0], checked by
 * add against the seen row's last advert -> CALI_SENS_E_KEY_MISMATCH), CTR ciphertext (counter = iv,
 * 128-bit LE; one keystream block, <= 15 payload bytes). docs/firmware.md "External sensors". */
typedef int (*cali_sensors_aes_fn)(const uint8_t key[16], const uint8_t in[16], uint8_t out[16]);
void cali_sensors_set_aes(cali_sensors_aes_fn fn);
/* The longest ext record body any type can produce (a 16-char alias, every field -9999.99), in
 * bytes: must stay below CALI_DIAG_BODY_MAX (test_sensors.py pins it). */
int cali_sensors_max_body_len(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_SENSORS_H */
