/* ap_identity_cli.c — host driver for ap_identity.c (tests/firmware/test_ap_identity.py ONLY).
 * Usage: ap_identity_cli KV_DIR [again]. Prints "ssid=<ssid> psk=<psk> qr=<qr text> rc=<code>"; exit 1
 * on failure. "again": erase the stored password, ask a second time and print that line too (the
 * second answer must come from the RAM copy). */
#include <stdio.h>

#include "cali_ap_identity.h"
#include "cali_platform.h"

static int show(void) {
    char ssid[NET_SSID_MAX + 1], psk[NET_PSK_MAX + 1], qr[128];
    int rc = cali_ap_identity(ssid, psk);

    if (rc < 0 || cali_ap_wifi_qr(qr, sizeof qr) < 0) return 1;
    printf("ssid=%s psk=%s qr=%s rc=%d\n", ssid, psk, qr, rc);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2 || cali_platform_init(argv[1]) != 0 || show() != 0) return 1;
    if (argc > 2) {
        cali_kv_erase(CALI_AP_KEY_PSK);
        return show();
    }
    return 0;
}
