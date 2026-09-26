/* cali_ble_nimble.h — the firmware's NimBLE glue (#154).
 *
 * Store part (ble_store_kv.c): NimBLE's bond store on the platform kv-store (cali_platform.h), so
 * bonds survive a restart on both builds — files on the host, NVS on the ESP32.
 */
#ifndef CALI_BLE_NIMBLE_H
#define CALI_BLE_NIMBLE_H

#ifdef __cplusplus
extern "C" {
#endif

/* Load the persisted bond/CCCD records from the kv-store and point
 * ble_hs_cfg.store_{read,write,delete}_cb at them. Call after cali_platform_init() and before the
 * host syncs (instead of ble_store_config_init()). Keys: sec_our_<n>, sec_peer_<n> (n <
 * BLE_STORE_MAX_BONDS), cccd_<n> (n < BLE_STORE_MAX_CCCDS); an empty value marks a free slot. A
 * corrupt record is treated as "no record" and logged once ("LOG store: corrupt record <key>
 * ignored"), so a damaged store boots unpaired. */
void cali_ble_store_init(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_BLE_NIMBLE_H */
