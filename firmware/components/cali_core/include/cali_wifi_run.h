/**
 * @file cali_wifi_run.h
 * @brief The WiFi runtime: drives the WiFi SM (cali_wifi_sm.h) over cali_net and the kv store, and
 *        exposes what the web page reports.
 *
 * All calls run on the one task that runs the tick. The runtime is off until
 * cali_wifi_run_init(): every call is then a no-op and the state stays WIFI_UNPROVISIONED (the host
 * build without HTTP), which is how the console tells "WiFi off" apart. The credentials live in
 * the kv store as CALI_WIFI_KEY_SSID and CALI_WIFI_KEY_PSK (written by the caller: the web
 * handler's `POST /api/wifi` and the console's `wifi set`); cali_wifi_run_boot() reads them, and
 * WACT_CLEAR_CREDS erases exactly those two keys. cali_wifi_run_set_creds() copies both strings (the
 * callers zero theirs right after).
 *
 * The one BLE coexistence gate: cali_wifi_run_scan() never starts a scan while a BLE pairing flow
 * is active (`cali_runner_state()->st` not PAIR_IDLE, PAIR_BONDED or PAIR_ERROR). The request is
 * held and started on the first tick after the flow ends. A request while a scan is in flight is
 * dropped (until its SCAN_DONE). AP_START also asks for a scan, so the setup page has a list.
 *
 * Console lines (cali_log): "wifi: setup hotspot up (calictl-esp-setup)" (AP_STARTED), "wifi:
 * setup hotspot closed" (WACT_AP_STOP), "wifi: joining <ssid>" (each WACT_STA_START), "wifi:
 * online <a.b.c.d>" (GOT_IP accepted), "wifi: lost" (LOST while online), "wifi: failed
 * <not_found|auth|other>" (WACT_LOG_REASON), "wifi: credentials cleared" or "wifi: credential
 * erase failed" (WACT_CLEAR_CREDS), "wifi: setup hotspot failed to start" (ap_start refused),
 * "wifi: credentials replaced, reconnecting" (set_creds outside setup), "wifi: replacement
 * failed, back to <ssid>" (WACT_RESTORE_PREV), "wifi: storing the restored credentials failed",
 * and "wifi: captive DNS unavailable" (port 53 not bindable, e.g. an unprivileged host run).
 *
 * Replacing a joined network: cali_wifi_run_set_creds() while CONNECTING, ONLINE or RETRYING with
 * credentials that joined keeps those in RAM as the fallback and feeds WEV_CREDS_REPLACE. If the
 * new ones fail (not_found, auth, other or the join timeout), WACT_RESTORE_PREV puts the fallback
 * back (RAM and kv, `wifi_ok` "1"), the SM retries it as a joined network and
 * cali_wifi_run_reverted() turns 1. `last_fail` then stays the replacement's reason across the
 * fallback's GOT_IP, until the next set_creds() or forget(). The fallback is dropped on any GOT_IP,
 * on forget, and on a setup-flow CREDS_SET. It is never logged.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: A failed WiFi replacement returns to the previous, joined network by itself
 *    :id: R_FW_WIFI_ROLLBACK
 *    :status: implemented
 *    :tags: esp32, wifi
 *
 *    New credentials (``POST /api/wifi`` or ``wifi set``) for a station whose current credentials have
 *    joined keep those as the fallback (RAM only, never logged). If the new join fails (network not
 *    found, wrong password, any other failure or the join timeout), the firmware shall restore the
 *    previous credentials in RAM and in the kv store, rejoin them on the retry backoff, close the setup
 *    hotspot ``NET_AP_CLOSE_MS`` after it is back, and report ``"reverted": true`` with ``last_error``
 *    = the replacement's reason on ``GET /api/wifi`` until the next credential change or forget. A
 *    replacement that joins drops the fallback. Without a joined network (first setup) a failed join
 *    clears the typed credentials and stays in setup, as in ``R_FW_WIFI_PROVISION``.
 * @endverbatim
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: WiFi never disturbs the BLE session; scans wait for an active pairing flow
 *    :id: R_FW_WIFI_BLE_COEX
 *
 *    WiFi events (join, loss, retries, hotspot up/down) never call into the BLE session: the link,
 *    the ``1003`` heartbeat cadence and notification-driven ``SNAP`` lines continue unchanged through a
 *    WiFi loss and reconnect. The only coupling is scans: a WiFi scan is deferred while a BLE pairing
 *    flow is **active** (runner state not idle, bonded or error — a failed pairing does not block it)
 *    and started on the first tick after it ends.
 * @endverbatim
 */
#ifndef CALI_WIFI_RUN_H
#define CALI_WIFI_RUN_H

#include <stdint.h>

#include "cali_net.h"
#include "cali_wifi_sm.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The kv keys of the station credentials: one definition for every writer, reader and eraser (the
 * web handler's POST /api/wifi, the console's "wifi set", cali_wifi_run_boot and WACT_CLEAR_CREDS). */

