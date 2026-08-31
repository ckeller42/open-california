/* codec_cli.c — batched line-protocol driver for the parity harness.
 *
 * tests/test_codec_parity.py writes ALL input lines, closes stdin, reads ALL
 * output: exactly one output line per input line, in order, so thousands of ops
 * cost one process spawn (that's why no cffi is needed). Text only — no JSON
 * parser in C.
 *
 * Ops (see the plan / csrc/README.md):
 *   D <func> <hex|->                       -> OK Name=1 ... | ERR nofunc|parse
 *   E <func> <frame_bytes> [Name=val ...]  -> OK <hex>      | ERR width|range|nodefault|frame|nofunc|parse
 * Malformed input never crashes: it answers "ERR parse" and keeps reading (the
 * fuzz pass feeds garbage on purpose).
 */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "codec.h"
#include "ports.h"

static int hexval(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* "-" = empty frame; returns 0 on success */
static int parse_hex(const char *s, uint8_t *buf, size_t max, size_t *len)
{
    *len = 0;
    if (strcmp(s, "-") == 0)
        return 0;
    size_t n = strlen(s);
    if (n % 2 || n / 2 > max)
        return -1;
    for (size_t i = 0; i < n; i += 2) {
        int hi = hexval(s[i]), lo = hexval(s[i + 1]);
        if (hi < 0 || lo < 0)
            return -1;
        buf[*len] = (uint8_t)((hi << 4) | lo);
        (*len)++;
    }
    return 0;
}

static const char *err_name(int rc)
{
    switch (rc) {
    case CODEC_ERR_NOFUNC:    return "nofunc";
    case CODEC_ERR_WIDTH:     return "width";
    case CODEC_ERR_RANGE:     return "range";
    case CODEC_ERR_NODEFAULT: return "nodefault";
    case CODEC_ERR_FRAME:     return "frame";
    default:                  return "unknown";
    }
}

static void op_decode(void)
{
    const char *fn = strtok(NULL, " "), *hex = strtok(NULL, " ");
    if (!fn || !hex) { puts("ERR parse"); return; }
    const codec_func_t *f = codec_func_by_name(fn);
    if (!f) { puts("ERR nofunc"); return; }
    uint8_t raw[512];
    size_t len;
    if (parse_hex(hex, raw, sizeof raw, &len)) { puts("ERR parse"); return; }
    codec_kv_t kv[CODEC_KV_MAX];
    int n = codec_decode(f, raw, len, kv);
    fputs("OK", stdout);
    for (int i = 0; i < n; i++)
        printf(" %s=%" PRIu32, kv[i].name, kv[i].value);
    putchar('\n');
}

static void op_encode(void)
{
    const char *fn = strtok(NULL, " "), *fb_s = strtok(NULL, " ");
    if (!fn || !fb_s) { puts("ERR parse"); return; }
    const codec_func_t *f = codec_func_by_name(fn);
    if (!f) { puts("ERR nofunc"); return; }
    char *end;
    unsigned long fb = strtoul(fb_s, &end, 10);
    if (*end || fb > CODEC_FRAME_MAX) { puts("ERR parse"); return; }
    codec_kv_t vals[CODEC_KV_MAX];
    size_t nvals = 0;
    for (char *tok = strtok(NULL, " "); tok; tok = strtok(NULL, " ")) {
        char *eq = strchr(tok, '=');
        if (!eq || nvals >= CODEC_KV_MAX) { puts("ERR parse"); return; }
        *eq = '\0';
        unsigned long long v = strtoull(eq + 1, &end, 10);
        if (*end || v > 0xFFFFFFFFull) { puts("ERR width"); return; }  /* > uint32: cannot fit any field */
        vals[nvals].name = tok;
        vals[nvals].value = (uint32_t)v;
        vals[nvals].supplied = 1;
        nvals++;
    }
    uint8_t out[CODEC_FRAME_MAX];
    size_t out_len;
    int rc = codec_encode(f, vals, nvals, (size_t)fb, out, &out_len);
    if (rc != CODEC_OK) { printf("ERR %s\n", err_name(rc)); return; }
    fputs("OK ", stdout);
    for (size_t i = 0; i < out_len; i++)
        printf("%02x", out[i]);
    putchar('\n');
}

/* F <nf|-> <pf|-> <ng|-> <pg|->  ('-' = missing liters value) */
static void op_freshness(void)
{
    int32_t v[4] = {0, 0, 0, 0};
    uint8_t have = 0;
    for (int i = 0; i < 4; i++) {
        const char *tok = strtok(NULL, " ");
        if (!tok) { puts("ERR parse"); return; }
        if (strcmp(tok, "-") == 0)
            continue;
        char *end;
        long x = strtol(tok, &end, 10);
        if (*end) { puts("ERR parse"); return; }
        v[i] = (int32_t)x;
        have |= (uint8_t)(1u << i);
    }
    printf("OK %d\n", freshness_implausible_drop(v[0], v[1], v[2], v[3], have));
}

/* A [key=value ...]  keys: batt2_v soc2_level cooler_installed cooler_level
 * quiet_from quiet_to roof_installed roof_position level_roll level_pitch */
static void op_anchors(void)
{
    anchors_in_t in;
    memset(&in, 0, sizeof in);
    for (char *tok = strtok(NULL, " "); tok; tok = strtok(NULL, " ")) {
        char *eq = strchr(tok, '=');
        if (!eq) { puts("ERR parse"); return; }
        *eq = '\0';
        char *end;
        float fv = strtof(eq + 1, &end);
        long iv = (long)fv;
        if (*end) { puts("ERR parse"); return; }
        if (strcmp(tok, "batt2_v") == 0)            { in.batt2_v = fv; in.have |= ANCHOR_BATT2_V; }
        else if (strcmp(tok, "soc2_level") == 0)    { in.soc2_level = (int32_t)iv; in.have |= ANCHOR_SOC2_LEVEL; }
        else if (strcmp(tok, "cooler_installed") == 0) in.cooler_installed = iv != 0;
        else if (strcmp(tok, "cooler_level") == 0)  { in.cooler_level = (int32_t)iv; in.have |= ANCHOR_COOLER_LEVEL; }
        else if (strcmp(tok, "quiet_from") == 0)    { in.quiet_from = (int32_t)iv; in.have |= ANCHOR_QUIET_FROM; }
        else if (strcmp(tok, "quiet_to") == 0)      { in.quiet_to = (int32_t)iv; in.have |= ANCHOR_QUIET_TO; }
        else if (strcmp(tok, "roof_installed") == 0) in.roof_installed = iv != 0;
        else if (strcmp(tok, "roof_position") == 0) { in.roof_position = (int32_t)iv; in.have |= ANCHOR_ROOF_POSITION; }
        else if (strcmp(tok, "level_roll") == 0)    { in.level_roll = fv; in.have |= ANCHOR_LEVEL_ROLL; }
        else if (strcmp(tok, "level_pitch") == 0)   { in.level_pitch = fv; in.have |= ANCHOR_LEVEL_PITCH; }
        else { puts("ERR parse"); return; }
    }
    printf("OK %" PRIu32 "\n", anchors_check(&in));
}

/* C <seed> <tick_ms> <elapsed_ms>  (integers; elapsed is uint64) */
static void op_counter(void)
{
    const char *a = strtok(NULL, " "), *b = strtok(NULL, " "), *c = strtok(NULL, " ");
    if (!a || !b || !c) { puts("ERR parse"); return; }
    char *end;
    unsigned long long seed = strtoull(a, &end, 10);
    if (*end || seed > 0xFFFFFFFFull) { puts("ERR parse"); return; }
    unsigned long long tick = strtoull(b, &end, 10);
    if (*end || tick == 0 || tick > 0xFFFFFFFFull) { puts("ERR parse"); return; }
    unsigned long long ms = strtoull(c, &end, 10);
    if (*end) { puts("ERR parse"); return; }
    uint32_t ctr = roof_safety_counter((uint32_t)seed, ms, (uint32_t)tick);
    uint8_t beat[4];
    roof_beat_bytes(ctr, beat);
    printf("OK %" PRIu32 " %02x%02x%02x%02x\n", ctr, beat[0], beat[1], beat[2], beat[3]);
}

int main(void)
{
    char line[4096];
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\r\n")] = '\0';
        const char *op = strtok(line, " ");
        if (!op)
            puts("ERR parse");
        else if (strcmp(op, "D") == 0)
            op_decode();
        else if (strcmp(op, "E") == 0)
            op_encode();
        else if (strcmp(op, "F") == 0)
            op_freshness();
        else if (strcmp(op, "A") == 0)
            op_anchors();
        else if (strcmp(op, "C") == 0)
            op_counter();
        else
            puts("ERR parse");
    }
    return 0;
}
