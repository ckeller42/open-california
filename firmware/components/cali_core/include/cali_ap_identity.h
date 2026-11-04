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

/**
 * @brief Give the hotspot's name and password; make and store the password on the first call.
 * @param ssid Receives ``calictl-esp-XXXX``.
 * @param psk Receives the 14-character password.
 * @return 0 ok; -1 when the MAC is unreadable or the new password cannot be stored (the outputs are
 *         then not valid).
 */
int cali_ap_identity(char ssid[NET_SSID_MAX + 1], char psk[NET_PSK_MAX + 1]);

/**
 * @brief The text of the QR code that joins the hotspot: ``WIFI:T:WPA;S:<ssid>;P:<psk>;;``.
 * @param out Receives the text, NUL-terminated.
 * @param cap Size of @p out in bytes.
 * @return The text length; -1 when the identity is unavailable or the text does not fit.
 */
int cali_ap_wifi_qr(char *out, size_t cap);

#ifdef __cplusplus
}
#endif

#endif /* CALI_AP_IDENTITY_H */
