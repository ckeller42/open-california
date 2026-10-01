/* cali_display_model.h — pure status-display model (#154): a cali_status_t snapshot in, three rows
 * (device / WiFi / camper), the footer mode and the screen brightness out. No LVGL, no platform:
 * host-tested by tests/firmware/test_display_model.py; the LVGL view (Task 4) only paints this. */
#ifndef CALI_DISPLAY_MODEL_H
#define CALI_DISPLAY_MODEL_H

#include <stdint.h>

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

typedef struct {
    cali_row_t device, wifi, camper;
    int footer_setup;        /* 1: show NET_AP_SSID / NET_AP_PSK; 0: http://NET_HOSTNAME.local */
    uint8_t brightness_pct;  /* DISPLAY_BRIGHT_PCT or DISPLAY_DIM_PCT */
} cali_display_view_t;

/* Dimming state: the screen dims DISPLAY_DIM_AFTER_MS after the last change of any row's colour,
 * wording or the footer mode (changing numbers such as ages and uptime do not count). */
typedef struct {
    uint64_t last_change_ms;
    uint32_t sig;
    uint8_t started;
} cali_display_model_t;

void cali_display_model_init(cali_display_model_t *m);

/* Fills *out from *s. now_ms is the uptime clock; lang is CALI_LANG_DE / CALI_LANG_EN. */
void cali_display_model(cali_display_model_t *m, const cali_status_t *s, uint64_t now_ms, int lang,
                        cali_display_view_t *out);

/* The setup-footer template "WiFi %s · password %s" (SSID, PSK) in the chosen language. */
const char *cali_display_footer_setup_fmt(int lang);

#ifdef __cplusplus
}
#endif

#endif /* CALI_DISPLAY_MODEL_H */
