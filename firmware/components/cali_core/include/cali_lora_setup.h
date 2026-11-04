/**
 * @file cali_lora_setup.h
 * @brief LoRa onboarding, the platform-free part: the packed form of a The Things Stack key, the setup
 *        and repair QR strings, and the reassembly and decryption of the key-transfer downlink.
 *
 * A key `NNSXS.<39 base32>.<52 base32>` packs into 25 + 33 = 58 bytes (the prefix and the dot are implied).
 * The key transfer arrives on FPort 12 as fragments `seq(1) | body`: bit 7 of `seq` marks the last
 * fragment, bits 0..2 are the index, bits 3..6 stay 0. Every fragment but the last carries exactly
 * CALI_LORA_FRAG_BODY bytes, so fragment `i` lands at offset `i * CALI_LORA_FRAG_BODY`. The reassembled
 * ciphertext is `nonce(12) | body | tag(16)`; AES-128-GCM with the transfer key opens it, with the DevEUI as
 * associated data. The decryption itself is passed in (`cali_lora_aead_open_fn`): no platform crypto is
 * linked here.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite shows LoRa setup QR strings and accepts the key transfer in the agreed formats
 *    :id: R_FW_LORA_SETUP
 *    :status: implemented
 *    :tags: esp32, experimental, lora
 *
 *    The setup QR text shall be ``OCS1*<DEVEUI 16 hex>*<APPKEY 32 hex>*<TRANSFERKEY 32 hex>`` (87
 *    characters, upper case). The repair QR text shall be ``OCR1*<CLUSTER>*<APP_ID>*<DEV_ID>*<READ_KEY>*<CMD_KEY>``
 *    with the IDs in upper case and the keys in their normal ``NNSXS.…`` form; both use only QR alphanumeric
 *    characters. The key-transfer plaintext (version 1) shall be ``ver(1) | cluster_len(1) | cluster |
 *    app_len(1) | app_id | dev_len(1) | dev_id | read_key(58) | cmd_key(58)``; the cluster is at most 64
 *    characters, the app ID and the device ID at most 36. It shall travel on FPort 12 as AES-128-GCM
 *    ciphertext (12-byte nonce, 16-byte tag, the DevEUI as associated data) cut into fragments of at most
 *    50 bytes, each ``seq(1)`` (bit 7 = last, bits 0..2 = index) followed by the bytes. The confirmation
 *    uplink on FPort 12 shall be ``status(1) | sha256(plaintext)[0..3]``: status 0 ok, 1 bad tag; status 2
 *    (missing fragments) is ``02 | bitmap`` with bit ``i`` set for every received index ``i``; status 3
 *    (``03``) answers a fragment that arrives without a transfer key. ``lora_ttn`` shall be stored only
 *    from a plaintext with a valid tag that fits ``lora_ttn`` and encodes as the repair QR (else status 1).
 *    The confirmation shall go out as soon as the 24-h airtime budget has room, ahead of ports 2/3/4; a
 *    send without ``+EVT:TX_DONE`` is repeated after at least 60 s, at most 5 times, then dropped.
 *    ``lora_tk`` shall be erased after a status-0 confirmation that the module confirmed with
 *    ``+EVT:TX_DONE``, and only while it is still the key of that transfer. The display shall show the setup QR (the module's DevEUI) until
 *    ``lora_ttn`` exists, then the repair QR, and none while ``exp_lora`` is off or the module is absent. The setup
 *    keys shall live in NVS: ``lora_dev`` (AppKey 16 B + JoinEUI 8 B), ``lora_tk`` (the transfer key, a new
 *    one at every setup start) and ``lora_ttn`` (the delivered plaintext). The AppKey, the transfer key and
 *    the TTN keys shall never appear in ``/api/state``, the console, the log or any other output;
 *    Only a missing ``lora_dev`` shall be made; a store read error shall never regenerate the AppKey.
 *    ``device.lora`` shall report only ``setup`` (``none``/``keys_made``/``joined``/``ttn_keys``/``error``),
 *    ``has_ttn_keys`` and the DevEUI (``dev_eui``, ``null`` while unknown).
 * @endverbatim
 *
 * C99, no malloc, no ESP-IDF headers. Proven by `tests/firmware/test_lora_setup.py`
 * (`test/lora_setup_cli.c`).
 */
#ifndef CALI_LORA_SETUP_H
#define CALI_LORA_SETUP_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The FPort of the key transfer (downlink) and of its confirmation (uplink). */
#define CALI_LORA_XFER_PORT 12
/** @brief The packed size of one The Things Stack key in bytes. */
#define CALI_LORA_KEY_PACKED 58
/** @brief The most bytes of one whole fragment, the `seq` byte included. */
#define CALI_LORA_FRAG_MAX 51
/** @brief The most body bytes of one fragment (CALI_LORA_FRAG_MAX - 1). */
#define CALI_LORA_FRAG_BODY 50
/** @brief The largest reassembled ciphertext in bytes (nonce + body + tag). */
#define CALI_LORA_XFER_MAX 320
/** @brief The length of an unpacked key in characters, without the NUL. */
#define CALI_LORA_KEY_LEN 98
/** @brief The longest cluster name in characters. */
#define CALI_LORA_CLUSTER_MAX 64
/** @brief The longest application ID or device ID in characters. */
#define CALI_LORA_ID_MAX 36

