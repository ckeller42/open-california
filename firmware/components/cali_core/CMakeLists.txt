# Platform-free core: pairing SM, WiFi SM + runtime, runner, session, console + snapshot, JSON writer, HTTP core,
# captive DNS, web endpoints. No NimBLE/ESP headers
# (test/ is host-only).
idf_component_register(SRCS "pairing_sm.c" "runner.c" "session.c" "console.c" "snapshot.c" "json.c" "wifi_sm.c" "wifi_run.c" "http_core.c" "captive_dns.c" "web.c"
                       INCLUDE_DIRS "include"
                       PRIV_INCLUDE_DIRS "../../web"
                       REQUIRES csrc
                       PRIV_REQUIRES platform)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
