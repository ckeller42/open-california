/* display_model.c — pure status-display model, see include/cali_display_model.h (#154).
 * Implements ``R_FW_STATUS_DISPLAY`` (docs/firmware.md): three rows (device always green; WiFi
 * green/amber/red; camper green/amber/red/grey), a stale rule (no snapshot for DISPLAY_STALE_MS turns
 * the camper row red), the footer, all derived from one cali_status_t; and the screen state + brightness
 * from the light/proximity/touch inputs (R_FW_DISPLAY_WAKE); the LoRa screen's gestures and texts
 * (R_FW_LORA_SCREEN). Every text is an EN/DE pair generated from firmware/web/strings.json. */
#include "cali_display_model.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "cali_wifi_run.h"
#include "net_consts.h"
#include "pairing_consts.h"
#include "strings_gen.h"

/* Every text names its macros literally (no token pasting) so tests/test_web_strings.py can find
 * the keys in this file. */
enum {
    K_DEVICE, K_WIFI, K_CAMPER, K_RUNNING, K_SETUP, K_JOINING, K_RETRYING, K_WIFI_OFF, K_CONNECTED,
    K_CONNECTING, K_PAIRING, K_PASSKEY, K_LINK_LOST, K_STALE, K_PAIR_ERROR, K_NOT_PAIRED, K_QR_JOIN_TITLE, K_QR_NEXT_TITLE, K_QR_WLAN, K_QR_PASS, K_FAIL_NOT_FOUND, K_FAIL_AUTH, K_FAIL_OTHER, K_BATT, K_CHARGING, K_HEATER_LOCKED, K_HEATER_ERROR,
    K_LORA_SCAN, K_LORA_WAIT, K_LORA_JOINED, K_LORA_NOT_JOINED, K_LORA_CONFIRMED, K_LORA_NONE, K_COUNT
};
static const struct { const char *de, *en; } TXT[K_COUNT] = {
    [K_DEVICE] = {WEB_STR_DE_DEVICE, WEB_STR_EN_DEVICE},
    [K_WIFI] = {WEB_STR_DE_D_WIFI, WEB_STR_EN_D_WIFI},
    [K_CAMPER] = {WEB_STR_DE_D_CAMPER, WEB_STR_EN_D_CAMPER},
    [K_RUNNING] = {WEB_STR_DE_D_RUNNING, WEB_STR_EN_D_RUNNING},
    [K_SETUP] = {WEB_STR_DE_D_SETUP, WEB_STR_EN_D_SETUP},
    [K_JOINING] = {WEB_STR_DE_D_JOINING, WEB_STR_EN_D_JOINING},
    [K_RETRYING] = {WEB_STR_DE_D_RETRYING, WEB_STR_EN_D_RETRYING},
    [K_WIFI_OFF] = {WEB_STR_DE_D_WIFI_OFF, WEB_STR_EN_D_WIFI_OFF},
    [K_CONNECTED] = {WEB_STR_DE_D_CONNECTED, WEB_STR_EN_D_CONNECTED},
    [K_CONNECTING] = {WEB_STR_DE_D_CONNECTING, WEB_STR_EN_D_CONNECTING},
    [K_PAIRING] = {WEB_STR_DE_D_PAIRING, WEB_STR_EN_D_PAIRING},
    [K_PASSKEY] = {WEB_STR_DE_D_PASSKEY, WEB_STR_EN_D_PASSKEY},
    [K_LINK_LOST] = {WEB_STR_DE_D_LINK_LOST, WEB_STR_EN_D_LINK_LOST},
    [K_STALE] = {WEB_STR_DE_D_STALE, WEB_STR_EN_D_STALE},
    [K_PAIR_ERROR] = {WEB_STR_DE_D_PAIR_ERROR, WEB_STR_EN_D_PAIR_ERROR},
    [K_NOT_PAIRED] = {WEB_STR_DE_D_NOT_PAIRED, WEB_STR_EN_D_NOT_PAIRED},
    [K_FAIL_NOT_FOUND] = {WEB_STR_DE_D_FAIL_NOT_FOUND, WEB_STR_EN_D_FAIL_NOT_FOUND},
    [K_FAIL_AUTH] = {WEB_STR_DE_D_FAIL_AUTH, WEB_STR_EN_D_FAIL_AUTH},
    [K_FAIL_OTHER] = {WEB_STR_DE_D_FAIL_OTHER, WEB_STR_EN_D_FAIL_OTHER},
    [K_QR_JOIN_TITLE] = {WEB_STR_DE_D_QR_JOIN_TITLE, WEB_STR_EN_D_QR_JOIN_TITLE},
    [K_QR_NEXT_TITLE] = {WEB_STR_DE_D_QR_NEXT_TITLE, WEB_STR_EN_D_QR_NEXT_TITLE},
    [K_QR_WLAN] = {WEB_STR_DE_D_QR_WLAN, WEB_STR_EN_D_QR_WLAN},
    [K_QR_PASS] = {WEB_STR_DE_D_QR_PASS, WEB_STR_EN_D_QR_PASS},
    [K_BATT] = {WEB_STR_DE_D_BATT, WEB_STR_EN_D_BATT},
    [K_CHARGING] = {WEB_STR_DE_D_CHARGING, WEB_STR_EN_D_CHARGING},
    [K_HEATER_LOCKED] = {WEB_STR_DE_D_HEATER_LOCKED, WEB_STR_EN_D_HEATER_LOCKED},
    [K_HEATER_ERROR] = {WEB_STR_DE_D_HEATER_ERROR, WEB_STR_EN_D_HEATER_ERROR},
    [K_LORA_SCAN] = {WEB_STR_DE_D_LORA_SCAN, WEB_STR_EN_D_LORA_SCAN},
    [K_LORA_WAIT] = {WEB_STR_DE_D_LORA_WAIT, WEB_STR_EN_D_LORA_WAIT},
    [K_LORA_JOINED] = {WEB_STR_DE_D_LORA_JOINED, WEB_STR_EN_D_LORA_JOINED},
    [K_LORA_NOT_JOINED] = {WEB_STR_DE_D_LORA_NOT_JOINED, WEB_STR_EN_D_LORA_NOT_JOINED},
    [K_LORA_CONFIRMED] = {WEB_STR_DE_D_LORA_CONFIRMED, WEB_STR_EN_D_LORA_CONFIRMED},
    [K_LORA_NONE] = {WEB_STR_DE_D_LORA_NONE, WEB_STR_EN_D_LORA_NONE},
};

