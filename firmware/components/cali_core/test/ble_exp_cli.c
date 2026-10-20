/* ble_exp_cli.c — line-protocol driver for tests/firmware/test_ble_exp.py ONLY; not part of the ESP build.
 * stdin, one line each:
 *   start <min> <from> <to>            cali_bx_start
 *   stop                               cali_bx_stop
 *   act <now_ms>                       cali_bx_activity
 *   step <now_ms> <hour> <mode> <busy> cali_bx_step -> "OUT set=<n> probe=<0|1> done=<n> aborted=<0|1> started=<n>"
 *   win <hour> <from> <to>             cali_bx_in_window -> "WIN <0|1>"
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_ble_exp.h"

int main(void) {
    char line[128];
    cali_bx_t x;
    memset(&x, 0, sizeof x);
    while (fgets(line, sizeof line, stdin)) {
        unsigned u;
        int a, b, c, d;
        long long now;
        if (sscanf(line, "start %u %d %d", &u, &a, &b) == 3) {
            cali_bx_start(&x, u, a, b);
        } else if (strncmp(line, "stop", 4) == 0) {
            cali_bx_stop(&x);
        } else if (sscanf(line, "act %lld", &now) == 1) {
            cali_bx_activity(&x, (uint64_t)now);
        } else if (sscanf(line, "step %lld %d %d %d", &now, &a, &b, &c) == 4) {
            cali_bx_out_t o = cali_bx_step(&x, (uint64_t)now, a, b, c);
            printf("OUT set=%d probe=%d done=%d aborted=%d started=%d\n", o.set_mode, o.probe, o.done_mode, o.aborted,
                   o.started);
        } else if (sscanf(line, "win %d %d %d", &a, &b, &d) == 3) {
            printf("WIN %d\n", cali_bx_in_window(a, b, d));
        } else {
            printf("ERR %s", line);
        }
    }
    return 0;
}
