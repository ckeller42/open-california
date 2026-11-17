# The remote hub's MQTT client (hub_esp.c): the port under cali_core's hub_run.c (cali_hub.h). Board package
# only, like cali_lora's lora_esp.c: with CONFIG_CALI_DISPLAY=n (the QEMU tier) the component has no sources
# and cali_core links hub_port_none.c instead. Never built by firmware/host.
set(srcs "")
set(frags "")
if(CONFIG_CALI_DISPLAY)
    set(srcs "hub_esp.c")
    set(frags "linker.lf")
endif()
idf_component_register(SRCS ${srcs}
                       PRIV_REQUIRES cali_core cali_lora platform mqtt esp-tls mbedtls freertos heap
                       LDFRAGMENTS ${frags}
                       WHOLE_ARCHIVE)   # nothing calls hub_esp.c by name: only cali_core's hub_run.c, which links later
# A source-less component is an INTERFACE library: no compile options to set.
if(CONFIG_CALI_DISPLAY)
    include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
endif()
