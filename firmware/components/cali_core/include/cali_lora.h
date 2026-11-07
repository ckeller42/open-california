/**
 * @file cali_lora.h
 * @brief LoRaWAN telemetry (experimental): the platform-free part — the RAK3172 AT line parser, airtime
 *        and the daily budgets, the uplink packers, the snapshot the owner task hands to the LoRa task,
 *        and `/api/state` `device.lora`.
 *
 * A RAK3172 module (M5Stack Module LoRaWAN-EU868, RUI3 AT firmware) on the CoreS3's UART1 sends the van's
 * energy picture to The Things Stack while no router runs: far away, the phone reads it from the Things
 * Stack's Storage Integration. The UART and the task live in the ESP-only component `cali_lora`
 * (`lora_esp.c`); on the host and in QEMU `lora_port_none.c` stands in and the module reads "absent".
 * Nothing here runs, and no pin is touched, while the switch `exp_lora` is off.
 *
 * **Uplinks** (all multi-byte values big-endian; raw frames exactly as the unit sent them, so the
 * reader interprets them with Open California's own semantics):
 * - port 2 (CALI_LORA_ENERGY_LEN = 50 B, fits SF12): [0] version 1, [1..2] boot (16 bit),
 *   [3..24] the energy frame (22 B), [25..30] the water frame (6 B), [31..32] the first Victron charger's
 *   battery current ×10 (signed), [33..34] its PV power W, [35..36] its yield today ×100 kWh, [37] its
 *   charge state, [38] its error code, [39..40] the battery sense's voltage ×100, [41..49] the energy
 *   counters (R_FW_ENERGY_COUNTERS) as three 24-bit deci-Wh values: solar, load, battery net (signed).
 *   A missing frame, or a sensor without a reading in the last 60 s, reads 0xFF bytes.
 * - port 3: the functions whose frame changed, or was not sent for CALI_LORA_REFRESH_S, oldest first:
 *   [char index in CODEC_CHARS][length][frame]..., whole entries only. Energy, water and general are
 *   never in it (ports 2 and 4 carry them).
 * - port 4 (CALI_LORA_DAILY_LEN = 24 B), once a day and after each join: [0] version 1, [1..2] boot,
 *   [3..6] uptime s, [7..15] the general frame (9 B), [16..23] the firmware version, NUL-padded.
 *
 * **Airtime** follows Semtech AN1200.13 for EU868 125 kHz, CR 4/5. The firmware keeps at most
 * CALI_LORA_UPLINK_MS of uplink airtime and CALI_LORA_DOWNLINKS downlinks per rolling 24 h (The Things
 * Network's fair use); port 2 may use CALI_LORA_ENERGY_SHARE % of the airtime, the rest stays for ports 3
 * and 4. A send counts as on air only when the module confirms it with `+EVT:TX_DONE`; the budget also
 * charges a send the module accepted without that confirmation, but `/api/state` reports it apart, so a
 * silent module can never look like a working link.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The satellite sends its energy picture over LoRaWAN while the experimental switch is on
 *    :id: R_FW_LORA
 *    :status: implemented
 *    :tags: esp32, experimental, lora
 *
 *    With the experimental switch ``exp_lora`` on (default off; ``POST /api/experimental {"lora":…}``,
 *    console ``lora on|off``), the firmware shall drive a RAK3172 on UART1 (TX GPIO43, RX GPIO18, RST
 *    GPIO5): reset it, detect it by ``AT``, join by OTAA (EU868, class C, ADR) with the module's own DevEUI
 *    and the JoinEUI/AppKey of the setup (``lora_dev``; R_FW_LORA_SETUP), or else the DevEUI/JoinEUI/AppKey
 *    stored by ``lora keys`` (NVS, never printed; written to the module before each join attempt), not join without either, and send port 2 at the interval the airtime
 *    budget allows (at least 600 s), port 3 when a function changed, and port 4 daily. Uplink airtime and
 *    downlinks (every downlink counts) stay within 25 s and 8 per rolling 24 h, join requests included: no uplink goes out once either is used up. A send counts as airtime in ``/api/state`` only
 *    after ``+EVT:TX_DONE``. After 3 AT commands in a row without any answer the firmware shall reset the
 *    module, report it not joined, and try again every 10 min while it stays silent. With the switch off,
 *    no LoRa pin is touched. ``/api/state`` reports ``device.lora`` only while the switch is on, with the setup state
 *    (``setup``, ``has_ttn_keys``, ``dev_eui``; R_FW_LORA_SETUP).
 * @endverbatim
 *
 * C99, no malloc, no ESP-IDF headers. Proven by `tests/firmware/test_lora.py` (`test/lora_cli.c`).
 */
