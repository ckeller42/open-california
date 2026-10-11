# ESP-only firmware update (R_FW_OTA): cali_core's portable state machine (ota.c) over esp_https_ota
# + the certificate bundle. Never built by firmware/host (the host has no OTA I/O: /api/ota -> 503).
idf_component_register(SRCS "ota_esp.c"
                       INCLUDE_DIRS "include"
                       PRIV_REQUIRES cali_core platform esp_https_ota esp_http_client app_update esp_partition
                                     mbedtls freertos)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