static const char *tx(int key, int lang) { return lang == CALI_LANG_EN ? TXT[key].en : TXT[key].de; }

/* Set the row text from a bounded temp copy; a cut never splits a UTF-8 sequence. */
void cali_display_fit_text(char dst[CALI_ROW_TEXT_MAX], const char *src) {
    size_t n = strlen(src);
    if (n >= CALI_ROW_TEXT_MAX) {
        n = CALI_ROW_TEXT_MAX - 1;
        while (n > 0 && ((unsigned char)src[n] & 0xC0) == 0x80) n--;  /* src[n] = first dropped byte */
    }
    memcpy(dst, src, n);
    dst[n] = 0;
}

static void set_text(cali_row_t *r, const char *src) { cali_display_fit_text(r->text, src); }

static void row(cali_row_t *r, cali_dot_t dot, const char *label, int key, int lang, const char *arg, const char *arg2) {
    char tmp[2 * CALI_ROW_TEXT_MAX + 2 * (NET_SSID_MAX + 1)];
    r->dot = dot;
    r->label = label;
    snprintf(tmp, sizeof tmp, tx(key, lang), arg ? arg : "", arg2 ? arg2 : "");
    set_text(r, tmp);
}

/* "N min" (< 1 h), "H h M min" (< 1 d), else "D d H h". */
static void fmt_uptime(uint64_t ms, char out[24]) {
    uint64_t min = ms / 60000u, h = min / 60u, d = h / 24u;
    if (h == 0) snprintf(out, 24, "%u min", (unsigned)min);
    else if (d == 0) snprintf(out, 24, "%u h %u min", (unsigned)h, (unsigned)(min % 60u));
    else snprintf(out, 24, "%u d %u h", (unsigned)d, (unsigned)(h % 24u));
}

