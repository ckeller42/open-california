# ESP-IDF project for the camper-unit satellite on the esp32s3 (M5Stack CoreS3), #154.
# Pinned ESP-IDF: see firmware/README.md "Pins" (CI: the firmware-build job's container).
# Components come from ./components (auto-discovered); firmware/host is the Linux host build and is
# not part of this project.
cmake_minimum_required(VERSION 3.22)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(cali_fw)

# Read-only firmware (#154): codec_encode is compiled out (components/csrc, -DCODEC_NO_ENCODE) and
# the link must never contain it -- the device twin of firmware/host/Makefile's cali-host guard.
add_custom_command(TARGET ${CMAKE_PROJECT_NAME}.elf POST_BUILD
    COMMAND sh -c "if ${CMAKE_NM} \"$<TARGET_FILE:${CMAKE_PROJECT_NAME}.elf>\" | grep -qw codec_encode; then echo 'cali_fw: codec_encode linked into read-only firmware' >&2; exit 1; fi"
    VERBATIM)

# The QEMU boot tier's probe (main/Kconfig.projbuild CONFIG_CALI_QEMU_PROBE: kvprobe console command,
# no BT controller init) must never ship: without the option, "kvprobe" must not appear anywhere in
# the ELF (code, strings or symbols).
if(NOT CONFIG_CALI_QEMU_PROBE)
    add_custom_command(TARGET ${CMAKE_PROJECT_NAME}.elf POST_BUILD
        COMMAND sh -c "if grep -aq kvprobe \"$<TARGET_FILE:${CMAKE_PROJECT_NAME}.elf>\"; then echo 'cali_fw: kvprobe (QEMU probe) linked into the release image' >&2; exit 1; fi"
        VERBATIM)
endif()