/**
 * @brief Opens one AES-128-GCM message.
 * @param key The 16-byte transfer key.
 * @param nonce The 12-byte nonce.
 * @param ad The associated data (the DevEUI).
 * @param ad_len The length of `ad`.
 * @param ct The ciphertext.
 * @param ct_len The length of `ct` and of `out`.
 * @param tag The 16-byte tag.
 * @param out Receives the plaintext.
 * @return 0 when the tag is valid, -1 when it is not.
 */
typedef int (*cali_lora_aead_open_fn)(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad,
                                      size_t ad_len, const uint8_t *ct, size_t ct_len,
                                      const uint8_t *tag, uint8_t *out);

/** @brief The state of one key transfer in progress. */
typedef struct {
    /** @brief The reassembled ciphertext. */
    uint8_t buf[CALI_LORA_XFER_MAX];
    /** @brief The bitmap of the fragment indexes received (bit i = index i). */
    uint8_t have;
    /** @brief 0 until the fragment with the last bit arrived, then its index + 1. */
    uint8_t last;
    /** @brief The body length of each received fragment. */
    size_t len[8];
} cali_lora_xfer_t;

/**
 * @brief Packs a key.
 * @param key `NNSXS.` + 39 upper-case base32 characters + `.` + 52 base32 characters, NUL-terminated.
 * @param out Receives CALI_LORA_KEY_PACKED bytes.
 * @return 0, or -1 when `key` has another form.
 */
int cali_lora_key_pack(const char *key, uint8_t *out);

/**
 * @brief Unpacks a key.
 * @param in CALI_LORA_KEY_PACKED bytes.
 * @param out Receives CALI_LORA_KEY_LEN characters and a NUL (99 bytes).
 * @return 0, or -1 when a padding bit is not zero.
 */
int cali_lora_key_unpack(const uint8_t *in, char *out);

/**
 * @brief Writes the setup QR text.
 * @param out Receives the NUL-terminated text.
 * @param max The size of `out`.
 * @param deveui The 8-byte DevEUI.
 * @param appkey The 16-byte AppKey.
 * @param tk The 16-byte transfer key.
 * @return The text length (87), or 0 when `max` is too small.
 */
size_t cali_lora_setup_qr(char *out, size_t max, const uint8_t *deveui, const uint8_t *appkey,
                          const uint8_t *tk);

/**
 * @brief Writes the repair QR text from a key-transfer plaintext.
 * @param out Receives the NUL-terminated text.
 * @param max The size of `out`.
 * @param plain The plaintext.
 * @param len The length of `plain`.
 * @return The text length, or 0 for a bad plaintext (version, lengths, characters) or a small `max`.
 */
size_t cali_lora_repair_qr(char *out, size_t max, const uint8_t *plain, size_t len);

/**
 * @brief Clears a transfer.
 * @param x The transfer.
 */
void cali_lora_xfer_reset(cali_lora_xfer_t *x);

/**
 * @brief Takes one fragment.
 * @param x The transfer.
 * @param frag The FPort 12 payload.
 * @param n The length of `frag`.
 * @param tk The 16-byte transfer key.
 * @param deveui The 8-byte DevEUI (associated data).
 * @param open Opens the AES-128-GCM message.
 * @param plain Receives the plaintext on status 0; CALI_LORA_XFER_MAX bytes. It holds a secret: the caller
 *        wipes it after use. On a bad tag it is zeroed.
 * @param plain_len Receives the plaintext length (0 unless status 0).
 * @param confirm Receives the confirmation payload; 6 bytes.
 * @param confirm_len Receives the confirmation length.
 * @return -1 for a malformed or inconsistent fragment (the transfer is reset); 0 when nothing is to be
 *         sent yet; 2 when the fragment with the last bit arrived and fragments are missing (`confirm` =
 *         `02 | bitmap`, the transfer goes on); 1 when the transfer is complete (`confirm` = `00 | sha256[0..3]`
 *         with `plain` valid, or `01` for a bad tag; the transfer is reset).
 */
int cali_lora_xfer_rx(cali_lora_xfer_t *x, const uint8_t *frag, size_t n, const uint8_t *tk,
                      const uint8_t *deveui, cali_lora_aead_open_fn open, uint8_t *plain,
                      size_t *plain_len, uint8_t *confirm, size_t *confirm_len);

/**
 * @brief The first 4 bytes of SHA-256.
 * @param in The message.
 * @param n The length of `in`.
 * @param out Receives 4 bytes.
 */
void cali_lora_sha256_4(const uint8_t *in, size_t n, uint8_t *out);

#ifdef __cplusplus
}
#endif

#endif
