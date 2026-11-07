/* AES-128-GCM open through PSA (cali_lora_aead.h). Pattern of aes128_ecb() in ble_nimble.c. */
#include <string.h>

#include "psa/crypto.h"

#include "cali_lora_aead.h"
#include "cali_lora_setup.h"

int cali_lora_aead_open_psa(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len,
                            const uint8_t *ct, size_t ct_len, const uint8_t *tag, uint8_t *out) {
    uint8_t in[CALI_LORA_XFER_MAX + 16], pt[CALI_LORA_XFER_MAX]; /* PSA wants ct||tag in one buffer */
    psa_key_attributes_t at = PSA_KEY_ATTRIBUTES_INIT;
    psa_key_id_t id;
    size_t olen = 0;
    int rc = -1;
    if (ct_len > CALI_LORA_XFER_MAX || psa_crypto_init() != PSA_SUCCESS) return -1;
    memcpy(in, ct, ct_len);
    memcpy(in + ct_len, tag, 16);
    psa_set_key_usage_flags(&at, PSA_KEY_USAGE_DECRYPT);
    psa_set_key_algorithm(&at, PSA_ALG_GCM);
    psa_set_key_type(&at, PSA_KEY_TYPE_AES);
    psa_set_key_bits(&at, 128);
    if (psa_import_key(&at, key, 16, &id) == PSA_SUCCESS) {
        if (psa_aead_decrypt(id, PSA_ALG_GCM, nonce, 12, ad, ad_len, in, ct_len + 16, pt, sizeof pt, &olen) ==
                PSA_SUCCESS &&
            olen == ct_len) {
            memcpy(out, pt, ct_len);
            rc = 0;
        }
        (void)psa_destroy_key(id);
    }
    cali_wipe(pt, sizeof pt); /* plaintext is a secret */
    return rc;
}
