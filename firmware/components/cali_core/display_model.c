/* display_model.c — pure status-display model, see include/cali_display_model.h (#154).
 * Implements ``R_FW_STATUS_DISPLAY`` (docs/firmware.md): three rows (device always green; WiFi
 * green/amber/red; camper green/amber/red/grey), a stale rule (no snapshot for DISPLAY_STALE_MS turns
 * the camper row red), the footer, and a dim-after-idle brightness, all derived from one
 * cali_status_t. Every text is an EN/DE pair generated from firmware/web/strings.json. */
#include "cali_display_model.h"

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
    K_CONNECTING, K_PAIRING, K_PASSKEY, K_LINK_LOST, K_STALE, K_PAIR_ERROR, K_NOT_PAIRED, K_FOOTER_SETUP, K_FAIL_NOT_FOUND, K_FAIL_AUTH, K_FAIL_OTHER, K_COUNT
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
    [K_FOOTER_SETUP] = {WEB_STR_DE_D_FOOTER_SETUP, WEB_STR_EN_D_FOOTER_SETUP},
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

static int wifi_row(cali_row_t *r, const cali_status_t *s, int lang) {
    const char *label = tx(K_WIFI, lang);
    if (s->wifi_mode == CALI_WIFI_MODE_STATION && s->ip != 0) {
        char ip[16], tmp[2 * CALI_ROW_TEXT_MAX];
        cali_status_ip_str(s->ip, ip);
        if (s->rssi) snprintf(tmp, sizeof tmp, "%s · %s · %d dBm", s->ssid, ip, s->rssi);
        else snprintf(tmp, sizeof tmp, "%s · %s", s->ssid, ip);  /* 0 = unknown, as /api/state's null */
        r->dot = CALI_DOT_GREEN;
        r->label = label;
        set_text(r, tmp);
        return 0; /* the text key: only the colour/wording matters for dimming, not the numbers */
    }
    if (s->wifi_mode == CALI_WIFI_MODE_SETUP) {
        row(r, CALI_DOT_AMBER, label, K_SETUP, lang, NET_AP_SSID, NET_AP_ADDR);
        return K_SETUP;
    }
    if (s->wifi_state == WIFI_CONNECTING) {
        row(r, CALI_DOT_AMBER, label, K_JOINING, lang, s->ssid, NULL);
        return K_JOINING;
    }
    if (s->wifi_state == WIFI_RETRYING && s->wifi_mode == CALI_WIFI_MODE_STATION) {
        row(r, CALI_DOT_AMBER, label, K_RETRYING, lang, s->ssid, NULL);
        return K_RETRYING;
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
    return K_WIFI_OFF;
}

static int camper_row(cali_row_t *r, const cali_status_t *s, int lang) {
    const char *label = tx(K_CAMPER, lang);
    char age[24];
    switch (s->pair_state) {
    case PAIR_SCANNING: case PAIR_CONNECTING: case PAIR_RESETTING:
        row(r, CALI_DOT_AMBER, label, K_CONNECTING, lang, NULL, NULL);
        return K_CONNECTING;
    case PAIR_WAITING_PASSKEY:
        row(r, CALI_DOT_AMBER, label, K_PASSKEY, lang, NULL, NULL);
        return K_PASSKEY;
    case PAIR_PAIRING: case PAIR_VERIFYING:
        row(r, CALI_DOT_AMBER, label, K_PAIRING, lang, NULL, NULL);
        return K_PAIRING;
    case PAIR_ERROR:
        row(r, CALI_DOT_RED, label, K_PAIR_ERROR, lang, NULL, NULL);
        return K_PAIR_ERROR;
    default:
        break;
    }
    if (!s->address[0]) {
        row(r, CALI_DOT_GREY, label, K_NOT_PAIRED, lang, NULL, NULL);
        return K_NOT_PAIRED;
    }
    if (!s->link_up) {
        row(r, CALI_DOT_RED, label, K_LINK_LOST, lang, NULL, NULL);
        return K_LINK_LOST;
    }
    if (s->snap_age_ms < 0) {  /* link up, nothing received yet */
        row(r, CALI_DOT_AMBER, label, K_CONNECTING, lang, NULL, NULL);
        return K_CONNECTING;
    }
    if (s->snap_age_ms > DISPLAY_STALE_MS) {  /* whole seconds, rounded down */
        snprintf(age, sizeof age, "%u s", (unsigned)(s->snap_age_ms / 1000));
        row(r, CALI_DOT_RED, label, K_STALE, lang, age, NULL);
        return K_STALE;
    }
    {  /* rounded up, at least 1 s */
        unsigned sec = (unsigned)((s->snap_age_ms + 999) / 1000);
        snprintf(age, sizeof age, "%u s", sec ? sec : 1u);
    }
    row(r, CALI_DOT_GREEN, label, K_CONNECTED, lang, age, NULL);
    return K_CONNECTED;
}

void cali_display_model_init(cali_display_model_t *m) { memset(m, 0, sizeof *m); }

void cali_display_model(cali_display_model_t *m, const cali_status_t *s, uint64_t now_ms, int lang,
                        cali_display_view_t *out) {
    char up[24];
    int kw = wifi_row(&out->wifi, s, lang);
    int kc = camper_row(&out->camper, s, lang);
    uint32_t sig;
    fmt_uptime(s->uptime_ms, up);
    row(&out->device, CALI_DOT_GREEN, tx(K_DEVICE, lang), K_RUNNING, lang, up, NULL);
    out->footer_setup = s->wifi_mode == CALI_WIFI_MODE_SETUP;
    if (out->footer_setup) {
        char tmp[2 * CALI_ROW_TEXT_MAX];
        snprintf(tmp, sizeof tmp, tx(K_FOOTER_SETUP, lang), NET_AP_SSID, NET_AP_PSK);
        cali_display_fit_text(out->footer, tmp);
    } else {
        cali_display_fit_text(out->footer, "http://" NET_HOSTNAME ".local");
    }

    /* dim signature: dots, text keys and footer; never the changing numbers */
    sig = 2166136261u;
    {
        unsigned parts[] = {(unsigned)out->wifi.dot, (unsigned)kw, (unsigned)out->camper.dot,
                            (unsigned)kc, (unsigned)out->footer_setup};
        size_t i;
        for (i = 0; i < sizeof parts / sizeof parts[0]; i++) sig = (sig ^ parts[i]) * 16777619u;
    }
    if (!m->started || sig != m->sig) {
        m->started = 1;
        m->sig = sig;
        m->last_change_ms = now_ms;
    }
    out->brightness_pct = now_ms - m->last_change_ms < DISPLAY_DIM_AFTER_MS ? DISPLAY_BRIGHT_PCT : DISPLAY_DIM_PCT;
}
