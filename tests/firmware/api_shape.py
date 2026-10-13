"""The firmware web API's JSON key sets (``firmware/components/cali_core/include/cali_web.h``).

One copy for the tests that pin them: ``tests/firmware/test_web_handlers.py`` asserts them of the
real C handlers (``web.c``), ``tests/test_ux_gallery_esp_fixtures.py`` of the canned JSON behind the
docs screenshots — so a handler shape change that updates one and not the other fails.
"""

STATE_KEYS = frozenset({"t", "fn", "device"})  # GET /api/state
DEVICE_KEYS = frozenset(
    {
        "pairing",
        "link",
        "wifi",
        "control",
        "uptime_ms",
        "fw",
        "ota",
        "time",
        "sd",
        "res",
        "battery",
        "auto_camper",
        "display",
        "ux",
        "water_held",
    }
)  # /api/state device
OTA_KEYS = frozenset({"state", "available_sha", "progress_pct", "error"})  # /api/state device.ota (R_FW_OTA)
TIME_KEYS = frozenset({"source", "synced", "boot", "up"})  # /api/state device.time (R_FW_TIME)
SD_KEYS = frozenset({"state", "total_mb", "free_mb", "last_seq", "dropped"})  # device.sd (R_FW_SD_LOG)
RES_KEYS = frozenset({"heap_free_kb", "heap_min_kb", "psram_free_kb", "cpu_pct"})  # device.res
BATTERY_KEYS = frozenset({"pct", "mv", "usb", "charging", "runtime_min"})  # device.battery (R_FW_BATTERY)
# device.display (R_FW_DISPLAY_WAKE): the live screen state + the settings
DISPLAY_KEYS = frozenset(
    {"state", "lux", "prox", "brightness_pct", "sensor", "auto", "prox_wake", "min", "max", "offset"}
)
CONTROL_KEYS = frozenset({"writes"})  # /api/state device.control: POST /api/command accepted (station mode)
PAIRING_KEYS = frozenset({"state", "address"})  # /api/state device.pairing
# GET/POST /api/pairing: calictl serve.pairing_snapshot()
PAIRING_API_KEYS = frozenset({"state", "attempts", "error", "address", "radio_busy"})
LINK_KEYS = frozenset({"up", "last_snap_age_ms"})
WIFI_KEYS = frozenset({"mode", "ssid", "ip", "rssi"})  # /api/state device.wifi
WIFI_GET_KEYS = WIFI_KEYS | {"last_error", "scan"}  # GET /api/wifi
AP_KEYS = frozenset({"ssid", "rssi", "secure"})  # one GET /api/wifi scan entry
