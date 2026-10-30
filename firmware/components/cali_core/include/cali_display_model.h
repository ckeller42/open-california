/**
 * @file cali_display_model.h
 * @brief The pure status-display model: a cali_status_t snapshot plus the light, proximity, motion and
 *        touch inputs in; three rows (device, WiFi, camper), the footer text, the screen state
 *        (on, dim, off) and the brightness out.
 *
 * No LVGL, no platform: `tests/firmware/test_display_model.py` tests it on the host and the LVGL view
 * only paints its output. The settings and the last painted state (NVS, `/api/state` `device.display`)
 * live in display_cfg.c, declared at the end of this header.
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The CoreS3 screen shows device, WiFi and camper-unit status
 *    :id: R_FW_STATUS_DISPLAY
 *    :status: implemented
 *    :tags: esp32, display
 *
 *    The satellite's screen shall show three rows — device running, WiFi (setup hotspot / joining /
 *    online with SSID, IP and signal / off with reason), camper unit (not paired / pairing / connected
 *    with data age / stale after ``DISPLAY_STALE_MS`` (90 s) without a snapshot / link lost) — in German or English, using the same status
 *    snapshot as ``/api/state``; when it is lit and how bright: ``R_FW_DISPLAY_WAKE``.
 * @endverbatim
 *
 * @verbatim embed:rst:leading-asterisk
 * .. req:: The CoreS3 screen wakes on a tap, a hand near, motion or an alert, and follows the room light
 *    :id: R_FW_DISPLAY_WAKE
 *    :status: implemented
 *    :tags: esp32, display
 *
 *    The satellite's screen shall be on for 60 s after a wake event — a touch, a hand near the
 *    LTR-553 proximity sensor (two reads in a row over the threshold, with hysteresis), motion on the
 *    BMI270 accelerometer (the accel vector's change between reads over a high threshold two reads in a
 *    row; the rising edge only, re-armed after a still period, so continuous shaking never keeps it on;
 *    no reading or no sensor = no motion wake), or a status row newly turning red or amber — then dim, and off (backlight off) after 5 min; a tap that wakes it
 *    shall not reach a widget. Its brightness shall follow the LTR-553 ambient light on a log curve
 *    between a minimum and a maximum, smoothed (EMA, ~3 s) and changed only beyond a 15 % deadband,
 *    falling back to fixed levels after 5 s without a valid reading. The settings (auto brightness,
 *    wake on hand, wake on motion, min, max, a −50…+50 brightness offset clamped to min..max) persist in NVS, ``/api/state`` reports ``device.display`` and
 *    ``POST /api/display`` changes them (station mode only); Satellite settings shows it, EN + DE.
 *    Diagnostic aid: ``POST /api/display {"dfs"}`` switches dynamic frequency scaling at
 *    once without storing it; ``device.display.dfs``/``pm`` (``/api/state?diag=1``) report the live PM config.
 * @endverbatim
 */
#ifndef CALI_DISPLAY_MODEL_H
#define CALI_DISPLAY_MODEL_H

#include <stdint.h>

#include "cali_battery.h"
#include "cali_json.h"
#include "cali_status.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The colour of a row's status dot. */
typedef enum {
    CALI_DOT_GREEN,   /**< Fine. */
    CALI_DOT_AMBER,   /**< Needs attention. */
    CALI_DOT_RED,     /**< Failed. */
    CALI_DOT_GREY     /**< Not applicable or unknown. */
} cali_dot_t;

/** @brief The display language. */
enum cali_lang_e {
    CALI_LANG_DE = 0,   /**< German. */
    CALI_LANG_EN = 1    /**< English. */
};

/** @brief Size of a row's text buffer in bytes, including the NUL. */
#define CALI_ROW_TEXT_MAX 64

/** @brief One status row. */
typedef struct {
    cali_dot_t dot;                  /**< The dot colour. */
    const char *label;               /**< The row's label (device, WiFi or camper). */
    char text[CALI_ROW_TEXT_MAX];    /**< The row's text in the chosen language. */
} cali_row_t;

/** @brief The screen state. */
typedef enum {
    CALI_DISP_ON,    /**< Lit at the on level. */
    CALI_DISP_DIM,   /**< Lit at the dim level. */
    CALI_DISP_OFF    /**< Backlight off. */
} cali_disp_state_t;

/** @brief The light sensor's state. */
typedef enum {
    CALI_SENSOR_OK,       /**< A valid reading came recently. */
    CALI_SENSOR_ABSENT,   /**< The sensor did not answer at init. */
    CALI_SENSOR_ERROR     /**< No valid reading for DISPLAY_SENSOR_FAIL_MS. */
} cali_sensor_t;

