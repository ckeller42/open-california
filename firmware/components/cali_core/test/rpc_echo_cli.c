/* rpc_echo_cli.c — host driver for tests/firmware/test_hub.py ONLY. Links hub_rpc.c with a stub router that
 * echoes the request body it receives as the response body, so a test sees the exact bytes (control
 * bytes included) that cali_hub_rpc handed to cali_web_handle. One request JSON per stdin line; prints
 * the answer. The stub clock has no valid time, so no "t" check runs. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cali_hub.h"
#include "cali_http.h"
#include "cali_platform.h"
#include "cali_time.h"
#include "cali_web.h"

uint64_t cali_uptime_ms(void) { return 0; }
int cali_time_ts(int64_t *out) {
    (void)out;
    return CALI_TSRC_NONE;
}
int cali_web_handle(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx) {
    (void)ctx;
    resp->body = req->body;
    resp->body_len = req->body_len;
    return 1;
}
static int pump(void) { return 0; }

int main(void) {
    static char line[8192], res[16384];
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\n")] = 0;
        (void)cali_hub_rpc(line, res, sizeof res, pump);
        printf("%s\n", res);
    }
    return 0;
}
