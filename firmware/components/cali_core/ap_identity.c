/* ap_identity.c — the per-unit setup hotspot name and password. Contract: include/cali_ap_identity.h.
 * Proven by tests/firmware/test_ap_identity.py. */
#include "cali_ap_identity.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cali_platform.h"

#define ALPHABET "23456789abcdefghijkmnpqrstuvwxyz" /* 32 symbols: one random byte & 31 each */

/* xxxx-xxxx-xxxx over ALPHABET? */
static int psk_valid(const char *p, size_t n) {
    if (n != CALI_AP_PSK_LEN) return 0;
    for (size_t i = 0; i < n; i++) {
        if (i == 4 || i == 9) {
            if (p[i] != '-') return 0;
        } else if (p[i] == 0 || strchr(ALPHABET, p[i]) == NULL) {
            return 0;
        }
    }
    return 1;
}

int cali_ap_identity(char ssid[NET_SSID_MAX + 1], char psk[NET_PSK_MAX + 1]) {
    uint8_t mac[6], rnd[CALI_AP_PSK_LEN];
    char key[CALI_AP_PSK_LEN + 1];
    size_t n = sizeof key;

    if (cali_mac(mac) != 0) return -1;
    snprintf(ssid, NET_SSID_MAX + 1, NET_AP_SSID_PREFIX "%02X%02X", mac[4], mac[5]);
    if (cali_kv_get(CALI_AP_KEY_PSK, key, &n) == CALI_KV_OK && psk_valid(key, n)) {
        memcpy(psk, key, n);
        psk[n] = 0;
        return 0;
    }
    cali_random(rnd, sizeof rnd);
    for (size_t i = 0; i < CALI_AP_PSK_LEN; i++)
        psk[i] = (i == 4 || i == 9) ? '-' : ALPHABET[rnd[i] & 31];
    psk[CALI_AP_PSK_LEN] = 0;
    if (cali_kv_set(CALI_AP_KEY_PSK, psk, CALI_AP_PSK_LEN) != 0) return -1;
    return 0;
}

int cali_ap_wifi_qr(char *out, size_t cap) {
    char ssid[NET_SSID_MAX + 1], psk[NET_PSK_MAX + 1];
    int n;

    if (cali_ap_identity(ssid, psk) != 0) return -1;
    n = snprintf(out, cap, "WIFI:T:WPA;S:%s;P:%s;;", ssid, psk);
    return n < 0 || (size_t)n >= cap ? -1 : n;
}