/** @brief Lux at or below which the screen uses the minimum brightness. */
#define DISPLAY_LUX_LOW 1
/** @brief Lux at or above which the screen uses the maximum brightness; a log curve lies between. */
#define DISPLAY_LUX_HIGH 1000
/** @brief Time constant in ms of the lux smoothing (an EMA on log10 of lux). */
#define DISPLAY_LUX_TAU_MS 3000
/** @brief The applied level moves only on a relative change larger than this many percent. */
#define DISPLAY_DEADBAND_PCT 15
/** @brief The dim level is this percent of the on level. */
#define DISPLAY_DIM_OF_ON_PCT 30
/** @brief The dim level is at least this percent. */
#define DISPLAY_DIM_FLOOR_PCT 3
/** @brief After this many ms without a valid lux the fixed fallback levels apply. */
#define DISPLAY_SENSOR_FAIL_MS 5000
/** @brief LTR-553 proximity counts (11 bit): near after two reads in a row above this. */
#define DISPLAY_PROX_NEAR 200
/** @brief Proximity counts: far again below this (hysteresis). */
#define DISPLAY_PROX_FAR 150
/**
 * @brief BMI270 motion wake threshold in mg: the accel vector's change between two 200 ms reads.
 *
 * The value is deliberately high (a pick-up or a firm knock; a van rocking under a step is about
 * 50 mg). A wake needs two reads in a row above it.
 */
#define DISPLAY_MOTION_WAKE_MG 200
/** @brief Motion below this many mg counts as still. */
#define DISPLAY_MOTION_STILL_MG 50
/** @brief Motion re-arms only once every read stayed below DISPLAY_MOTION_STILL_MG for this many ms (driving gives one wake, not a pinned screen). */
#define DISPLAY_MOTION_REARM_MS 10000

/**
 * @brief The display settings.
 *
 * NVS keys in namespace `cali`: `disp_auto`, `disp_prox`, `disp_min`, `disp_max`, `disp_off`, `disp_motion`.
 */
typedef struct {
    uint8_t auto_on;      /**< Brightness follows the room light (else always `max_pct`). */
    uint8_t prox_wake;    /**< A hand near wakes the screen. */
    uint8_t min_pct;      /**< Minimum brightness, 1..50. */
    uint8_t max_pct;      /**< Maximum brightness, 20..100, above `min_pct`. */
    int8_t offset;        /**< Points added to the on level (curve or max), -50..50, clamped to min..max. */
    uint8_t motion_wake;  /**< Moving the device (BMI270) wakes the screen. */
} cali_display_cfg_t;

/** @brief Initializer for the default settings. The fallback levels (sensor absent or in error) ignore the offset. */
#define CALI_DISPLAY_CFG_DEFAULT {1, 1, 8, 100, 0, 1}

/** @brief One tick's sensor and touch inputs. */
typedef struct {
    int32_t lux;       /**< Ambient light in lux, -1 = no valid reading this tick. */
    int32_t prox;      /**< Proximity counts, -1 = no reading. */
    uint8_t present;   /**< The light sensor answered at init. */
    uint8_t touched;   /**< The screen was touched since the last tick. */
    cali_batt_t batt;  /**< The station footer's battery (R_FW_BATTERY); never wakes the screen. */
    uint8_t acc_ok;    /**< `acc` holds a reading this tick (0: no IMU, or the read failed). */
    int32_t acc[3];    /**< BMI270 acceleration x, y, z in mg. */
} cali_display_in_t;

/** @brief What the painter draws. */
typedef struct {
    cali_row_t device;       /**< The device row. */
    cali_row_t wifi;         /**< The WiFi row. */
    cali_row_t camper;       /**< The camper-unit row. */
    int footer_setup;        /**< 1: the footer shows NET_AP_SSID and NET_AP_PSK; 0: `http://NET_HOSTNAME.local` and the battery. */
    char footer[CALI_ROW_TEXT_MAX];   /**< The footer text. */
    cali_disp_state_t state; /**< On, dim or off. */
    cali_sensor_t sensor;    /**< The light sensor's state. */
    int32_t lux;             /**< The last valid lux, -1 = none. */
    int32_t prox;            /**< The last valid proximity reading, -1 = none. */
    int32_t motion;          /**< This tick's accel change in mg; -1 = none (no IMU, a failed read, the first read). */
    uint8_t brightness_pct;  /**< Backlight percent, 0 when off. */
} cali_display_view_t;

/**
 * @brief The wake and brightness state.
 *
 * Wake events are a touch, a hand near (rising edge, when `prox_wake`), motion (rising edge, when
 * `motion_wake`), a row turning red or amber, and the first call. The screen is on for
 * DISPLAY_DIM_AFTER_MS after the last event, then dim, and off after DISPLAY_OFF_AFTER_MS. Ages,
 * uptime, the footer (battery) and green or grey changes never wake it.
 */
