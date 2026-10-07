# ESP-IDF project for the camper-unit satellite on the esp32s3 (M5Stack CoreS3), #154.
# Pinned ESP-IDF: see firmware/README.md "Pins" (CI: the firmware-build job's container).
# Components come from ./components (auto-discovered); firmware/host is the Linux host build and is
# not part of this project.
cmake_minimum_required(VERSION 3.22)
# The firmware version (esp_app_desc_t.version -> cali_fw_version() -> /api/state device.fw): the
# repo's `git describe --always --tags --dirty` at configure time (32 bytes max), e.g. "e281649" or
# "e281649-dirty". ESP-IDF's own fallback runs the same describe but gives "1" when git refuses the
# checkout (a container's "dubious ownership"), hence safe.directory; no git at all -> "unknown".
if(NOT DEFINED PROJECT_VER)
    execute_process(COMMAND git -c safe.directory=* describe --always --tags --dirty
                    WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
                    OUTPUT_VARIABLE PROJECT_VER OUTPUT_STRIP_TRAILING_WHITESPACE
                    RESULT_VARIABLE cali_git_rc ERROR_QUIET)
    if(NOT cali_git_rc EQUAL 0 OR PROJECT_VER STREQUAL "")
        set(PROJECT_VER "unknown")
    endif()
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(cali_fw)

# The QEMU boot tier's probe (main/Kconfig.projbuild CONFIG_CALI_QEMU_PROBE: kvprobe console command,
# no BT controller init) must never ship: without the option, "kvprobe" must not appear anywhere in
# the ELF (code, strings or symbols).
if(NOT CONFIG_CALI_QEMU_PROBE)
    add_custom_command(TARGET ${CMAKE_PROJECT_NAME}.elf POST_BUILD
        COMMAND sh -c "if grep -aq kvprobe \"$<TARGET_FILE:${CMAKE_PROJECT_NAME}.elf>\"; then echo 'cali_fw: kvprobe (QEMU probe) linked into the release image' >&2; exit 1; fi"
        VERBATIM)
endif()
