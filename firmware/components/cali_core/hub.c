/* Remote hub core (cali_hub.h): sealed frames, replay guard, data key, config, QR text. Platform-free. */
#include <stdio.h>
#include <string.h>

#include "cali_hub.h"
#include "cali_platform.h"

static cali_hub_seal_fn s_seal;
static cali_hub_open_fn s_open;

void cali_hub_set_aead(cali_hub_seal_fn seal, cali_hub_open_fn open) {
    s_seal = seal;
    s_open = open;
}

static void new_session(cali_hub_crypto_t *c) {
    cali_random(c->sess, sizeof c->sess);
    c->tx_ctr = 0;
    memset(c->rx_last, 0, sizeof c->rx_last);
    memset(c->rx_sess, 0, sizeof c->rx_sess);
}

int cali_hub_key_load_or_make(cali_hub_crypto_t *c) {
    size_t n = sizeof c->key;
    new_session(c);
    if (cali_kv_get(CALI_HUB_KEY_KEY, c->key, &n) == CALI_KV_OK && n == sizeof c->key) return 0;
    return cali_hub_rekey(c); /* missing or corrupt: make a fresh one */
}

int cali_hub_rekey(cali_hub_crypto_t *c) {
    cali_random(c->key, sizeof c->key);
    new_session(c);
    return cali_kv_set(CALI_HUB_KEY_KEY, c->key, sizeof c->key) == 0 ? 0 : -1;
}

static void put_be64(uint8_t *p, uint64_t v) {
    for (int i = 7; i >= 0; i--, v >>= 8) p[i] = (uint8_t)v;
}
static uint64_t get_be64(const uint8_t *p) {
    uint64_t v = 0;
    for (int i = 0; i < 8; i++) v = v << 8 | p[i];
    return v;
}

size_t cali_hub_seal(cali_hub_crypto_t *c, uint8_t dir, const uint8_t *pt, size_t n, uint8_t *out, size_t max) {
    if (!s_seal || dir < 1 || dir > 3 || n > max || max - n < CALI_HUB_OVERHEAD) return 0;
    out[0] = CALI_HUB_VER;
    out[1] = dir;
    memcpy(out + 2, c->sess, 4);
    put_be64(out + 6, ++c->tx_ctr);
    if (s_seal(c->key, out + 2, out, 14, pt, n, out + 14, out + 14 + n, 16)) return 0;
    return n + CALI_HUB_OVERHEAD;
}

int cali_hub_open(cali_hub_crypto_t *c, const uint8_t *frame, size_t n, uint8_t *out, size_t max, uint8_t *dir) {
    if (!s_open || n < CALI_HUB_OVERHEAD || frame[0] != CALI_HUB_VER || frame[1] < 1 || frame[1] > 3) return -1;
    size_t len = n - CALI_HUB_OVERHEAD;
    uint8_t d = frame[1];
    uint64_t ctr = get_be64(frame + 6);
    int same = !memcmp(frame + 2, c->rx_sess[d], 4);
    if (len > max || (same && ctr <= c->rx_last[d])) return -1;
    if (s_open(c->key, frame + 2, frame, 14, frame + 14, len, frame + 14 + len, 16, out)) return -1;
    memcpy(c->rx_sess[d], frame + 2, 4); /* only an authentic frame moves the guard */
    c->rx_last[d] = ctr;
    if (dir) *dir = d;
    return (int)len;
}

int cali_hub_cfg_load(cali_hub_cfg_t *cfg) {
    size_t n = sizeof *cfg;
    if (cali_kv_get(CALI_HUB_CFG_KEY, cfg, &n) == CALI_KV_OK && n == sizeof *cfg) return 0;
    memset(cfg, 0, sizeof *cfg);
    return -1;
}

int cali_hub_cfg_save(const cali_hub_cfg_t *cfg) { return cali_kv_set(CALI_HUB_CFG_KEY, cfg, sizeof *cfg) == 0 ? 0 : -1; }

size_t cali_hub_qr_text(char *out, size_t max, const cali_hub_cfg_t *cfg, const uint8_t key[16]) {
    char hex[33];
    const char *f[] = {cfg->van, cfg->url, cfg->app_user, cfg->app_pass};
    for (int i = 0; i < 4; i++)
        if (strchr(f[i], '*')) return 0; /* would break the field split */
    for (int i = 0; i < 16; i++) snprintf(hex + 2 * i, 3, "%02x", key[i]);
    int r = snprintf(out, max, "OCH1*%s*%s*%s*%s*%s", cfg->van, cfg->url, cfg->app_user, cfg->app_pass, hex);
    return r > 0 && (size_t)r < max ? (size_t)r : 0;
}