typedef struct {
    uint64_t last_wake_ms;   /**< Time of the last wake event. */
    uint64_t last_lux_ms;    /**< Time of the last lux sample. */
    uint64_t last_good_ms;   /**< Time of the last valid lux. */
    float lux_log;           /**< EMA of log10(max(lux, 1)). */
    uint64_t last_shake_ms;  /**< The last read with motion at or above DISPLAY_MOTION_STILL_MG. */
    int32_t lux;             /**< Last valid lux. */
    int32_t prox;            /**< Last valid proximity reading. */
    int32_t motion;          /**< Last motion value. */
    int32_t acc[3];          /**< The previous valid accel read (when `have_acc`). */
    uint32_t cfg_sig;        /**< Signature of the settings last applied. */
    uint8_t started;         /**< The first call happened. */
    uint8_t have_lux;        /**< A valid lux was seen. */
    uint8_t level;           /**< The applied on level in percent. */
    uint8_t near;            /**< A hand is near. */
    uint8_t near_reads;      /**< Consecutive reads above DISPLAY_PROX_NEAR while not near. */
    uint8_t have_acc;        /**< `acc` holds a previous read. */
    uint8_t moving;          /**< The motion wake fired and is disarmed until the unit is still again. */
    uint8_t motion_reads;    /**< Consecutive reads above the motion threshold. */
    uint8_t dots[2];         /**< The WiFi and camper row colours last tick. */
} cali_display_model_t;

/**
 * @brief Reset the model to its first-call state.
 * @param m  The model.
 */
void cali_display_model_init(cali_display_model_t *m);

/**
 * @brief Compute one tick's view.
 * @param m       The model; updated in place.
 * @param s       The device status snapshot.
 * @param now_ms  The uptime clock in ms.
 * @param lang    CALI_LANG_DE or CALI_LANG_EN.
 * @param in      This tick's sensor and touch inputs.
 * @param cfg     The settings.
 * @param out     Filled with the rows, the footer, the screen state and the brightness.
 */
void cali_display_model(cali_display_model_t *m, const cali_status_t *s, uint64_t now_ms, int lang,
                        const cali_display_in_t *in, const cali_display_cfg_t *cfg, cali_display_view_t *out);

/**
 * @brief The touch read filter, run in LVGL's task.
 *
 * A press that starts while the screen is not on only wakes it. The whole press, until release, is swallowed.
 *
 * @param swallow  Persistent flag between reads.
 * @param pressed  1 while the finger is down.
 * @param awake    1 when the screen is on.
 * @return 1 to deliver the read to the widgets, 0 to report "released" instead.
 */
int cali_display_touch_gate(uint8_t *swallow, int pressed, int awake);

/* ---- display_cfg.c: the settings in NVS and the last painted state for /api/state ---- */

/**
 * @brief Get the display settings.
 * @return The settings, loaded from the kv store on first use (defaults when missing or invalid).
 */
const cali_display_cfg_t *cali_display_cfg(void);

/**
 * @brief Check a settings value.
 * @param c  The settings.
 * @return 1 if in range (flags 0 or 1, min 1..50, max 20..100, min below max, offset -50..50), else 0.
 */
int cali_display_cfg_valid(const cali_display_cfg_t *c);

/**
 * @brief Validate and persist settings.
 * @param c  The new settings.
 * @return 0 ok, -1 out of range (nothing changed), -2 store failed.
 */
int cali_display_cfg_set(const cali_display_cfg_t *c);

/**
 * @brief Publish the painter's last state. Never called without a screen, so the live members of `device.display` stay null.
 * @param v  The view just painted.
 */
void cali_display_publish(const cali_display_view_t *v);

/**
 * @brief The painter's last raw sensor readings for the diag record.
 * @param lux     Lux, or -1 when unknown (no screen yet).
 * @param prox    Proximity counts, or -1.
 * @param motion  Motion in mg, or -1.
 */
void cali_display_sensors(int *lux, int *prox, int *motion);

/**
 * @brief Write the `device.display` object.
 *
 * Members: `state`, `brightness_pct`, `sensor`, `auto`, `prox_wake`, `motion_wake`, `min`, `max`, `offset`.
 * With `diag` (`/api/state?diag=1`) it also writes the raw sensors `lux`, `prox`, `motion` and `dfs`,
 * `pm`: the live cali_pm_dfs state (a bool, and a string `"MIN-MAX"` in MHz; both null without PM, on
 * the host and in QEMU).
 *
 * @param j     The JSON writer; the caller writes the key first.
 * @param diag  1 for the diag members.
 */
void cali_display_json(cali_json_t *j, int diag);

/**
 * @brief Copy a string into a row buffer, cut at a UTF-8 boundary when too long.
 * @param dst  Destination, CALI_ROW_TEXT_MAX bytes.
 * @param src  Source string.
 */
void cali_display_fit_text(char dst[CALI_ROW_TEXT_MAX], const char *src);

#ifdef __cplusplus
}
#endif

#endif /* CALI_DISPLAY_MODEL_H */
