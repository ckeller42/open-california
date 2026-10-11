# ESP-only SD log glue (R_FW_SD_LOG): card power + mount + the SD task over cali_core's portable
# sdlog.c. Board package only, like cali_display: with CONFIG_CALI_DISPLAY=n (the QEMU tier) the
# component has no sources and nothing is logged. Never built by firmware/host.
set(srcs "")
if(CONFIG_CALI_DISPLAY)
    set(srcs "sdcard_esp.c")
endif()
idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS "include"
                       PRIV_REQUIRES cali_core platform fatfs sdmmc esp_driver_sdspi freertos)
if(CONFIG_CALI_DISPLAY)
    include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
endif()
