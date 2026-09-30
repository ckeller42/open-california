# cali_platform.h on ESP-IDF: kv-store on NVS, esp_timer clock, printf log, firmware version; and
# cali_net.h on esp_wifi + esp_netif + lwIP sockets + mdns (esp/net_esp.c, include/cali_net_esp.h;
# a no-driver stub when CONFIG_CALI_WIFI=n, the QEMU image). host/ and test/ belong to the
# Linux/macOS host build only — including host/net_host.c (cali_net over POSIX sockets + the
# scripted fake WiFi) and include/cali_net_host.h.
# cali_core is REQUIRED (public: cali_net_esp.h includes its cali_net.h) although cali_core itself
# PRIV_REQUIRES platform; ESP-IDF resolves the cycle.
idf_component_register(SRCS "esp/platform_esp.c" "esp/net_esp.c"
                       INCLUDE_DIRS "include"
                       REQUIRES cali_core
                       PRIV_REQUIRES nvs_flash esp_timer esp_app_format esp_wifi esp_netif esp_event lwip
                                     espressif__mdns)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
