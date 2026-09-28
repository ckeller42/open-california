idf_component_register(SRCS "app_main.c"
                       PRIV_REQUIRES cali_core cali_ble_nimble platform bt nvs_flash esp_timer esp_netif esp_event
                                     esp_driver_uart esp_driver_usb_serial_jtag)
include(${CMAKE_CURRENT_LIST_DIR}/../cmake/cali_strict.cmake)