#ifndef CALI_LORA_H
#define CALI_LORA_H

#include <stddef.h>
#include <stdint.h>

#include "cali_energy.h"
#include "cali_json.h"
#include "cali_sensors.h"
#include "codec.h"
#include "codec_chars.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The NVS key of the switch. */
#define CALI_EXP_LORA_KEY "exp_lora"
/** @brief The NVS key of the 8+8+16 key bytes (DevEUI, JoinEUI, AppKey). */
#define CALI_LORA_KEYS_KEY "lora_keys"
/** @brief The NVS key of the setup keys: AppKey 16 B + JoinEUI 8 B (never printed). */
#define CALI_LORA_DEV_KEY "lora_dev"
/** @brief The NVS key of the 16-byte transfer key (never printed). */
#define CALI_LORA_TK_KEY "lora_tk"
/** @brief The NVS key of the TTN keys the transfer delivered (plaintext, at most 256 B; never printed). */
#define CALI_LORA_TTN_KEY "lora_ttn"
/** @brief The most bytes of the `lora_ttn` value. */
#define CALI_LORA_TTN_MAX 256

/** @brief Setup state: the store could not be read (corrupt or wrong-sized value); reported as `"error"`. */
#define CALI_LORA_SETUP_ERROR (-1)
/** @brief Setup state: no setup keys yet. */
#define CALI_LORA_SETUP_NONE 0
/** @brief Setup state: the AppKey and the transfer key exist, no TTN keys yet. */
#define CALI_LORA_SETUP_KEYS_MADE 1
/** @brief Setup state: the module joined (the status view of KEYS_MADE; the store cannot know it). */
#define CALI_LORA_SETUP_JOINED 2
/** @brief Setup state: the TTN keys are stored. */
#define CALI_LORA_SETUP_TTN_KEYS 3

/** @brief energy + water + chargers + counters */
#define CALI_LORA_PORT_ENERGY 2
/** @brief changed functions */
#define CALI_LORA_PORT_CHANGED 3
/** @brief daily */
#define CALI_LORA_PORT_DAILY 4
/** @brief port 2 length */
#define CALI_LORA_ENERGY_LEN 50
/** @brief port 4 length */
#define CALI_LORA_DAILY_LEN 24
/** @brief every tracked function at least every 12 h */
#define CALI_LORA_REFRESH_S 43200u
/** @brief LoRaWAN bytes around the payload: MHDR 1 + FHDR 7 + FPort 1 + MIC 4 */
#define CALI_LORA_OVERHEAD 13u
/** @brief the budgets' rolling window, s */
#define CALI_LORA_DAY_S 86400u
/** @brief uplink airtime per rolling 24 h */
#define CALI_LORA_UPLINK_MS 25000u
/** @brief downlinks per rolling 24 h */
#define CALI_LORA_DOWNLINKS 8u
/** @brief port 2's share of the airtime, % */
#define CALI_LORA_ENERGY_SHARE 60u
/** @brief the shortest port-2 interval, s */
#define CALI_LORA_BASE_S 600u
/** @brief unanswered AT commands in a row before a module reset */
#define CALI_LORA_SILENT_MAX 3u
/** @brief ports 2 + 3 at the 600 s base interval are 288 a day; a full ring reads as no room */
#define CALI_LORA_UP_RING 320
/** @brief the downlink ring (at least CALI_LORA_DOWNLINKS) */
#define CALI_LORA_DL_RING 16
/** @brief how often the owner task hands a snapshot over */
#define CALI_LORA_SNAP_MS 10000u

