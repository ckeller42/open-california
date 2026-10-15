/* cali_wifi_run.h — the WiFi runtime: drives the WiFi SM (cali_wifi_sm.h) over cali_net and the kv
 * store, and exposes what the web page reports (#154).
 *
 * Declared here by Task 6 (web.c codes against it; tests/firmware/test_web_handlers.py fakes it in
 * test/web_cli.c); implemented by Task 7 (wifi_run.c). All calls on the one task that runs the tick.
 *
 * Off until cali_wifi_run_init(): every call is then a no-op and the state stays WIFI_UNPROVISIONED
 * (the host without --http; the ESP until Task 9 wires it) — which is how the console tells "WiFi
 * off" apart. Credentials live in the kv store as CALI_WIFI_KEY_SSID / _PSK (written by the caller:
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
 * <not_found|auth|other>" (WACT_LOG_REASON), "wifi: credentials cleared" / "wifi: credential
 * erase failed" (WACT_CLEAR_CREDS), "wifi: setup hotspot failed to start" (ap_start refused),
 * "wifi: credentials replaced, reconnecting" (set_creds outside setup), "wifi: replacement
 * failed, back to <ssid>" (WACT_RESTORE_PREV), "wifi: storing the restored credentials failed", "wifi:
 * captive DNS unavailable" (port 53 not bindable, e.g. an unprivileged host run).
 *
 * Replacing a joined network (G2): set_creds() while CONNECTING/ONLINE/RETRYING with creds that
 * joined keeps those creds in RAM as the fallback and feeds WEV_CREDS_REPLACE; if the new ones fail
 * (not_found/auth/other/the join timeout) WACT_RESTORE_PREV puts the fallback back (RAM + kv, wifi_ok
 * "1"), the SM retries it as a joined network and cali_wifi_run_reverted() turns 1 — last_fail then
 * stays the replacement's reason across the fallback's GOT_IP, until the next set_creds()/forget().
 * The fallback is dropped on any GOT_IP, forget, or setup-flow CREDS_SET; never logged.
 */
#ifndef CALI_WIFI_RUN_H
#define CALI_WIFI_RUN_H

#include <stdint.h>

#include "cali_net.h"
#include "cali_wifi_sm.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The kv keys of the station credentials — one definition for every writer/reader/eraser (web.c
 * POST /api/wifi, console.c "wifi set", wifi_run.c boot + WACT_CLEAR_CREDS). */
#define CALI_WIFI_KEY_SSID "wifi_ssid"
#define CALI_WIFI_KEY_PSK "wifi_psk"
/* "1" once the stored creds joined (GOT_IP), "0" from set_creds()/WACT_CLEAR_CREDS until then. A boot
 * with creds and "0" joins them as typed-in-setup creds (hotspot up, a failure clears them): a typo
 * saved just before a power cut never boots as "validated" (#273 M1). Missing = stored before this
 * key existed: trusted, as before. */
#define CALI_WIFI_KEY_OK "wifi_ok"

void cali_wifi_run_init(const cali_net_t *net);
void cali_wifi_run_boot(void);                       /* stored creds -> WEV_BOOT_WITH_CREDS, else _NO_CREDS */
void cali_wifi_run_tick(uint64_t now_ms);
/* -> WEV_CREDS_SET in setup, WEV_CREDS_REPLACE for a station whose creds joined (G2, above); the same
 * SSID + PSK again while CONNECTING or ONLINE is a no-op (the setup page's retry of a POST that
 * arrived but whose answer got lost must not restart the join). */
void cali_wifi_run_set_creds(const char *ssid, const char *psk);
void cali_wifi_run_forget(void);                     /* -> WEV_CREDS_FORGET */
const cali_wifi_state_t *cali_wifi_run_state(void);
void cali_wifi_run_scan(void);                       /* starts a scan; its SCAN_DONE replaces the list
                                                        (a failed one, nscan < 0, keeps the last list) */
/* A scan nobody explicitly asked for (web.c: each setup-mode GET /api/wifi): like
 * cali_wifi_run_scan(), but only if no scan started in the last NET_SCAN_MIN_INTERVAL_MS (by the
 * tick's clock) — every scan takes the shared radio off the setup hotspot's channel. The first one
 * (no scan started yet) always runs; the scan AP_START starts counts. */
void cali_wifi_run_scan_auto(void);

/* What the web page reports. */
const char *cali_wifi_run_ssid(void);                /* the station SSID, or NULL when none */
uint32_t cali_wifi_run_ip(void);                     /* station IPv4, host byte order; 0 = none */
int cali_wifi_run_rssi(void);                        /* station RSSI in dBm; 0 = none */
int cali_wifi_run_scan_list(const cali_net_ap_t **out);   /* the last SCAN_DONE's list, one entry per SSID (the strongest): count, *out set */
/* Why the last join failed: "not_found" | "auth" | "other" (the reason of the last WACT_LOG_REASON),
 * or NULL — none yet, or cleared by new credentials (WEV_CREDS_SET) or by joining (GOT_IP). The
 * setup page shows it as one of three texts (ruling R23). */
const char *cali_wifi_run_last_fail(void);
/* 1 = the last replacement failed and the previous network was restored (G2); 0 after the next
 * set_creds() or forget(). GET /api/wifi's "reverted". */
int cali_wifi_run_reverted(void);

/* What "mode" the page and the console report for a WiFi SM state: SETUP while the setup hotspot
 * serves (WIFI_SETUP_AP, WIFI_SETUP_AP_RETRYING); STATION when online, and while a station with
 * creds that joined before / came from flash reconnects (CONNECTING/RETRYING with joined_once 1);
 * OFF otherwise — unprovisioned (WiFi off), and a setup-flow or replacement join in progress. Shared by web.c
 * (/api/state, /api/wifi) and console.c (status, wifi status) so they never disagree. */
enum { CALI_WIFI_MODE_OFF, CALI_WIFI_MODE_SETUP, CALI_WIFI_MODE_STATION };

static inline int cali_wifi_mode(const cali_wifi_state_t *w) {
    switch (w->st) {
    case WIFI_SETUP_AP:
    case WIFI_SETUP_AP_RETRYING: return CALI_WIFI_MODE_SETUP;
    case WIFI_ONLINE: return CALI_WIFI_MODE_STATION;
    case WIFI_CONNECTING:
    case WIFI_RETRYING: return w->joined_once == 1 ? CALI_WIFI_MODE_STATION : CALI_WIFI_MODE_OFF;
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
