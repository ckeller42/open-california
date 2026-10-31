/**
 * @file cali_ota.h
 * @brief Firmware update from a GitHub release: the portable state machine behind `POST /api/ota`.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite updates itself on request from the GitHub release, verified, with rollback
 *    :id: R_FW_OTA
 *    :status: implemented
 *    :tags: esp32, ota
 *
 *    On request only (``POST /api/ota``, station mode only, or the console), the firmware shall fetch
 *    its channel's release's (``esp-stable`` by default, ``esp-latest`` once the stored channel is
 *    ``latest``) ``manifest.json`` over HTTPS on its own task (never blocking the BLE
 *    session or the web server), report ``up_to_date`` when its sha is the running build's and
 *    ``available`` otherwise, and on install write ``cali_fw.bin`` into the inactive OTA slot, check
 *    the SHA-256 of what landed against the manifest, and only on a match switch the boot partition
 *    and restart; any failure leaves the boot partition untouched. A new image stays revocable until
 *    it has run 60 s healthy (tick alive, web server listening); a reset before that boots the
 *    previous slot. Progress is ``device.ota`` in ``/api/state``. The channel (``stable``: the build the
 *    ``esp-stable`` tag points at; ``latest``: every main merge) is persisted and set with
 *    ``POST /api/ota {"channel"}``.
 * @endverbatim
 *
 * The channel is `esp-stable` (the build the moving `esp-stable` tag points at; the default) or
 * `esp-latest` (every main merge), picked by the kv byte CALI_OTA_CHANNEL_KEY.
 *
 * The state machine fetches manifest.json, compares its sha with the running firmware
 * (cali_fw_version(), the build's `git describe`, i.e. a sha prefix), and on install streams
 * cali_fw.bin into the inactive slot, checks the SHA-256 of what landed against the manifest, and
 * only then switches the boot partition and restarts. The I/O is the platform's (cali_ota_ops_t:
 * components/cali_ota on the ESP, a scripted fake in test/ota_cli.c).
 *
 * Threads: cali_ota_request() and cali_ota_json() run on the owner task (web, console);
 * cali_ota_work() runs on the OTA task, which may block for minutes on HTTPS. The owner never waits
 * for it.
 *
 * The `device.ota` object (cali_ota_json()) reads
 * `{"state":"idle|checking|up_to_date|available|downloading|verifying|rebooting|error",
 * "available_sha":"40 hex digits"|null,"progress_pct":0..100,"error":"code"|null,
 * "channel":"stable|latest"}`. The error codes are `manifest` (fetch or parse), `download`,
 * `checksum` and `commit`.
 *
 * C99, no malloc, no ESP-IDF headers. Proven by `tests/firmware/test_ota.py` (`test/ota_cli.c`).
 */
#ifndef CALI_OTA_H
#define CALI_OTA_H

#include <stddef.h>
#include <stdint.h>

#include "cali_json.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief printf format of the release URL; the `%s` takes cali_ota_channel_name(). */
#define CALI_OTA_URL_FMT "https://github.com/ckeller42/open-california/releases/download/esp-%s/"
/** @brief printf format of the manifest URL (`%s` = the channel name). */
#define CALI_OTA_MANIFEST_FMT CALI_OTA_URL_FMT "manifest.json"
/** @brief printf format of the firmware image URL (`%s` = the channel name). */
#define CALI_OTA_BIN_FMT CALI_OTA_URL_FMT "cali_fw.bin"
/** @brief The kv key of the channel: 1 byte, 0 stable, 1 latest; absent means stable. */
#define CALI_OTA_CHANNEL_KEY "ota_channel"
/** @brief The largest manifest in bytes (a real one is about 200). */
#define CALI_OTA_MANIFEST_MAX 512
/** @brief The size of one OTA slot in bytes (`firmware/partitions.csv`, ota_0 and ota_1). */
#define CALI_OTA_SLOT_SIZE 0x300000u

