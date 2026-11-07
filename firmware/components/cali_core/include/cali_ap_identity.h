/**
 * @file cali_ap_identity.h
 * @brief The satellite's own setup hotspot: a name from the MAC, a random stored password.
 *
 * Every satellite opens its own WPA2 hotspot while it has no WiFi. The SSID is ``calictl-esp-XXXX``
 * (``NET_AP_SSID_PREFIX`` plus the upper-case hex of MAC bytes 4 and 5). The password is 14
 * characters ``xxxx-xxxx-xxxx`` from the alphabet ``23456789abcdefghijkmnpqrstuvwxyz`` (no 0, 1, l,
 * o), drawn from ``cali_random()`` on the first call and kept in the KV store under ``ap_psk``. It
 * is never derived from the MAC and never leaves the unit except on its display. A stored value
 * that is missing or not of that shape (a power cut mid-write) is replaced by a new one.
 */
#ifndef CALI_AP_IDENTITY_H
#define CALI_AP_IDENTITY_H

#include <stddef.h>

#include "net_consts.h"

#ifdef __cplusplus
extern "C" {
#endif

/** KV key of the stored hotspot password. */
#define CALI_AP_KEY_PSK "ap_psk"
/** Length of the hotspot password, including its two dashes. */
#define CALI_AP_PSK_LEN 14

/** KV key (namespace cali): 1 byte = a first-time setup began on the hotspot and is not done. Set only by
 * POST /api/wifi while the unit is in setup mode; erased by POST /api/setup {"done":true}. Absent = not
 * pending (a field unit that was set up before never sees the wizard). */
#define CALI_SETUP_QR_KEY "setup_qr"

/** Return code of cali_ap_identity(): the password is stored (it survives a reboot). */
#define CALI_AP_STORED 0
/** Return code of cali_ap_identity(): the password is valid and in use, but the KV store refused it,
 * so the next boot makes a new one. The hotspot still works; only its password is not lasting. */
#define CALI_AP_UNSTORED 1

/**
 * @brief Give the hotspot's name and password; make and store the password on the first call.
 *
 * The first successful call keeps the result in RAM; every later call returns that copy and does not
 * touch the KV store.
 * @param ssid Receives ``calictl-esp-XXXX``.
 * @param psk Receives the 14-character password.
 * @return CALI_AP_STORED (0); CALI_AP_UNSTORED (1), the outputs are valid; -1 when the MAC is
 *         unreadable (the outputs are then not valid).
 */
int cali_ap_identity(char ssid[NET_SSID_MAX + 1], char psk[NET_PSK_MAX + 1]);

/**
 * @brief The text of the QR code that joins the hotspot: ``WIFI:T:WPA;S:<ssid>;P:<psk>;;``.
 * @param out Receives the text, NUL-terminated.
 * @param cap Size of @p out in bytes.
 * @return The text length (also for CALI_AP_UNSTORED); -1 when the identity is unavailable or the text does not fit.
 */
int cali_ap_wifi_qr(char *out, size_t cap);

/**
 * @brief Whether a first-time setup began on the hotspot and is not done (kv CALI_SETUP_QR_KEY).
 *
 * Read from the KV store once and kept in RAM; only cali_setup_pending_set() writes the key, so the
 * copy is exact. Cheap enough for the 200 ms display refresh.
 * @return 1 pending, 0 not.
 */
int cali_setup_pending(void);

/**
 * @brief Mark the first-time setup begun (1) or done (0): writes or erases CALI_SETUP_QR_KEY.
 * @param on Nonzero = pending.
 * @return 0; -1 when the KV store refused (the RAM copy then stays as it was).
 */
int cali_setup_pending_set(int on);

#ifdef __cplusplus
}
#endif

#endif /* CALI_AP_IDENTITY_H */
