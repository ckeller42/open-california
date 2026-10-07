/* control_cli.c — line driver for tests/firmware/test_control_parity.py ONLY; not in any firmware
 * build. Links control.c + csrc/codec.c. stdin, one command per line; only P/C/W/A print, one line each:
 *   X                     forget every function's state
 *   S <fn> [Field=v ...]  replace fn's decoded state
 *   P <fn> <what> <tok>   cali_ctl_plan; tok = n (null -> "") | i:<decimal> | s:<percent-encoded>
 *                         -> OK <char>/<delay_ms>/<hex> ... | REFUSED <reason> | ELSEWHERE <reason>
 *                            | BAD | NONE | ERR parse
 *   C                     -> CFG[ <Key>=<v>]... cali_light_cfg: the known latch keys, CALI_LCFG_KEYS order
 *   W <hex char> <len>    -> OK 1|0 (cali_ctl_write_ok)
 *   A                     -> OK <char>/<len> ... every allowed pair: chars 0..0xffff ascending,
 *                            lengths 0..CODEC_FRAME_MAX+1
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_control.h"

#define MAXF 1024
static struct {
    char fn[24];
    char field[48];
    uint32_t v;
} s_st[MAXF];
static size_t s_n;

static int get(const char *fn, const char *field, uint32_t *out) {
    for (size_t i = 0; i < s_n; i++)
        if (strcmp(s_st[i].fn, fn) == 0 && strcmp(s_st[i].field, field) == 0) {
            *out = s_st[i].v;
            return 1;
        }
    return 0;
}

static void set_state(char *rest) {
    char *fn = strtok(rest, " "), *kv;
    size_t w = 0;
    if (!fn) return;
    for (size_t i = 0; i < s_n; i++)
        if (strcmp(s_st[i].fn, fn) != 0) s_st[w++] = s_st[i];
    s_n = w;
    while ((kv = strtok(NULL, " ")) != NULL && s_n < MAXF) {
        char *eq = strchr(kv, '=');
        if (!eq) continue;
        *eq = 0;
        snprintf(s_st[s_n].fn, sizeof s_st[s_n].fn, "%s", fn);
        snprintf(s_st[s_n].field, sizeof s_st[s_n].field, "%s", kv);
        s_st[s_n].v = (uint32_t)strtoul(eq + 1, NULL, 10);
        s_n++;
    }
}

static void unpercent(const char *s, char *out, size_t cap) {
    size_t n = 0;
    while (*s && n + 1 < cap) {
        if (s[0] == '%' && s[1] && s[2]) {
            char h[3] = {s[1], s[2], 0};
            out[n++] = (char)strtoul(h, NULL, 16);
            s += 3;
        } else {
            out[n++] = *s++;
        }
    }
    out[n] = 0;
}

static void plan_line(char *rest) {
    char *fn = strtok(rest, " "), *what = strtok(NULL, " "), *tok = strtok(NULL, " ");
    char value[1024];   /* wider than CALI_CTL_VALUE_MAX on purpose: over-long vectors must reach the twin intact */
    static cali_ctl_plan_t p;
    if (!fn || !what || !tok) {
        puts("ERR parse");
        return;
    }
    if (strcmp(tok, "n") == 0) value[0] = 0;
    else if (strncmp(tok, "i:", 2) == 0) snprintf(value, sizeof value, "%s", tok + 2);
    else if (strncmp(tok, "s:", 2) == 0) unpercent(tok + 2, value, sizeof value);
    else {
        puts("ERR parse");
        return;
    }
    cali_ctl_plan(fn, what, value, get, &p);
    switch (p.rc) {
    case CALI_CTL_OK:
        fputs("OK", stdout);
        for (size_t i = 0; i < p.n; i++) {
            printf(" %04x/%u/", p.f[i].chr, (unsigned)p.f[i].delay_ms);
            for (size_t k = 0; k < p.f[i].len; k++) printf("%02x", p.f[i].data[k]);
        }
        putchar('\n');
        break;
    case CALI_CTL_REFUSED: printf("REFUSED %s\n", p.reason); break;
    case CALI_CTL_ELSEWHERE: printf("ELSEWHERE %s\n", p.reason); break;
    case CALI_CTL_BAD_VALUE: puts("BAD"); break;
    default: puts("NONE"); break;
    }
}

int main(void) {
    static char line[4096];
    while (fgets(line, sizeof line, stdin)) {
        unsigned c, len;
        line[strcspn(line, "\n")] = 0;
        if (strcmp(line, "X") == 0) {
            s_n = 0;
        } else if (strncmp(line, "S ", 2) == 0) {
            set_state(line + 2);
        } else if (strncmp(line, "P ", 2) == 0) {
            plan_line(line + 2);
        } else if (strcmp(line, "C") == 0) {
            cali_light_cfg_t cfg;
            cali_light_cfg(get, &cfg);
            fputs("CFG", stdout);
            for (int k = 0; k < CALI_LCFG_N; k++)
                if (cfg.have >> k & 1u) printf(" %s=%lu", CALI_LCFG_KEYS[k], (unsigned long)cfg.v[k]);
            putchar('\n');
        } else if (sscanf(line, "W %x %u", &c, &len) == 2) {
            printf("OK %d\n", cali_ctl_write_ok((uint16_t)c, len));
        } else if (strcmp(line, "A") == 0) {
            fputs("OK", stdout);
            for (unsigned ch = 0; ch <= 0xffff; ch++)
                for (unsigned n = 0; n <= CODEC_FRAME_MAX + 1; n++)
                    if (cali_ctl_write_ok((uint16_t)ch, n)) printf(" %04x/%u", ch, n);
            putchar('\n');
        }
    }
    return 0;
}
