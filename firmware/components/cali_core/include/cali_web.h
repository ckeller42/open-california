/* cali_web.h — the firmware's HTTP endpoints, web UI and WiFi recovery page, on the portable HTTP core
 * (cali_http.h) (#154).
 *
 * Routes (cali_web_handle):
 * Static answers (/, /app, /device, the PWA assets) carry a strong ETag (FNV-1a 64 of the bytes
 * served) — a matching If-None-Match gets the core's 304 (cali_http.h); HTML + manifest are
 * Cache-Control no-cache, icons max-age=86400. JSON built by finish_json (/api/state, /api/wifi,
 * /api/pairing) is Cache-Control no-store, no ETag.
 *   GET /, /app       200 text/html, Content-Encoding gzip, Cache-Control no-cache, in every mode — the
 *                     calictl web UI (app_bundle_gen.h's WEB_APP_HTML_GZ, tools/gen_c_dict.py), which
 *                     interprets /api/state in the browser (calictl/webui/semantics.js) and opens on
 *                     its WiFi-setup screen in setup/off mode (the captive portal lands here).
 *   GET /device       200 text/html: the WiFi recovery page (firmware/web/index_gen.html, rendered
 *                     from index.html by tools/gen_c_dict.py; strings_gen.h's WEB_INDEX_HTML byte
 *                     array), in every mode — a way back onto WiFi if the UI fails
 *   GET /api/state    200 application/json:
 *                       {"t":<uptime_ms>,"fn":{<the SNAP "fn" object, cali_snapshot_fn>},
 *                        "device":{"pairing":{"state":"<PAIR_STATE_NAMES>","address":"…"|null},
 *                                  "link":{"up":<bool>,"last_snap_age_ms":<int>|null},
 *                                  "wifi":{"mode":"setup"|"station"|"off","ssid":"…"|null,
 *                                          "ip":"a.b.c.d"|null,"rssi":<int>|null},
 *                                  "control":{"writes":<bool>},   -- POST /api/command accepted (station mode)
 *                                  "uptime_ms":<int>,"fw":"<cali_fw_version()>",
 *                                  "ota":{"state","available_sha","progress_pct","error","channel"},   -- cali_ota.h
 *                                  "time":{"source","synced","boot","up"},   -- cali_time.h (R_FW_TIME)
 *                                  "sd":{"state","total_mb","free_mb","last_seq","dropped"},   -- cali_sdlog.h
 *                                  "res":{"heap_free_kb","heap_min_kb","psram_free_kb","cpu_pct"},
 *                                        -- cali_res_stats; each an int or null (unknown: host, no PSRAM)
 *                                  "battery":{"pct","mv","usb","charging","runtime_min"},   -- cali_battery.h
 *                                        (R_FW_BATTERY); each null when unknown (host, QEMU, no AXP2101)
 *                                  "auto_camper":{"enabled":<bool>,"state":"idle"|"armed"|"restoring"},
 *                                                -- cali_auto_camper.h (R_FW_AUTO_CAMPER)
 *                                  "pi_wake":{"enabled":<bool>,"last_s":<int>|null,
 *                                             "reason":"ignition"|"manual"|null,"halt_after_min","halt_soc_pct",
 *                                             "halt_in_s":<int>|null,"halted":<bool>,"halt_last_s":<int>|null,
 *                                             "halt_reason":"timer"|"battery"|"manual"|null,"batt_low":<bool>},
 *                                                -- cali_pi_wake.h (R_FW_PI_WAKE, R_FW_PI_HALT); last_s = s since the last pulse
 *                                  "ble":{"mode":"hold"|"quiet"|"off","probing":<bool>,"exp":null|{"on":true,
 *                                         "phase_min","from","to","phase":"hold"|"quiet"|"off"|null,
 *                                         "remaining_ms":<int>|null},"idle_min":0..60,"unit_adv_seen_ms_ago":<int>|null},
 *                                                -- cali_ble_exp.h (R_FW_BLE_MODE, R_FW_BLE_EXP, R_FW_BLE_IDLE); a
 *                                                GET of /api/state is a viewer: it holds the experiment and the
 *                                                idle policy
 *                                  "display":{"state":"on"|"dim"|"off","lux","prox","motion","brightness_pct",
 *                                             "sensor":"ok"|"absent"|"error","auto":<bool>,
 *                                             "prox_wake":<bool>,"motion_wake":<bool>,"min","max","offset",
 *                                             "dfs":<bool>|null,"pm":"<min>-<max>"|null (the live esp_pm config)}
 *                                             -- R_FW_DISPLAY_WAKE; motion = mg, null without a BMI270 reading;
 *                                             the live members are null without a screen (host, QEMU)
 *                                  "ux":{"enabled":<bool>},   -- "Send usage data" (NVS ux_on, default on;
 *                                                                the web UI's _meta.ux, R_UX_TELEMETRY)
 *                                  "experimental":{"sensors":<bool>},   -- the sensor track switch (NVS
 *                                                                exp_sensors, default off; R_FW_EXPERIMENTAL)
 *                                  "water_held":<bool>,
 *                                  "water_held_age_ms":<int>},   -- only while held AND the baseline
 *                                                                   was measured on this boot
 *                      "ext":{"<alias>":{"type":"<id>",<field>:<num|null>,…,"age_s":<int|null>},…}}   -- ONLY while
 *                                  exp_sensors is on (off: the key is absent); cali_sensors_state_json
 *                     built whole in one handler call (one consistent snapshot) into a
 *                     NET_JSON_MAX buffer; overflow -> 500 + LOG "http: overflow".
 *   GET /api/wifi     200 {"mode","ssid","ip","rssi","last_error":"not_found"|"auth"|"other"|null,
 *                     "reverted":<bool>,"scan":[{"ssid","rssi","secure":<bool>}]} (last_error = why
 *                     the last join failed, cali_wifi_run_last_fail(); reverted = a failed
 *                     replacement put the previous network back, cali_wifi_run_reverted() — then
 *                     ssid is that network and last_error the replacement's reason; scan = the last
 *                     SCAN_DONE's list); in setup mode and in WIFI_ONLINE it also asks for a fresh scan
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
 *                     Optional "local_now": the page's wall clock read as UTC (s), the wake-up
 *                     builder's only clock (calictl accepts and ignores it); null or absent = no clock
 *                     (the wake-up is refused with the clock reason). A string, a fraction/exponent, a
 *                     boolean or an integer before 2026-01-01T00:00Z (1767225600) -> 400 bad_value; an
 *                     integer is read saturated at INT64_MAX, so one past the 32-bit Timestamp is the
 *                     builder's bad value (400). No skew check (ruling R1).
 *                     Then web.py's checks: 400 missing_function_or_what, 400 confirm_required
 *                     (airheater, roof without "confirm":true), then 400 bad_value for local_now.
 *                     Then cali_ctl_submit (cali_control.h):
 *                       accepted  -> CALI_HTTP_PENDING until the sequencer's done callback, then
 *                                    200 {"ok":true,"applied":null,"state":null,"error":null,"function":fn}
 *                                    (applied never true: no readback check; plus "unconfirmed":true
 *                                    when the link dropped after a frame went out), or 502 write_failed /
 *                                    504 write_timeout — the answer waits for the write ACKs
 *                                    (<= CALI_CTL_DEADLINE_MS)
 *                       refused / elsewhere -> 200 {"ok":true,"applied":false,"refused":<reason>,
 *                                    "state":null,"error":null,"function":fn} (calictl's shape)
 *                       bad value -> 400 bad_value; no such control -> 400 unknown_control;
 *                       busy -> 409 busy; not ready (no armed link / state) -> 503 not_connected
 *                     Every error is {"ok":false,"error":<code>}. Any other method: 405 method.
 *                     Known, deliberate differences from calictl: the Content-Type header is not
 *                     checked (calictl answers 415 for a non-JSON one; the core does not expose
 *                     headers, the UI always sends application/json); the integer -0 passes as the
 *                     text "-0" (Python's json gives int 0 -> "0"), which the C twin refuses as
 *                     not on/off where calictl would read OFF — the safe direction; a value of
 *                     CALI_CTL_VALUE_MAX (64) bytes or more is 400 bad_json BEFORE the gates, while
 *                     the console `set` (Task 2 I1) runs the gate first and refuses — both refuse.
 *   GET /api/pairing  200 calictl's pairing_snapshot(): {"state","attempts","error","address",
 *                     "radio_busy":false} — the console STATE members (cali_snapshot_pairing).
 *   POST /api/pairing calictl's wizard request {"action":"start"|"passkey"|"cancel"|"reset",
 *                     "value":"<6 digits>","confirm":true} (fixed-shape parser: those keys, each at most
 *                     once, any order; action a string, value a string/integer/null, confirm a bool).
 *                     In EVERY WiFi mode (connection management, not a control write). Checks in
 *                     web.py's order, calictl's error bodies {"error":<code>} (no "ok"): 400 bad_json,
 *                     400 bad_action, 400 confirm_required (reset without confirm:true), 400
 *                     bad_passkey (passkey value not a 6-digit string), then 409 busy for start/reset
 *                     while a control command is pending (cali_ctl_busy: the single link is in use).
 *                     Then the runner: start = console "pair" (ignored by the SM unless idle/error),
 *                     passkey = "passkey N" (ignored unless waiting_passkey), cancel =
 *                     cali_runner_cancel while a flow runs (scanning..verifying; else a no-op, so a
 *                     stale cancel never drops a bonded link), reset = "forget" (drops the bond) -> 200 the post-action
 *                     snapshot. Known differences from calictl: unknown keys, a non-string action and a
 *                     non-bool confirm are bad_json (calictl ignores / reads truthiness); the
 *                     Content-Type header is not checked (calictl answers 415 for a non-JSON one; the
 *                     core does not expose headers, the UI always sends application/json); a cancel
 *                     outside a running flow is a no-op (calictl steps its SM to idle).
 *   POST /api/ux      the web UI's usage beacon (R_UX_TELEMETRY; calictl web.ux_batch's shape and rules):
 *                     station mode only (403 setup_mode), body <= CALI_UX_BODY_MAX (else 413 {"error":
 *                     "too_large"}); "Send usage data" off -> 204, nothing recorded; not exactly the
 *                     shape -> 400 {"error":"bad_ux"}; else one SD text record per event
 *                     {…stamp,"ev":"ux","sid","ua_class","standalone","origin","type",<the event's keys>}
 *                     (cali_sdlog_text; no card: none) and 204. 405 for another method.
 *   POST /api/ux_setting  {"enabled":<bool>} (exactly that object): the Satellite settings switch -> NVS ux_on,
 *                     200 {"ok":true,"ux":<bool>}; station mode only (403), 400 bad_json, 500 store, 405.
 *   POST /api/experimental  {"sensors":<bool>} (exactly that object; R_FW_EXPERIMENTAL): the Satellite settings
 *                     "Sensors (experimental)" switch -> cali_exp_sensors_set (NVS exp_sensors, applied at
 *                     once, LOG exp: sensors on|off) -> 200 {"ok":true,"sensors":<bool>}; station mode
 *                     only (403 setup_mode), 400 bad_json, 500 store, 405 for another method.
 *   GET|POST /api/sensors  (R_FW_SENSORS, cali_sensors.h) every WiFi mode; 404 {"ok":false,"error":
 *                     "disabled"} on both while exp_sensors is off (checked first). GET -> 200
 *                     {"configured":[{id,type,alias,last_seen_s,value|null,has_key}],"seen":[{id,type,
 *                     model,rssi}],"scan":"ok"|"paused"} and cali_sensors_kick. POST exactly one of
 *                     {"add":<id>,"alias"?:"…","key"?:"<32 hex>"} | {"remove":<id>} | {"id":<id>,
 *                     "alias":"…"} (any order, each key once; id 0..65535) -> 200 the GET body; 400
 *                     bad_json (not that shape) | bad_alias | bad_key | key_mismatch | unknown_id |
 *                     full, 500 store; 405 for another method.
 *   POST /api/auto_camper  {"enabled":<bool>} (exactly that object; R_FW_AUTO_CAMPER, serve's endpoint):
 *                     cali_ac_set_enabled -> 200 {"ok":true,"auto_camper":<bool>}; station mode only
 *                     (403 setup_mode), 400 bad_json, 500 store (not saved), 405 for another method.
 *   POST /api/pi_wake  {} or no body (R_FW_PI_WAKE): cali_pw_wake("manual"), GPIO9 low 200 ms -> 200
 *                     {"ok":true}; 409 busy (a pulse in progress); station mode only (403 setup_mode),
 *                     400 bad_json (any other body), 405 for another method.
 *   POST /api/pi_halt  {} or no body (R_FW_PI_HALT): cali_ph_halt("manual"), GPIO8 low 500 ms -> 200
 *                     {"ok":true}; 409 busy (either Pi line low); station mode only (403 setup_mode),
 *                     400 bad_json (any other body), 405 for another method.
 *   GET  /api/ble     200 the device.ble object above (R_FW_BLE_EXP)
 *   POST /api/ble     any of {"mode":"hold"|"quiet"|"off"}, {"probe":true}, {"exp_min":1..720,"from"?:0..23,
 *                     "to"?:0..23} (the night experiment, window default 22-06), {"exp_min":0} (stop),
 *                     {"idle_min":0..60} (the idle policy, stored; R_FW_BLE_IDLE), each key once -> 200
 *                     device.ble; station mode only (403 setup_mode), 400 bad_json (unknown or repeated key,
 *                     wrong type, none of mode/probe/exp_min/idle_min), 400 bad_value (out of range), 409
 *                     no_time (exp_min without a wall clock, nothing applied), 409 busy (probe refused), 500
 *                     store (idle_min not stored), 405 for another method.
 *   POST /api/pi_wake_setting  a non-empty subset of {"enabled":<bool>,"halt_after_min":0..1440,
 *                     "halt_soc_pct":0..100}, each key once (R_FW_PI_WAKE, R_FW_PI_HALT): wake on ignition,
 *                     the halt timer and battery guard -> 200 {"ok":true,"pi_wake":<bool>,"halt_after_min":n,
 *                     "halt_soc_pct":n}; station mode only (403 setup_mode), 400 bad_json, 400 bad_value
 *                     (out of range), 500 store (not saved), 405 for another method.
 *   POST /api/ota     {"action":"check"|"install"} (R_FW_OTA, cali_ota.h): starts the OTA task and
 *                     answers at once 200 {"ok":true}; progress in /api/state device.ota. Station mode
 *                     only, like /api/command (else 403 setup_mode, checked first after the method);
 *                     400 bad_json (the /api/pairing parser: action/value/confirm keys), 400
 *                     bad_action, 409 busy (one runs), 503 ota_unavailable (no OTA I/O: the host
 *                     build, the QEMU image). Any other method: 405 method.
 *                     {"channel":"stable"|"latest"} instead (never with "action"): cali_ota_set_channel
 *                     -> 200 {"ok":true} (stored; the next check uses it; no OTA I/O needed), 400
 *                     bad_value (another string), 500 store; same method/mode checks.
 *   POST /api/display {"auto"?:<bool>,"prox_wake"?:<bool>,"min"?:<int>,"max"?:<int>,"offset"?:<int>,
 *                     "motion_wake"?:<bool>,"dfs"?:<bool>} (R_FW_DISPLAY_WAKE): merged into the current settings,
 *                     persisted (kv disp_auto/disp_prox/disp_min/disp_max/disp_off/disp_motion);
 *                     "dfs" is NOT a setting: cali_pm_dfs(on) at once, never persisted (ponytail: the
 *                     #346 touch-NACK A/B knob; device.display.dfs/pm = the live PM state)
 *                     -> 200 {"ok":true}; station mode only (403 setup_mode), 400 bad_json (other keys,
 *                     a key twice, a non-bool flag, a non-integer percent), 400 bad_value (min 1..50,
 *                     max 20..100, min < max, offset -50..50), 500 store, 405 for another method.
 *   GET /api/log?since=<seq>&max=<n>   (R_FW_LOG_API, buspi's `calictl esp-ingest`) one page of the
 *                     SD log, 200 application/x-ndjson: the record lines with seq > since (default 0),
 *                     at most max (0/absent = CALI_SDLOG_PAGE_DEFAULT, 2000) and what fits the page
 *                     buffer, then {"next","more","boot","lost"} (cali_sdlog.h cali_sdlog_page). In
 *                     every WiFi mode (read only). The SD task reads it (cali_sdlog_page_request ->
 *                     CALI_HTTP_PENDING until cali_sdlog_page_result); the owner task never touches
 *                     the card. 400 bad_value (anything but since/max digits), 503 no_card (card not
 *                     ok, or no page buffer: host, QEMU), 503 sd_read (the read failed), 409 busy (an
 *                     earlier page still being read), 405 method.
 *   other method on /api/wifi, /api/state or /api/pairing -> 405 {"ok":false,"error":"method"}
 *   anything else     setup mode: an OS captive-portal probe path (cali_captive_is_probe) -> 302
 *                     Location "http://" NET_AP_ADDR "/"; any other path -> 302 Location "/".
 *                     Otherwise: not handled (the core answers 404).
 *
 * device.wifi.mode: "setup" while the WiFi SM is in WIFI_SETUP_AP or WIFI_SETUP_AP_RETRYING;
 * "station" in WIFI_ONLINE, and in WIFI_CONNECTING/WIFI_RETRYING when joined_once is 1 (creds that
 * joined before or came from flash: a station reconnecting); "off" otherwise — WIFI_UNPROVISIONED, and
 * a setup-flow or replacement join in progress (WIFI_CONNECTING with joined_once 0 or WIFI_REPLACING:
 * the page shows its own post-submit view, and no scan disturbs the join).
 *
 * C99, no malloc, no ESP-IDF/POSIX headers. Driven in tests/firmware/test_web_handlers.py through
 * test/web_cli.c.
 */
