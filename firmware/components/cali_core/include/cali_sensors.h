/**
 * @file cali_sensors.h
 * @brief External BLE sensors (experimental): Govee thermo-hygrometers and Victron SmartSolar and
 *        Battery Sense devices, read from their adverts.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: External BLE sensors (experimental)
 *    :id: R_FW_SENSORS
 *    :status: open
 *
 *    Behind exp_sensors (R_FW_EXPERIMENTAL): a 5 s passive scan window every 60 s (skipped while a
 *    connect is pending or a pairing flow runs) delivers manufacturer-data adverts; configured
 *    addresses decode by type (govee, victron_mppt, victron_battery) into /api/state ext.<alias> and
 *    an ext SD record at most once a minute; unconfigured ones fill a 16-row seen table with
 *    boot-local ids. The list (<= 16, NVS sens_list) survives the flag; keys never leave the ESP.
 * @endverbatim
 *
 * The transport opens the window (`cali_transport_t.scan_window`) and delivers CALI_TEV_ADV through
 * the runner's sink to cali_sensors_on_adv(). A sensor is found by its manufacturer id (the type
 * table in sensors.c). A configured address decodes into a reading (fields per type; NaN means "not
 * available" and becomes JSON null or is left out of the record). An unconfigured one fills the seen
 * table (type, model = the local name without its "_XXXX" suffix, rssi; never logged). Ids are
 * boot-local: (crc32 of the 6 address bytes and 0xffff) OR 1, never 0. The MAC never leaves the ESP.
 *
 * C99, no malloc, no ESP-IDF or NimBLE headers, no clock of its own (callers pass `now_ms`).
 * Everything runs on the owner task, the one that delivers transport events. Proven by
 * `tests/firmware/test_sensors.py` (`test/sensors_cli.c`) and `test_sensors_e2e.py` (host NimBLE).
 */
#ifndef CALI_SENSORS_H
#define CALI_SENSORS_H

#include <stdint.h>

#include "cali_json.h"
#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Error: the alias is not `[a-z0-9_-]{1,16}`, is taken, or was omitted for a type already configured. */
#define CALI_SENS_E_ALIAS (-1)
/** @brief Error: a key type without 32 hex digits, or a key that is not 32 hex digits. */
#define CALI_SENS_E_KEY (-2)
/** @brief Error: the key's check byte does not match the advert (Victron). */
#define CALI_SENS_E_KEY_MISMATCH (-3)
/** @brief Error: no such id (seen less than 10 minutes ago for add, configured for remove and rename). */
#define CALI_SENS_E_UNKNOWN (-4)
/** @brief Error: 16 sensors are configured already. */
#define CALI_SENS_E_FULL (-5)
/** @brief Error: the NVS write failed. */
#define CALI_SENS_E_STORE (-6)

/** @brief One decoded reading. */
typedef struct {
    uint64_t at_ms;   /**< Uptime in ms of the advert. */
    float f[8];       /**< The type's field i; NaN = not available. */
    uint8_t n;        /**< Number of fields the type has. */
} cali_sensor_reading_t;

/** @brief Load `sens_list` from the kv store and empty the seen table and the readings. */
void cali_sensors_init(void);

/**
 * @brief Run the window cadence: one window of 5 s every 60 s (or 15 s after a kick) while enabled and no pairing flow runs.
 *
 * With a pairing flow running the scan state is "paused". A busy transport is retried on the next
 * tick; a kick then stays pending until a window opens.
 *
 * @param now_ms          Uptime in ms.
 * @param t               The transport that opens the window.
 * @param enabled         The exp_sensors flag.
 * @param pairing_active  A pairing flow runs.
 */
void cali_sensors_tick(uint64_t now_ms, const cali_transport_t *t, int enabled, int pairing_active);

/**
 * @brief Handle one advert (CALI_TEV_ADV).
 * @param e       The transport event.
 * @param now_ms  Uptime in ms.
 */
void cali_sensors_on_adv(const cali_tevent_t *e, uint64_t now_ms);

/** @brief Ask for a window: the next tick opens one unless one ran less than 15 s ago. Called by `GET /api/sensors`. */
void cali_sensors_kick(void);

/**
 * @brief Write the `/api/state` `ext` members.
 *
 * Writes `"ALIAS":{"type":"ID",FIELD:NUMBER_OR_NULL,...,"age_s":INT_OR_NULL},...` into the open
 * object. The caller writes the braces.
 *
 * @param j       The JSON writer.
 * @param now_ms  Uptime in ms.
 */
void cali_sensors_state_json(cali_json_t *j, uint64_t now_ms);

/**
 * @brief Write the members of `GET /api/sensors` into the open object.
 *
 * Members: `"configured":[{id,type,alias,last_seen_s,value,has_key}]`, `"seen":[{id,type,model,rssi}]`
 * and `"scan":"ok"|"paused"`. `value` is the fields object while the last reading is less than 10
 * minutes old, else null. Seen rows older than 10 minutes are left out.
 *
 * @param j       The JSON writer.
 * @param now_ms  Uptime in ms.
 */
void cali_sensors_api_json(cali_json_t *j, uint64_t now_ms);

/**
 * @brief Configure a seen sensor.
 * @param id       The boot-local id of a seen row.
 * @param alias    The alias, or NULL for the type's default.
 * @param key_hex  The 32-digit hex key, or NULL for a type without a key.
 * @param now_ms   Uptime in ms; ages the seen row (a row the API hides is CALI_SENS_E_UNKNOWN).
 * @return 0 or a CALI_SENS_E_* error.
 */
int cali_sensors_add(uint16_t id, const char *alias, const char *key_hex, uint64_t now_ms);

/**
 * @brief Remove a configured sensor.
 * @param id  The sensor's id.
 * @return 0 or a CALI_SENS_E_* error.
 */
int cali_sensors_remove(uint16_t id);

/**
 * @brief Rename a configured sensor.
 * @param id     The sensor's id.
 * @param alias  The new alias.
 * @return 0 or a CALI_SENS_E_* error.
 */
int cali_sensors_rename(uint16_t id, const char *alias);

/** @return The scan state: "ok" or "paused". */
const char *cali_sensors_scan_state(void);

/**
 * @brief AES-128 ECB of one block, the keystream source of the Victron decoders.
 * @param key  The 16-byte key.
 * @param in   One input block.
 * @param out  One output block.
 * @return 0 ok.
 */
typedef int (*cali_sensors_aes_fn)(const uint8_t key[16], const uint8_t in[16], uint8_t out[16]);

/**
 * @brief Install the AES function: on the ESP, the PSA aes128_ecb of ble_nimble.c; in tests, a stub.
 *
 * Without one a Victron advert never decodes. The Victron container is: company id, `0x10 0x00`,
 * model u16 LE, readout type, iv u16 LE, key-check byte (= key[0], checked by cali_sensors_add()
 * against the seen row's last advert, giving CALI_SENS_E_KEY_MISMATCH), then the CTR ciphertext
 * (counter = iv, 128-bit LE; one keystream block covers 16 payload bytes, more needs the LE counter
 * increment). See `docs/firmware.md`, "External sensors".
 *
 * @param fn  The AES function.
 */
void cali_sensors_set_aes(cali_sensors_aes_fn fn);

/**
 * @brief The longest ext record body any type can produce (a 16-char alias, every field -9999.99), in bytes.
 *
 * It must stay below CALI_DIAG_BODY_MAX; `test_sensors.py` pins it.
 *
 * @return The length in bytes.
 */
int cali_sensors_max_body_len(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_SENSORS_H */
