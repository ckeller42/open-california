# ESP-only status display (#154). Never built by firmware/host (no LVGL there). With
# CONFIG_CALI_DISPLAY=n (the QEMU tier, qemu/sdkconfig.qemu) the component has no sources.
set(srcs "")
if(CONFIG_CALI_DISPLAY)
    set(srcs "display.c")
endif()
idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS "include"
                       PRIV_REQUIRES cali_core platform esp_driver_i2c)
# A source-less component is an INTERFACE library: no compile options to set.
if(CONFIG_CALI_DISPLAY)
    include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
endif()
