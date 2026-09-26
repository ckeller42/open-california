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
OWN_INC := $(COMPONENTS)/platform/include $(COMPONENTS)/cali_ble_nimble/include
COMPONENT_OBJ := platform_host.o ble_store_kv.o
OWN_OBJ := spike_main.o $(COMPONENT_OBJ)
$(OWN_OBJ): CFLAGS += -Wall -Wextra -Werror $(addprefix -I, $(OWN_INC))
LIBS := $(NIMBLE_LDFLAGS) $(MBEDTLS_LIBS) -lrt -lpthread -lstdc++

.PHONY: all clean
all: cali-spike

clean:
	rm -f $(NIMBLE_ALL_OBJ) *.o cali-spike cali-host
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

ble_store_kv.o: $(COMPONENTS)/cali_ble_nimble/ble_store_kv.c $(COMPONENTS)/cali_ble_nimble/include/cali_ble_nimble.h \
		$(COMPONENTS)/platform/include/cali_platform.h syscfg/syscfg.h | $(MBEDTLS_LIBS)
	$(CC) -c $(CFLAGS) -o $@ $<

cali-spike: $(NIMBLE_ALL_OBJ) $(OWN_OBJ) $(MBEDTLS_LIBS)
	$(LD) -o $@ $(NIMBLE_ALL_OBJ) $(OWN_OBJ) $(LIBS)
