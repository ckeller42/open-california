/* cali_web.h — the firmware's HTTP endpoints and status/setup page, on the portable HTTP core
 * (cali_http.h) (#154).
 *
 * Routes (cali_web_handle):
 *   GET /             200 text/html: the status/setup page (firmware/web/index_gen.html, rendered
 *                     from index.html + strings.json by tools/gen_c_dict.py; strings_gen.h's
 *                     WEB_INDEX_HTML byte array)
 *   GET /api/state    200 application/json:
 *                       {"t":<uptime_ms>,"fn":{<the SNAP "fn" object, cali_snapshot_fn>},
 *                        "device":{"pairing":{"state":"<PAIR_STATE_NAMES>","address":"…"|null},
 *                                  "link":{"up":<bool>,"last_snap_age_ms":<int>|null},
 *                                  "wifi":{"mode":"setup"|"station"|"off","ssid":"…"|null,
 *                                          "ip":"a.b.c.d"|null,"rssi":<int>|null},
 *                                  "uptime_ms":<int>,"fw":"<cali_fw_version()>"}}
 *                     built whole in one handler call (one consistent snapshot) into a
 *                     NET_JSON_MAX buffer; overflow -> 500 + LOG "http: overflow".
 *   GET /api/wifi     200 {"mode","ssid","ip","rssi","scan":[{"ssid","rssi","secure":<bool>}]} (the
 *                     last SCAN_DONE's list); in setup mode it also starts a fresh scan — unless a
 *                     BLE pairing flow is active (runner state not idle/bonded): no scan then.
 *   POST /api/wifi    body {"ssid":"…","psk":"…"} — a fixed-shape parser: one object, exactly the
 *                     two keys (either order, once each), string values with only \" and \\
 *                     escapes, no control bytes/NUL, whitespace between tokens allowed. SSID 1 ..
 *                     NET_SSID_MAX bytes, PSK NET_PSK_MIN .. NET_PSK_MAX bytes. Valid ->
 *                     cali_kv_set("wifi_ssid"), cali_kv_set("wifi_psk"), cali_wifi_run_set_creds
 *                     -> 200 {"ok":true}; else 400 {"ok":false,"error":"json"|"ssid"|"psk"} (json
 *                     checked first); a kv write failure -> 500 {"ok":false,"error":"store"}.
 *   DELETE /api/wifi  cali_wifi_run_forget() -> 200 {"ok":true}
 *   other method on /api/wifi or /api/state -> 405 {"ok":false,"error":"method"}
 *   anything else     setup mode: an OS captive-portal probe path (cali_captive_is_probe) -> 302
 *                     Location "http://" NET_AP_ADDR "/"; any other path -> 302 Location "/".
 *                     Otherwise: not handled (the core answers 404).
 *
 * device.wifi.mode: "setup" while the WiFi SM is in WIFI_SETUP_AP or WIFI_SETUP_AP_RETRYING;
 * "station" in WIFI_ONLINE, and in WIFI_CONNECTING/WIFI_RETRYING when joined_once (creds that joined
 * before or came from flash: a station reconnecting); "off" otherwise — WIFI_UNPROVISIONED, and a
 * setup-flow join in progress (WIFI_CONNECTING with joined_once 0: the page shows its own
 * post-submit view, and no scan disturbs the join).
 *
 * C99, no malloc, no ESP-IDF/POSIX headers. Driven in tests/firmware/test_web_handlers.py through
 * test/web_cli.c.
 */
#ifndef CALI_WEB_H
#define CALI_WEB_H

#include <stdint.h>

#include "cali_http.h"
#include "cali_net.h"
#include "cali_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Starts the HTTP core on port with cali_web_handle. t: the BLE transport, for the pairing address
 * (NULL = address always null). Returns cali_http_init's result (0 ok, -1 listen failed). */
int cali_web_init(const cali_net_t *net, const cali_transport_t *t, uint16_t port);
void cali_web_poll(uint64_t now_ms);   /* cali_http_poll */
int cali_web_handle(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx);   /* 1 = handled */

#ifdef __cplusplus
}
#endif

#endif /* CALI_WEB_H */