/** @brief One line from the module, classified. */
typedef enum {
    CALI_RAK_NONE,         /**< no answer, or a line that is none of these */
    CALI_RAK_OK,           /**< `OK` */
    CALI_RAK_ERROR,        /**< `AT_..._ERROR` */
    CALI_RAK_BUSY,         /**< `AT_BUSY_ERROR` */
    CALI_RAK_NOT_JOINED,   /**< `AT_NO_NETWORK_JOINED` */
    CALI_RAK_JOINED,       /**< `+EVT:JOINED` */
    CALI_RAK_JOIN_FAILED,  /**< `+EVT:JOIN_FAILED...` */
    CALI_RAK_TX_DONE,      /**< `+EVT:TX_DONE`: the module sent the uplink */
    CALI_RAK_LINKCHECK,    /**< `+EVT:LINKCHECK:...` */
    CALI_RAK_RX,           /**< `+EVT:RX_...`: a downlink */
    CALI_RAK_VALUE,        /**< the value line of `AT+X=?` */
} cali_rak_kind_t;

/** @brief A parsed line. */
typedef struct {
    cali_rak_kind_t kind;  /**< what the line is */
    int lc_status;         /**< LINKCHECK: 0 = answered */
    int lc_margin;         /**< LINKCHECK: demodulation margin, dB */
    int lc_gateways;       /**< LINKCHECK: gateways that heard the request */
    int rssi;              /**< LINKCHECK, RX: dBm */
    int snr;               /**< LINKCHECK, RX: dB */
    char value[64];          /**< VALUE: the reply of `AT+X=?` */
    int port;                /**< RX: FPort */
    uint8_t payload[64];     /**< RX: application payload */
    unsigned payload_len;    /**< RX: payload bytes */
} cali_rak_event_t;

/**
 * @brief A line to or from the module as it may be logged: the value of a `...KEY=` or `...EUI=` line
 *        (AppKey, DevEUI, JoinEUI, in a command or in the module's reply) becomes `<redacted>`, and so does
 *        the payload of an FPort-12 downlink (`+EVT:RX_…:UNICAST:12:<redacted>`, a key-transfer fragment);
 *        a `=?` query and every other line stay as they are.
 * @param line  The line without CR/LF.
 * @param out   Out: the loggable line (truncated to n; 48 bytes always hold a redacted one).
 * @param n     Size of out.
 * @return 1 when out is a redacted line, 0 when the line is safe to log as it is.
 */
int cali_rak_redact(const char *line, char *out, size_t n);

/**
 * @brief Classify one line from the RAK3172 (RUI3): OK, AT_ERROR & co., `+EVT:JOINED`, `+EVT:TX_DONE`,
 *        `+EVT:LINKCHECK:…`, `+EVT:RX_1:…`/`RX_C` + `UNICAST:<port>:<hex>`, or a value reply.
 * @param line  The line without CR/LF.
 * @return The event; kind NONE for a line that is none of these (the boot banner, an echo).
 */
cali_rak_event_t cali_rak_parse(const char *line);
/**
 * @brief The kind's name, for the log.
 * @param k The kind.
 * @return "OK", "TX_DONE", ...
 */
const char *cali_rak_kind_name(cali_rak_kind_t k);

/** @brief What one `AT+SEND` counts as. */
typedef struct {
    int charged;     /**< 1: into the budget, it may be on air */
    int confirmed;   /**< 1: the module reported the transmission */
} cali_lora_outcome_t;
/**
 * @brief The outcome of a send from its final status and whether `+EVT:TX_DONE` followed.
 * @param status   The final status (NONE = no answer).
 * @param tx_done  1 when `+EVT:TX_DONE` came.
 * @return Not charged for a refusal (ERROR, BUSY, NOT_JOINED); confirmed only with TX_DONE.
 */
cali_lora_outcome_t cali_lora_outcome(cali_rak_kind_t status, int tx_done);

/**
 * @brief Airtime of one uplink in ms.
 * @param phy_len Payload + CALI_LORA_OVERHEAD.
 * @param sf 7..12.
 * @return ms.
 */
