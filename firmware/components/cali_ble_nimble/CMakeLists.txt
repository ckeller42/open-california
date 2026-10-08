# cali_transport_t on NimBLE + the bond store on cali_kv_* (NVS here). Compiled UNCHANGED against
# ESP-IDF's esp-nimble (firmware/README.md "Pins"); test/ is host-only.
idf_component_register(SRCS "ble_nimble.c" "ble_store_kv.c"
                       INCLUDE_DIRS "include"
                       REQUIRES cali_core
                       PRIV_REQUIRES bt platform csrc mbedtls)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