/** @brief The OTA states; cali_ota_state() returns one of them. */
enum cali_ota_state_e {
    CALI_OTA_IDLE,         /**< No request yet. */
    CALI_OTA_CHECKING,     /**< Fetching and comparing the manifest. */
    CALI_OTA_UP_TO_DATE,   /**< The manifest's sha is the running build's. */
    CALI_OTA_AVAILABLE,    /**< The manifest names another build. */
    CALI_OTA_DOWNLOADING,  /**< Streaming the image into the inactive slot. */
    CALI_OTA_VERIFYING,    /**< Checking the SHA-256 of the slot. */
    CALI_OTA_REBOOTING,    /**< The boot partition is switched; the restart follows. */
    CALI_OTA_ERROR,        /**< A step failed; the boot partition is untouched. */
};

/** @brief The platform's I/O for the OTA state machine. */
typedef struct {
    /**
     * @brief GET a URL, following redirects, into a buffer (NUL-terminated).
     * @param url The URL.
     * @param buf The destination.
     * @param cap The size of buf.
     * @return The body length, or -1 (an HTTP error status, a body that does not fit cap - 1, no network).
     */
    int (*get)(const char *url, char *buf, size_t cap);
    /**
     * @brief Stream a URL into the inactive slot, calling cali_ota_progress() as it goes.
     * The slot stays open for slot_sha256 and then commit or abort.
     * @param url The URL of the image.
     * @param size The expected size in bytes.
     * @return 0 when exactly size bytes landed, else -1.
     */
    int (*download)(const char *url, size_t size);
    /**
     * @brief The SHA-256 of the first bytes of the slot just written.
     * @param size The number of bytes to hash.
     * @param out The 32-byte digest.
     * @return 0 ok, -1 read error.
     */
    int (*slot_sha256)(size_t size, uint8_t out[32]);
    int (*commit)(void);    /**< Make the slot the boot partition: 0 ok. */
    void (*abort)(void);    /**< Drop the slot; the boot partition is untouched. */
    void (*restart)(void);  /**< Restart the chip. */
    void (*wake)(void);     /**< Any task: have the OTA task call cali_ota_work(). */
} cali_ota_ops_t;

/**
 * @brief Set the platform I/O. Owner task, once.
 * @param ops The I/O. NULL (host, QEMU): every request answers -1.
 */
void cali_ota_init(const cali_ota_ops_t *ops);
/**
 * @brief Start a check or a check and install on the OTA task. Owner task.
 * @param install 0 for a check, 1 for a check and install.
 * @return 0 started, -1 no OTA on this build, -2 busy (one already runs).
 */
int cali_ota_request(int install);
/** @brief Run the started request to its end (blocking I/O). OTA task. With no request it returns at once. */
void cali_ota_work(void);
/**
 * @brief Report the download progress. Called from ops->download.
 * @param done Bytes written so far.
 * @param total Bytes in all.
 */
void cali_ota_progress(size_t done, size_t total);
/** @brief The current state. @return One of #cali_ota_state_e. */
int cali_ota_state(void);
/**
 * @brief The name of a state as it appears in `device.ota`.
 * @param st One of #cali_ota_state_e.
 * @return A static string.
 */
const char *cali_ota_state_name(int st);
/** @brief The release channel (the kv byte, read once). @return 0 stable, 1 latest. */
int cali_ota_channel(void);
/**
 * @brief Store the release channel; the next request uses it. Owner task.
 * @param latest Not 0 for latest, 0 for stable.
 * @return 0 ok, -1 store failed.
 */
int cali_ota_set_channel(int latest);
/**
 * @brief The name of a channel.
 * @param ch 0 or 1, as cali_ota_channel() returns.
 * @return `stable` or `latest`.
 */
const char *cali_ota_channel_name(int ch);
/**
 * @brief Write the `device.ota` object. It is a value: call cali_json_key() first.
 * @param j The JSON writer.
 */
void cali_ota_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_OTA_H */
