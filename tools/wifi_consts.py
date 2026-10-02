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
    "NET_HTTP_BODY_MAX": 20480,
    # Status display (#154, spec 2026-10-01): refresh, stale threshold, dimming, LVGL lock bound
    # (the tick runs on the NimBLE host task: never wait on a wedged render longer than this).
    "DISPLAY_REFRESH_MS": 500,
    "DISPLAY_STALE_MS": 10000,
    "DISPLAY_DIM_AFTER_MS": 60000,
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
    # home_chan_dwell_time, 30..150 ms) and keep each active channel short; and start at most one
    # scan nobody explicitly asked for (a setup-page GET) per interval.
    "NET_SCAN_HOME_DWELL_MS": 100,
    "NET_SCAN_CHAN_MAX_MS": 60,
    "NET_SCAN_MIN_INTERVAL_MS": 30000,
    # The satellite UI bundle (spec 2026-10-01 shared UI): gzipped calictl web UI served at GET /.
    # A budget for the flash array and the ~1 s first load over the single-connection core.
    "WEB_APP_GZ_MAX": 65536,
}
