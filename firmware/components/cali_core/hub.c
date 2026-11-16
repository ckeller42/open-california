/* Remote hub core (cali_hub.h): sealed frames, replay guard, data key, config, QR text. Platform-free. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cali_hub.h"
#include "cali_lora_setup.h" /* cali_wipe */
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
    memset(c->rx_ring, 0, sizeof c->rx_ring);
    memset(c->rx_n, 0, sizeof c->rx_n);
    memset(c->rx_cur, 0, sizeof c->rx_cur);
}

int cali_hub_key_load_or_make(cali_hub_crypto_t *c) {
    size_t n = sizeof c->key;
    new_session(c);
    if (cali_kv_get(CALI_HUB_KEY_KEY, c->key, &n) == CALI_KV_OK && n == sizeof c->key) return 0;
    return cali_hub_rekey(c); /* missing or corrupt: make a fresh one */
}

int cali_hub_rekey(cali_hub_crypto_t *c) {
    uint8_t k[16];
    int rc = -1;
    cali_random(k, sizeof k);
    if (cali_kv_set(CALI_HUB_KEY_KEY, k, sizeof k) == 0) { /* the old key stays unless the new one is stored */
        memcpy(c->key, k, sizeof k);
        new_session(c);
        rc = 0;
    }
    cali_wipe(k, sizeof k);
    return rc;
}

static void put_be32(uint8_t *p, uint32_t v) {
    for (int i = 3; i >= 0; i--, v >>= 8) p[i] = (uint8_t)v;
}
static uint32_t get_be32(const uint8_t *p) {
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) v = v << 8 | p[i];
    return v;
}

size_t cali_hub_seal(cali_hub_crypto_t *c, uint8_t dir, const uint8_t *pt, size_t n, uint8_t *out, size_t max) {
    if (!s_seal || dir < 1 || dir > 3 || n > max || max - n < CALI_HUB_OVERHEAD) return 0;
    if (c->tx_ctr == UINT32_MAX) { /* rotate before the counter wraps: never reuse a nonce */
        cali_random(c->sess, sizeof c->sess);
        c->tx_ctr = 0;
    }
    out[0] = CALI_HUB_VER;
    out[1] = dir;
    out[2] = dir; /* nonce = dir | session(7) | counter(4) */
    memcpy(out + 3, c->sess, 7);
    put_be32(out + 10, ++c->tx_ctr);
    if (s_seal(c->key, out + 2, out, 14, pt, n, out + 14, out + 14 + n, 16)) return 0;
    return n + CALI_HUB_OVERHEAD;
}

int cali_hub_open(cali_hub_crypto_t *c, const uint8_t *frame, size_t n, uint8_t *out, size_t max, unsigned expect_mask,
                  uint8_t *dir) {
    if (!s_open || n < CALI_HUB_OVERHEAD || frame[0] != CALI_HUB_VER || frame[1] < 1 || frame[1] > 3) return -1;
    uint8_t d = frame[1];
    size_t len = n - CALI_HUB_OVERHEAD;
    const uint8_t *sess = frame + 3;
    uint32_t ctr = get_be32(frame + 10);
    int at = -1;
    if (!(expect_mask >> d & 1) || frame[2] != d || len > max) return -1;
    for (int i = 0; i < c->rx_n[d]; i++)
        if (!memcmp(sess, c->rx_ring[d][i], 7)) at = i;
    if (at >= 0 && ctr <= c->rx_last[d][at]) return -1; /* a replay */
    if (s_open(c->key, frame + 2, frame, 14, frame + 14, len, frame + 14 + len, 16, out)) return -1;
    if (at < 0) { /* only an authentic frame moves the guard */
        at = c->rx_n[d] < CALI_HUB_RING ? c->rx_n[d]++ : (c->rx_cur[d] + 1) % CALI_HUB_RING;
        c->rx_cur[d] = (uint8_t)at;
        memcpy(c->rx_ring[d][at], sess, 7);
    }
    c->rx_last[d][at] = ctr;
    if (dir) *dir = d;
    return (int)len;
}

int cali_hub_cfg_load(cali_hub_cfg_t *cfg) {
    size_t n = sizeof *cfg;
    if (cali_kv_get(CALI_HUB_CFG_KEY, cfg, &n) == CALI_KV_OK && n == sizeof *cfg) {
        cfg->van[sizeof cfg->van - 1] = cfg->url[sizeof cfg->url - 1] = cfg->user[sizeof cfg->user - 1] = 0;
        cfg->pass[sizeof cfg->pass - 1] = cfg->app_user[sizeof cfg->app_user - 1] = cfg->app_pass[sizeof cfg->app_pass - 1] = 0;
        return 0;
    }
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
    cali_wipe(hex, sizeof hex);
    return r > 0 && (size_t)r < max ? (size_t)r : 0;
}
