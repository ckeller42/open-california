# Host build of the firmware against the upstream NimBLE Linux port (porting/examples/linux),
# talking HCI over TCP (NimBLE socket transport, BLE_SOCK_TYPE linux_tcp) to a Bumble virtual
# controller. Linux-only: the NPL linux port uses pthread/rt. Fetch the deps first: ./fetch_nimble.sh
#
# NimBLE's porting Makefile.defs forces -m32 ("places in NimBLE assume 4-byte pointers"; the ESP32-S3
# is 32-bit too), so the build needs a 32-bit toolchain: gcc-multilib + g++-multilib on x86_64, or
# CROSS_COMPILE=i686-linux-gnu- elsewhere. A 32-bit system libmbedtls is rarely installable, so Mbed
# TLS is built here from source (static, -m32) — the same approach as upstream NimBLE's port CI.
# See firmware/README.md "Host build notes".

CROSS_COMPILE ?=
CC      := $(CROSS_COMPILE)gcc
CXX     := $(CROSS_COMPILE)g++
LD      := $(CROSS_COMPILE)gcc
AR      := $(CROSS_COMPILE)ar
NM      := $(CROSS_COMPILE)nm

NIMBLE_ROOT := _deps/nimble
NIMBLE_CFG_MBEDTLS := 1

MBEDTLS_ROOT := _deps/mbedtls
MBEDTLS_LIBS := $(MBEDTLS_ROOT)/library/libmbedtls.a $(MBEDTLS_ROOT)/library/libmbedx509.a \
	$(MBEDTLS_ROOT)/library/libmbedcrypto.a

# Skip files that don't build for this port (same list as the upstream linux example)
NIMBLE_IGNORE := $(NIMBLE_ROOT)/porting/nimble/src/hal_timer.c \
	$(NIMBLE_ROOT)/porting/nimble/src/os_cputime.c \
	$(NIMBLE_ROOT)/porting/nimble/src/os_cputime_pwr2.c \
	$(NULL)

include $(NIMBLE_ROOT)/porting/nimble/Makefile.defs

