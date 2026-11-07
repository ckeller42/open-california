/* lora_setup.c — LoRa onboarding formats and key-transfer reassembly (R_FW_LORA_SETUP). Platform-free. */
#include "cali_lora_setup.h"

#include <string.h>

void cali_wipe(void *p, size_t n) {
    volatile uint8_t *b = (volatile uint8_t *)p;
    while (n--) *b++ = 0;
}

static const char B32[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
static const char HEX[] = "0123456789ABCDEF";
#define KEY_PREFIX "NNSXS."
#define KEY_A 39
#define KEY_B 52
#define KEY_A_BYTES 25
#define KEY_B_BYTES 33

/* base32 -> bytes. The bit string is cut into whole bytes; the leftover bits (3 of 195, 4 of 260) sit in the
 * low bits of one more byte, whose top bits are 0. */
static void b32_dec(const char *s, size_t n, uint8_t *out) {
    uint32_t acc = 0;
    int bits = 0;
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        acc = (acc << 5) | (uint32_t)(strchr(B32, s[i]) - B32);
        bits += 5;
        if (bits >= 8) {
            bits -= 8;
            out[o++] = (uint8_t)(acc >> bits);
        }
    }
    if (bits) out[o] = (uint8_t)(acc & ((1u << bits) - 1));
}

static int b32_valid(const char *s, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (!s[i] || !strchr(B32, s[i])) return 0;
    return 1;
}

static void b32_enc(const uint8_t *in, size_t n_chars, char *out) {
    size_t full = n_chars * 5 / 8;
    int rem = (int)(n_chars * 5 % 8);
    for (size_t c = 0; c < n_chars; c++) {
        unsigned v = 0;
        for (size_t k = c * 5; k < c * 5 + 5; k++) {
            unsigned bit = k < full * 8 ? (in[k / 8] >> (7 - k % 8)) & 1 : (in[full] >> (rem - 1 - (int)(k - full * 8))) & 1;
            v = v << 1 | bit;
        }
        out[c] = B32[v];
    }
}

int cali_lora_key_pack(const char *key, uint8_t *out) {
    size_t pl = sizeof KEY_PREFIX - 1;
    if (strlen(key) != pl + KEY_A + 1 + KEY_B || strncmp(key, KEY_PREFIX, pl) || key[pl + KEY_A] != '.')
        return -1;
    if (!b32_valid(key + pl, KEY_A) || !b32_valid(key + pl + KEY_A + 1, KEY_B)) return -1;
    b32_dec(key + pl, KEY_A, out);
    b32_dec(key + pl + KEY_A + 1, KEY_B, out + KEY_A_BYTES);
    return 0;
}

int cali_lora_key_unpack(const uint8_t *in, char *out) {
    /* the padding bits (top 5 of byte 24, top 4 of byte 57) must be 0, or pack would not round-trip */
    if ((in[KEY_A_BYTES - 1] & 0xf8) || (in[CALI_LORA_KEY_PACKED - 1] & 0xf0)) return -1;
    size_t pl = sizeof KEY_PREFIX - 1;
    memcpy(out, KEY_PREFIX, pl);
    b32_enc(in, KEY_A, out + pl);
    out[pl + KEY_A] = '.';
    b32_enc(in + KEY_A_BYTES, KEY_B, out + pl + KEY_A + 1);
    out[CALI_LORA_KEY_LEN] = 0;
    return 0;
}

static char *put_hex(char *p, const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) {
        *p++ = HEX[b[i] >> 4];
        *p++ = HEX[b[i] & 15];
    }
    return p;
}

size_t cali_lora_setup_qr(char *out, size_t max, const uint8_t *deveui, const uint8_t *appkey, const uint8_t *tk) {
    const size_t len = 4 + 1 + 16 + 1 + 32 + 1 + 32;
    if (max < len + 1) return 0;
    char *p = out;
    memcpy(p, "OCS1*", 5);
    p = put_hex(p + 5, deveui, 8);
    *p++ = '*';
    p = put_hex(p, appkey, 16);
    *p++ = '*';
    p = put_hex(p, tk, 16);
    *p = 0;
    return len;
}

/* copy one ID in upper case; only the QR-alphanumeric characters of a TTS ID are allowed */
static char *put_id(char *p, const char *end, const uint8_t *s, size_t n) {
    if (!p || n == 0 || (size_t)(end - p) < n + 1) return NULL;
    for (size_t i = 0; i < n; i++) {
        uint8_t c = s[i];
        if (c >= 'a' && c <= 'z') c = (uint8_t)(c - 32);
        else if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '.')) return NULL;
        *p++ = (char)c;
    }
    return p;
}

