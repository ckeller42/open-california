/* lora_setup_cli.c — host driver for lora_setup.c (R_FW_LORA_SETUP), one command per stdin line:
 *   keypack <key> | keyunpack <hex> | setupqr <deveui hex> <appkey hex> <tk hex> | repairqr <plain hex> |
 *   xfer <tk hex> <deveui hex> <fragment hex> | sha4 <hex or ->
 * Output: keypack/keyunpack/setupqr/repairqr print the result (hex or text) or "-1"/"0"; xfer prints
 *   "<rc> <confirm hex or -> <plain hex or ->" and keeps the transfer state between lines.
 * The stub "open" XORs the ciphertext with the key; the tag 00..00 is valid, any other is not.
 * Driven by tests/firmware/test_lora_setup.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_lora_setup.h"

static size_t unhex(const char *s, uint8_t *out, size_t max) {
    size_t n = 0;
    if (!strcmp(s, "-")) return 0;
    for (; s[0] && s[1] && n < max; s += 2) {
        char t[3] = {s[0], s[1], 0};
        out[n++] = (uint8_t)strtoul(t, NULL, 16);
    }
    return n;
}
static void hex(const uint8_t *b, size_t n) {
    if (!n) putchar('-');
    for (size_t i = 0; i < n; i++) printf("%02x", b[i]);
}

static int stub_open(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len, const uint8_t *ct,
                     size_t ct_len, const uint8_t *tag, uint8_t *out) {
    (void)nonce, (void)ad, (void)ad_len;
    for (size_t i = 0; i < 16; i++)
        if (tag[i]) return -1;
    for (size_t i = 0; i < ct_len; i++) out[i] = ct[i] ^ key[i % 16];
    return 0;
}

int main(void) {
    static cali_lora_xfer_t x;
    char line[2048], a[1024], b[1024], c[1024];
    while (fgets(line, sizeof line, stdin)) {
        uint8_t u[512], v[512], w[512];
        if (sscanf(line, "keypack %1023s", a) == 1) {
            int r = cali_lora_key_pack(a, u);
            if (r) puts("-1");
            else hex(u, CALI_LORA_KEY_PACKED), putchar('\n');
        } else if (sscanf(line, "keyunpack %1023s", a) == 1) {
            char k[99];
            if (unhex(a, u, sizeof u) != CALI_LORA_KEY_PACKED || cali_lora_key_unpack(u, k)) puts("-1");
            else puts(k);
        } else if (sscanf(line, "setupqr %1023s %1023s %1023s", a, b, c) == 3) {
            uint8_t d[8], k[16], t[16];
            char q[200];
            unhex(a, d, 8), unhex(b, k, 16), unhex(c, t, 16);
            size_t n = cali_lora_setup_qr(q, sizeof q, d, k, t);
            printf("%zu %s\n", n, q);
        } else if (sscanf(line, "repairqr %1023s", a) == 1) {
            char q[600];
            size_t n = unhex(a, u, sizeof u);
            n = cali_lora_repair_qr(q, sizeof q, u, n);
            printf("%zu %s\n", n, n ? q : "-");
        } else if (sscanf(line, "xfer %1023s %1023s %1023s", a, b, c) == 3) {
            uint8_t tk[16], de[8], pl[CALI_LORA_XFER_MAX], cf[6];
            size_t pn = 0, cn = 0;
            unhex(a, tk, 16), unhex(b, de, 8);
            size_t fn = unhex(c, v, sizeof v);
            int r = cali_lora_xfer_rx(&x, v, fn, tk, de, stub_open, pl, &pn, cf, &cn);
            printf("%d ", r);
            hex(cf, cn), putchar(' '), hex(pl, pn), putchar('\n');
        } else if (sscanf(line, "sha4 %1023s", a) == 1) {
            cali_lora_sha256_4(u, unhex(a, u, sizeof u), w);
            hex(w, 4), putchar('\n');
        }
    }
    return 0;
}
