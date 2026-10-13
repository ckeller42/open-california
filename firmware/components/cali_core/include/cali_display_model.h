/* cali_display_model.h — pure status-display model (#154): a cali_status_t snapshot plus the
 * light/proximity/motion/touch inputs in, three rows (device / WiFi / camper), the footer text, the screen
 * state (on / dim / off) and the brightness out (R_FW_DISPLAY_WAKE). No LVGL, no platform:
 * host-tested by tests/firmware/test_display_model.py; the LVGL view only paints this.
 * The settings + the last painted state (NVS, /api/state device.display) live in display_cfg.c. */
#ifndef CALI_DISPLAY_MODEL_H
#define CALI_DISPLAY_MODEL_H

#include <stdint.h>

#include "cali_battery.h"
#include "cali_json.h"
#include "cali_status.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { CALI_DOT_GREEN, CALI_DOT_AMBER, CALI_DOT_RED, CALI_DOT_GREY } cali_dot_t;
enum { CALI_LANG_DE = 0, CALI_LANG_EN = 1 };
#define CALI_ROW_TEXT_MAX 64

typedef struct {
    cali_dot_t dot;
    const char *label;
    char text[CALI_ROW_TEXT_MAX];
} cali_row_t;

typedef enum { CALI_DISP_ON, CALI_DISP_DIM, CALI_DISP_OFF } cali_disp_state_t;
typedef enum { CALI_SENSOR_OK, CALI_SENSOR_ABSENT, CALI_SENSOR_ERROR } cali_sensor_t;

/* Calibration knobs (R_FW_DISPLAY_WAKE; spec 2026-10-11 — one bench/van pass owed, firmware/README.md). */
#define DISPLAY_LUX_LOW 1           /* lux at or below: the "min" brightness */
#define DISPLAY_LUX_HIGH 1000       /* lux at or above: the "max" brightness; log curve between */
#define DISPLAY_LUX_TAU_MS 3000     /* EMA time constant, on log10(lux) */
#define DISPLAY_DEADBAND_PCT 15     /* the applied level moves only on a larger relative change */
#define DISPLAY_DIM_OF_ON_PCT 30    /* dim = this % of the on level ... */
#define DISPLAY_DIM_FLOOR_PCT 3     /* ... but at least this */
#define DISPLAY_SENSOR_FAIL_MS 5000 /* no valid lux this long: fixed DISPLAY_BRIGHT_PCT / DISPLAY_DIM_PCT */
#define DISPLAY_PROX_NEAR 200       /* LTR-553 PS counts (11 bit): near after 2 reads in a row above */
#define DISPLAY_PROX_FAR 150        /* ... far again below this (hysteresis) */
/* BMI270 motion: the accel vector's change between two 200 ms reads, mg. Deliberately high (a pick-up
 * or a firm knock, not a van rocking under a step, ~50 mg): a wake after 2 reads in a row above WAKE;
 * re-armed only once every read stayed below STILL for REARM_MS (driving / engine: one wake, not a pinned screen). */
#define DISPLAY_MOTION_WAKE_MG 200
#define DISPLAY_MOTION_STILL_MG 50
#define DISPLAY_MOTION_REARM_MS 10000

/* Settings (NVS keys disp_auto / disp_prox / disp_min / disp_max / disp_off / disp_motion in namespace cali). */
typedef struct {
    uint8_t auto_on;   /* brightness from the room light (else always max_pct) */
    uint8_t prox_wake; /* a hand near wakes the screen */
    uint8_t min_pct;   /* 1..50 */
    uint8_t max_pct;   /* 20..100, > min_pct */
    int8_t offset;     /* -50..50 points on the on level (curve or max), clamped to min..max */
    uint8_t motion_wake; /* moving the device (BMI270) wakes the screen */
} cali_display_cfg_t;
#define CALI_DISPLAY_CFG_DEFAULT {1, 1, 8, 100, 0, 1}   /* the fallback levels (sensor absent/error) ignore offset */

