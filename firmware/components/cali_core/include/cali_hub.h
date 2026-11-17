/**
 * @file cali_hub.h
 * @brief Remote hub, platform-free part: the sealed WiFi frame, the replay guard, the data key (KV
 *        "hub_key"), the hub config (KV "hub_cfg") and the `OCH1*...` QR text.
 *
 * Frame: ver(1)=0x01 | dir(1) | nonce(12) | ct | tag(16); AD = the first 14 bytes; nonce = dir(1) |
 * random session(7) | counter(4, big-endian): the direction is inside the nonce, so two senders with
 * the one key never reuse a nonce across directions. dir 1 ESP->app, 2 app->ESP, 3 bridge->app.
 */
#ifndef CALI_HUB_H
#define CALI_HUB_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_HUB_KEY_KEY "hub_key"
#define CALI_HUB_CFG_KEY "hub_cfg"
/** @brief KV key of the last accepted LoRa command counter (2 bytes, big-endian); reset to 0 with every new data key. */
#define CALI_LORA_CMD_CTR_KEY "lora_cmd_ctr"
#define CALI_HUB_VER 1
#define CALI_HUB_DIR_ESP 1
#define CALI_HUB_DIR_APP 2
#define CALI_HUB_DIR_BRIDGE 3
/** @brief The bytes a frame adds to the plaintext (ver, dir, nonce, tag). */
#define CALI_HUB_OVERHEAD 30

/** @brief AES-128-GCM seal (cali_aead_seal_psa); tag_len is 16 here. @return 0 or -1. */
typedef int (*cali_hub_seal_fn)(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len,
                                const uint8_t *pt, size_t len, uint8_t *ct, uint8_t *tag, size_t tag_len);
/** @brief AES-128-GCM open (cali_aead_open_psa); `out` is written only for a valid tag. @return 0 or -1. */
typedef int (*cali_hub_open_fn)(const uint8_t *key, const uint8_t *nonce, const uint8_t *ad, size_t ad_len,
                                const uint8_t *ct, size_t len, const uint8_t *tag, size_t tag_len, uint8_t *out);

/** @brief Sets the cipher (cali_core cannot link cali_lora, which links it). Call once at boot. */
void cali_hub_set_aead(cali_hub_seal_fn seal, cali_hub_open_fn open);

/** @brief The session IDs remembered per dir (the replay ring). */
#define CALI_HUB_RING 8

typedef struct {
    uint8_t key[16];
    /** @brief This side's random 7-byte session (new per boot, per rekey and before the counter wraps). */
    uint8_t sess[7];
    /** @brief The last counter sent; the next one is tx_ctr + 1. */
    uint32_t tx_ctr;
    /** @brief The highest counter accepted per dir and per ring entry. */
    uint32_t rx_last[4][CALI_HUB_RING];
    /** @brief Per dir the peer sessions accepted this boot (a ring, oldest overwritten). */
    uint8_t rx_ring[4][CALI_HUB_RING][7];
    /** @brief Per dir how many ring entries are used. */
    uint8_t rx_n[4];
    /** @brief Per dir the ring index of the newest peer session (eviction goes on from there). */
    uint8_t rx_cur[4];
} cali_hub_crypto_t;

/** @brief Loads the key from KV, or makes and stores one; starts a new session. @return 0, -1 if it cannot be stored. */
int cali_hub_key_load_or_make(cali_hub_crypto_t *c);
/** @brief Makes and stores a new key and a new session (the old app is locked out). @return 0 or -1. */
int cali_hub_rekey(cali_hub_crypto_t *c);

/** @brief Seals `pt` (dir 1..3) into `out`. @return the frame length, or 0 (bad dir, `max` too small, cipher failed). */
size_t cali_hub_seal(cali_hub_crypto_t *c, uint8_t dir, const uint8_t *pt, size_t n, uint8_t *out, size_t max);
/**
 * @brief Opens a frame.
 *
 * Replay guard: per dir, the last CALI_HUB_RING peer sessions are remembered, each with its own last
 * counter. A frame of a remembered session is accepted only with a counter above that entry's last one, so
 * several live senders in one dir (A1, B1, A2, B2) work and a replayed frame is -1. An unknown session is
 * added (the oldest entry is evicted) and its counter starts fresh.
 * Replay state lives in RAM only: it is per boot. A replay after a reboot, or of a session older than the
 * ring, is covered by the 120 s time check of the caller (the RPC layer, Task 3), not here.
 * @param expect_mask Bit d set = a frame with dir d is accepted (ESP: 1<<2; app: (1<<1)|(1<<3)).
 * @param dir Receives the frame's dir; may be NULL.
 * @return the plaintext length, or -1 (bad version, dir, nonce, size, tag or replay).
 */
