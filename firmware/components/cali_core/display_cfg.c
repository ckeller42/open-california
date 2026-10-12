/* display_cfg.c — the screen settings in the kv store + the painter's last state for /api/state
 * device.display (R_FW_DISPLAY_WAKE). Contract: include/cali_display_model.h. Owner task only (the
 * painter's tick and the web handlers run there). Proven by tests/firmware/test_web_handlers.py. */
#include <stddef.h>

#include "cali_display_model.h"
#include "cali_platform.h"

/* one byte each, namespace cali; never shared with the bond / WiFi / water / auto-camper keys */
static const char *const KEYS[5] = {"disp_auto", "disp_prox", "disp_min", "disp_max", "disp_off"};
static const char *const STATE_NAMES[] = {"on", "dim", "off"};
static const char *const SENSOR_NAMES[] = {"ok", "absent", "error"};

static cali_display_cfg_t s_cfg = CALI_DISPLAY_CFG_DEFAULT;
static int s_loaded;
static cali_display_view_t s_live;
static int s_has_live;

static uint8_t *field(cali_display_cfg_t *c, int i) {
    return i == 0 ? &c->auto_on : i == 1 ? &c->prox_wake : i == 2 ? &c->min_pct : i == 3 ? &c->max_pct
                                                                               : (uint8_t *)&c->offset;
}

int cali_display_cfg_valid(const cali_display_cfg_t *c) {
    return c->auto_on <= 1 && c->prox_wake <= 1 && c->min_pct >= 1 && c->min_pct <= 50 && c->max_pct >= 20 &&
           c->max_pct <= 100 && c->min_pct < c->max_pct && c->offset >= -50 && c->offset <= 50;
}

const cali_display_cfg_t *cali_display_cfg(void) {
    if (!s_loaded) {
        cali_display_cfg_t c = CALI_DISPLAY_CFG_DEFAULT;
        s_loaded = 1;
        for (int i = 0; i < 5; i++) {
            uint8_t b;
            size_t n = 1;
            if (cali_kv_get(KEYS[i], &b, &n) == CALI_KV_OK && n == 1) *field(&c, i) = b;
        }
        if (cali_display_cfg_valid(&c)) s_cfg = c;
        else cali_log("display: stored settings out of range, using defaults");
    }
    return &s_cfg;
}

int cali_display_cfg_set(const cali_display_cfg_t *c) {
    cali_display_cfg_t v = *c;
    if (!cali_display_cfg_valid(c)) return -1;
    for (int i = 0; i < 5; i++)
        if (cali_kv_set(KEYS[i], field(&v, i), 1) != 0) return -2;
    s_cfg = v;
    s_loaded = 1;
    cali_log("display: settings auto=%u prox=%u min=%u max=%u offset=%d", v.auto_on, v.prox_wake, v.min_pct, v.max_pct,
             v.offset);
    return 0;
}

void cali_display_publish(const cali_display_view_t *v) {
    s_live = *v;
    s_has_live = 1;
}

static void int_or_null(cali_json_t *j, const char *key, int ok, long long v) {
    cali_json_key(j, key);
    if (ok) cali_json_int(j, v); else cali_json_null(j);
}

void cali_display_json(cali_json_t *j) {
    const cali_display_cfg_t *c = cali_display_cfg();
    cali_json_obj_begin(j);
    cali_json_key(j, "state");
    if (s_has_live) cali_json_str(j, STATE_NAMES[s_live.state]); else cali_json_null(j);
    int_or_null(j, "lux", s_has_live && s_live.lux >= 0, s_live.lux);
    int_or_null(j, "prox", s_has_live && s_live.prox >= 0, s_live.prox);
    int_or_null(j, "brightness_pct", s_has_live, s_live.brightness_pct);
    cali_json_key(j, "sensor");
    if (s_has_live) cali_json_str(j, SENSOR_NAMES[s_live.sensor]); else cali_json_null(j);
    cali_json_key(j, "auto");
    cali_json_bool(j, c->auto_on);
    cali_json_key(j, "prox_wake");
    cali_json_bool(j, c->prox_wake);
    cali_json_key(j, "min");
    cali_json_int(j, c->min_pct);
    cali_json_key(j, "max");
    cali_json_int(j, c->max_pct);
    cali_json_key(j, "offset");
    cali_json_int(j, c->offset);
    cali_json_obj_end(j);
}
