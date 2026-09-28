/* kv_cli.c — line-protocol driver for tests/firmware/test_platform_kv.py ONLY; not part of the ESP
 * build.
 *
 * Usage: kv_cli STORE_DIR, then lines on stdin:
 *   set KEY HEX    cali_kv_set (HEX "-" = zero-length value); prints OK | FAIL
 *   get KEY        cali_kv_get into a 256-byte buffer; prints the value as lowercase hex ("-" if
 *                  empty) | MISSING | CORRUPT
 *   erase          cali_kv_erase_all; prints OK | FAIL
 */
#include <stdio.h>
#include <string.h>

#include "cali_platform.h"

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

int main(int argc, char **argv) {
    static char line[8192];
    static unsigned char val[4096];

    if (argc != 2 || cali_platform_init(argv[1]) != 0) {
        fprintf(stderr, "usage: kv_cli STORE_DIR (a usable directory)\n");
        return 2;
    }
    while (fgets(line, sizeof line, stdin)) {
        char cmd[16], key[64], hex[sizeof line];
        int n = sscanf(line, "%15s %63s %8191s", cmd, key, hex);
        if (n < 1) continue;

        if (strcmp(cmd, "set") == 0 && n == 3) {
            size_t len = 0, hl = strlen(hex);
            int bad = strcmp(hex, "-") != 0 && (hl % 2 != 0 || hl / 2 > sizeof val);
            for (size_t i = 0; !bad && strcmp(hex, "-") != 0 && i < hl; i += 2) {
                int hi = hexval(hex[i]), lo = hexval(hex[i + 1]);
                if (hi < 0 || lo < 0) bad = 1;
                else val[len++] = (unsigned char)(hi << 4 | lo);
            }
            puts(!bad && cali_kv_set(key, val, len) == 0 ? "OK" : "FAIL");
        } else if (strcmp(cmd, "get") == 0 && n >= 2) {
            unsigned char out[256];
            size_t len = sizeof out;
            int rc = cali_kv_get(key, out, &len);
            if (rc == CALI_KV_MISSING) {
                puts("MISSING");
            } else if (rc != CALI_KV_OK) {
                puts("CORRUPT");
            } else if (len == 0) {
                puts("-");
            } else {
                for (size_t i = 0; i < len; i++) printf("%02x", out[i]);
                putchar('\n');
            }
        } else if (strcmp(cmd, "erase") == 0) {
            puts(cali_kv_erase_all() == 0 ? "OK" : "FAIL");
        } else {
            puts("ERR");
        }
        fflush(stdout);
    }
    return 0;
}
