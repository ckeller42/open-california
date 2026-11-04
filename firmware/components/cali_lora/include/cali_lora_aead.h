/**
 * @file cali_lora_aead.h
 * @brief AES-128-GCM open through PSA crypto (Mbed TLS 3.6 on the host, 4.x in ESP-IDF v6), the real
 *        `cali_lora_aead_open_fn` for the LoRa key transfer (cali_lora_setup.h).
 */
#ifndef CALI_LORA_AEAD_H
#define CALI_LORA_AEAD_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Opens one AES-128-GCM message with PSA; the key is imported per call and destroyed.
 * @param key The 16-byte transfer key.
 * @param nonce The 12-byte nonce.
 * @param ad The associated data (the DevEUI).
 * @param ad_len The length of `ad`.
 * @param ct The ciphertext.
 * @param ct_len The length of `ct` and of `out`; at most CALI_LORA_XFER_MAX.
 * @param tag The 16-byte tag.
 * @param out Receives the plaintext; written only when the tag is valid.
 * @return 0 when the tag is valid, -1 when it is not or PSA fails.
 */
int cali_lora_aead_open_psa(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len,
                            const uint8_t *ct, size_t ct_len, const uint8_t *tag, uint8_t *out);

#ifdef __cplusplus
}
#endif

#endif