static void wifi_row(cali_row_t *r, const cali_status_t *s, int lang) {
    const char *label = tx(K_WIFI, lang);
    if (s->wifi_mode == CALI_WIFI_MODE_STATION && s->ip != 0) {
        char ip[16], tmp[2 * CALI_ROW_TEXT_MAX];
        cali_status_ip_str(s->ip, ip);
        if (s->rssi) snprintf(tmp, sizeof tmp, "%s · %s · %d dBm", s->ssid, ip, s->rssi);
        else snprintf(tmp, sizeof tmp, "%s · %s", s->ssid, ip);  /* 0 = unknown, as /api/state's null */
        r->dot = CALI_DOT_GREEN;
        r->label = label;
        set_text(r, tmp);
        return;
    }
    if (s->wifi_mode == CALI_WIFI_MODE_SETUP) {
        row(r, CALI_DOT_AMBER, label, K_SETUP, lang, s->ap_ssid, NET_AP_ADDR);
        return;
    }
    if (s->wifi_state == WIFI_CONNECTING) {
        row(r, CALI_DOT_AMBER, label, K_JOINING, lang, s->ssid, NULL);
        return;
    }
    if (s->wifi_state == WIFI_RETRYING && s->wifi_mode == CALI_WIFI_MODE_STATION) {
        row(r, CALI_DOT_AMBER, label, K_RETRYING, lang, s->ssid, NULL);
        return;
    }
    row(r, CALI_DOT_RED, label, K_WIFI_OFF, lang, NULL, NULL);
    if (s->last_fail[0]) {
        char tmp[2 * CALI_ROW_TEXT_MAX];
        int k = !strcmp(s->last_fail, "not_found") ? K_FAIL_NOT_FOUND
                : !strcmp(s->last_fail, "auth")    ? K_FAIL_AUTH
                                                   : K_FAIL_OTHER;
        snprintf(tmp, sizeof tmp, "%s · %s", r->text, tx(k, lang));
        set_text(r, tmp);
    }
}

static void camper_row(cali_row_t *r, const cali_status_t *s, int lang) {
    const char *label = tx(K_CAMPER, lang);
    char age[24];
    switch (s->pair_state) {
    case PAIR_SCANNING: case PAIR_CONNECTING: case PAIR_RESETTING:
        row(r, CALI_DOT_AMBER, label, K_CONNECTING, lang, NULL, NULL);
        return;
    case PAIR_WAITING_PASSKEY:
        row(r, CALI_DOT_AMBER, label, K_PASSKEY, lang, NULL, NULL);
        return;
    case PAIR_PAIRING: case PAIR_VERIFYING:
        row(r, CALI_DOT_AMBER, label, K_PAIRING, lang, NULL, NULL);
        return;
    case PAIR_ERROR:
        row(r, CALI_DOT_RED, label, K_PAIR_ERROR, lang, NULL, NULL);
        return;
    default:
        break;
    }
    if (!s->address[0]) {
        row(r, CALI_DOT_GREY, label, K_NOT_PAIRED, lang, NULL, NULL);
        return;
    }
    if (!s->link_up) {
        row(r, CALI_DOT_RED, label, K_LINK_LOST, lang, NULL, NULL);
        return;
    }
    if (s->snap_age_ms < 0) {  /* link up, nothing received yet */
        row(r, CALI_DOT_AMBER, label, K_CONNECTING, lang, NULL, NULL);
        return;
    }
    if (s->snap_age_ms > DISPLAY_STALE_MS) {  /* whole seconds, rounded down */
        snprintf(age, sizeof age, "%u s", (unsigned)(s->snap_age_ms / 1000));
        row(r, CALI_DOT_RED, label, K_STALE, lang, age, NULL);
        return;
    }
    if (s->heater_fault > 0) {  /* the unit clears its ErrorCode within seconds: the satellite's kept one */
        snprintf(age, sizeof age, "%d", s->heater_fault);
        row(r, CALI_DOT_AMBER, label, s->heater_fault == 4 ? K_HEATER_LOCKED : K_HEATER_ERROR, lang, age, NULL);
        return;
    }
    {  /* rounded up, at least 1 s */
        unsigned sec = (unsigned)((s->snap_age_ms + 999) / 1000);
        snprintf(age, sizeof age, "%u s", sec ? sec : 1u);
    }
    row(r, CALI_DOT_GREEN, label, K_CONNECTED, lang, age, NULL);
}