/* One tick's sensor + touch inputs. */
typedef struct {
    int32_t lux;     /* ambient light, lux; -1 = no valid reading this tick */
    int32_t prox;    /* proximity counts; -1 = no reading */
    uint8_t present; /* the light sensor answered at init */
    uint8_t touched; /* the screen was touched since the last tick */
    cali_batt_t batt; /* the station footer's battery (R_FW_BATTERY); never wakes the screen */
    uint8_t acc_ok;  /* acc holds a reading this tick (0: no IMU, or the read failed) */
    int32_t acc[3];  /* BMI270 acceleration x/y/z, mg */
} cali_display_in_t;

typedef struct {
    cali_row_t device, wifi, camper;
    int footer_setup;        /* 1: footer shows NET_AP_SSID / NET_AP_PSK; 0: http://NET_HOSTNAME.local + battery */
    char footer[CALI_ROW_TEXT_MAX];
    cali_disp_state_t state;
    cali_sensor_t sensor;
    int32_t lux, prox;       /* the last valid readings, -1 = none */
    int32_t motion;          /* this tick's accel change, mg; -1 = none (no IMU, a failed read, the first read) */
    uint8_t brightness_pct;  /* 0 when off */
} cali_display_view_t;

/* Wake/brightness state. Wake events: a touch, a hand near (rising edge, when prox_wake), motion
 * (rising edge, when motion_wake), a row
 * turning red/amber, the first call. on for DISPLAY_DIM_AFTER_MS after the last one, then dim, off
 * after DISPLAY_OFF_AFTER_MS. Ages, uptime, the footer (battery) and green/grey changes never wake. */
typedef struct {
    uint64_t last_wake_ms, last_lux_ms, last_good_ms;
    float lux_log;          /* EMA of log10(max(lux, 1)) */
    uint64_t last_shake_ms; /* the last read with motion >= DISPLAY_MOTION_STILL_MG */
    int32_t lux, prox, motion;
    int32_t acc[3];         /* the previous valid accel read (have_acc) */
    uint32_t cfg_sig;
    uint8_t started, have_lux, level, near, near_reads, have_acc, moving, motion_reads;
    uint8_t dots[2];        /* the WiFi and camper row colours last tick */
} cali_display_model_t;

void cali_display_model_init(cali_display_model_t *m);

/* Fills *out from *s, *in and *cfg. now_ms is the uptime clock; lang is CALI_LANG_DE / CALI_LANG_EN. */
void cali_display_model(cali_display_model_t *m, const cali_status_t *s, uint64_t now_ms, int lang,
                        const cali_display_in_t *in, const cali_display_cfg_t *cfg, cali_display_view_t *out);

/* The touch read filter (runs in LVGL's task): a press that starts while the screen is not on only
 * wakes it — the whole press, until release, is swallowed. *swallow persists between reads.
 * Returns 1 to deliver the read to the widgets, 0 to report "released" instead. */
int cali_display_touch_gate(uint8_t *swallow, int pressed, int awake);

/* ---- display_cfg.c: the settings in NVS + the last painted state for /api/state ---- */

/* The settings (loaded from the kv store on first use; defaults when missing or invalid). */
const cali_display_cfg_t *cali_display_cfg(void);
/* 1 if *c is in range (flags 0/1, min 1..50, max 20..100, min < max, offset -50..50). */
int cali_display_cfg_valid(const cali_display_cfg_t *c);
/* Validates + persists *c. 0 ok, -1 out of range (nothing changed), -2 store failed. */
int cali_display_cfg_set(const cali_display_cfg_t *c);
/* The painter's last state (never called without a screen: device.display's live members stay null). */
void cali_display_publish(const cali_display_view_t *v);
/* device.display: {"state","lux","prox","motion","brightness_pct","sensor","auto","prox_wake","motion_wake","min","max","offset"}. */
void cali_display_json(cali_json_t *j);

/* Copies src into dst (CALI_ROW_TEXT_MAX bytes), cut at a UTF-8 boundary when too long. */
void cali_display_fit_text(char dst[CALI_ROW_TEXT_MAX], const char *src);

#ifdef __cplusplus
}
#endif

#endif /* CALI_DISPLAY_MODEL_H */
