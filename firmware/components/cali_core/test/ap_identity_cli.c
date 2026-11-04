/* ap_identity_cli.c — host driver for ap_identity.c (tests/firmware/test_ap_identity.py ONLY).
 * Usage: ap_identity_cli KV_DIR. Prints "ssid=<ssid> psk=<psk> qr=<qr text>", exit 1 on failure. */
#include <stdio.h>

#include "cali_ap_identity.h"
#include "cali_platform.h"

int main(int argc, char **argv) {
    char ssid[NET_SSID_MAX + 1], psk[NET_PSK_MAX + 1], qr[128];

    if (argc != 2 || cali_platform_init(argv[1]) != 0) return 1;
    if (cali_ap_identity(ssid, psk) != 0 || cali_ap_wifi_qr(qr, sizeof qr) < 0) return 1;
    printf("ssid=%s psk=%s qr=%s\n", ssid, psk, qr);
    return 0;
}
