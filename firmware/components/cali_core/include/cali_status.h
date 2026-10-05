/* cali_status.h — one snapshot of what the device knows about itself (#154): read by /api/state and
 * the status display, so the two can never disagree. Pure C99; values come from the runner, session,
 * WiFi runner and platform accessors. */
#ifndef CALI_STATUS_H
#define CALI_STATUS_H

#include <stdint.h>
#include <stdio.h>

#include "cali_transport.h"
#include "net_consts.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t pair_state;          /* PAIR_* (pairing_consts.h) */
    char address[18];            /* bonded identity "AA:BB:…", "" = none */
    uint8_t link_up;             /* cali_session_link_up() */
    int64_t snap_age_ms;         /* now - last SNAP; -1 = never */
    uint8_t wifi_mode;           /* CALI_WIFI_MODE_* */
    uint8_t wifi_state;          /* WIFI_* of the SM (joining/retrying detail) */
    char ssid[NET_SSID_MAX + 1]; /* "" = none */
    uint32_t ip;                 /* host order; 0 = none */
    int rssi;                    /* dBm; 0 = none */
    char last_fail[12];          /* "", "not_found", "auth", "other" */
    uint64_t uptime_ms;
    const char *fw;              /* cali_fw_version() */
} cali_status_t;

/* Fills *s from the live accessors; t is the BLE transport (may be NULL), now_ms the uptime. */
void cali_status_get(cali_status_t *s, const cali_transport_t *t, uint64_t now_ms);
/* "a.b.c.d" from a host-order address (one implementation for status.c and the display). */
static inline void cali_status_ip_str(uint32_t ip, char out[16]) {
    snprintf(out, 16, "%u.%u.%u.%u", (unsigned)(ip >> 24), (unsigned)(ip >> 16 & 0xffu),
             (unsigned)(ip >> 8 & 0xffu), (unsigned)(ip & 0xffu));
}

#ifdef __cplusplus
}
#endif

#endif /* CALI_STATUS_H */