uint32_t cali_lora_airtime_ms(unsigned phy_len, unsigned sf);
/**
 * @brief The EU868 maximum payload at a spreading factor.
 * @param sf 7..12.
 * @return Bytes.
 */
unsigned cali_lora_max_payload(unsigned sf);

/** @brief The rolling 24-h budgets. */
typedef struct {
    /** @brief One charged uplink. */
    struct cali_lora_up {
        uint32_t t_s;        /**< uptime s */
        uint32_t ms;         /**< airtime */
        uint8_t confirmed;   /**< 1 after +EVT:TX_DONE */
    } up[CALI_LORA_UP_RING]; /**< the uplink ring */
    unsigned up_head;        /**< its oldest entry */
    unsigned up_n;           /**< its entries */
    uint32_t dl[CALI_LORA_DL_RING];  /**< downlink times, uptime s */
    unsigned dl_head;        /**< its oldest entry */
    unsigned dl_n;           /**< its entries */
} cali_lora_budget_t;

/**
 * @brief Empty the budgets.
 * @param b The budget.
 */
void cali_lora_budget_init(cali_lora_budget_t *b);
/**
 * @brief Charge an uplink.
 * @param b Budget.
 * @param now_s Uptime s.
 * @param ms Airtime.
 * @param confirmed TX_DONE seen.
 */
void cali_lora_uplink_add(cali_lora_budget_t *b, uint32_t now_s, uint32_t ms, int confirmed);
/**
 * @brief Airtime charged in the last 24 h.
 * @param b Budget.
 * @param now_s Uptime s.
 * @return ms.
 */
uint32_t cali_lora_uplink_used_ms(cali_lora_budget_t *b, uint32_t now_s);
/**
 * @brief Airtime confirmed on air in the last 24 h.
 * @param b Budget.
 * @param now_s Uptime s.
 * @return ms.
 */
uint32_t cali_lora_uplink_confirmed_ms(cali_lora_budget_t *b, uint32_t now_s);
/**
 * @brief Airtime left in the 24-h budget, never below 0.
 * @param b Budget.
 * @param now_s Uptime s.
 * @return ms; 0 while the ring holds CALI_LORA_UP_RING unexpired uplinks.
 */
uint32_t cali_lora_uplink_room_ms(cali_lora_budget_t *b, uint32_t now_s);
/**
 * @brief Take one downlink.
 * @param b Budget.
 * @param now_s Uptime s.
 * @return 1 when one was left, else 0.
 */
int cali_lora_downlink_take(cali_lora_budget_t *b, uint32_t now_s);
/**
 * @brief Downlinks in the last 24 h.
 * @param b Budget.
 * @param now_s Uptime s.
 * @return Count.
 */
unsigned cali_lora_downlinks_used(cali_lora_budget_t *b, uint32_t now_s);
/**
 * @brief Seconds until the next port-2 uplink: DAY / floor(share / airtime) rounded up, so no rolling
 *        24 h holds more port-2 uplinks at this SF than fit in its share; at least CALI_LORA_BASE_S, and
 *        never past the rolling budget.
 * @param b      Budget.
 * @param now_s  Uptime s.
 * @param sf     Spreading factor.
 * @param len    Payload length.
 * @param share  Percent of the daily airtime this packet may use.
 * @return Seconds.
 */
uint32_t cali_lora_next_interval_s(cali_lora_budget_t *b, uint32_t now_s, unsigned sf, unsigned len, unsigned share);
/**
 * @brief The wait after the n-th failed join: 60 s doubling, at most 1 h.
 * @param attempt 0..
 * @return Seconds.
 */
uint32_t cali_lora_join_backoff_s(unsigned attempt);

