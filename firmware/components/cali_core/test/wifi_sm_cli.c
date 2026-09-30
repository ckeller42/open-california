/* wifi_sm_cli.c — line-protocol driver for tests/firmware/test_wifi_sm_parity.py ONLY; not part of
 * the ESP build.
 *
 * Reads lines from stdin:
 *   S st ap joined retry since next   sets the state directly; prints nothing.
 *   E ev arg                          calls cali_wifi_step(); prints the 6 state fields, "|", then
 *                                     " act:arg" per action.
 */
#include <stdio.h>

#include "cali_wifi_sm.h"

int main(void) {
    cali_wifi_state_t s = {0, 0, 0, 0, 0, 0};
    char line[256];

    while (fgets(line, sizeof line, stdin)) {
        char tag;
        if (sscanf(line, " %c", &tag) != 1) continue;

        if (tag == 'S') {
            unsigned st, ap, joined;
            unsigned long retry;
            unsigned long long since, next;
            if (sscanf(line, " S %u %u %u %lu %llu %llu", &st, &ap, &joined, &retry, &since, &next) == 6) {
                s.st = (uint8_t)st;
                s.ap_up = (uint8_t)ap;
                s.joined_once = (uint8_t)joined;
                s.retry_ms = (uint32_t)retry;
                s.since_ms = (uint64_t)since;
                s.next_try_ms = (uint64_t)next;
            }
        } else if (tag == 'E') {
            unsigned ev;
            unsigned long long arg = 0;
            if (sscanf(line, " E %u %llu", &ev, &arg) >= 1) {
                cali_wifi_action_t acts[WIFI_MAX_ACTIONS];
                int n = cali_wifi_step(&s, (uint8_t)ev, (uint64_t)arg, acts);
                printf("%u %u %u %lu %llu %llu |", (unsigned)s.st, (unsigned)s.ap_up,
                       (unsigned)s.joined_once, (unsigned long)s.retry_ms,
                       (unsigned long long)s.since_ms, (unsigned long long)s.next_try_ms);
                for (int i = 0; i < n; i++) printf(" %u:%lu", (unsigned)acts[i].act, (unsigned long)acts[i].arg);
                printf("\n");
            }
        }
    }
    return 0;
}
