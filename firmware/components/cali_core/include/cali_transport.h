/* cali_transport.h — everything cali_core may ask of a BLE stack (#154).
 *
 * cali_core (runner, session) never includes a NimBLE or ESP-IDF header; it talks BLE only through
 * this table. Implementations: cali_ble_nimble/ble_nimble.c (NimBLE: the Linux host build and
 * ESP-IDF) and cali_core/test/runner_fake.c (a scripted fake for tests/firmware/test_runner_fake.py).
 *
 * All calls are async: a call returns 0 when the operation was started (or a nonzero stack error
 * when it could not be), and its completion comes back later as an event through the sink the
 * runner registered with set_sink(). Calls and events are not thread-safe: make every call, and
 * expect every event, on the one task that runs the BLE host (NimBLE: the host task).
 */
#ifndef CALI_TRANSPORT_H
#define CALI_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    CALI_TEV_FOUND,         /* scan saw an advertiser with the requested name; addr set */
    CALI_TEV_CONNECTED,     /* link up (status 0) */
    CALI_TEV_CONNECT_FAIL,  /* connect attempt failed; status = stack error */
    CALI_TEV_PASSKEY_REQ,   /* the peer displays a passkey; type it via inject_passkey() */
    CALI_TEV_ENC_OK,        /* link encrypted (paired, or re-encrypted with a stored bond) */
    CALI_TEV_ENC_FAIL,      /* encryption/pairing failed; status = stack error */
    CALI_TEV_DISCONNECTED,  /* the current link went down; status = reason */
    CALI_TEV_READ,          /* read(char_short) completed; status 0 -> data/len valid */
    CALI_TEV_NOTIFY,        /* notification on char_short; data/len valid */
    CALI_TEV_DISCOVERED,    /* discover() completed; status 0 -> every char is known */
    CALI_TEV_HEARTBEAT,     /* a write_heartbeat() completed; status 0 = written (acknowledged) */
    CALI_TEV_WRITTEN,       /* a write() completed; char_short set; status 0 = ACKed by the unit.
                               May arrive inside the write() call (a request the stack fails at
                               once); never for a write() that returned nonzero */
    CALI_TEV_ADV,           /* a passive scan window saw an advert with manufacturer data: data/len =
                               the mfg AD payload (company id first, little-endian), addr, rssi, name
                               set; status = addr type (0 public, 1 random) */
    CALI_TEV_UNIT_ADV       /* a passive scan window saw the bonded unit's own advert (its address
                               resolved to the bond's identity); no payload — nothing address-like
                               leaves the transport (R_FW_BLE_EXP: the sleep indicator) */
} cali_tev_t;

/* data points into transport memory valid only for the duration of the sink call: copy it. */
typedef struct {
    cali_tev_t ev;
    int status;
    uint16_t char_short;
    const uint8_t *data;
    size_t len;
    char addr[18];          /* "AA:BB:CC:DD:EE:FF" for FOUND and ADV, else "" */
    int8_t rssi;            /* ADV: dBm */
    char name[24];          /* ADV: the local name ("" if none) */
} cali_tevent_t;

typedef void (*cali_tsink_t)(const cali_tevent_t *e, void *ctx);

typedef struct {
    void (*set_sink)(cali_tsink_t sink, void *ctx);
    int  (*start_scan)(const char *name);          /* FOUND when the name matches */
    int  (*stop_scan)(void);
    int  (*connect_found)(void);                   /* CONNECTED / CONNECT_FAIL */
    int  (*connect_bonded)(void);                  /* direct to the stored identity; once CONNECTED
                                                      it re-encrypts with the stored keys itself:
                                                      ENC_OK / ENC_FAIL follow */
    int  (*pair)(void);                            /* PASSKEY_REQ, then ENC_OK / ENC_FAIL */
    int  (*inject_passkey)(uint32_t pk);
    int  (*discover)(void);                        /* DISCOVERED once all chars are known */
    int  (*read)(uint16_t char_short);             /* READ */
    int  (*subscribe)(uint16_t char_short);        /* NOTIFY events afterwards */
    int  (*write_heartbeat)(uint32_t counter);     /* the liveness counter, target 0x1003; HEARTBEAT
                                                      (subscribe's CCCD write only enables
                                                      notifications) */
    int  (*disconnect)(void);
    int  (*remove_bond)(void);
    int  (*has_bond)(void);
    const char *(*identity)(void);                 /* bonded identity address or NULL */
    int  (*write)(uint16_t char_short, const uint8_t *data, size_t len);   /* a control frame, write
                                                      with response: WRITTEN. Refused (nonzero, nothing
                                                      sent) unless cali_ctl_write_ok(char_short, len) */
    int  (*keep_only_bond)(void);                  /* delete every bond but the connected peer's
                                                      (the ESP serves one unit, #255) */
    int  (*scan_window)(uint32_t ms);              /* a passive window of ms; ADV events during it; -1
                                                      when the stack is busy (a connect pending, a
                                                      pairing scan running), 0 started */
} cali_transport_t;

#ifdef __cplusplus
}
#endif

#endif /* CALI_TRANSPORT_H */
