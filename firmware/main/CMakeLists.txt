# cali_display is required unconditionally: CONFIG_* is not defined while ESP-IDF expands component
# requirements (a clean build), so an `if(CONFIG_CALI_DISPLAY)` here never fires. The component
# itself compiles to nothing when the option is off (qemu/sdkconfig.qemu).
idf_component_register(SRCS "app_main.c"
                       PRIV_REQUIRES cali_core cali_ble_nimble platform bt nvs_flash esp_timer esp_netif esp_event
                                     esp_driver_uart esp_driver_usb_serial_jtag cali_display)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/cali_strict.cmake)
