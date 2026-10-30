/**
 * @file cali_status.h
 * @brief One snapshot of what the device knows about itself, for /api/state and the display.
 *
 * Pure C99.
 */
#ifndef CALI_STATUS_H
#define CALI_STATUS_H

#include <stdint.h>
#include <stdio.h>

#include "cali_transport.h"
#include "net_consts.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief One snapshot of what the device knows about itself.
 *
 * Read by /api/state and the status display, so the two can never disagree. Values come from the
 * runner, session, WiFi runner and platform accessors.
 */
typedef struct {
    uint8_t pair_state;          /**< PAIR_* (pairing_consts.h). */
    char address[18];            /**< Bonded identity "AA:BB:...", "" = none. */
    uint8_t link_up;             /**< cali_session_link_up(). */
    uint8_t link_ready;          /**< cali_session_ready(): commands are accepted now. */
    uint8_t link_rest;           /**< cali_session_rest(): power-save rest, no reconnect. */
    uint8_t link_redial;         /**< cali_session_redial(): a planned redial for a viewer. */
    int64_t rest_next_ms;        /**< cali_session_rest_next_ms(): -1 = none. */
    int64_t snap_age_ms;         /**< Now minus the last SNAP, in ms; -1 = never. */
    uint8_t wifi_mode;           /**< CALI_WIFI_MODE_*. */
    uint8_t wifi_state;          /**< WIFI_* of the SM (joining and retrying detail). */
    char ssid[NET_SSID_MAX + 1]; /**< Joined or configured SSID; "" = none. */
    uint32_t ip;                 /**< IPv4 address in host order; 0 = none. */
    int rssi;                    /**< Signal strength in dBm; 0 = none. */
    char last_fail[12];          /**< Last WiFi failure: "", "not_found", "auth" or "other". */
    uint64_t uptime_ms;          /**< Uptime in ms (the now_ms given to cali_status_get). */
    const char *fw;              /**< cali_fw_version(). */
} cali_status_t;

/**
 * @brief Fill a status snapshot from the live accessors.
 * @param s Receives the snapshot.
 * @param t The BLE transport; may be NULL.
 * @param now_ms Uptime clock in ms.
 */
void cali_status_get(cali_status_t *s, const cali_transport_t *t, uint64_t now_ms);

/**
 * @brief Format a host-order IPv4 address as "a.b.c.d".
 *
 * One implementation for status.c and the display.
 *
 * @param ip Address in host order.
 * @param out Receives the text, at most 16 bytes with the terminator.
 */
static inline void cali_status_ip_str(uint32_t ip, char out[16]) {
    snprintf(out, 16, "%u.%u.%u.%u", (unsigned)(ip >> 24), (unsigned)(ip >> 16 & 0xffu),
             (unsigned)(ip >> 8 & 0xffu), (unsigned)(ip & 0xffu));
}

#ifdef __cplusplus
}
#endif

#endif /* CALI_STATUS_H */
