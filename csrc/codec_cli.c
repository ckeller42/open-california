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
        else
            puts("ERR parse");
    }
    return 0;
}
