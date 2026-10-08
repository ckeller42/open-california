"""The firmware web API's JSON key sets (``firmware/components/cali_core/include/cali_web.h``).

One copy for the tests that pin them: ``tests/firmware/test_web_handlers.py`` asserts them of the
real C handlers (``web.c``), ``tests/test_ux_gallery_esp_fixtures.py`` of the canned JSON behind the
docs screenshots — so a handler shape change that updates one and not the other fails.
"""

STATE_KEYS = frozenset({"t", "fn", "device"})  # GET /api/state
DEVICE_KEYS = frozenset({"pairing", "link", "wifi", "control", "uptime_ms", "fw"})  # /api/state device
CONTROL_KEYS = frozenset({"writes"})  # /api/state device.control: POST /api/command accepted (station mode)
PAIRING_KEYS = frozenset({"state", "address"})  # /api/state device.pairing
# GET/POST /api/pairing: calictl serve.pairing_snapshot()
PAIRING_API_KEYS = frozenset({"state", "attempts", "error", "address", "radio_busy"})
LINK_KEYS = frozenset({"up", "last_snap_age_ms"})
WIFI_KEYS = frozenset({"mode", "ssid", "ip", "rssi"})  # /api/state device.wifi
WIFI_GET_KEYS = WIFI_KEYS | {"last_error", "scan"}  # GET /api/wifi
AP_KEYS = frozenset({"ssid", "rssi", "secure"})  # one GET /api/wifi scan entry
