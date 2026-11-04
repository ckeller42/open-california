"""The firmware web API's JSON key sets (``firmware/components/cali_core/include/cali_web.h``).

One copy for the tests that pin them: ``tests/firmware/test_web_handlers.py`` asserts them of the
real C handlers (``web.c``), ``tests/test_ux_gallery_esp_fixtures.py`` of the canned JSON behind the
docs screenshots — so a handler shape change that updates one and not the other fails.
"""

STATE_KEYS = frozenset({"fn", "device"})  # GET /api/state (+ "ext" while exp_sensors is on)
# GET /api/state?diag=1 adds these diagnostics (each set: the members it adds to that object); the
# default reply leaves them out, so the page's poll carries only what it shows (R_FW_STATE_DIAG)
STATE_DIAG_KEYS = frozenset({"t"})
DEVICE_DIAG_KEYS = frozenset({"res"})
TIME_DIAG_KEYS = frozenset({"up"})
SD_DIAG_KEYS = frozenset({"last_seq"})
BLE_DIAG_KEYS = frozenset({"probing", "unit_adv_seen_ms_ago"})
DISPLAY_DIAG_KEYS = frozenset({"lux", "prox", "motion", "dfs", "pm"})
EXT_ENTRY_KEYS = frozenset({"type", "age_s"})  # every ext.<alias> entry: these + its type's fields
SENSORS_KEYS = frozenset({"configured", "seen", "scan"})  # GET /api/sensors (R_FW_SENSORS)
SENSOR_CFG_KEYS = frozenset({"id", "type", "alias", "last_seen_s", "value", "has_key"})  # .configured[]
SENSOR_SEEN_KEYS = frozenset({"id", "type", "model", "rssi"})  # .seen[]
DEVICE_KEYS = frozenset(
    {
        "pairing",
        "link",
        "wifi",
        "setup",
        "control",
        "uptime_ms",
        "fw",
        "ota",
        "time",
        "sd",
        "battery",
        "auto_camper",
        "pi_wake",
        "ble",
        "display",
        "ux",
        "experimental",
        "energy",
        "water_held",
    }
)  # /api/state device
# device.lora (R_FW_LORA, R_FW_LORA_SETUP): present only while exp_lora is on
LORA_KEYS = frozenset(
    {
        "module", "joined", "at_unanswered", "sf", "last_s", "airtime_ms_24h", "airtime_charged_ms_24h",
        "unconfirmed_sends", "downlinks_24h", "uplinks", "note", "setup", "has_ttn_keys", "dev_eui",
    }
)
# device.thermostat (R_FW_THERMOSTAT_API): present only while a reference thermometer is selected
THERMOSTAT_STATE_KEYS = frozenset({"mode", "target", "t_ref", "level", "reason"})
# GET/POST /api/thermostat (R_FW_THERMOSTAT_API) and its "status" member (cali_thermo_state_json)
THERMOSTAT_API_KEYS = frozenset(
    {"rev", "mode", "target", "runtime_min", "ref", "backup", "out", "outside_on", "night", "cfg", "status"}
)
THERMOSTAT_STATUS_KEYS = frozenset(
    {
        "enabled",
        "mode",
        "reason",
        "heater",
        "t_ref",
        "t_out",
        "target",
        "level",
        "active",
        "outside_active",
        "end",
        "blocked",
        "wait_left_s",
        "night",
        "eta_s",
        "eta_ends_first",
    }
)
OTA_KEYS = frozenset(
    {"state", "available_sha", "progress_pct", "error", "channel"}
)  # /api/state device.ota (R_FW_OTA)
TIME_KEYS = frozenset({"source", "synced", "boot"})  # /api/state device.time (R_FW_TIME)
EXPERIMENTAL_KEYS = frozenset({"sensors", "lora"})  # device.experimental (R_FW_EXPERIMENTAL, R_FW_LORA)
ENERGY_KEYS = frozenset({"boot", "solar_wh", "load_wh", "batt_net_wh", "load_a"})  # device.energy
SD_KEYS = frozenset({"state", "total_mb", "free_mb", "dropped"})  # device.sd (R_FW_SD_LOG)
RES_KEYS = frozenset({"heap_free_kb", "heap_min_kb", "psram_free_kb", "cpu_pct"})  # device.res (diag only)
BLE_KEYS = frozenset({"mode", "exp", "idle_min", "rest_poll_min"})  # /api/state device.ble
BATTERY_KEYS = frozenset({"pct", "mv", "usb", "charging", "runtime_min"})  # device.battery (R_FW_BATTERY)
# device.display (R_FW_DISPLAY_WAKE): the live screen state + the settings
DISPLAY_KEYS = frozenset(
    {"state", "brightness_pct", "sensor", "auto", "prox_wake", "motion_wake", "min", "max", "offset"}
)
CONTROL_KEYS = frozenset({"writes"})  # /api/state device.control: POST /api/command accepted (station mode)
PAIRING_KEYS = frozenset({"state", "address"})  # /api/state device.pairing
# GET/POST /api/pairing: calictl serve.pairing_snapshot()
PAIRING_API_KEYS = frozenset({"state", "attempts", "error", "address", "radio_busy"})
LINK_KEYS = frozenset({"up", "ready", "rest", "redial", "rest_next_s", "last_snap_age_ms"})
WIFI_KEYS = frozenset({"mode", "ssid", "ip", "rssi"})  # /api/state device.wifi
STATE_WIFI_KEYS = WIFI_KEYS | {"ap_ssid"}  # device.wifi also names the setup hotspot
WIFI_GET_KEYS = WIFI_KEYS | {"last_error", "reverted", "scan"}  # GET /api/wifi
AP_KEYS = frozenset({"ssid", "rssi", "secure"})  # one GET /api/wifi scan entry
