/* cali_ble_nimble.h — the firmware's NimBLE glue (#154).
 *
 * Store part (ble_store_kv.c): NimBLE's bond store on the platform kv-store (cali_platform.h), so
 * bonds survive a restart on both builds — files on the host, NVS on the ESP32.
 * Transport part (ble_nimble.c): cali_transport_t (cali_core/include/cali_transport.h) on NimBLE.
 */
#ifndef CALI_BLE_NIMBLE_H
#define CALI_BLE_NIMBLE_H

#include "cali_transport.h"

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

/* Re-assert the store: if ble_hs_cfg.store_{read,write,delete}_cb are not ours any more, log
 * "LOG store: ERROR bond store callbacks were replaced by the BLE stack, re-installed", put them
 * back (the RAM tables loaded by cali_ble_store_init() are kept) and return 1; else return 0.
 * cali_ble_nimble's sync callback calls it before anything can pair or reconnect: ESP-IDF's
 * esp-nimble swaps in its RAM-only ble_store_config during host sync when
 * CONFIG_BT_NIMBLE_STATIC_TO_DYNAMIC=y (ble_hs_pvcy.c; firmware/README.md "ESP-IDF build"). */
int cali_ble_store_ensure(void);

/* Configure the NimBLE host for the unit: LE Secure Connections passkey entry (io_cap
 * KEYBOARD_ONLY, MITM, bonding, ENC+ID key distribution) in ble_hs_cfg, the persistent bond store
 * (cali_ble_store_init(), so cali_platform_init() must have run), and the host's sync/reset
 * callbacks. Call after nimble_port_init() and BEFORE nimble_port_run() / host sync, so no link
 * ever exists with other SM settings. `on_sync` (may be NULL) runs on the host task once the host
 * is synced: from then on the transport may be used. */
void cali_ble_nimble_init(void (*on_sync)(void));

/* The NimBLE transport. Every call, and every event it delivers, runs on the NimBLE host task
 * (marshal console input there through ble_npl_eventq_put, as firmware/host/host_main.c does).
 * Notes: subscribe() needs discover() first (it returns BLE_HS_ENOTSUP for a char without NOTIFY/
 * INDICATE) and reports failures only as a LOG line. write_heartbeat() writes with response (like
 * calictl.device's heartbeat) and reports every completion as HEARTBEAT (status = ATT/host error),
 * so the session can treat a failed beat as a lost link. connect_bonded() re-encrypts by itself
 * once connected (ENC_OK / ENC_FAIL). disconnect() is silent: the link it drops (or the
 * connect it cancels) produces no DISCONNECTED/CONNECT_FAIL; only a drop the stack or the peer
 * caused is reported. remove_bond() drops the link, then deletes every bonded peer. pair()
 * replaces a bond already stored for the connected peer, so a fresh SMP pairing runs even when
 * the unit forgot us (it logs "LOG pair: replacing stored bond"). */
const cali_transport_t *cali_ble_nimble_transport(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_BLE_NIMBLE_H */
