/* ota_cli.c — line-protocol driver for tests/firmware/test_ota.py ONLY; not part of the ESP build.
 * Links ota.c + json.c with a scripted fake fetcher/slot (cali_ota_ops_t).
 *
 * stdin, one line each:
 *   fw <version>        what cali_fw_version() answers (default "abc1234")
 *   manifest <text>|-   the manifest body ops->get returns ("-": the GET fails)
 *   download ok|fail    ops->download's result (default ok; it reports progress 50 % then 100 %)
 *   slot <64 hex>|fail  the SHA-256 ops->slot_sha256 reports (default all zero)
 *   commit ok|fail      ops->commit's result (default ok)
 *   noinit              cali_ota_init(NULL) (default: the fake ops)
 *   check | install     cali_ota_request -> "REQ <rc>"
 *   work                cali_ota_work()
 *   json                "JSON <the device.ota object>"
 * ops calls print "CALL get <url>", "CALL download <url> <size>", "CALL slot_sha256 <size>",
 * "CALL commit", "CALL abort", "CALL restart", "CALL wake"; cali_log prints "LOG <text>".
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_json.h"
#include "cali_ota.h"
#include "cali_platform.h"

static char s_fw[64] = "abc1234";
static char s_manifest[1024];
static int s_get_fail, s_download_fail, s_slot_fail, s_commit_fail;
static uint8_t s_slot[32];

const char *cali_fw_version(void) { return s_fw; }
void cali_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    printf("LOG ");
    vprintf(fmt, ap);
    printf("\n");
    va_end(ap);
}

static int f_get(const char *url, char *buf, size_t cap) {
    size_t n = strlen(s_manifest);
    printf("CALL get %s\n", url);
    if (s_get_fail || n >= cap) return -1;
    memcpy(buf, s_manifest, n + 1);
    return (int)n;
}
static int f_download(const char *url, size_t size) {
    printf("CALL download %s %lu\n", url, (unsigned long)size);
    cali_ota_progress(size / 2, size);
    if (s_download_fail) return -1;
    cali_ota_progress(size, size);
    return 0;
}
static int f_slot(size_t size, uint8_t out[32]) {
    printf("CALL slot_sha256 %lu\n", (unsigned long)size);
    memcpy(out, s_slot, 32);
    return s_slot_fail ? -1 : 0;
}
static int f_commit(void) { printf("CALL commit\n"); return s_commit_fail ? -1 : 0; }
static void f_abort(void) { printf("CALL abort\n"); }
static void f_restart(void) { printf("CALL restart\n"); }
static void f_wake(void) { printf("CALL wake\n"); }

static const cali_ota_ops_t fake_ops = {
    .get = f_get, .download = f_download, .slot_sha256 = f_slot, .commit = f_commit,
    .abort = f_abort, .restart = f_restart, .wake = f_wake,
};

int main(void) {
    char line[1200];
    cali_ota_init(&fake_ops);
    while (fgets(line, sizeof line, stdin)) {
        char *arg;
        line[strcspn(line, "\n")] = 0;
        arg = strchr(line, ' ');
        arg = arg ? arg + 1 : line + strlen(line);
        if (strncmp(line, "fw ", 3) == 0) snprintf(s_fw, sizeof s_fw, "%s", arg);
        else if (strncmp(line, "manifest ", 9) == 0) {
            s_get_fail = strcmp(arg, "-") == 0;
            snprintf(s_manifest, sizeof s_manifest, "%s", arg);
        } else if (strncmp(line, "download ", 9) == 0) s_download_fail = strcmp(arg, "fail") == 0;
        else if (strncmp(line, "commit ", 7) == 0) s_commit_fail = strcmp(arg, "fail") == 0;
        else if (strncmp(line, "slot ", 5) == 0) {
            s_slot_fail = strcmp(arg, "fail") == 0;
            for (int i = 0; !s_slot_fail && i < 32; i++) {
                unsigned b = 0;
                (void)sscanf(arg + 2 * i, "%2x", &b);
                s_slot[i] = (uint8_t)b;
            }
        } else if (strcmp(line, "noinit") == 0) cali_ota_init(NULL);
        else if (strcmp(line, "check") == 0 || strcmp(line, "install") == 0)
            printf("REQ %d\n", cali_ota_request(line[0] == 'i'));
        else if (strcmp(line, "work") == 0) cali_ota_work();
        else if (strcmp(line, "json") == 0) {
            char buf[256];
            cali_json_t j;
            cali_json_begin(&j, buf, sizeof buf);
            cali_json_key(&j, "ota");
            cali_ota_json(&j);
            if (cali_json_end(&j) < 0) printf("JSON overflow\n");
            else printf("JSON %s\n", buf);
        } else printf("UNKNOWN %s\n", line);
    }
    return 0;
}