/* "http://<host>.local · <battery>" (R_FW_BATTERY): on USB "USB 87 % charging" / "USB 100 %", on
 * the battery "Batt 64 % ~2 h 10" (DE "Akku …"; "~45 min" under an hour; no "~…" while the estimate is
 * unknown); the URL alone while the percent is unknown. Plain text: the Latin-1 fonts have no ⚡. */
static void station_footer(char dst[CALI_ROW_TEXT_MAX], const cali_batt_t *b, int lang) {
    char tmp[2 * CALI_ROW_TEXT_MAX];
    int n = snprintf(tmp, sizeof tmp, "http://" NET_HOSTNAME ".local");
    if (b->pct >= 0 && b->usb == 1)
        snprintf(tmp + n, sizeof tmp - (size_t)n, " · USB %d %%%s%s", b->pct, b->charging == 1 ? " " : "",
                 b->charging == 1 ? tx(K_CHARGING, lang) : "");
    else if (b->pct >= 0 && b->runtime_min >= 60)
        snprintf(tmp + n, sizeof tmp - (size_t)n, " · %s %d %% ~%d h %02d", tx(K_BATT, lang), b->pct,
                 b->runtime_min / 60, b->runtime_min % 60);
    else if (b->pct >= 0 && b->runtime_min >= 0)
        snprintf(tmp + n, sizeof tmp - (size_t)n, " · %s %d %% ~%d min", tx(K_BATT, lang), b->pct, b->runtime_min);
    else if (b->pct >= 0)
        snprintf(tmp + n, sizeof tmp - (size_t)n, " · %s %d %%", tx(K_BATT, lang), b->pct);
    cali_display_fit_text(dst, tmp);
}

void cali_display_model_init(cali_display_model_t *m) {
    memset(m, 0, sizeof *m);
    m->lux = m->prox = m->motion = -1;
}

/* log curve: DISPLAY_LUX_LOW -> min, DISPLAY_LUX_HIGH -> max (x = the smoothed log10 lux) */
static uint8_t on_level(float x, const cali_display_cfg_t *c) {
    const float lo = log10f(DISPLAY_LUX_LOW), hi = log10f(DISPLAY_LUX_HIGH);
    float f = x <= lo ? 0.0f : x >= hi ? 1.0f : (x - lo) / (hi - lo);
    return (uint8_t)(c->min_pct + f * (float)(c->max_pct - c->min_pct) + 0.5f);
}

/* The LoRa screen's gestures (R_FW_LORA_SCREEN), from the press duration seen each tick: a press is new
 * when the duration starts or drops; one that starts on a dark screen is spent (the wake press); an
 * unspent press fires once at DISPLAY_LORA_PRESS_MS; an unspent press that ends shorter, or a tap
 * between two ticks, closes the screen. */
static void lora_screen(cali_display_model_t *m, uint64_t now_ms, const cali_display_in_t *in, cali_display_view_t *out) {
    uint32_t p = in->pressed_ms;
    int new_press = p && (!m->press_ms || p < m->press_ms);
    int ended = m->press_ms && (!p || new_press);
    out->lora_request = 0;
    if (m->show_lora && ((ended && !m->press_spent) || (in->touched && !p && !m->press_ms))) m->show_lora = 0;
    if (new_press)   /* dim or off; the open LoRa screen is lit whatever the last tick was */
        m->press_spent = !m->show_lora && m->started && now_ms - m->last_wake_ms >= DISPLAY_DIM_AFTER_MS;
    if (p >= DISPLAY_LORA_PRESS_MS && !m->press_spent) {
        m->press_spent = 1;
        if (!m->show_lora && in->lora_available) {   /* open: the selector; a new transfer key before the TTN keys */
            m->show_lora = 1;
            out->lora_request = !in->lora_has_ttn_keys;
            m->lora_since = now_ms;
        } else if (m->show_lora == 1 && in->lora_has_ttn_keys) {   /* rotation (spec 5.3) */
            m->show_lora = 2;
            out->lora_request = 1;
            m->lora_since = now_ms;
        }
    }
    m->press_ms = p;
    if (m->show_lora && (!in->lora_available || now_ms - m->lora_since >= DISPLAY_LORA_SHOW_MS)) {
        m->show_lora = 0;
        m->press_spent = 1;   /* a press held across the close never reopens it (nor makes a new key) */
    }
    out->show_lora = m->show_lora;
}

