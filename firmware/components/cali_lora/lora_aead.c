/* AES-128-GCM open through PSA (cali_lora_aead.h). Pattern of aes128_ecb() in ble_nimble.c. */
#include <stdlib.h>
#include <string.h>

#include "psa/crypto.h"

#include "cali_lora_aead.h"
#include "cali_lora_setup.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
/* WiFi hub frames carry several KB: keep them out of the tight internal heap (PSRAM first). */
#define AEAD_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
#define AEAD_FREE heap_caps_free
#else
#define AEAD_ALLOC malloc
#define AEAD_FREE free
#endif

/* One AES-128-GCM op through PSA; tag_len 16 or 8 (shortened-tag GCM). The work buffers are on the heap, sized to len. */
static int aead_psa(int seal, const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len,
                    const uint8_t *in_text, size_t len, uint8_t *out_text, uint8_t *tag, size_t tag_len) {
    size_t cap = len + 16; /* PSA wants ct||tag in one buffer */
    uint8_t *in, *res;
    psa_key_attributes_t at = PSA_KEY_ATTRIBUTES_INIT;
    psa_key_id_t id;
    size_t olen = 0;
    int rc = -1;
    psa_algorithm_t alg = tag_len == 8 ? PSA_ALG_AEAD_WITH_SHORTENED_TAG(PSA_ALG_GCM, 8) : PSA_ALG_GCM;
    if ((tag_len != 16 && tag_len != 8) || len > (size_t)1 << 24 || psa_crypto_init() != PSA_SUCCESS) return -1;
    in = AEAD_ALLOC(cap);
    res = AEAD_ALLOC(cap);
    if (!in || !res) {
        AEAD_FREE(in);
        AEAD_FREE(res);
        return -1;
    }
    memcpy(in, in_text, len);
    if (!seal) memcpy(in + len, tag, tag_len);
    psa_set_key_usage_flags(&at, seal ? PSA_KEY_USAGE_ENCRYPT : PSA_KEY_USAGE_DECRYPT);
    psa_set_key_algorithm(&at, alg);
    psa_set_key_type(&at, PSA_KEY_TYPE_AES);
    psa_set_key_bits(&at, 128);
    if (psa_import_key(&at, key, 16, &id) == PSA_SUCCESS) {
        psa_status_t st = seal ? psa_aead_encrypt(id, alg, nonce, 12, ad, ad_len, in, len, res, cap, &olen)
                               : psa_aead_decrypt(id, alg, nonce, 12, ad, ad_len, in, len + tag_len, res, cap, &olen);
        if (st == PSA_SUCCESS && olen == (seal ? len + tag_len : len)) {
            memcpy(out_text, res, len);
            if (seal) memcpy(tag, res + len, tag_len);
            rc = 0;
        }
        (void)psa_destroy_key(id);
    }
    cali_wipe(in, cap); /* plaintext is a secret */
    cali_wipe(res, cap);
    AEAD_FREE(in);
    AEAD_FREE(res);
    return rc;
}

int cali_aead_seal_psa(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len, const uint8_t *pt,
                       size_t len, uint8_t *ct, uint8_t *tag, size_t tag_len) {
    return aead_psa(1, key, nonce, ad, ad_len, pt, len, ct, tag, tag_len);
}

int cali_aead_open_psa(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len, const uint8_t *ct,
                       size_t len, const uint8_t *tag, size_t tag_len, uint8_t *out) {
    return aead_psa(0, key, nonce, ad, ad_len, ct, len, out, (uint8_t *)tag, tag_len);
}

int cali_lora_aead_open_psa(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len,
                            const uint8_t *ct, size_t ct_len, const uint8_t *tag, uint8_t *out) {
    if (ct_len > CALI_LORA_XFER_MAX) return -1; /* the transfer keeps its cap */
    return cali_aead_open_psa(key, nonce, ad, ad_len, ct, ct_len, tag, 16, out);
}
