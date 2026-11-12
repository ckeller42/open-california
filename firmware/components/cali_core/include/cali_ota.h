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
 *    its verdict is valid: the camper link up once, or no advertising unit by 15 min with the BLE
 *    host synced (never before 60 s, never while the web server is down). The unit advertising
 *    without a link for 40 min, or a BLE host that never synced by 15 min, fails it: it is never
 *    confirmed, and a reset boots the previous slot. The verdict is ``device.ota.verify``. Progress is ``device.ota`` in ``/api/state``. The channel (``stable``: the build the
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
 * "channel":"stable|latest","verify":"pending|valid|failed"}`. The error codes are `manifest` (fetch
 * or parse), `download`, `checksum` and `commit`.
 *
 * `verify` is the verdict on a freshly installed image (cali_ota_verify_step()): `pending` while the
 * bootloader still holds the previous slot as the fallback, `valid` once the image is confirmed (and
 * on every image that needs no verdict), `failed` when the image showed a dead camper link. A failed
 * image is never confirmed: the next reset boots the previous slot. Until then no other OTA can start
 * (the bootloader refuses one while the running image is unconfirmed).
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
/** @brief The verdicts on a freshly installed image (`device.ota.verify`). */
enum cali_ota_verify_e {
    CALI_OTA_VERIFY_PENDING,   /**< Not decided yet: a reset boots the previous slot. */
    CALI_OTA_VERIFY_VALID,     /**< Confirm the image (or there is nothing to confirm). */
    CALI_OTA_VERIFY_FAILED,    /**< Never confirm it: the next reset boots the previous slot. */
};
/** @brief Never a verdict before this run time (the tick and the web server live this long). */
#define CALI_OTA_VERIFY_MIN_MS 60000u
/**
 * @brief No counted unit advert by this run time: valid. The unit's absence (camper unit off, the van
 * parked alone) is no fault, so a healthy image is not held back by it.
 */
#define CALI_OTA_VERIFY_T_MS (15u * 60000u)
/**
 * @brief The unit advertised (with a bond, the BLE mode not off) and no link came up this long after the
 * first advert: failed. Longer than the power-save rest poll (#CALI_SESSION_REST_POLL_DEFAULT_MIN,
 * 30 min), the only time the session leaves an advertising unit alone on purpose.
 */
#define CALI_OTA_VERIFY_W_MS (40u * 60000u)
/**
 * @brief An advert older than this ends the window (the unit fell asleep again: a door opened, then
 * shut); the next advert starts a new one, so only a unit that keeps advertising without a link fails.
 * Five sensor scan windows (one a minute) long.
 */
#define CALI_OTA_VERIFY_GONE_MS (5u * 60000u)

/** @brief What cali_ota_verify_step() reads each tick; the int members are 0 or 1. */
typedef struct {
    int web_up;           /**< The web server listens. */
    int link_up;          /**< The camper link is up (`device.link.up`). */
    int64_t adv_ago_ms;   /**< ms since the bonded unit's own advert (`unit_adv_seen_ms_ago`), -1 never. */
    int bonded;           /**< A bond is stored. */
    int ble_off;          /**< The BLE mode is off: the session does not connect on purpose. */
    int ble_synced;       /**< The BLE host has synced with its controller. */
} cali_ota_verify_in_t;

/**
 * @brief Start (or skip) the verdict. Owner task, once at boot; times count from now_ms.
 * @param pending Not 0: the running image awaits its verdict (the bootloader's pending-verify state).
 *                0: nothing to verify, the verdict is `valid` at once.
 * @param now_ms The current run time.
 * @param min_ms #CALI_OTA_VERIFY_MIN_MS (a test passes shorter times, here and below).
 * @param t_ms #CALI_OTA_VERIFY_T_MS.
 * @param w_ms #CALI_OTA_VERIFY_W_MS.
 * @param gone_ms #CALI_OTA_VERIFY_GONE_MS.
 */
void cali_ota_verify_begin(int pending, uint64_t now_ms, uint32_t min_ms, uint32_t t_ms, uint32_t w_ms,
                           uint32_t gone_ms);
/**
 * @brief Advance the verdict. Owner task, every tick. Once `valid` or `failed`, it stays.
 *
 * Valid: the link was up once (from the begin on, latched: a rest probe's link is brief). Failed: with
 * a bond and the BLE mode not off, the unit kept advertising (no gap of gone_ms) and no link came up
 * within w_ms of the window's first advert. Failed: the BLE host never synced by t_ms (a dead BLE
 * stack sees no advert at all, so it would otherwise read as "no unit"). Valid: at t_ms or later, no
 * window open (the unit is not advertising). Nothing before min_ms, nothing while the web server is
 * down. Every verdict logs and emits an `ota_verify` diagnostics event.
 * @param now_ms The current run time.
 * @param in This tick's inputs.
 * @return One of #cali_ota_verify_e.
 */
int cali_ota_verify_step(uint64_t now_ms, const cali_ota_verify_in_t *in);
/** @brief The current verdict. @return One of #cali_ota_verify_e. */
int cali_ota_verify(void);

/**
 * @brief Write the `device.ota` object. It is a value: call cali_json_key() first.
 * @param j The JSON writer.
 */
void cali_ota_json(cali_json_t *j);

#ifdef __cplusplus
}
#endif

#endif /* CALI_OTA_H */