#ifndef CALI_WEB_H
#define CALI_WEB_H

#define CALI_UX_ON_KEY "ux_on"     /* NVS (namespace cali): "Send usage data", 1 byte, absent = on */
#define CALI_UX_BODY_MAX 2048      /* POST /api/ux body cap (calictl web.UX_MAX_BODY) */
#define CALI_EXP_SENSORS_KEY "exp_sensors"   /* NVS (namespace cali): the experimental sensor track, 1 byte, absent = off (R_FW_EXPERIMENTAL) */

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

/* device.res: the satellite's own resources. Internal heap free now / lowest since boot, PSRAM free
 * (KB), and the busy % of both cores since the previous call. -1 = unknown (JSON null). */
typedef struct {
    int heap_free_kb, heap_min_kb, psram_free_kb, cpu_pct;
} cali_res_t;
/* Platform: fill r. web.c's weak default reports everything unknown (host); app_main.c overrides. */
void cali_res_stats(cali_res_t *r);
void cali_web_poll(uint64_t now_ms);   /* cali_http_poll */
int cali_web_handle(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx);   /* 1 = handled */

/* 1 while the experimental sensor track is switched on (POST /api/experimental), else 0. Lazy kv read. */
int cali_exp_sensors(void);
/* Stores the switch (kv CALI_EXP_SENSORS_KEY), updates the cache, logs "exp: sensors on|off".
 * 0 ok, -1 store failure (cache unchanged). */
int cali_exp_sensors_set(int on);

#ifdef __cplusplus
}
#endif

#endif /* CALI_WEB_H */