/* The LoRa screen's texts: "Preparing…" or the scan hint; "<joined> · <last confirmed uplink>". */
static void lora_texts(const cali_display_in_t *in, int lang, cali_display_view_t *out) {
    char age[24], tmp[2 * CALI_ROW_TEXT_MAX], last[CALI_ROW_TEXT_MAX + 24];
    out->lora_hint = "";
    out->lora_line[0] = 0;
    if (!out->show_lora) return;
    out->lora_hint = tx(in->lora_qr_ready ? K_LORA_SCAN : K_LORA_WAIT, lang);
    if (in->lora_confirmed_age_s >= 0) {
        fmt_uptime((uint64_t)in->lora_confirmed_age_s * 1000u, age);
        snprintf(last, sizeof last, tx(K_LORA_CONFIRMED, lang), age);
    } else {
        snprintf(last, sizeof last, "%s", tx(K_LORA_NONE, lang));
    }
    snprintf(tmp, sizeof tmp, "%s · %s", tx(in->lora_joined ? K_LORA_JOINED : K_LORA_NOT_JOINED, lang), last);
    cali_display_fit_text(out->lora_line, tmp);
}

static int alert(cali_dot_t d) { return d == CALI_DOT_RED || d == CALI_DOT_AMBER; }

/* Wake, lux EMA + deadband and the on/dim/off state (R_FW_DISPLAY_WAKE). */
static void wake_and_brightness(cali_display_model_t *m, uint64_t now_ms, const cali_display_in_t *in,
                                const cali_display_cfg_t *c, cali_display_view_t *out) {
    int wake = !m->started || in->touched;
    uint32_t sig = (uint32_t)c->auto_on | (uint32_t)c->min_pct << 8 | (uint32_t)c->max_pct << 16 |
                   (uint32_t)(uint8_t)c->offset << 24;
    uint8_t on, dim;
    if (!m->started) m->last_good_ms = now_ms;   /* boot grace before "error" */
    /* a row turning red/amber wakes; the device row is always green */
    if (m->started && ((alert(out->wifi.dot) && out->wifi.dot != m->dots[0]) ||
                       (alert(out->camper.dot) && out->camper.dot != m->dots[1])))
        wake = 1;
    {   /* a QR code appearing or changing (hotspot code, then the setup page's): someone has to scan it */
        uint32_t h = 0;   /* FNV-1a; 0 = no QR (the text holds the password, so no copy is kept) */
        if (out->qr[0]) {
            h = 2166136261u;
            for (const char *q = out->qr; *q; q++) h = (h ^ (uint8_t)*q) * 16777619u;
            if (!h) h = 1;
        }
        if (h && h != m->qr_hash) wake = 1;
        m->qr_hash = h;
    }
    m->dots[0] = (uint8_t)out->wifi.dot;
    m->dots[1] = (uint8_t)out->camper.dot;
    m->started = 1;

    if (in->prox >= 0) {   /* near after two reads in a row above NEAR, far again below FAR */
        m->prox = in->prox;
        if (!m->near) {
            m->near_reads = in->prox > DISPLAY_PROX_NEAR ? (uint8_t)(m->near_reads + 1) : 0;
            if (m->near_reads >= 2) {
                m->near = 1;
                if (c->prox_wake) wake = 1;   /* the rising edge only: a cup in front never pins it on */
            }
        } else if (in->prox < DISPLAY_PROX_FAR) {
            m->near = 0;
            m->near_reads = 0;
        }
    }
    if (in->acc_ok) {   /* motion = |acc - previous acc|; moving after two reads in a row above WAKE */
        if (m->have_acc) {
            float d[3], sq = 0;
            for (int i = 0; i < 3; i++) d[i] = (float)(in->acc[i] - m->acc[i]), sq += d[i] * d[i];
            m->motion = (int32_t)(sqrtf(sq) + 0.5f);
            if (m->motion >= DISPLAY_MOTION_STILL_MG) m->last_shake_ms = now_ms;
            if (!m->moving) {
                m->motion_reads = m->motion > DISPLAY_MOTION_WAKE_MG ? (uint8_t)(m->motion_reads + 1) : 0;
                if (m->motion_reads >= 2) {
                    m->moving = 1;
                    if (c->motion_wake) wake = 1;   /* the rising edge only: driving never pins it on */
                }
            } else if (now_ms - m->last_shake_ms >= DISPLAY_MOTION_REARM_MS) {
                m->moving = 0;
                m->motion_reads = 0;
            }
        }
        memcpy(m->acc, in->acc, sizeof m->acc);
        m->have_acc = 1;
    } else {   /* no reading: nothing to compare the next one with, and unseen is not still */
        if (m->moving) m->last_shake_ms = now_ms;
        m->have_acc = 0;
        m->motion_reads = 0;
        m->motion = -1;
    }
    if (in->present && in->lux >= 0) {   /* EMA on log10(lux), time constant DISPLAY_LUX_TAU_MS */
        float x = log10f(in->lux > 1 ? (float)in->lux : 1.0f);
        if (!m->have_lux) {
            m->lux_log = x;
        } else {
            float dt = (float)(now_ms - m->last_lux_ms);
            m->lux_log += (x - m->lux_log) * dt / (DISPLAY_LUX_TAU_MS + dt);
        }
        m->have_lux = 1;
        m->lux = in->lux;
        m->last_lux_ms = m->last_good_ms = now_ms;
    }
    if (wake || m->show_lora) m->last_wake_ms = now_ms;   /* the LoRa screen stays lit */

    out->sensor = !in->present ? CALI_SENSOR_ABSENT
                  : now_ms - m->last_good_ms > DISPLAY_SENSOR_FAIL_MS ? CALI_SENSOR_ERROR : CALI_SENSOR_OK;
    if (c->auto_on && (out->sensor != CALI_SENSOR_OK || !m->have_lux)) {   /* fallback: fixed levels */
        on = DISPLAY_BRIGHT_PCT;
        dim = DISPLAY_DIM_PCT;
        m->level = 0;   /* re-take the curve at once when readings come back */
    } else {
        int t = (c->auto_on ? on_level(m->lux_log, c) : c->max_pct) + c->offset;   /* the owner's tweak */
        uint8_t target = (uint8_t)(t < c->min_pct ? c->min_pct : t > c->max_pct ? c->max_pct : t);
        if (m->level == 0 || sig != m->cfg_sig ||
            (target > m->level ? target - m->level : m->level - target) * 100 > DISPLAY_DEADBAND_PCT * m->level)
            m->level = target;
        on = m->level;
        dim = (uint8_t)(on * DISPLAY_DIM_OF_ON_PCT / 100);   /* so the offset reaches dim proportionally */
        if (dim < DISPLAY_DIM_FLOOR_PCT) dim = DISPLAY_DIM_FLOOR_PCT;
    }
    m->cfg_sig = sig;
    out->lux = m->lux;
    out->prox = m->prox;
    out->motion = m->motion;
    {
        uint64_t idle = now_ms - m->last_wake_ms;
        out->state = idle < DISPLAY_DIM_AFTER_MS ? CALI_DISP_ON : idle < DISPLAY_OFF_AFTER_MS ? CALI_DISP_DIM : CALI_DISP_OFF;
    }
    out->brightness_pct = m->show_lora                     ? DISPLAY_LORA_BRIGHT_PCT
                          : out->state == CALI_DISP_ON  ? on
                          : out->state == CALI_DISP_DIM ? dim
                                                        : 0;
}

