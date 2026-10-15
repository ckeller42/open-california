"""wifi_consts.py — the single Python source for the ESP32 firmware's WiFi AP/setup-portal
constants (#154 Task 1+). ``tools.gen_c_dict.generate_net()`` turns ``CONSTS`` into
``csrc/net_consts.h`` for the firmware; the calictl-side setup flow (Task 3) imports this same
``CONSTS`` dict, so the two languages can never drift on an SSID, a timeout or a size limit.

Stdlib-only, no runtime import cost: plain data, safe to import from ``calictl/*`` at module top
if a later task wants to (unlike ``bleak``/``paho``/etc, which stay lazy per the project's hard
rule).

.. req:: WiFi setup constants are generated for C from one Python source
   :id: R_NET_CONSTS_SINGLE_SOURCE
   :status: implemented
   :tags: esp32, wifi, protocol

   The ESP32 firmware's AP SSID/PSK, hostname, address, timeouts and buffer-size limits for its
   WiFi setup portal shall live in exactly one place, ``tools.wifi_consts.CONSTS``, and reach the
   C firmware only through the generated, freshness-checked ``csrc/net_consts.h``
   (``tools.gen_c_dict.generate_net``) — never hand-typed a second time in C or in the Python
   setup flow that will consume the same dict.
"""

CONSTS = {
    "NET_AP_SSID": "calictl-esp-setup",
    "NET_AP_PSK": "calictl-setup",
    "NET_HOSTNAME": "calictl-esp",
    "NET_AP_ADDR": "192.168.4.1",
    "NET_AP_ADDR_U32": 0xC0A80401,
    "NET_AP_CLOSE_MS": 30000,
    "NET_RETRY_MIN_MS": 1000,
    "NET_RETRY_MAX_MS": 60000,
    "NET_SETUP_AFTER_MS": 300000,
    "NET_PAGE_POLL_MS": 2000,
    # The setup page's Connect POST: give up on an answer after NET_CONNECT_TIMEOUT_MS, then retry
    # once NET_CONNECT_RETRY_MS later (the phone can drop off the hotspot for a moment, bench walk).
    "NET_CONNECT_TIMEOUT_MS": 8000,
    "NET_CONNECT_RETRY_MS": 3000,
    "NET_HTTP_PORT": 80,
    "NET_HTTP_REQ_MAX": 2048,
    # The status/setup page budget (tests/test_web_strings.py): the rendered index_gen.html must fit.
    # A flash-only budget (the page is a const array, no RAM buffer); 24 KB since the auto-camper row,
    # 28 KB since the "Send usage data" row (R_UX_TELEMETRY; the screen row left ~100 B),
    # 32 KB since the Experimental + Sensors boxes (R_FW_EXPERIMENTAL, R_FW_SENSORS),
    # 40 KB since the WiFi box + Satellite settings (PR C).
    "NET_HTTP_BODY_MAX": 40960,
    # Status display (#154, spec 2026-10-01): refresh, stale threshold, dimming, LVGL lock bound
    # (the tick runs on the NimBLE host task: never wait on a wedged render longer than this).
    # The tick also polls the light/proximity sensor (R_FW_DISPLAY_WAKE): 200 ms, so a hand near
    # (two reads in a row) wakes the screen in well under a second; off after DISPLAY_OFF_AFTER_MS.
    "DISPLAY_REFRESH_MS": 200,
    # Stale = no snapshot for three water re-read periods (3 * CALI_SESSION_WATER_REREAD_MS, 30 s):
    # parked, that re-read is the ONLY refresh (no notifications; the 1003 heartbeat is a write), so
    # the data age normally saws up to ~31 s. The web page's SAT_OFFLINE_S (90 s) is the same rule
    # (tests/test_semantics_js_parity.py pins both). 10 s used to flip the row red -> woke the screen.
    "DISPLAY_STALE_MS": 90000,
    "DISPLAY_DIM_AFTER_MS": 60000,
    "DISPLAY_OFF_AFTER_MS": 300000,
    "DISPLAY_BRIGHT_PCT": 100,
    "DISPLAY_DIM_PCT": 10,
    "DISPLAY_LOCK_TIMEOUT_MS": 50,
    "NET_JSON_MAX": 8192,
    "NET_SSID_MAX": 32,
    "NET_PSK_MIN": 8,
    "NET_PSK_MAX": 63,
    "NET_SCAN_MAX": 16,
    # Scans vs the setup hotspot (bench walk #154): one radio serves the softAP and the scan, so a
    # scan that leaves the AP channel for whole seconds disassociates the phone on the setup page
    # (its Connect POST then fails). Return to the AP channel between scanned channels (IDF
    # home_chan_dwell_time, 30..150 ms; the per-channel dwell stays at IDF's default, which it
    # requires with Bluetooth on); and start at most one scan nobody explicitly asked for (a
    # setup-page GET) per interval.
    "NET_SCAN_HOME_DWELL_MS": 100,
    "NET_SCAN_MIN_INTERVAL_MS": 30000,
    # The satellite UI bundle (spec 2026-10-01 shared UI): gzipped calictl web UI served at GET /.
    # A budget for the flash array and the first load over the single-connection core (raised 64 -> 96 KB
    # 2026-10-12: only tests read it, the bundle is a const in flash; repeat opens are 304s since #321).
    "WEB_APP_GZ_MAX": 98304,
}
