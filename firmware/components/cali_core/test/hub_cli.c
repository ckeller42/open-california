/* hub_cli.c — host driver for hub.c with the real PSA AES-GCM and an in-memory KV.
 * Three contexts (0..2). Lines: load i | reload i | rekey i | key i | peer i j (i gets j's key, prints ok) | seal i dir pthex |
 * open i mask (last frame) | kvfail 0/1 | setctr i n | cfgfull | flip | badver | big i | cfg | qr van url user pass keyhex.
 * Output: key = hex; seal = frame hex or "0"; open = "<rc> <dir> <pthex>"; big = "<rc> <len> <ok>".
 * Driven by tests/firmware/test_hub.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_hub.h"
#include "cali_lora_aead.h"
#include "cali_platform.h"

static uint8_t s_kv[4][2048];
static size_t s_kv_len[4];
static const char *const s_names[4] = {CALI_HUB_KEY_KEY, CALI_HUB_CFG_KEY, "", ""};
static int slot(const char *key) {
    for (int i = 0; i < 2; i++)
        if (!strcmp(key, s_names[i])) return i;
    return -1;
}
int cali_kv_get(const char *key, void *buf, size_t *len) {
    int i = slot(key);
    if (i < 0 || !s_kv_len[i]) return CALI_KV_MISSING;
    if (*len < s_kv_len[i]) return CALI_KV_CORRUPT;
    memcpy(buf, s_kv[i], s_kv_len[i]);
    *len = s_kv_len[i];
    return CALI_KV_OK;
}
static int s_kv_fail;
int cali_kv_set(const char *key, const void *buf, size_t len) {
    int i = slot(key);
    if (s_kv_fail || i < 0 || len > sizeof s_kv[i]) return -1;
    memcpy(s_kv[i], buf, len);
    s_kv_len[i] = len;
    return 0;
}
void cali_random(void *buf, size_t len) {
    FILE *f = fopen("/dev/urandom", "rb");
    if (!f || fread(buf, 1, len, f) != len) exit(2);
    fclose(f);
}

static void hex(const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) printf("%02x", b[i]);
}
static size_t unhex(const char *s, uint8_t *out, size_t max) {
    size_t n = 0;
    if (!strcmp(s, "-")) return 0;
    for (; s[0] && s[1] && n < max; s += 2) {
        char t[3] = {s[0], s[1], 0};
        out[n++] = (uint8_t)strtoul(t, NULL, 16);
    }
    return n;
}

int main(void) {
    static cali_hub_crypto_t c[3];
    static char line[16384], a[8192], b[8192], d[64], e[64], f[64];
    static uint8_t last[8192 + 32], u[8192], v[8192], big[8192], bigf[8192 + 32], bigo[8192];
    int i, dir;
    unsigned mask;
    size_t lastn = 0;
    cali_hub_set_aead(cali_aead_seal_psa, cali_aead_open_psa);
    while (fgets(line, sizeof line, stdin)) {
        if (sscanf(line, "load %d", &i) == 1 || sscanf(line, "reload %d", &i) == 1) {
            if (line[0] == 'r') memset(&c[i], 0, sizeof c[i]);
            if (cali_hub_key_load_or_make(&c[i])) puts("-1");
            else hex(c[i].key, 16), putchar('\n');
        } else if (sscanf(line, "rekey %d", &i) == 1) {
            if (cali_hub_rekey(&c[i])) puts("-1");
            else hex(c[i].key, 16), putchar('\n');
        } else if (sscanf(line, "key %d", &i) == 1) {
            hex(c[i].key, 16), putchar('\n');
        } else if (sscanf(line, "peer %d %d", &i, &dir) == 2) { /* ctx i: same key as ctx dir, own new session */
            memset(&c[i], 0, sizeof c[i]);
            memcpy(c[i].key, c[dir].key, 16);
            cali_random(c[i].sess, 7);
            puts("ok");
        } else if (sscanf(line, "seal %d %d %8191s", &i, &dir, a) == 3) {
            size_t n = unhex(a, u, sizeof u);
            size_t r = cali_hub_seal(&c[i], (uint8_t)dir, u, n, v, sizeof v);
            memcpy(last, v, r), lastn = r; /* the frame "open i" and "flip" use */
            if (r) hex(v, r), putchar('\n');
            else puts("0");
        } else if (sscanf(line, "kvfail %d", &dir) == 1) {
            s_kv_fail = dir;
            puts("ok");
        } else if (sscanf(line, "setctr %d %u", &i, &mask) == 2) {
            c[i].tx_ctr = mask;
            puts("ok");
        } else if (!strncmp(line, "cfgfull", 7)) { /* a stored config with no NUL in any field */
            cali_hub_cfg_t g, r;
            memset(&g, 'x', sizeof g);
            cali_kv_set(CALI_HUB_CFG_KEY, &g, sizeof g);
            cali_hub_cfg_load(&r);
            printf("%u %u %u %u %u %u\n", (unsigned)strlen(r.van), (unsigned)strlen(r.url), (unsigned)strlen(r.user),
                   (unsigned)strlen(r.pass), (unsigned)strlen(r.app_user), (unsigned)strlen(r.app_pass));
        } else if (!strncmp(line, "flip", 4)) { /* one tag bit of the last frame */
            last[lastn - 1] ^= 1;
            puts("ok");
        } else if (!strncmp(line, "badver", 6)) {
            last[0] = 2;
            puts("ok");
        } else if (sscanf(line, "open %d %u", &i, &mask) == 2) { /* the last sealed frame; mask = expected dirs */
            uint8_t dd = 0;
            int r = cali_hub_open(&c[i], last, lastn, v, sizeof v, mask, &dd);
            printf("%d %u ", r, dd);
            if (r >= 0) hex(v, (size_t)r);
            putchar('\n');
        } else if (sscanf(line, "big %d", &i) == 1) { /* 8 KB through both directions */
            uint8_t dd = 0;
            for (size_t k = 0; k < sizeof big; k++) big[k] = (uint8_t)(k * 7 + 3);
            size_t fl = cali_hub_seal(&c[i], CALI_HUB_DIR_ESP, big, sizeof big, bigf, sizeof bigf);
            int r = fl ? cali_hub_open(&c[i], bigf, fl, bigo, sizeof bigo, 2, &dd) : -2;
            printf("%d %d %d\n", r, (int)fl, r == (int)sizeof big && !memcmp(big, bigo, sizeof big));
        } else if (!strncmp(line, "cfg", 3)) {
            cali_hub_cfg_t w = {"van1", "wss://h:8884/mqtt", "u", "p", "au", "ap"}, r;
            int s = cali_hub_cfg_save(&w);
            memset(&r, 0xff, sizeof r);
            int l = cali_hub_cfg_load(&r);
            printf("%d %d %d %s\n", s, l, !memcmp(&w, &r, sizeof w), r.url);
        } else if (sscanf(line, "qr %63s %63s %63s %63s %63s", a, b, d, e, f) == 5) {
            cali_hub_cfg_t g;
            uint8_t k[16];
            char out[300];
            memset(&g, 0, sizeof g);
            strcpy(g.van, a), strcpy(g.url, b), strcpy(g.app_user, d), strcpy(g.app_pass, e);
            unhex(f, k, 16);
            size_t n = cali_hub_qr_text(out, sizeof out, &g, k);
            puts(n ? out : "0");
        }
        fflush(stdout);
    }
    return 0;
}