int cali_hub_open(cali_hub_crypto_t *c, const uint8_t *frame, size_t n, uint8_t *out, size_t max, unsigned expect_mask,
                  uint8_t *dir);

typedef struct {
    char van[24], url[96], user[32], pass[48], app_user[32], app_pass[48];
} cali_hub_cfg_t;

/** @brief @return 0, or -1 (missing or wrong size; `cfg` is then all zero). */
int cali_hub_cfg_load(cali_hub_cfg_t *cfg);
/** @brief @return 0 or -1. */
int cali_hub_cfg_save(const cali_hub_cfg_t *cfg);

/** @brief Writes `OCH1*<van>*<url>*<app user>*<app pass>*<key hex32>`. @return its length, or 0 if it does not fit
 *         or a field holds a '*'. */
size_t cali_hub_qr_text(char *out, size_t max, const cali_hub_cfg_t *cfg, const uint8_t key[16]);

/** Longest request body (after unescaping) cali_hub_rpc forwards; a longer one answers 413. */
#define CALI_HUB_RPC_BODY_MAX 2560
/** Longest a pending handler may take (ms); then 504. */
#define CALI_HUB_RPC_PENDING_MS 60000u
/** Largest |device clock - "t"| accepted while SNTP time is valid (ms). */
#define CALI_HUB_RPC_SKEW_MS 120000

/**
 * @brief Runs one API request over the hub through cali_web_handle (hub_rpc.c).
 *
 * `req_json` is `{"id":<u32>,"m":"GET|POST|DELETE","p":"/api/...","b":<string|null>,"t":<unix ms>}`; the
 * answer is `{"id":..,"s":<status>,"b":<string>}`. The router is called as the HTTP core would: while it
 * answers CALI_HTTP_PENDING it is asked again with `resume = 1`, `pump` called between the tries (nonzero
 * return = give up, 504), at most CALI_HUB_RPC_PENDING_MS. The body is copied out of the router's static
 * buffer. Write routes keep their own 403 `setup_mode` outside station mode.
 *
 * Allowlist (ruling R21): only GET /api/... and POST /api/command reach the router; any other method or path
 * answers 403 with the body `{"error":"local_only"}` (settings, WiFi, OTA and pairing stay local).
 *
 * Status: 400 bad request (not that JSON, an unknown method, a path without a leading '/', no `t`), 404 no
 * such route (empty body), 408 `stale` (SNTP time valid and |now - t| > CALI_HUB_RPC_SKEW_MS), 413 body too
 * long, 500 the answer does not fit `max`, 504 pending too long. Without SNTP time the `t` check is skipped
 * (the replay ring of cali_hub_open is then the only guard). The 2.7 KB work buffer is on the heap (PSRAM first on the ESP) for the call. A \u0000 in a string and a
 * duplicate known key are 400; unknown keys are ignored.
 * @param req_json The request plaintext, NUL-terminated.
 * @param res_json Receives the answer, NUL-terminated.
 * @param max Size of `res_json`.
 * @param pump Required (NULL answers 500 `no_pump`): called between re-asks, it must wait or yield so the
 *        task does not spin up to 60 s (the caller's loop step, e.g. a short delay plus the control tick).
 * @return the answer length, or 0 if `max` is too small even for the short error answer.
 */
size_t cali_hub_rpc(const char *req_json, char *res_json, size_t max, int (*pump)(void));

/** @brief One API request over the hub, run in steps on the owner task (cali_hub_rpc_start/poll). */
typedef struct {
    char *buf;          /**< The request body + path (heap, PSRAM first); NULL = idle. */
    char method[8];     /**< GET, POST or DELETE. */
    uint32_t id;        /**< The request's "id", echoed in the answer. */
    uint64_t t0;        /**< Uptime ms of the first ask (CALI_HUB_RPC_PENDING_MS counts from here). */
    size_t body_len;
    char *path;         /**< Inside `buf`. */
    const char *query;  /**< Inside `buf`, or "". */
} cali_hub_rpc_t;

