/* time_cli.c — line-protocol driver for tests/firmware/test_time.py ONLY; not part of the ESP build.
 * Links time.c + json.c + the real host platform (platform_host.c: the kv store is files under
 * argv[1], so the boot counter persists across runs like NVS across boots).
 *
 * stdin, one line each:
 *   init          cali_time_init -> "BOOT <n>"
 *   sntp          cali_time_source(fake clock)
 *   clock <ms>    what the fake clock answers (default 0)
 *   synced        cali_time_synced()
 *   ts            "TS <epoch ms>" | "TS none"
 *   json          "JSON <the device.time object>"
 * cali_log prints "LOG <text>".
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
            if (cali_time_ts_ms(&ts) == 0) printf("TS %lld\n", (long long)ts);
            else printf("TS none\n");
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
