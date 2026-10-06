/* cali_web.h — the firmware's HTTP endpoints and status/setup page, on the portable HTTP core
 * (cali_http.h) (#154).
 *
 * Routes (cali_web_handle):
 *   GET /             station mode: 200 text/html, Content-Encoding gzip, Cache-Control no-cache — the
 *                     calictl web UI (app_bundle_gen.h's WEB_APP_HTML_GZ, tools/gen_c_dict.py), which
 *                     interprets /api/state in the browser (calictl/webui/semantics.js). Setup and off
 *                     mode: the status/setup page below (the captive portal lands here).
 *   GET /device       200 text/html: the status/setup page (firmware/web/index_gen.html, rendered
 *                     from index.html + page.js + strings.json by tools/gen_c_dict.py; strings_gen.h's
 *                     WEB_INDEX_HTML byte array), in every mode
 *   GET /api/state    200 application/json:
 *                       {"t":<uptime_ms>,"fn":{<the SNAP "fn" object, cali_snapshot_fn>},
 *                        "device":{"pairing":{"state":"<PAIR_STATE_NAMES>","address":"…"|null},
 *                                  "link":{"up":<bool>,"last_snap_age_ms":<int>|null},
 *                                  "wifi":{"mode":"setup"|"station"|"off","ssid":"…"|null,
 *                                          "ip":"a.b.c.d"|null,"rssi":<int>|null},
 *                                  "control":{"writes":<bool>},   -- POST /api/command accepted (station mode)
 *                                  "uptime_ms":<int>,"fw":"<cali_fw_version()>"}}
 *                     built whole in one handler call (one consistent snapshot) into a
 *                     NET_JSON_MAX buffer; overflow -> 500 + LOG "http: overflow".
 *   GET /api/wifi     200 {"mode","ssid","ip","rssi","last_error":"not_found"|"auth"|"other"|null,
 *                     "scan":[{"ssid","rssi","secure":<bool>}]} (last_error = why the last join
 *                     failed, cali_wifi_run_last_fail(); scan = the last SCAN_DONE's list); in setup mode it also asks for a fresh scan
 *                     (cali_wifi_run_scan_auto: at most one per NET_SCAN_MIN_INTERVAL_MS, held
 *                     back while a BLE pairing flow is active — the one coex gate lives in the
 *                     runner, cali_wifi_run.h).
 *   POST /api/wifi    body {"ssid":"…","psk":"…"} — a fixed-shape parser: one object, exactly the
 *                     two keys (either order, once each), string values with only \" and \\
 *                     escapes, no control bytes/NUL, whitespace between tokens allowed. SSID 1 ..
 *                     NET_SSID_MAX bytes, PSK NET_PSK_MIN .. NET_PSK_MAX bytes. Valid ->
 *                     cali_kv_set("wifi_ssid"), cali_kv_set("wifi_psk"), cali_wifi_run_set_creds
 *                     -> 200 {"ok":true}; else 400 {"ok":false,"error":"json"|"ssid"|"psk"} (json
 *                     checked first); a kv write failure -> 500 {"ok":false,"error":"store"}.
 *   DELETE /api/wifi  cali_wifi_run_forget() -> 200 {"ok":true}
 *   POST /api/command calictl's control request {"function":"…","what":"…","value":<string|int|null>,
 *                     "confirm":<bool>} (fixed-shape parser: one object, those keys in any order, each
 *                     at most once; value null or absent = ""; an integer is passed as its decimal text;
 *                     booleans, fractions, nested values, an over-long function/what/value or any
 *                     other key = 400 bad_json — plan B decision 5). Station mode only: in every other
 *                     WiFi mode (setup hotspot, setup-flow join = "off", unprovisioned) 403
 *                     {"ok":false,"error":"setup_mode"} before anything reaches the control module.
 *                     Then web.py's checks: 400 missing_function_or_what, 400 confirm_required
 *                     (airheater, roof without "confirm":true). Then cali_ctl_submit (cali_control.h):
 *                       accepted  -> CALI_HTTP_PENDING until the sequencer's done callback, then
 *                                    200 {"ok":true,"applied":null,"state":null,"error":null,"function":fn}
 *                                    (applied never true: no readback check), or 502 write_failed /
 *                                    504 write_timeout — the answer waits for the write ACKs
 *                                    (<= CALI_CTL_DEADLINE_MS)
 *                       refused / elsewhere -> 200 {"ok":true,"applied":false,"refused":<reason>,
 *                                    "state":null,"error":null,"function":fn} (calictl's shape)
 *                       bad value -> 400 bad_value; no such control -> 400 unknown_control;
 *                       busy -> 409 busy; not ready (no armed link / state) -> 503 not_connected
 *                     Every error is {"ok":false,"error":<code>}. Any other method: 405 method.
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