/* "Label value" -> "Label\nvalue" (the last space becomes a line break; none: unchanged). */
static void break_before_value(char *t) {
    char *sp = strrchr(t, ' ');
    if (sp) *sp = '\n';
}

/* The setup QR screens: the hotspot's join code, or the address once the phone is on the home WiFi. */
static void qr_screen(cali_display_view_t *out, const cali_status_t *s, const char *ap_qr, int lang) {
    char tmp[2 * CALI_ROW_TEXT_MAX];
    out->qr[0] = out->qr_title[0] = out->qr_lines[0][0] = out->qr_lines[1][0] = 0;
    if (s->wifi_mode == CALI_WIFI_MODE_SETUP && ap_qr && strlen(ap_qr) < CALI_QR_TEXT_MAX) {
        const char *p = strstr(ap_qr, ";P:");   /* WIFI:T:WPA;S:<ssid>;P:<psk>;; */
        size_t n = p ? strcspn(p + 3, ";") : 0;
        char psk[NET_PSK_MAX + 1];
        if (n == 0 || n > NET_PSK_MAX) return;
        memcpy(psk, p + 3, n);
        psk[n] = 0;
        snprintf(out->qr, CALI_QR_TEXT_MAX, "%s", ap_qr);
        cali_display_fit_text(out->qr_title, tx(K_QR_JOIN_TITLE, lang));
        snprintf(tmp, sizeof tmp, tx(K_QR_WLAN, lang), s->ap_ssid);
        break_before_value(tmp);   /* the SSID on its own line: the narrow text box must not break it */
        cali_display_fit_text(out->qr_lines[0], tmp);
        snprintf(tmp, sizeof tmp, tx(K_QR_PASS, lang), psk);   /* full-width line under the code */
        cali_display_fit_text(out->qr_lines[1], tmp);
    } else if (s->wifi_mode == CALI_WIFI_MODE_STATION && s->ip != 0 && s->setup_pending) {
        char ip[16];
        cali_status_ip_str(s->ip, ip);
        snprintf(out->qr, CALI_QR_TEXT_MAX, "http://%s/#setup", ip);
        snprintf(tmp, sizeof tmp, tx(K_QR_NEXT_TITLE, lang), s->ssid);
        cali_display_fit_text(out->qr_title, tmp);
        snprintf(tmp, sizeof tmp, "http://\n%s", ip);   /* the address alone on a line: it must not break */
        cali_display_fit_text(out->qr_lines[0], tmp);
        cali_display_fit_text(out->qr_lines[1], "http://" NET_HOSTNAME ".local");
    }
}

