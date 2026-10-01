# ESP-only status display (#154). Never built by firmware/host (no LVGL there). With
# CONFIG_CALI_DISPLAY=n (the QEMU tier, qemu/sdkconfig.qemu) the component has no sources.
set(srcs "")
if(CONFIG_CALI_DISPLAY)
    set(srcs "display.c" "shot.c" "font_latin1_16.c" "font_latin1_24.c")
endif()
idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS "include"
                       REQUIRES cali_core
                       PRIV_REQUIRES platform esp_driver_i2c heap)
# A source-less component is an INTERFACE library: no compile options to set.
if(CONFIG_CALI_DISPLAY)
    include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
    # the lv_font_conv output (tools/gen_display_font.sh) includes "lvgl.h" only with this defined
    target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
endif()
