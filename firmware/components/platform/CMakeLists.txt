# cali_platform.h on ESP-IDF: kv-store on NVS, esp_timer clock, printf log. host/ and test/ belong to
# the Linux/macOS host build only.
idf_component_register(SRCS "esp/platform_esp.c"
                       INCLUDE_DIRS "include"
                       PRIV_REQUIRES nvs_flash esp_timer)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
