# ESP-only clock (R_FW_TIME): SNTP over esp_netif_sntp, feeding cali_core's time.c (device.time, the
# log's ts). Never built by firmware/host (the host has no SNTP: source "none").
idf_component_register(SRCS "sntp_esp.c"
                       INCLUDE_DIRS "include"
                       PRIV_REQUIRES cali_core platform esp_netif esp_event lwip)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
