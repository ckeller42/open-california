/**
 * @file cali_hub.h
 * @brief Remote hub, platform-free part: the sealed WiFi frame, the replay guard, the data key (KV
 *        "hub_key"), the hub config (KV "hub_cfg") and the `OCH1*...` QR text.
 *
 * Frame: ver(1)=0x01 | dir(1) | nonce(12) | ct | tag(16); AD = the first 14 bytes; nonce = 4 B random
 * session + 8 B big-endian counter. dir 1 ESP->app, 2 app->ESP, 3 bridge->app.
 */
#ifndef CALI_HUB_H
#define CALI_HUB_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_HUB_KEY_KEY "hub_key"
#define CALI_HUB_CFG_KEY "hub_cfg"
#define CALI_HUB_VER 1
#define CALI_HUB_DIR_ESP 1
#define CALI_HUB_DIR_APP 2
#define CALI_HUB_DIR_BRIDGE 3
/** @brief The bytes a frame adds to the plaintext (ver, dir, nonce, tag). */
#define CALI_HUB_OVERHEAD 30

/** @brief AES-128-GCM seal (cali_aead_seal_psa); tag_len is 16 here. @return 0 or -1. */
typedef int (*cali_hub_seal_fn)(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len,
                                const uint8_t *pt, size_t len, uint8_t *ct, uint8_t *tag, size_t tag_len);
/** @brief AES-128-GCM open (cali_aead_open_psa); `out` is written only for a valid tag. @return 0 or -1. */
typedef int (*cali_hub_open_fn)(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len,
                                const uint8_t *ct, size_t len, const uint8_t *tag, size_t tag_len, uint8_t *out);

/** @brief Sets the cipher (cali_core cannot link cali_lora, which links it). Call once at boot. */
void cali_hub_set_aead(cali_hub_seal_fn seal, cali_hub_open_fn open);

typedef struct {
    uint8_t key[16];
    /** @brief This side's random 4-byte session (new per boot and per rekey). */
    uint8_t sess[4];
    /** @brief The next counter to send is tx_ctr + 1. */
    uint64_t tx_ctr;
    /** @brief The highest counter accepted per dir (index 1..3) in the peer session rx_sess[dir]. */
    uint64_t rx_last[4];
    /** @brief The peer session last accepted per dir. */
    uint8_t rx_sess[4][4];
} cali_hub_crypto_t;

/** @brief Loads the key from KV, or makes and stores one; starts a new session. @return 0, -1 if it cannot be stored. */
int cali_hub_key_load_or_make(cali_hub_crypto_t *c);
/** @brief Makes and stores a new key and a new session (the old app is locked out). @return 0 or -1. */
int cali_hub_rekey(cali_hub_crypto_t *c);

/** @brief Seals `pt` (dir 1..3) into `out`. @return the frame length, or 0 (bad dir, `max` too small, cipher failed). */
size_t cali_hub_seal(cali_hub_crypto_t *c, uint8_t dir, const uint8_t *pt, size_t n, uint8_t *out, size_t max);
/**
 * @brief Opens a frame. A counter at or below rx_last[dir] in the same peer session is a replay (-1). A
 *        frame with a different session than the last accepted one for that dir starts a new session
 *        (the counter restarts): the replay of an OLD session is caught by the time check in the RPC layer.
 * @return the plaintext length, or -1 (bad version, dir, size, tag or replay).
 */
int cali_hub_open(cali_hub_crypto_t *c, const uint8_t *frame, size_t n, uint8_t *out, size_t max, uint8_t *dir);

typedef struct {
    char van[24], url[96], user[32], pass[48], app_user[32], app_pass[48];
} cali_hub_cfg_t;

/** @brief @return 0, or -1 (missing or wrong size; `cfg` is then all zero). */
int cali_hub_cfg_load(cali_hub_cfg_t *cfg);
/** @brief @return 0 or -1. */
int cali_hub_cfg_save(const cali_hub_cfg_t *cfg);

/** @brief Writes `OCH1*<van>*<url>*<app user>*<app pass>*<key hex32>`. @return its length, or 0 if it does not fit
 *         or a field holds a '*'. */
size_t cali_hub_qr_text(char *out, size_t max, const cali_hub_cfg_t *cfg, const uint8_t key[16]);

#ifdef __cplusplus
}
#endif

#endif
