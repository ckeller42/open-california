/* cali_ota.h — firmware update from a GitHub release (R_FW_OTA, #154 standalone): the channel's
 * `esp-stable` (the build the moving `esp-stable` tag points at; the default) or `esp-latest` (every
 * main merge), picked by the kv byte CALI_OTA_CHANNEL_KEY.
 *
 * The portable state machine: fetch manifest.json, compare its sha with the running firmware
 * (cali_fw_version(), the build's `git describe`, i.e. a sha prefix), and on install stream
 * cali_fw.bin into the inactive slot, check the SHA-256 of what landed against the manifest, and
 * only then switch the boot partition and restart. The I/O is the platform's (cali_ota_ops_t:
 * components/cali_ota on the ESP, a scripted fake in test/ota_cli.c).
 *
 * Threads: cali_ota_request / cali_ota_json run on the owner task (web, console); cali_ota_work
 * runs on the OTA task, which may block for minutes on HTTPS. The owner never waits for it.
 *
 * device.ota (cali_ota_json): {"state":"idle|checking|up_to_date|available|downloading|verifying|
 * rebooting|error","available_sha":"<40 hex>"|null,"progress_pct":<0..100>,"error":"<code>"|null,
 * "channel":"stable|latest"}
 * error codes: "manifest" (fetch or parse), "download", "checksum", "commit".
 *
 * C99, no malloc, no ESP-IDF headers. Proven by tests/firmware/test_ota.py (test/ota_cli.c).
 */
#ifndef CALI_OTA_H
#define CALI_OTA_H

#include <stddef.h>
#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

/* printf formats: %s = cali_ota_channel_name() */
#define CALI_OTA_URL_FMT "https://github.com/ckeller42/open-california/releases/download/esp-%s/"
#define CALI_OTA_MANIFEST_FMT CALI_OTA_URL_FMT "manifest.json"
#define CALI_OTA_BIN_FMT CALI_OTA_URL_FMT "cali_fw.bin"
#define CALI_OTA_CHANNEL_KEY "ota_channel"   /* 1 byte: 0 stable, 1 latest; absent = stable */
#define CALI_OTA_MANIFEST_MAX 512        /* the manifest is ~200 B */
#define CALI_OTA_SLOT_SIZE 0x300000u     /* firmware/partitions.csv ota_0 / ota_1 */

enum {
    CALI_OTA_IDLE,
    CALI_OTA_CHECKING,
    CALI_OTA_UP_TO_DATE,
    CALI_OTA_AVAILABLE,
    CALI_OTA_DOWNLOADING,
    CALI_OTA_VERIFYING,
    CALI_OTA_REBOOTING,
    CALI_OTA_ERROR,
};

typedef struct {
    /* GET url (following redirects) into buf, NUL-terminated: the body length, or -1 (an HTTP
     * error status, a body that does not fit cap - 1, no network). */
    int (*get)(const char *url, char *buf, size_t cap);
    /* Stream url into the inactive slot, calling cali_ota_progress as it goes: 0 when exactly size
     * bytes landed, else -1. The slot stays open for slot_sha256 + commit or abort. */
    int (*download)(const char *url, size_t size);
    /* SHA-256 of the first size bytes of the slot just written: 0 ok, -1 read error. */
    int (*slot_sha256)(size_t size, uint8_t out[32]);
    int (*commit)(void);    /* make the slot the boot partition: 0 ok */
    void (*abort)(void);    /* drop the slot; the boot partition is untouched */
    void (*restart)(void);
    void (*wake)(void);     /* any task: have the OTA task call cali_ota_work() */
} cali_ota_ops_t;

/* Owner task, once. ops NULL (host, QEMU): every request answers -1. */
void cali_ota_init(const cali_ota_ops_t *ops);
/* Owner task: start a check (install 0) or a check + install (1) on the OTA task.
 * 0 started, -1 no OTA on this build, -2 busy (one already runs). */
int cali_ota_request(int install);
/* OTA task: run the started request to its end (blocking I/O). No request: returns at once. */
void cali_ota_work(void);
/* From ops->download: bytes written so far of total. */
void cali_ota_progress(size_t done, size_t total);
int cali_ota_state(void);
const char *cali_ota_state_name(int st);
/* The release channel: 0 stable, 1 latest (the kv byte, read once). */
int cali_ota_channel(void);
/* Owner task: store the channel (latest != 0: latest); the next request uses it. 0 ok, -1 store. */
int cali_ota_set_channel(int latest);
const char *cali_ota_channel_name(int ch);   /* "stable" / "latest" */
/* The device.ota object (a value: call cali_json_key first). */
void cali_ota_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_OTA_H */