/** @brief What the owner task hands to the LoRa task: copies, so the task never reads session state. */
typedef struct {
    uint8_t frame[CODEC_NCHARS][CODEC_FRAME_MAX];   /**< the raw state frame per CODEC_CHARS index */
    uint8_t len[CODEC_NCHARS];   /**< 0 = no frame */
    cali_sensor_reading_t mppt;     /**< the first Victron charger (n 0 = none) */
    cali_sensor_reading_t bsense;   /**< the first Victron battery sense (n 0 = none) */
    cali_energy_ctr_t ctr;       /**< the energy counters */
    uint32_t boot;               /**< the boot counter (R_FW_TIME) */
    uint32_t uptime_s;           /**< uptime, s */
    int enabled;                 /**< exp_lora */
} cali_lora_snap_t;

/**
 * @brief Fill a snapshot from the session, the sensors and the counters (owner task). A Victron reading
 *        older than CALI_ENERGY_MAX_AGE_MS goes in as none (`n` 0).
 * @param s Out.
 * @param now_ms Uptime.
 */
void cali_lora_snapshot(cali_lora_snap_t *s, uint64_t now_ms);

/**
 * @brief Pack port 2.
 * @param out The buffer.
 * @param s The snapshot.
 * @return CALI_LORA_ENERGY_LEN.
 */
size_t cali_lora_pack_energy(uint8_t *out, const cali_lora_snap_t *s);
/**
 * @brief Pack port 4.
 * @param out The buffer.
 * @param s The snapshot.
 * @param fw The firmware version.
 * @return CALI_LORA_DAILY_LEN.
 */
size_t cali_lora_pack_daily(uint8_t *out, const cali_lora_snap_t *s, const char *fw);

/** @brief Port 3's change tracker. */
typedef struct {
    uint8_t last[CODEC_NCHARS][CODEC_FRAME_MAX];   /**< the frame last sent */
    uint8_t last_len[CODEC_NCHARS];                /**< its length */
    uint32_t sent_at[CODEC_NCHARS];                /**< when, uptime s */
    uint8_t pending[CODEC_NCHARS];                 /**< 1: due on port 3 */
} cali_lora_tracker_t;

/**
 * @brief Start the tracker: every function is sent once.
 * @param t The tracker.
 */
void cali_lora_tracker_init(cali_lora_tracker_t *t);
/**
 * @brief Mark changed or due functions.
 * @param t Tracker.
 * @param s Snapshot.
 * @param now_s Uptime s.
 */
void cali_lora_tracker_update(cali_lora_tracker_t *t, const cali_lora_snap_t *s, uint32_t now_s);
/**
 * @brief Pack port 3 without consuming the changes.
 * @param t       Tracker.
 * @param s       Snapshot.
 * @param out     The buffer.
 * @param max     The payload limit at the current SF.
 * @param picked  Out: a bit per packed char index, for cali_lora_tracker_commit() after a good send.
 * @return The length; 0 = nothing to send.
 */
size_t cali_lora_pack_changed(const cali_lora_tracker_t *t, const cali_lora_snap_t *s, uint8_t *out, size_t max,
                              uint32_t *picked);
/**
 * @brief Consume the packed changes after a good send.
 * @param t Tracker.
 * @param s Snapshot.
 * @param picked From the pack.
 * @param now_s Uptime s.
 */
void cali_lora_tracker_commit(cali_lora_tracker_t *t, const cali_lora_snap_t *s, uint32_t picked, uint32_t now_s);

/** @brief What the LoRa task reports (copied to the owner task). */
typedef struct {
    int present;             /**< the module: -1 checking, 0 absent, 1 present */
    int joined;              /**< 1: the module's word; stale while at_unanswered > 0 */
    unsigned at_unanswered;  /**< AT commands in a row without an answer */
    int sf;                  /**< spreading factor 7..12 */
    uint32_t last_confirmed_s;   /**< uptime s of the last confirmed uplink, 0 = none */
    uint32_t airtime_ms;     /**< confirmed by TX_DONE in the last 24 h */
    uint32_t charged_ms;     /**< charged to the budget in the last 24 h */
    unsigned unconfirmed;    /**< sends without TX_DONE since boot */
    unsigned downlinks;      /**< downlinks in the last 24 h */
    unsigned uplinks[3];     /**< confirmed uplinks since boot: ports 2, 3, 4 */
    const char *note;        /**< "joining", "join failed", "module silent", ... or NULL */
    int setup;               /**< CALI_LORA_SETUP_NONE .. CALI_LORA_SETUP_TTN_KEYS; JOINED when joined and the state is KEYS_MADE */
    int has_ttn_keys;        /**< 1 when the TTN keys are stored (the keys themselves are never reported) */
    char dev_eui[17];        /**< the DevEUI as 16 hex digits, "" while unknown (the board task fills it) */
} cali_lora_status_t;