void cali_display_model(cali_display_model_t *m, const cali_status_t *s, uint64_t now_ms, int lang,
                        const cali_display_in_t *in, const cali_display_cfg_t *cfg, const char *ap_qr,
                        cali_display_view_t *out) {
    char up[24];
    wifi_row(&out->wifi, s, lang);
    camper_row(&out->camper, s, lang);
    fmt_uptime(s->uptime_ms, up);
    row(&out->device, CALI_DOT_GREEN, tx(K_DEVICE, lang), K_RUNNING, lang, up, NULL);
    qr_screen(out, s, ap_qr, lang);
    /* recovery = set up once (a stored network, no setup pending), back on the hotspot: the rows stay
       and a tap shows the hotspot QR for DISPLAY_RECOVERY_QR_MS */
    if (s->wifi_mode == CALI_WIFI_MODE_SETUP && s->ssid[0] && !s->setup_pending) {
        if (in->touched) m->qr_until_ms = now_ms + DISPLAY_RECOVERY_QR_MS;
        if (now_ms >= m->qr_until_ms) out->qr[0] = 0;
    } else {
        m->qr_until_ms = 0;
    }
    if (s->wifi_mode == CALI_WIFI_MODE_SETUP) out->footer[0] = 0;   /* no .local address on the hotspot */
    else station_footer(out->footer, &in->batt, lang);
    lora_screen(m, now_ms, in, out);
    lora_texts(in, lang, out);
    wake_and_brightness(m, now_ms, in, cfg, out);
}

int cali_display_touch_gate(uint8_t *swallow, int pressed, int awake) {
    if (!pressed) *swallow = 0;
    else if (!awake) *swallow = 1;
    return !*swallow;
}
