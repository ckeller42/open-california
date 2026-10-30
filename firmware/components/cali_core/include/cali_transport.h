/**
 * @file cali_transport.h
 * @brief Everything cali_core may ask of a BLE stack.
 *
 * cali_core (runner, session) never includes a NimBLE or ESP-IDF header; it talks BLE only through
 * the cali_transport_t table. Implementations: `cali_ble_nimble/ble_nimble.c` (NimBLE: the Linux
 * host build and ESP-IDF) and `cali_core/test/runner_fake.c` (a scripted fake for the firmware
 * tests).
 *
 * All calls are async: a call returns 0 when the operation was started (or a nonzero stack error
 * when it could not be), and its completion comes back later as an event through the sink the
 * runner registered with `set_sink()`. Calls and events are not thread-safe: make every call, and
 * expect every event, on the one task that runs the BLE host (NimBLE: the host task).
 */
#ifndef CALI_TRANSPORT_H
#define CALI_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The kind of a transport event. */
typedef enum {
    CALI_TEV_FOUND,         /**< scan saw an advertiser with the requested name; addr set */
    CALI_TEV_CONNECTED,     /**< link up (status 0) */
    CALI_TEV_CONNECT_FAIL,  /**< connect attempt failed; status = stack error */
    CALI_TEV_PASSKEY_REQ,   /**< the peer displays a passkey; type it via inject_passkey() */
    CALI_TEV_ENC_OK,        /**< link encrypted (paired, or re-encrypted with a stored bond) */
    CALI_TEV_ENC_FAIL,      /**< encryption/pairing failed; status = stack error */
    CALI_TEV_DISCONNECTED,  /**< the current link went down; status = reason */
    CALI_TEV_READ,          /**< read(char_short) completed; status 0 -> data/len valid */
    CALI_TEV_NOTIFY,        /**< notification on char_short; data/len valid */
    CALI_TEV_DISCOVERED,    /**< discover() completed; status 0 -> every char is known */
    CALI_TEV_HEARTBEAT,     /**< a write_heartbeat() completed; status 0 = written (acknowledged) */
    CALI_TEV_WRITTEN,       /**< a write() completed; char_short set; status 0 = ACKed by the unit.
                               May arrive inside the write() call (a request the stack fails at
                               once); never for a write() that returned nonzero */
    CALI_TEV_ADV,           /**< a passive scan window saw an advert with manufacturer data: data/len =
                               the mfg AD payload (company id first, little-endian), addr, rssi, name
                               set; status = addr type (0 public, 1 random) */
    CALI_TEV_UNIT_ADV       /**< a passive scan window saw the bonded unit's own advert (its address
                               resolved to the bond's identity); no payload — nothing address-like
                               leaves the transport (R_FW_BLE_EXP: the sleep indicator) */
} cali_tev_t;

/**
 * @brief One transport event, as delivered to the sink.
 *
 * `data` points into transport memory valid only for the duration of the sink call: copy it.
 */
typedef struct {
    cali_tev_t ev;          /**< The event kind. */
    int status;             /**< 0 = success, else a stack error or reason; per-event meaning above. */
    uint16_t char_short;    /**< The characteristic's short UUID, for READ, NOTIFY and WRITTEN. */
    const uint8_t *data;    /**< Payload for READ, NOTIFY and ADV; NULL otherwise. */
    size_t len;             /**< Length of `data`. */
    char addr[18];          /**< "AA:BB:CC:DD:EE:FF" for FOUND and ADV, else "". */
    int8_t rssi;            /**< ADV: signal strength in dBm. */
    char name[24];          /**< ADV: the local name ("" if none). */
} cali_tevent_t;

/**
 * @brief Receiver of transport events.
 * @param e   The event; valid only during the call.
 * @param ctx The pointer passed to `set_sink()`.
 */
typedef void (*cali_tsink_t)(const cali_tevent_t *e, void *ctx);

/**
 * @brief The BLE stack as cali_core sees it: a table of async operations.
 *
 * Every operation except the two queries `has_bond` and `identity` returns 0 when started and
 * nonzero when the stack could not start it. The outcome comes back as an event (named per member)
 * through the registered sink, except for `subscribe`, which has no completion event (its notifications
 * arrive later as NOTIFY). `has_bond`
 * and `identity` answer at once with their value, so a nonzero result is not a failure.
 */
typedef struct {
    /** Register the event sink. @param sink The receiver. @param ctx Passed back with each event. */
    void (*set_sink)(cali_tsink_t sink, void *ctx);
    /** Scan for an advertiser with this local name; FOUND when it matches. */
    int  (*start_scan)(const char *name);
    /** Stop the scan. */
    int  (*stop_scan)(void);
    /** Connect to the advertiser FOUND reported; CONNECTED or CONNECT_FAIL follows. */
    int  (*connect_found)(void);
    /** Connect directly to the stored bond identity. Once CONNECTED, the stack re-encrypts with the
     *  stored keys itself: ENC_OK or ENC_FAIL follows. */
    int  (*connect_bonded)(void);
    /** Start pairing: PASSKEY_REQ, then ENC_OK or ENC_FAIL. */
    int  (*pair)(void);
    /** Answer a PASSKEY_REQ with the passkey the unit displays. */
    int  (*inject_passkey)(uint32_t pk);
    /** Discover the unit's characteristics; DISCOVERED once all are known. */
    int  (*discover)(void);
    /** Read a characteristic by short UUID; READ follows. */
    int  (*read)(uint16_t char_short);
    /** Subscribe to notifications of a characteristic; NOTIFY events follow. */
    int  (*subscribe)(uint16_t char_short);
    /** Write the liveness counter to characteristic 0x1003; HEARTBEAT follows. (The CCCD write of
     *  `subscribe` only enables notifications.) */
    int  (*write_heartbeat)(uint32_t counter);
    /** Drop the current link; DISCONNECTED follows. */
    int  (*disconnect)(void);
    /** Delete the stored bond. */
    int  (*remove_bond)(void);
    /** @return 1 when a bond is stored, else 0. */
    int  (*has_bond)(void);
    /** @return The bonded identity address as "AA:BB:CC:DD:EE:FF", or NULL when there is none. */
    const char *(*identity)(void);
    /** Write a control frame with response; WRITTEN follows. Refused (nonzero, nothing sent) unless
     *  `cali_ctl_write_ok(char_short, len)` allows it. */
    int  (*write)(uint16_t char_short, const uint8_t *data, size_t len);
    /** Delete every bond except the connected peer's: the satellite serves one unit. */
    int  (*keep_only_bond)(void);
    /** Run a passive scan window of `ms` milliseconds; ADV events arrive during it. Returns 0 when
     *  started and -1 when the stack is busy (a connect is pending or a pairing scan runs). */
    int  (*scan_window)(uint32_t ms);
} cali_transport_t;

#ifdef __cplusplus
}
#endif

#endif /* CALI_TRANSPORT_H */
