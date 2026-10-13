# cali_display is required unconditionally: CONFIG_* is not defined while ESP-IDF expands component
# requirements (a clean build), so an `if(CONFIG_CALI_DISPLAY)` here never fires. The component
# itself compiles to nothing when the option is off (qemu/sdkconfig.qemu).
idf_component_register(SRCS "app_main.c"
                       PRIV_REQUIRES cali_core cali_ble_nimble platform bt nvs_flash esp_timer esp_netif esp_event
                                     esp_driver_uart esp_driver_usb_serial_jtag cali_display cali_ota cali_sntp cali_sdcard)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/cali_strict.cmake)
# The panic capture (R_FW_PANIC_CAPTURE): app_main.c's __wrap_esp_panic_handler runs first.
target_link_options(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=esp_panic_handler")
