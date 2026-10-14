/* sensors_cli.c — line-protocol driver for sensors.c, tests/firmware/test_sensors.py ONLY; not part of
 * the ESP build. argv[1] = the kv store directory (platform_host.c: the sens_list blob survives the
 * process = a reboot). stdin, one command per line; every command answers "OK <rc>" unless noted:
 *   init                                     cali_sensors_init()
 *   now <ms>                                 the clock adv uses (starts at 0)
 *   adv <addr> <addr_type> <rssi> <name> <mfg hex>   a CALI_TEV_ADV at the clock
 *   add <id> <alias|-> <keyhex|->            cali_sensors_add ("-" = NULL) at the clock
 *   remove <id> | rename <id> <alias>
 *   kick                                     cali_sensors_kick()
 *   busy 0|1                                 1: the fake transport refuses windows (prints nothing)
 *   tick <now_ms> <enabled> <pairing>        cali_sensors_tick on a fake transport: "WINDOW <ms>" per window
 *   state <now_ms>                           "EXT " + the ext object
 *   api <now_ms>                             "API " + the /api/sensors object
 *   maxbody                                  "MAXBODY <len>"
 * Every ext record reaches the diag sink, which prints "REC <body>". The AES hook is a stub whose
 * keystream block is the key itself (a Victron vector's "ciphertext" = plaintext XOR key). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_diag.h"
#include "cali_platform.h"
#include "cali_sensors.h"

static int rec(const char *body) {
    printf("REC %s\n", body);
    return 0;
}

static int stub_aes(const uint8_t key[16], const uint8_t in[16], uint8_t out[16]) {
    (void)in;
    memcpy(out, key, 16);
    return 0;
}

static int s_busy;
static int window(uint32_t ms) {
    if (s_busy) return -1;
    printf("WINDOW %u\n", (unsigned)ms);
    return 0;
}

/* cali_sensors_api_json / _state_json as one line behind prefix. */
static void print_json(const char *prefix, void (*fn)(cali_json_t *, uint64_t), uint64_t now) {
    char buf[4096];
    cali_json_t j;
    cali_json_begin(&j, buf, sizeof buf);
    fn(&j, now);
    if (cali_json_end(&j) < 0) printf("%sOVERFLOW\n", prefix);
    else printf("%s%s\n", prefix, buf);
}

int main(int argc, char **argv) {
    static cali_transport_t t;
    char line[512];
    uint64_t now = 0;
    if (argc < 2 || cali_platform_init(argv[1]) != 0) return 2;
    setvbuf(stdout, NULL, _IOLBF, 0);
    cali_diag_sink = rec;
    t.scan_window = window;
    cali_sensors_set_aes(stub_aes);
    while (fgets(line, sizeof line, stdin)) {
        char cmd[16], a[4][128];
        int n = sscanf(line, "%15s %127s %127s %127s %127s", cmd, a[0], a[1], a[2], a[3]);
        if (n < 1) continue;
        if (!strcmp(cmd, "init")) {
            cali_sensors_init();
            printf("OK 0\n");
        } else if (!strcmp(cmd, "now") && n == 2) {
            now = strtoull(a[0], NULL, 10);
            printf("OK 0\n");
        } else if (!strcmp(cmd, "adv") && n == 5) {
            char hex[128];
            uint8_t mfg[64];
            size_t len = 0;
            cali_tevent_t e;
            memset(&e, 0, sizeof e);
            /* the 6th token (mfg hex) did not fit the sscanf above */
            if (sscanf(line, "%*s %*s %*s %*s %*s %127s", hex) != 1) continue;
            while (len < sizeof mfg && hex[2 * len] && sscanf(hex + 2 * len, "%2hhx", &mfg[len]) == 1) len++;
            e.ev = CALI_TEV_ADV;
            snprintf(e.addr, sizeof e.addr, "%.17s", a[0]);
            e.status = atoi(a[1]);
            e.rssi = (int8_t)atoi(a[2]);
            snprintf(e.name, sizeof e.name, "%.23s", a[3]);
            e.data = mfg;
            e.len = len;
            cali_sensors_on_adv(&e, now);
            printf("OK 0\n");
        } else if (!strcmp(cmd, "add") && n == 4) {
            printf("OK %d\n", cali_sensors_add((uint16_t)atoi(a[0]), strcmp(a[1], "-") ? a[1] : NULL,
                                               strcmp(a[2], "-") ? a[2] : NULL, now));
        } else if (!strcmp(cmd, "remove") && n == 2) {
            printf("OK %d\n", cali_sensors_remove((uint16_t)atoi(a[0])));
        } else if (!strcmp(cmd, "rename") && n == 3) {
            printf("OK %d\n", cali_sensors_rename((uint16_t)atoi(a[0]), a[1]));
        } else if (!strcmp(cmd, "kick")) {
            cali_sensors_kick();
            printf("OK 0\n");
        } else if (!strcmp(cmd, "busy") && n == 2) {
            s_busy = atoi(a[0]);
            printf("OK 0\n");
        } else if (!strcmp(cmd, "tick") && n == 4) {
            cali_sensors_tick(strtoull(a[0], NULL, 10), &t, atoi(a[1]), atoi(a[2]));
            printf("OK 0\n");
        } else if (!strcmp(cmd, "state") && n == 2) {
            print_json("EXT ", cali_sensors_state_json, strtoull(a[0], NULL, 10));
        } else if (!strcmp(cmd, "api") && n == 2) {
            print_json("API ", cali_sensors_api_json, strtoull(a[0], NULL, 10));
        } else if (!strcmp(cmd, "maxbody")) {
            printf("MAXBODY %d\n", cali_sensors_max_body_len());
        } else {
            printf("UNKNOWN %s\n", cmd);
        }
    }
    return 0;
}