/**
 * @brief Write `device.lora`.
 * @param j The writer at a value.
 * @param st The status.
 * @param now_s Uptime s.
 */
void cali_lora_status_json(cali_json_t *j, const cali_lora_status_t *st, uint32_t now_s);

/** @brief The random-bytes source of the setup (the platform's on the device, a counting fake in tests). */
typedef void (*cali_lora_rand_fn)(void *buf, size_t n);

/**
 * @brief Open the setup: make the AppKey and a zero JoinEUI into `lora_dev` only when it is missing, and
 *        ALWAYS a new transfer key into `lora_tk`. Local key buffers are wiped before return.
 * @param rnd The random source.
 *        Only a MISSING `lora_dev` is made; any other read error (corrupt, wrong size) returns -1 and
 *        leaves the stored AppKey alone.
 * @return 0, or -1 when the store failed.
 */
int cali_lora_setup_open(cali_lora_rand_fn rnd);
/**
 * @brief The setup state from the store.
 * @return CALI_LORA_SETUP_NONE (no `lora_dev`), CALI_LORA_SETUP_KEYS_MADE, CALI_LORA_SETUP_TTN_KEYS
 *         (`lora_ttn` exists), or CALI_LORA_SETUP_ERROR when `lora_dev` or `lora_ttn` cannot be read or has
 *         the wrong shape (`lora_dev` not 24 bytes, `lora_ttn` not a plaintext cali_lora_ttn_ok() accepts).
 */
int cali_lora_setup_state(void);
/**
 * @brief Erase `lora_dev`, `lora_tk`, `lora_ttn` and `lora_keys` (every one is tried).
 * @return 0, or -1 when an erase failed.
 */
int cali_lora_erase_keys(void);
/**
 * @brief Switch `exp_lora` off, then erase `lora_dev`, `lora_tk`, `lora_ttn` and `lora_keys`, and tell the
 *        LoRa task (cali_lora_port_forgotten()).
 * @return 0, or -1 when a store call failed.
 */
int cali_lora_forget(void);

/**
 * @brief The QR text for the LoRa screen: the setup QR (`OCS1*…`, from the module's DevEUI, `lora_dev`'s
 *        AppKey and `lora_tk`) while the setup state is below CALI_LORA_SETUP_TTN_KEYS, then the repair QR
 *        (`OCR1*…`, from `lora_ttn`). Reads the store itself; local key buffers are wiped before return.
 * @param out Receives the NUL-terminated text; it holds secrets: the caller wipes it after use.
 * @param max The size of `out` (the repair QR needs about 300 bytes).
 * @param st The task's status: `present` and `dev_eui` are used.
 * @param exp_on The switch `exp_lora`.
 * @return The text length; 0 when the switch is off, the module is not present, the DevEUI is unknown,
 *         a key is missing, a store read fails or `out` is too small (then `out` is zeroed).
 */
size_t cali_lora_qr_text(char *out, size_t max, const cali_lora_status_t *st, int exp_on);
/**
 * @brief The setup QR text (`OCS1*…`) whatever the setup state: the LoRa screen's rotation (spec 5.3) shows
 *        it after the TTN keys arrived, when cali_lora_qr_text() shows the repair QR. Reads the store
 *        itself; local key buffers are wiped before return.
 * @param out Receives the NUL-terminated text; it holds secrets: the caller wipes it after use.
 * @param max The size of `out` (88 bytes are enough).
 * @param st The task's status: `present` and `dev_eui` are used.
 * @param exp_on The switch `exp_lora`.
 * @return The text length; 0 when the switch is off, the module is not present, the DevEUI is unknown,
 *         `lora_dev` or `lora_tk` is missing or unreadable, or `out` is too small (then `out` is zeroed).
 */
