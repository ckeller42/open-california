# ESP-only LoRaWAN glue (R_FW_LORA): the RAK3172 on UART1 and the LoRa task over cali_core's portable
# lora.c. Board package only, like cali_sdcard: with CONFIG_CALI_DISPLAY=n (the QEMU tier) the component
# has no sources and cali_core links lora_port_none.c instead. Never built by firmware/host.
set(srcs "")
if(CONFIG_CALI_DISPLAY)
    set(srcs "lora_esp.c")
endif()
idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS "include"
                       PRIV_REQUIRES cali_core platform esp_driver_uart esp_driver_gpio esp_app_format freertos)
if(CONFIG_CALI_DISPLAY)
    include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
endif()
