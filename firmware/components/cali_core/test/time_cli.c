/* time_cli.c — line-protocol driver for tests/firmware/test_time.py ONLY; not part of the ESP build.
 * Links time.c + json.c + csrc/codec.c + the real host platform (platform_host.c: the kv store is files under
 * argv[1], so the boot counter persists across runs like NVS across boots).
 *
 * stdin, one line each:
 *   init          cali_time_init -> "BOOT <n>"
 *   sntp          cali_time_source(fake clock)
 *   clock <ms>    what the fake clock answers (default 0)
 *   synced        cali_time_synced()
 *   ts            "TS <epoch ms> ntp|unit" | "TS none"
 *   vehicle <hex> cali_time_on_vehicle(the 1004 frame)
 *   utc Y M D h m s   cali_time_unit_utc -> "UTC <epoch ms>" | "UTC none"
 *   sleep <ms>    let the uptime clock run
 *   json          "JSON <the device.time object>"
 * cali_log prints "LOG <text>".
 */
#define _POSIX_C_SOURCE 200809L   /* nanosleep under -std=c99 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "cali_json.h"
#include "cali_platform.h"
#include "cali_time.h"

static int64_t s_clock;
static int64_t fake_clock(void) { return s_clock; }

int main(int argc, char **argv) {
    char line[128], buf[256];
    if (argc < 2 || cali_platform_init(argv[1]) != 0) return 2;
    setvbuf(stdout, NULL, _IOLBF, 0);
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\r\n")] = 0;
        if (strcmp(line, "init") == 0) {
            printf("BOOT %lu\n", (unsigned long)cali_time_init());
        } else if (strcmp(line, "sntp") == 0) {
            cali_time_source(fake_clock);
        } else if (strncmp(line, "clock ", 6) == 0) {
            s_clock = strtoll(line + 6, NULL, 10);
        } else if (strcmp(line, "synced") == 0) {
            cali_time_synced();
        } else if (strcmp(line, "ts") == 0) {
            int64_t ts;
            int src = cali_time_ts(&ts);
            if (src == CALI_TSRC_NONE) printf("TS none\n");
            else printf("TS %lld %s\n", (long long)ts, src == CALI_TSRC_NTP ? "ntp" : "unit");
        } else if (strncmp(line, "vehicle ", 8) == 0) {
            uint8_t f[32];
            size_t n = 0;
            for (const char *h = line + 8; n < sizeof f && h[0] && h[1]; h += 2) {
                unsigned b;
                if (sscanf(h, "%2x", &b) != 1) break;
                f[n++] = (uint8_t)b;
            }
            cali_time_on_vehicle(f, n);
        } else if (strncmp(line, "utc ", 4) == 0) {
            int v[6];
            int64_t ms;
            if (sscanf(line + 4, "%d %d %d %d %d %d", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) == 6 &&
                cali_time_unit_utc(v[0], v[1], v[2], v[3], v[4], v[5], &ms) == 0)
                printf("UTC %lld\n", (long long)ms);
            else
                printf("UTC none\n");
        } else if (strncmp(line, "sleep ", 6) == 0) {
            long ms = strtol(line + 6, NULL, 10);
            struct timespec d = {ms / 1000, (ms % 1000) * 1000000L};
            nanosleep(&d, NULL);
        } else if (strcmp(line, "json") == 0) {
            cali_json_t j;
            cali_json_begin(&j, buf, sizeof buf);
            cali_json_key(&j, "time");
            cali_time_json(&j);
            if (cali_json_end(&j) < 0) return 3;
            printf("JSON %s\n", buf);
        } else {
            printf("ERR %s\n", line);
        }
    }
    return 0;
}
