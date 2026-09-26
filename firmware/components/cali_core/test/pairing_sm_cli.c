/* pairing_sm_cli.c — line-protocol driver for tests/firmware/test_pairing_sm_parity.py ONLY; not
 * part of the ESP build.
 *
 * Reads lines from stdin:
 *   S st att err   sets the state directly; prints nothing.
 *   E ev arg       calls cali_pair_step(); prints "st att err |" then " act:arg" per action.
 */
#include <stdio.h>

#include "cali_pairing_sm.h"

int main(void) {
    cali_pair_state_t ps = {0, 0, 0};
    char line[256];

    while (fgets(line, sizeof line, stdin)) {
        char tag;
        if (sscanf(line, " %c", &tag) != 1) continue;

        if (tag == 'S') {
            unsigned st, att, err;
            if (sscanf(line, " S %u %u %u", &st, &att, &err) == 3) {
                ps.st = (uint8_t)st;
                ps.attempts = (uint8_t)att;
                ps.error = (uint8_t)err;
            }
        } else if (tag == 'E') {
            unsigned ev;
            unsigned long arg = 0;
            if (sscanf(line, " E %u %lu", &ev, &arg) >= 1) {
                cali_pair_action_t acts[CALI_PAIR_MAX_ACTIONS];
                int n = cali_pair_step(&ps, (uint8_t)ev, (uint32_t)arg, acts);
                printf("%d %d %d |", ps.st, ps.attempts, ps.error);
                for (int i = 0; i < n; i++) printf(" %d:%u", acts[i].act, (unsigned)acts[i].arg);
                printf("\n");
            }
        }
    }
    return 0;
}