SRC := $(NIMBLE_SRC) \
	$(wildcard $(NIMBLE_ROOT)/porting/npl/linux/src/*.c) \
	$(wildcard $(NIMBLE_ROOT)/porting/npl/linux/src/*.cc) \
	$(wildcard $(NIMBLE_ROOT)/nimble/transport/socket/src/*.c) \
	$(NULL)

# NimBLE includes "syscfg/syscfg.h", so the dir holding our syscfg/ goes BEFORE the example's include
# dir: ./syscfg/syscfg.h shadows the example's (and porting/nimble's) copy.
INC = \
	. \
	$(NIMBLE_ROOT)/porting/examples/linux/include \
	$(NIMBLE_ROOT)/porting/npl/linux/include \
	$(NIMBLE_ROOT)/nimble/transport/socket/include \
	$(NIMBLE_INCLUDE) \
	$(MBEDTLS_ROOT)/include \
	$(NULL)

INCLUDES := $(addprefix -I, $(INC))

SRC_C  = $(filter %.c,  $(SRC))
SRC_CC = $(filter %.cc, $(SRC))
NIMBLE_ALL_OBJ := $(SRC_C:.c=.o) $(SRC_CC:.cc=.o)

CFLAGS = $(NIMBLE_CFLAGS) $(INCLUDES) -g -D_GNU_SOURCE

# OUR sources (this repo's C under firmware/) build warning-clean; fetched NimBLE/Mbed TLS do not.
# Component sources live under ../components; their objects land here (gitignored firmware/host/*.o).
COMPONENTS := ../components
CSRC := ../../csrc
OWN_INC := $(COMPONENTS)/platform/include $(COMPONENTS)/cali_ble_nimble/include \
	$(COMPONENTS)/cali_core/include $(CSRC)
COMPONENT_OBJ := platform_host.o net_host.o ble_store_kv.o ble_nimble.o runner.o pairing_sm.o
# The rest of cali_core (console + json + session + wifi_sm + http_core) and the frame codec: only cali-host needs them.
CORE_OBJ := console.o json.o session.o codec.o wifi_sm.o http_core.o
OWN_OBJ := host_main.o store_cli.o ble_nimble_jw.o $(COMPONENT_OBJ) $(CORE_OBJ)
$(OWN_OBJ): CFLAGS += -Wall -Wextra -Werror $(addprefix -I, $(OWN_INC))
LIBS := $(NIMBLE_LDFLAGS) $(MBEDTLS_LIBS) -lrt -lpthread -lstdc++

.PHONY: all clean
all: cali-host store-cli

clean:
	rm -f $(NIMBLE_ALL_OBJ) *.o cali-host cali-host-jw store-cli
	-$(MAKE) -C $(MBEDTLS_ROOT)/library clean >/dev/null

$(MBEDTLS_LIBS) &:
	$(MAKE) -C $(MBEDTLS_ROOT)/library CC=$(CC) AR=$(AR) CFLAGS="-m32 -O2" \
		libmbedtls.a libmbedx509.a libmbedcrypto.a

# Every object depends on our syscfg: changing it must rebuild NimBLE, not just our code. The Mbed TLS
# headers come with the library, so build it first.
%.o: %.c syscfg/syscfg.h | $(MBEDTLS_LIBS)
	$(CC) -c $(CFLAGS) -o $@ $<

%.o: %.cc syscfg/syscfg.h
	$(CXX) -c $(CFLAGS) -o $@ $<

platform_host.o: $(COMPONENTS)/platform/host/platform_host.c $(COMPONENTS)/platform/include/cali_platform.h
	$(CC) -c $(CFLAGS) -o $@ $<

# cali_net on the host (#154): POSIX sockets on 127.0.0.1 + the scripted fake WiFi (--fake-wifi).
net_host.o: $(COMPONENTS)/platform/host/net_host.c $(COMPONENTS)/platform/include/cali_net_host.h \
		$(COMPONENTS)/cali_core/include/cali_net.h $(CSRC)/net_consts.h \
		$(COMPONENTS)/platform/include/cali_platform.h
	$(CC) -c $(CFLAGS) -o $@ $<

store_cli.o: $(COMPONENTS)/cali_ble_nimble/test/store_cli.c $(COMPONENTS)/cali_ble_nimble/include/cali_ble_nimble.h \
		$(COMPONENTS)/cali_core/include/cali_transport.h \
		$(COMPONENTS)/platform/include/cali_platform.h syscfg/syscfg.h | $(MBEDTLS_LIBS)
	$(CC) -c $(CFLAGS) -o $@ $<

# The NimBLE transport (cali_transport_t) — exercised end to end by cali-host (test_host_e2e.py).
ble_nimble.o: $(COMPONENTS)/cali_ble_nimble/ble_nimble.c $(COMPONENTS)/cali_ble_nimble/include/cali_ble_nimble.h \
		$(COMPONENTS)/cali_core/include/cali_transport.h $(CSRC)/codec_chars.h \
		$(COMPONENTS)/platform/include/cali_platform.h syscfg/syscfg.h | $(MBEDTLS_LIBS)
	$(CC) -c $(CFLAGS) -o $@ $<

# cali_core: no NimBLE headers; built with the same flags so the -m32 -Werror build covers it.
runner.o: $(COMPONENTS)/cali_core/runner.c $(COMPONENTS)/cali_core/include/cali_runner.h \
		$(COMPONENTS)/cali_core/include/cali_transport.h $(COMPONENTS)/cali_core/include/cali_pairing_sm.h \
		$(CSRC)/codec_chars.h $(CSRC)/pairing_consts.h
	$(CC) -c $(CFLAGS) -o $@ $<

pairing_sm.o: $(COMPONENTS)/cali_core/pairing_sm.c $(COMPONENTS)/cali_core/include/cali_pairing_sm.h \
		$(CSRC)/pairing_consts.h
	$(CC) -c $(CFLAGS) -o $@ $<

ble_store_kv.o: $(COMPONENTS)/cali_ble_nimble/ble_store_kv.c $(COMPONENTS)/cali_ble_nimble/include/cali_ble_nimble.h \
		$(COMPONENTS)/platform/include/cali_platform.h syscfg/syscfg.h | $(MBEDTLS_LIBS)
	$(CC) -c $(CFLAGS) -o $@ $<

CORE_H := $(COMPONENTS)/cali_core/include
console.o: $(COMPONENTS)/cali_core/console.c $(CORE_H)/cali_console.h $(CORE_H)/cali_session.h \
		$(CORE_H)/cali_runner.h $(CORE_H)/cali_transport.h $(CORE_H)/cali_pairing_sm.h \
		$(CORE_H)/cali_json.h $(CSRC)/codec.h $(CSRC)/codec_chars.h $(CSRC)/pairing_consts.h \
		$(COMPONENTS)/platform/include/cali_platform.h
	$(CC) -c $(CFLAGS) -o $@ $<

# The bounded JSON writer shared by console.c (STATE/SNAP) and, from Task 2 on, the web setup page.
json.o: $(COMPONENTS)/cali_core/json.c $(CORE_H)/cali_json.h
	$(CC) -c $(CFLAGS) -o $@ $<

# The WiFi provisioning/connectivity SM (#154 Task 3): pure, replays tests/vectors/wifi_sm.json.
wifi_sm.o: $(COMPONENTS)/cali_core/wifi_sm.c $(CORE_H)/cali_wifi_sm.h $(CSRC)/net_consts.h
	$(CC) -c $(CFLAGS) -o $@ $<

# The single-connection HTTP/1.1 responder over cali_net (#154 Task 4): tests/firmware/test_http_core.py.
http_core.o: $(COMPONENTS)/cali_core/http_core.c $(CORE_H)/cali_http.h $(CORE_H)/cali_net.h $(CSRC)/net_consts.h
	$(CC) -c $(CFLAGS) -o $@ $<

session.o: $(COMPONENTS)/cali_core/session.c $(CORE_H)/cali_session.h $(CORE_H)/cali_console.h \
		$(CORE_H)/cali_runner.h $(CORE_H)/cali_transport.h $(CSRC)/codec.h $(CSRC)/codec_chars.h \
		$(COMPONENTS)/platform/include/cali_platform.h
	$(CC) -c $(CFLAGS) -o $@ $<

# Read-only firmware (#154): codec_encode is compiled out of the firmware's codec (and its
# prototype hidden), and the link below fails if the symbol ever reaches cali-host.
codec.o: CFLAGS += -DCODEC_NO_ENCODE
codec.o: $(CSRC)/codec.c $(CSRC)/codec.h $(CSRC)/codec_dict.h
	$(CC) -c $(CFLAGS) -o $@ $<

host_main.o: host_main.c $(COMPONENTS)/cali_ble_nimble/include/cali_ble_nimble.h $(CORE_H)/cali_console.h \
		$(CORE_H)/cali_session.h $(CORE_H)/cali_runner.h $(CORE_H)/cali_transport.h \
		$(COMPONENTS)/platform/include/cali_platform.h syscfg/syscfg.h | $(MBEDTLS_LIBS)
	$(CC) -c $(CFLAGS) -o $@ $<

# The host firmware: console + runner + session on the NimBLE transport, HCI over TCP.
cali-host: $(NIMBLE_ALL_OBJ) host_main.o $(COMPONENT_OBJ) $(CORE_OBJ) $(MBEDTLS_LIBS)
	$(LD) -o $@ $(NIMBLE_ALL_OBJ) host_main.o $(COMPONENT_OBJ) $(CORE_OBJ) $(LIBS)
	@if $(NM) $@ | grep -qw codec_encode; then echo "cali-host: codec_encode linked into read-only firmware" >&2; rm -f $@; exit 1; fi

# Just Works regression build (tests/firmware/test_host_e2e.py::test_just_works_build_is_refused):
# cali-host with the transport compiled -DCALI_TEST_LATE_IO_CAP, which sets the passkey IO capability
# only after the Pairing Request left (the 2026-09-26 calictl bug). ble_nimble.c refuses the macro
# in an ESP-IDF build. Everything else is cali-host's objects.
JW_OBJ := $(filter-out ble_nimble.o,$(COMPONENT_OBJ)) ble_nimble_jw.o
ble_nimble_jw.o: CFLAGS += -DCALI_TEST_LATE_IO_CAP
ble_nimble_jw.o: $(COMPONENTS)/cali_ble_nimble/ble_nimble.c $(COMPONENTS)/cali_ble_nimble/include/cali_ble_nimble.h \
		$(COMPONENTS)/cali_core/include/cali_transport.h $(CSRC)/codec_chars.h \
		$(COMPONENTS)/platform/include/cali_platform.h syscfg/syscfg.h | $(MBEDTLS_LIBS)
	$(CC) -c $(CFLAGS) -o $@ $<

cali-host-jw: $(NIMBLE_ALL_OBJ) host_main.o $(JW_OBJ) $(CORE_OBJ) $(MBEDTLS_LIBS)
	$(LD) -o $@ $(NIMBLE_ALL_OBJ) host_main.o $(JW_OBJ) $(CORE_OBJ) $(LIBS)

# Test driver for the NimBLE bond store (tests/firmware/test_ble_store_kv.py): the store callbacks
# linked against the real NimBLE host objects, no controller/transport needed.
store-cli: $(NIMBLE_ALL_OBJ) store_cli.o $(COMPONENT_OBJ) $(MBEDTLS_LIBS)
	$(LD) -o $@ $(NIMBLE_ALL_OBJ) store_cli.o $(COMPONENT_OBJ) $(LIBS)