/** kv key of the station SSID. */
#define CALI_WIFI_KEY_SSID "wifi_ssid"
/** kv key of the station passphrase. */
#define CALI_WIFI_KEY_PSK "wifi_psk"
/**
 * kv key of the "validated" flag: "1" once the stored credentials joined (GOT_IP), "0" from
 * cali_wifi_run_set_creds() or WACT_CLEAR_CREDS until then. A boot with credentials and "0" joins
 * them as typed-in-setup credentials (hotspot up, a failure clears them): a typo saved just before
 * a power cut never boots as "validated". A missing key means the credentials were stored before
 * this key existed: they are trusted.
 */
#define CALI_WIFI_KEY_OK "wifi_ok"

/**
 * @brief Switch the runtime on and register it as the network's event sink.
 * @param net The network table; NULL leaves the runtime off.
 */
void cali_wifi_run_init(const cali_net_t *net);

/** @brief Read the stored credentials and feed WEV_BOOT_WITH_CREDS, or WEV_BOOT_NO_CREDS without. */
void cali_wifi_run_boot(void);

/**
 * @brief Advance the runtime: feed WEV_TICK, run due retries and start a held scan.
 * @param now_ms Monotonic milliseconds since boot.
 */
void cali_wifi_run_tick(uint64_t now_ms);

/**
 * @brief Set new station credentials.
 *
 * Feeds WEV_CREDS_SET in setup and WEV_CREDS_REPLACE for a station whose credentials joined. The
 * same SSID and passphrase again while CONNECTING or ONLINE is a no-op: the setup page's retry of a
 * POST that arrived but whose answer got lost must not restart the join.
 *
 * @param ssid The network name (1 to 32 bytes); copied.
 * @param psk  The passphrase (8 to 63 bytes); copied.
 */
void cali_wifi_run_set_creds(const char *ssid, const char *psk);

/** @brief Erase the credentials and reopen the setup hotspot (feeds WEV_CREDS_FORGET). */
void cali_wifi_run_forget(void);

/** @return The WiFi SM's state. */
const cali_wifi_state_t *cali_wifi_run_state(void);

/**
 * @brief Start a scan, subject to the BLE coexistence gate in the file description.
 *
 * Its SCAN_DONE replaces the list; a failed one (nscan < 0) keeps the last list.
 */
void cali_wifi_run_scan(void);

/**
 * @brief Start a scan nobody explicitly asked for (the web handler's setup-mode `GET /api/wifi`).
 *
 * Like cali_wifi_run_scan(), but only if no scan started in the last NET_SCAN_MIN_INTERVAL_MS (by
 * the tick's clock): every scan takes the shared radio off the setup hotspot's channel. The first
 * one (no scan started yet) always runs; the scan AP_START starts counts.
 */
void cali_wifi_run_scan_auto(void);

/** @return The station SSID, or NULL when none. */
const char *cali_wifi_run_ssid(void);

/** @return The station IPv4 address in host byte order; 0 = none. */
uint32_t cali_wifi_run_ip(void);

/** @return The station signal strength in dBm; 0 = none. */
int cali_wifi_run_rssi(void);

/**
 * @brief Get the list from the last SCAN_DONE, one entry per SSID (the strongest).
 * @param out Receives a pointer to the list.
 * @return The number of entries.
 */
int cali_wifi_run_scan_list(const cali_net_ap_t **out);

/**
 * @brief Say why the last join failed.
 *
 * The setup page shows it as one of three texts.
 *
 * @return "not_found", "auth" or "other" (the reason of the last WACT_LOG_REASON), or NULL: none
 *         yet, or cleared by new credentials (WEV_CREDS_SET) or by joining (GOT_IP).
 */
const char *cali_wifi_run_last_fail(void);

/**
 * @brief Tell whether the last replacement failed and the previous network was restored.
 * @return 1 for yes (`GET /api/wifi` reports it as "reverted"); 0 after the next
 *         cali_wifi_run_set_creds() or cali_wifi_run_forget().
 */
int cali_wifi_run_reverted(void);

/**
 * @brief The WiFi "mode" the page and the console report.
 *
 * Shared by the web handler (`/api/state`, `/api/wifi`) and the console (`status`, `wifi status`)
 * so they never disagree. See cali_wifi_mode().
 */
enum cali_wifi_mode_e {
    CALI_WIFI_MODE_OFF,      /**< Unprovisioned (WiFi off), or a setup-flow or replacement join in progress. */
    CALI_WIFI_MODE_SETUP,    /**< The setup hotspot serves (WIFI_SETUP_AP, WIFI_SETUP_AP_RETRYING). */
    CALI_WIFI_MODE_STATION   /**< Online, or a station whose credentials joined before (or came from flash) reconnects. */
};

/**
 * @brief Map a WiFi SM state to the mode the page and the console report.
 * @param w The WiFi SM state.
 * @return A CALI_WIFI_MODE_* value: SETUP in WIFI_SETUP_AP and WIFI_SETUP_AP_RETRYING; STATION in
 *         WIFI_ONLINE and in CONNECTING or RETRYING with `joined_once` 1; OFF otherwise.
 */
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

/**
 * @brief Name a mode for the page and the console.
 * @param mode A CALI_WIFI_MODE_* value.
 * @return "setup", "station" or "off".
 */
static inline const char *cali_wifi_mode_name(int mode) {
    return mode == CALI_WIFI_MODE_SETUP ? "setup" : mode == CALI_WIFI_MODE_STATION ? "station" : "off";
}

#ifdef __cplusplus
}
#endif

#endif /* CALI_WIFI_RUN_H */