size_t cali_lora_setup_qr_text(char *out, size_t max, const cali_lora_status_t *st, int exp_on);
/**
 * @brief Whether a key-transfer plaintext may be stored as `lora_ttn`: at most CALI_LORA_TTN_MAX bytes and
 *        encodable as the repair QR (the scratch text is wiped before return).
 * @param plain The plaintext.
 * @param len The length of `plain`.
 * @return 1 when it may be stored, else 0.
 */
int cali_lora_ttn_ok(const uint8_t *plain, size_t len);

/**
 * @brief The confirmation for a key-transfer fragment that arrives without a transfer key: `00 |
 *        sha256(lora_ttn)[0..3]` when usable TTN keys are stored (the transfer is done and its status-0
 *        confirmation was lost), else `03`.
 * @param confirm Receives the confirmation; 5 bytes.
 * @return Its length: 5 or 1.
 */
size_t cali_lora_confirm_no_tk(uint8_t *confirm);

/**
 * @brief The switch (lazy NVS read).
 * @return 1 when on.
 */
int cali_exp_lora(void);
/**
 * @brief Store the switch; logs `exp: lora on|off`.
 * @param on The switch.
 * @return 0, or -1 when NVS failed.
 */
int cali_exp_lora_set(int on);

/**
 * @brief The owner-task tick: every CALI_LORA_SNAP_MS a snapshot to the task, and its status back.
 * @param now_ms Uptime.
 */
void cali_lora_tick(uint64_t now_ms);
/**
 * @brief Write `device.lora` from the last status (only call while the switch is on).
 * @param j The writer.
 */
void cali_lora_json(cali_json_t *j);
/**
 * @brief Console `lora on|off|status|forget|keys <deveui> <joineui> <appkey>` (hex).
 * @param args  The text after "lora".
 * @return 0.
 */
int cali_lora_console(const char *args);

/* ---- the port: lora_esp.c (cali_lora) on the device, lora_port_none.c on the host ---- */

/**
 * @brief Hand a snapshot to the LoRa task (it keeps the newest).
 * @param s The snapshot.
 */
void cali_lora_port_post(const cali_lora_snap_t *s);
/**
 * @brief The task's newest status.
 * @param out Out.
 * @return 1 when there is one.
 */
int cali_lora_port_status(cali_lora_status_t *out);
/**
 * @brief The current QR text for the display (cali_lora_qr_text() over the task's published status); any task.
 * @param out Receives the NUL-terminated text; the caller wipes it after painting.
 * @param max The size of `out`.
 * @return The text length, 0 when there is none (module absent, switch off, no keys).
 */
int cali_lora_port_qr(char *out, size_t max);
/**
 * @brief The current setup QR text for the display (cali_lora_setup_qr_text() over the task's published
 *        status); any task.
 * @param out Receives the NUL-terminated text; the caller wipes it after painting.
 * @param max The size of `out`.
 * @return The text length, 0 when there is none (module absent, switch off, no transfer key).
 */
int cali_lora_port_setup_qr(char *out, size_t max);
/**
 * @brief Whether a setup request (cali_lora_port_setup_request()) is not done yet; any task. Until it is,
 *        the stored transfer key may be the old one, so the display shows no QR.
 * @return 1 while a request waits or runs, else 0.
 */
int cali_lora_port_setup_pending(void);

/**
 * @brief cali_lora_forget() has erased the keys; any task. The LoRa task is the only writer of `lora_dev`,
 *        `lora_tk` and `lora_ttn`: on its next pass with the switch off it erases them once more, after any
 *        write it had under way, so a key write that raced the forget never survives it. A no-op without a
 *        module driver.
 */
void cali_lora_port_forgotten(void);
/**
 * @brief Ask the LoRa task to open the setup (cali_lora_setup_open(): a new transfer key, the AppKey once);
 *        any task. The LoRa task does it at its next pass with the module present and the switch on, and
 *        configures the module and joins again when the AppKey is new. A no-op without a module driver.
 */
void cali_lora_port_setup_request(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_LORA_H */