/**
 * @brief cali_hub_rpc without the blocking loop: parses and asks the router once.
 *
 * The owner task on the ESP is the NimBLE host task: blocking it while a command is pending would hold
 * back the BLE events that complete the command. So the hub asks once per tick instead.
 * @return 1 when `res_json` holds the answer (`r` is idle again), 0 while the router answers PENDING
 *         (call cali_hub_rpc_poll on a later tick; `r` holds the request).
 */
int cali_hub_rpc_start(cali_hub_rpc_t *r, const char *req_json, char *res_json, size_t max);
/** @brief Re-asks a pending request once (504 after CALI_HUB_RPC_PENDING_MS). @return as cali_hub_rpc_start. */
int cali_hub_rpc_poll(cali_hub_rpc_t *r, char *res_json, size_t max);

/* ---- the client run (hub_run.c): owner task, over a port ---- */

/** @brief The publish period of the sealed /api/state; also the retry period of a data key that cannot be stored. */
#define CALI_HUB_PUB_MS 30000u
/** @brief The longest request frame taken from the port. */
#define CALI_HUB_REQ_MAX 8192
/** @brief The longest answer plaintext (an escaped /api/state of NET_JSON_MAX). */
#define CALI_HUB_RES_MAX 12288
/** @brief Topics of cali_hub_port_post: oc/<van>/state (retained) and oc/<van>/res. */
enum { CALI_HUB_T_STATE, CALI_HUB_T_RES };

/**
 * @brief Owner task, every tick (after cali_web_poll): (re)starts the client after a config change (and
 *        every CALI_HUB_PUB_MS while the data key cannot be stored), takes one request frame, opens it
 *        (dir 2 only, replay ring), runs it in steps (cali_hub_rpc_start/poll), and posts the sealed answer
 *        and then the sealed `/api/state?hub=1` body; the state also on connect, on a new key and every
 *        CALI_HUB_PUB_MS. Offline, answers and states are dropped. No router call while the HTTP core
 *        sends (cali_http_busy). Nothing runs while the hub config is missing.
 */
void cali_hub_tick(uint64_t now_ms);
/** @brief /api/hub stored a new config: the next tick restarts the client with it. Owner task. */
void cali_hub_cfg_changed(void);
/** @brief Any task (the port, after a state frame went out): stamps last_pub_s. */
void cali_hub_published(void);

typedef struct {
    int online;          /**< The client runs and is connected. */
    uint32_t last_pub_s; /**< Uptime s of the last state publish, 0 = none yet. */
    int key_error;       /**< The data key cannot be stored (retried every CALI_HUB_PUB_MS). */
} cali_hub_status_t;

/** @brief For /api/state device.hub. */
void cali_hub_status(cali_hub_status_t *st);
/** @brief The hub QR text (cali_hub_qr_text with the live key). @return its length, 0 = no hub. Owner task. */
size_t cali_hub_qr(char *out, size_t max);
/** @brief A new data key (cali_hub_rekey); the old app is locked out; the state goes out under it. Owner task.
 *  @return 0 or -1. */
int cali_hub_new_key(void);

/* ---- the port: the MQTT client (cali_hub/hub_esp.c on the board, hub_port_none.c elsewhere, a fake in
 * test/hub_run_cli.c). All owner task. ---- */

/** @brief Starts the client with this config (it copies what it keeps). @return 0, or -1 (retried later). */
int cali_hub_port_start(const cali_hub_cfg_t *cfg);
/** @brief Stops the client; drops the frames still queued. */
void cali_hub_port_stop(void);
/** @brief The client is connected. */
int cali_hub_port_online(void);
/** @brief Copies the next request frame into `buf`. @return its length, 0 = none (a longer one is dropped). */
size_t cali_hub_port_take(uint8_t *buf, size_t max);
/** @brief Hands a sealed frame to the client for `topic` (it copies it; never waits). @return 0 or -1. */
int cali_hub_port_post(int topic, const uint8_t *frame, size_t n);
/** @brief Every tick while the client runs: the port's own housekeeping (hub_esp.c: wake the MQTT task when
 *  frames wait). */
void cali_hub_port_tick(void);

#ifdef __cplusplus
}
#endif

#endif
