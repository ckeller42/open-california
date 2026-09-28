/* cali_wifi_run.h — the WiFi runtime: drives the WiFi SM (cali_wifi_sm.h) over cali_net and the kv
 * store, and exposes what the web page reports (#154).
 *
 * Declared here by Task 6 (web.c codes against it; tests/firmware/test_web_handlers.py fakes it in
 * test/web_cli.c); implemented by Task 7 (wifi_run.c). All calls on the one task that runs the tick.
 *
 * Off until cali_wifi_run_init(): every call is then a no-op and the state stays WIFI_UNPROVISIONED
 * (the host without --http; the ESP until Task 9 wires it) — which is how the console tells "WiFi
 * off" apart. Credentials live in the kv store as "wifi_ssid" / "wifi_psk" (written by the caller:
 * web.c's POST /api/wifi, console "wifi set"); boot() reads them, WACT_CLEAR_CREDS erases exactly
 * those two keys. set_creds() COPIES both strings (the callers zero theirs right after).
 *
 * The one BLE-coex gate (ruling R15): cali_wifi_run_scan() never starts a scan while a BLE pairing
 * flow is active (cali_runner_state()->st not PAIR_IDLE/PAIR_BONDED/PAIR_ERROR) — the request is
 * held and started on the first tick after the flow ends — and drops a request while a scan is in
 * flight (until its SCAN_DONE). AP_START also asks for a scan, so the setup page has a list.
 *
 * Console lines (cali_log): "wifi: setup hotspot up (calictl-esp-setup)" (AP_STARTED), "wifi:
 * setup hotspot closed" (WACT_AP_STOP), "wifi: joining <ssid>" (each WACT_STA_START), "wifi:
 * online <a.b.c.d>" (GOT_IP accepted), "wifi: lost" (LOST while online), "wifi: failed
 * <not_found|auth|other>" (WACT_LOG_REASON), "wifi: credentials cleared" (WACT_CLEAR_CREDS),
 * "wifi: credentials replaced, reconnecting" (set_creds outside setup), "wifi: captive DNS
 * unavailable" (port 53 not bindable, e.g. an unprivileged host run).
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
/* Why the last join failed: "not_found" | "auth" | "other" (the reason of the last WACT_LOG_REASON),
 * or NULL — none yet, or cleared by new credentials (WEV_CREDS_SET) or by joining (GOT_IP). The
 * setup page shows it as one of three texts (ruling R23). */
const char *cali_wifi_run_last_fail(void);

/* What "mode" the page and the console report for a WiFi SM state: SETUP while the setup hotspot
 * serves (WIFI_SETUP_AP, WIFI_SETUP_AP_RETRYING); STATION when online, and while a station with
 * creds that joined before / came from flash reconnects (CONNECTING/RETRYING with joined_once);
 * OFF otherwise — unprovisioned (WiFi off), and a setup-flow join in progress. Shared by web.c
 * (/api/state, /api/wifi) and console.c (status, wifi status) so they never disagree. */
enum { CALI_WIFI_MODE_OFF, CALI_WIFI_MODE_SETUP, CALI_WIFI_MODE_STATION };

static inline int cali_wifi_mode(const cali_wifi_state_t *w) {
    switch (w->st) {
    case WIFI_SETUP_AP:
    case WIFI_SETUP_AP_RETRYING: return CALI_WIFI_MODE_SETUP;
    case WIFI_ONLINE: return CALI_WIFI_MODE_STATION;
    case WIFI_CONNECTING:
    case WIFI_RETRYING: return w->joined_once ? CALI_WIFI_MODE_STATION : CALI_WIFI_MODE_OFF;
    default: return CALI_WIFI_MODE_OFF;
    }
}

static inline const char *cali_wifi_mode_name(int mode) {
    return mode == CALI_WIFI_MODE_SETUP ? "setup" : mode == CALI_WIFI_MODE_STATION ? "station" : "off";
}

#ifdef __cplusplus
}
#endif

#endif /* CALI_WIFI_RUN_H */