size_t cali_lora_repair_qr(char *out, size_t max, const uint8_t *plain, size_t len) {
    if (len < 4 || plain[0] != 1 || !max) return 0;
    size_t cl = plain[1], o = 2;
    if (cl > CALI_LORA_CLUSTER_MAX || o + cl + 1 > len) return 0;
    const uint8_t *cluster = plain + o;
    o += cl;
    size_t al = plain[o++];
    if (al > CALI_LORA_ID_MAX || o + al + 1 > len) return 0;
    const uint8_t *app = plain + o;
    o += al;
    size_t dl = plain[o++];
    if (dl > CALI_LORA_ID_MAX || o + dl + 2 * CALI_LORA_KEY_PACKED != len) return 0;
    const uint8_t *dev = plain + o;
    o += dl;
    char rk[CALI_LORA_KEY_LEN + 1], ck[CALI_LORA_KEY_LEN + 1];
    size_t r = 0;
    /* the exact output length first: every write below is then inside `out` */
    if (!cali_lora_key_unpack(plain + o, rk) && !cali_lora_key_unpack(plain + o + CALI_LORA_KEY_PACKED, ck) &&
        max >= 5 + cl + 1 + al + 1 + dl + 1 + 2 * CALI_LORA_KEY_LEN + 1 + 1) {
        const char *end = out + max;
        char *p = out;
        memcpy(p, "OCR1*", 5);
        p += 5;
        p = put_id(p, end, cluster, cl);
        if (p) *p++ = '*';
        p = put_id(p, end, app, al);
        if (p) *p++ = '*';
        p = put_id(p, end, dev, dl);
        if (p && (size_t)(end - p) >= 2 * (CALI_LORA_KEY_LEN + 1) + 1) {
            *p++ = '*';
            memcpy(p, rk, CALI_LORA_KEY_LEN);
            p += CALI_LORA_KEY_LEN;
            *p++ = '*';
            memcpy(p, ck, CALI_LORA_KEY_LEN);
            p += CALI_LORA_KEY_LEN;
            *p = 0;
            r = (size_t)(p - out);
        }
    }
    cali_wipe(rk, sizeof rk);   /* the unpacked keys */
    cali_wipe(ck, sizeof ck);
    return r;
}

/* ---- SHA-256 (small, portable) ---- */
static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha_block(uint32_t h[8], const uint8_t *b) {
    uint32_t w[64], v[8];
    for (int i = 0; i < 16; i++)
        w[i] = (uint32_t)b[4 * i] << 24 | (uint32_t)b[4 * i + 1] << 16 | (uint32_t)b[4 * i + 2] << 8 | b[4 * i + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    memcpy(v, h, sizeof v);
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = v[7] + (ROR(v[4], 6) ^ ROR(v[4], 11) ^ ROR(v[4], 25)) + ((v[4] & v[5]) ^ (~v[4] & v[6])) + K[i] + w[i];
        uint32_t t2 = (ROR(v[0], 2) ^ ROR(v[0], 13) ^ ROR(v[0], 22)) + ((v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]));
        memmove(v + 1, v, 7 * sizeof v[0]);
        v[4] += t1;
        v[0] = t1 + t2;
    }
    for (int i = 0; i < 8; i++) h[i] += v[i];
}

void cali_lora_sha256_4(const uint8_t *in, size_t n, uint8_t *out) {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    size_t i = 0;
    for (; i + 64 <= n; i += 64) sha_block(h, in + i);
    uint8_t t[128] = {0};
    size_t r = n - i;
    memcpy(t, in + i, r);
    t[r] = 0x80;
    size_t tl = r < 56 ? 64 : 128;
    uint64_t bits = (uint64_t)n * 8;
    for (int k = 0; k < 8; k++) t[tl - 1 - k] = (uint8_t)(bits >> (8 * k));
    for (size_t k = 0; k < tl; k += 64) sha_block(h, t + k);
    for (int k = 0; k < 4; k++) out[k] = (uint8_t)(h[0] >> (24 - 8 * k));
    cali_wipe(t, sizeof t);   /* the message's tail: a key or the plaintext */
}

/* ---- key transfer ---- */
void cali_lora_xfer_reset(cali_lora_xfer_t *x) { cali_wipe(x, sizeof *x); }

static int bad(cali_lora_xfer_t *x) {
    cali_lora_xfer_reset(x);
    return -1;
}

int cali_lora_xfer_rx(cali_lora_xfer_t *x, const uint8_t *frag, size_t n, const uint8_t *tk, const uint8_t *deveui,
                      cali_lora_aead_open_fn open, uint8_t *plain, size_t *plain_len, uint8_t *confirm,
                      size_t *confirm_len) {
    *plain_len = 0;
    *confirm_len = 0;
    if (n < 2 || n > CALI_LORA_FRAG_MAX || (frag[0] & 0x78)) return bad(x);
    unsigned idx = frag[0] & 7, is_last = frag[0] >> 7;
    size_t body = n - 1, off = idx * CALI_LORA_FRAG_BODY;
    if (off + body > CALI_LORA_XFER_MAX) return bad(x);
    if (!is_last && body != CALI_LORA_FRAG_BODY) return bad(x);
    if (x->have & (1u << idx)) {
        /* a repeat is harmless only when it is byte-for-byte the same */
        if (x->len[idx] != body || memcmp(x->buf + off, frag + 1, body)) return bad(x);
    } else {
        memcpy(x->buf + off, frag + 1, body);
        x->len[idx] = body;
        x->have |= (uint8_t)(1u << idx);
    }
    if (is_last) {
        if (x->last && x->last != idx + 1) return bad(x);
        x->last = (uint8_t)(idx + 1);
    }
    /* no fragment may lie beyond the last one */
    if (x->last && (x->have >> x->last)) return bad(x);
    if (!x->last) return 0;
    unsigned want = (1u << x->last) - 1;
    if (x->have != want) {
        if (!is_last) return 0;
        confirm[0] = 2;
        confirm[1] = x->have;
        *confirm_len = 2;
        return 2;
    }
    size_t total = (size_t)(x->last - 1) * CALI_LORA_FRAG_BODY + x->len[x->last - 1];
    if (total <= 12 + 16) return bad(x);
    size_t ct = total - 12 - 16;
    if (open(tk, x->buf, deveui, 8, x->buf + 12, ct, x->buf + total - 16, plain) == 0) {
        confirm[0] = 0;
        cali_lora_sha256_4(plain, ct, confirm + 1);
        *confirm_len = 5;
        *plain_len = ct;
    } else {
        cali_wipe(plain, ct); /* no unauthenticated bytes leave */
        confirm[0] = 1;
        *confirm_len = 1;
    }
    cali_lora_xfer_reset(x);
    return 1;
}
