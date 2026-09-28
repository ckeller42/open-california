# Platform-free core: pairing SM, runner, session, console. No NimBLE/ESP headers (test/ is host-only).
idf_component_register(SRCS "pairing_sm.c" "runner.c" "session.c" "console.c"
                       INCLUDE_DIRS "include"
                       REQUIRES csrc
                       PRIV_REQUIRES platform)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
