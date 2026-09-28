/* cali_wifi_run.h — the WiFi runtime: drives the WiFi SM (cali_wifi_sm.h) over cali_net and the kv
 * store, and exposes what the web page reports (#154).
 *
 * Declared here by Task 6 (web.c codes against it; tests/firmware/test_web_handlers.py fakes it in
 * test/web_cli.c); implemented by Task 7 (wifi_run.c). All calls on the one task that runs the tick.
 */
#ifndef CALI_WIFI_RUN_H
#define CALI_WIFI_RUN_H

#include <stdint.h>

#include "cali_net.h"
#include "cali_wifi_sm.h"

#ifdef __cplusplus
extern "C" {
#endif

void cali_wifi_run_init(const cali_net_t *net);
void cali_wifi_run_boot(void);                       /* stored creds -> WEV_BOOT_WITH_CREDS, else _NO_CREDS */
void cali_wifi_run_tick(uint64_t now_ms);
void cali_wifi_run_set_creds(const char *ssid, const char *psk);   /* -> WEV_CREDS_SET */
void cali_wifi_run_forget(void);                     /* -> WEV_CREDS_FORGET */
const cali_wifi_state_t *cali_wifi_run_state(void);
void cali_wifi_run_scan(void);                       /* starts a scan; its SCAN_DONE replaces the list */

/* What the web page reports. */
const char *cali_wifi_run_ssid(void);                /* the station SSID, or NULL when none */
uint32_t cali_wifi_run_ip(void);                     /* station IPv4, host byte order; 0 = none */
int cali_wifi_run_rssi(void);                        /* station RSSI in dBm; 0 = none */
int cali_wifi_run_scan_list(const cali_net_ap_t **out);   /* the last SCAN_DONE's list: count, *out set */

#ifdef __cplusplus
}
#endif

#endif /* CALI_WIFI_RUN_H */
