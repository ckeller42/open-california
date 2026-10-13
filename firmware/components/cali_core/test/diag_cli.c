/* diag_cli.c — line-protocol driver for tests/firmware/test_diag.py ONLY; not part of the ESP build.
 * Links diag.c alone; the sink prints each record body.
 *
 * stdin, one line each:
 *   nosink                       cali_diag_sink = NULL
 *   evt <kind> [raw fields]      cali_diag_evt(kind, "%s", fields)
 *   health max|min               cali_diag_health with every field at its longest | unknown
 *   touch max|min                cali_diag_touch with every field at its longest | unknown
 *   boot <reason> <fw> <n> <prev>   cali_diag_boot
 *   due <now>                    "DUE 0|1"
 *   http <status> <bytes> <ms> [path]   cali_diag_http (no path = NULL)
 *   ble <hex chr> <op> <ms> <now>       cali_diag_ble
 *   log <now> <line…>            cali_diag_log ("\e" in the line = ESC)
 *   logflush <now>               cali_diag_log(now, NULL)
 *   panic <task> <reason> <hex pc>…   seal a capture (reason: '_' -> ' ')
 *   panicbad magic|sum|depth     corrupt the sealed capture
 *   panicnosink                  cali_diag_panic with the sink failing ("PANIC rc"; capture kept)
 *   paniclog                     "PANIC <rc>" = cali_diag_panic
 * stdout: "REC {<body>}" per record handed to the sink; "RC <rc>" after evt/health/boot.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_diag.h"

static int sink(const char *body) {
    printf("REC {%s}\n", body);
    return 0;
}

static int sink_fail(const char *body) {
    (void)body;
    return -1;
}

static cali_panic_t cap;

int main(void) {
    char line[512];
    cali_diag_sink = sink;
    setvbuf(stdout, NULL, _IOLBF, 0);
    while (fgets(line, sizeof line, stdin)) {
        char a[64] = "", b[64] = "", *rest;
        line[strcspn(line, "\r\n")] = 0;
        if (strcmp(line, "nosink") == 0) {
            cali_diag_sink = NULL;
        } else if (strncmp(line, "evt ", 4) == 0) {
            rest = strchr(line + 4, ' ');
            if (rest) *rest++ = 0;
            printf("RC %d\n", cali_diag_evt(line + 4, "%s", rest ? rest : ""));
        } else if (strncmp(line, "health ", 7) == 0) {
            cali_diag_health_t h = {-1, -1, -1, -1, 0, {0}, {0}, 0, 0, 0, 0};
            if (strcmp(line + 7, "max") == 0) {
                static const char *const N[CALI_DIAG_STACKS] = {"owner", "sd", "display", "ota", "console",
                                                                "lwip", "timer", "event"};
                h = (cali_diag_health_t){99999999, 99999999, 99999999, 100, CALI_DIAG_STACKS, {0}, {0},
                                         -100, -100, 1, 4294967295ul};
                for (int i = 0; i < CALI_DIAG_STACKS; i++) {
                    h.stack_name[i] = N[i];
                    h.stack_min[i] = 99999;
                }
            }
            printf("RC %d\n", cali_diag_health(&h));
        } else if (strncmp(line, "touch ", 6) == 0) {
            cali_diag_touch_t t = {0, NULL, -1, -1, -1, NULL, NULL, -1, -1};
            if (strcmp(line + 6, "max") == 0)   /* every field at its longest */
                t = (cali_diag_touch_t){4294967295ul, "ESP_ERR_INVALID_RESPONSE_LONGEST_NAME_40", 240, 0, 0, "dim",
                                        "none", 4294967295l, 255};
            printf("RC %d\n", cali_diag_touch(&t));
        } else if (strncmp(line, "boot ", 5) == 0) {
            unsigned long n;
            long prev;
            if (sscanf(line + 5, "%63s %63s %lu %ld", a, b, &n, &prev) != 4) return 3;
            printf("RC %d\n", cali_diag_boot(a, b, n, prev));
        } else if (strncmp(line, "due ", 4) == 0) {
            printf("DUE %d\n", cali_diag_due(strtoull(line + 4, NULL, 10)));
        } else if (strncmp(line, "http ", 5) == 0) {
            int st;
            unsigned long by;
            unsigned long long ms;
            char path[256] = "";
            int k = sscanf(line + 5, "%d %lu %llu %255s", &st, &by, &ms, path);
            if (k < 3) return 3;
            cali_diag_http(k == 4 ? path : NULL, st, by, ms);
        } else if (strncmp(line, "ble ", 4) == 0) {
            unsigned c;
            unsigned long ms;
            unsigned long long now;
            if (sscanf(line + 4, "%x %63s %lu %llu", &c, a, &ms, &now) != 4) return 3;
            cali_diag_ble((uint16_t)c, a, (uint32_t)ms, now);
        } else if (strncmp(line, "log ", 4) == 0) {
            char *end, *p;
            unsigned long long now = strtoull(line + 4, &end, 10);
            while ((p = strstr(end, "\\e")) != NULL) {   /* "\e" -> ESC */
                *p = '\033';
                memmove(p + 1, p + 2, strlen(p + 2) + 1);
            }
            cali_diag_log(now, end + 1);
        } else if (strncmp(line, "logflush ", 9) == 0) {
            cali_diag_log(strtoull(line + 9, NULL, 10), NULL);
        } else if (strncmp(line, "panic ", 6) == 0) {
            static char tok[2][64];
            char *p, *end;
            memset(&cap, 0, sizeof cap);
            if (sscanf(line + 6, "%63s %63s", tok[0], tok[1]) != 2) return 3;
            for (p = tok[1]; *p; p++) if (*p == '_') *p = ' ';
            strncpy(cap.task, tok[0], sizeof cap.task - 1);
            strncpy(cap.reason, tok[1], sizeof cap.reason - 1);
            p = strchr(strchr(line + 6, ' ') + 1, ' ');
            while (p && cap.depth < CALI_PANIC_DEPTH) {
                unsigned long v = strtoul(p, &end, 16);
                if (end == p) break;
                cap.pc[cap.depth++] = (uint32_t)v;
                p = end;
            }
            cali_panic_seal(&cap);
        } else if (strcmp(line, "panicbad magic") == 0) {
            cap.magic ^= 1;
        } else if (strcmp(line, "panicbad sum") == 0) {
            cap.pc[0] ^= 1;
        } else if (strcmp(line, "panicbad depth") == 0) {
            cap.depth = CALI_PANIC_DEPTH + 1;
            cap.sum = cali_panic_sum(&cap);
        } else if (strcmp(line, "panicnosink") == 0) {
            cali_diag_sink = sink_fail;
            printf("PANIC %d\n", cali_diag_panic(&cap));
            cali_diag_sink = sink;
        } else if (strcmp(line, "paniclog") == 0) {
            printf("PANIC %d\n", cali_diag_panic(&cap));
        } else {
            printf("ERR %s\n", line);
        }
    }
    return 0;
}
