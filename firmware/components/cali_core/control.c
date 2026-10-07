/* control.c — calictl.control's builders + command_precondition in C (#154 B). Contract:
 * include/cali_control.h. Every constant and gate text comes from the generated csrc/control_consts.h;
 * each rule cites the Python it ports (calictl/control.py). No roof builder exists, by design.
 * Proven by tests/firmware/test_control_parity.py (tests/vectors/control.json).
 */
#include "cali_control.h"

#include <limits.h>
#include <stdio.h>
#include <string.h>

#include "codec_chars.h"
#include "control_consts.h"

#define N_OF(a) (sizeof (a) / sizeof *(a))
#define WS " \t\n\r\v\f"

/* ---- Python value coercions ---- */

static int is_ws(char c) { return c && strchr(WS, c) != NULL; }

static char lower(char c) { return c >= 'A' && c <= 'Z' ? (char)(c - 'A' + 'a') : c; }

/* str(value).strip().lower(), ASCII, truncated to cap - 1 bytes. */
static void norm(const char *v, char *out, size_t cap) {
    size_t n = 0, len;
    while (is_ws(*v)) v++;
    len = strlen(v);
    while (len && is_ws(v[len - 1])) len--;
    for (size_t i = 0; i < len && n + 1 < cap; i++) out[n++] = lower(v[i]);
    out[n] = 0;
}

static int one_of(const char *s, const char *const *set, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (strcmp(s, set[i]) == 0) return 1;
    return 0;
}

/* control.is_onoff / _truthy: 1 for on/true/1, 0 for off/false/0, -1 for anything else (ruling R3:
 * never read as off; the gate answers CALI_REASON_NOT_ONOFF before a builder sees it). */
static int onoff(const char *v) {
    static const char *const ON[] = {"on", "true", "1"};
    static const char *const OFF[] = {"off", "false", "0"};
    char s[CALI_CTL_VALUE_MAX];
    norm(v, s, sizeof s);
    return one_of(s, ON, N_OF(ON)) ? 1 : one_of(s, OFF, N_OF(OFF)) ? 0 : -1;
}

/* Python int(str): surrounding whitespace, an optional sign, ASCII digits with single '_' between
 * digits. 1 and *out (saturated at LONG_MIN/LONG_MAX: only its sign and range ever matter), or 0.
 * ASCII only: Python also takes Unicode digits/whitespace ("٣", "  5"), which C refuses as
 * BAD — the safe direction (C never writes a frame Python would not; the parity vectors are ASCII). */
static int py_int(const char *s, long *out) {
    int neg = 0, digits = 0, sat = 0;
    long v = 0;
    while (is_ws(*s)) s++;
    if (*s == '+' || *s == '-') neg = *s++ == '-';
    for (;; s++) {
        if (*s >= '0' && *s <= '9') {
            int d = *s - '0';
            if (v > (LONG_MAX - d) / 10) sat = 1;
            else v = v * 10 + d;
            digits++;
        } else if (!(*s == '_' && digits && s[1] >= '0' && s[1] <= '9')) {
            break;
        }
    }
    while (is_ws(*s)) s++;
    if (!digits || *s) return 0;
    *out = sat ? (neg ? LONG_MIN : LONG_MAX) : (neg ? -v : v);
    return 1;
}

/* control._int_range */
static int int_range(const char *v, long lo, long hi, long *out) {
    return py_int(v, out) && *out >= lo && *out <= hi;
}

/* control._hhmm on a string: exactly one ':', each side int()-parsed, 0-23 / 0-59. */
static int hhmm(const char *v, long *h, long *m) {
    char s[CALI_CTL_VALUE_MAX];
    char *c;
    snprintf(s, sizeof s, "%s", v);
    c = strchr(s, ':');
    if (!c || strchr(c + 1, ':')) return 0;
    *c = 0;
    return py_int(s, h) && py_int(c + 1, m) && *h >= 0 && *h <= 23 && *m >= 0 && *m <= 59;
}

static int lookup(const struct cali_ctl_name *t, size_t n, const char *k, long *out) {
    for (size_t i = 0; i < n; i++)
        if (strcmp(t[i].name, k) == 0) {
            *out = (long)t[i].value;
            return 1;
        }
    return 0;
}

/* ---- frame assembly ---- */

typedef struct {
    codec_kv_t kv[CODEC_KV_MAX];
    size_t n;
    int overflow;   /* a put() past CODEC_KV_MAX: add() then refuses the frame instead of defaulting a field */
} vals_t;

/* dict assignment: vals[name] = value */
static void put(vals_t *v, const char *name, uint32_t value) {
    for (size_t i = 0; i < v->n; i++)
        if (strcmp(v->kv[i].name, name) == 0) {
            v->kv[i].value = value;
            return;
        }
    if (v->n == CODEC_KV_MAX) {   /* cannot happen: lighting, the widest, has 20 fields */
        v->overflow = 1;
        return;
    }
    v->kv[v->n].name = name;
    v->kv[v->n].value = value;
    v->kv[v->n].supplied = 1;
    v->n++;
}

/* state.get(field, dflt) */
static uint32_t st(cali_ctl_get_t get, const char *fn, const char *field, uint32_t dflt) {
    uint32_t v;
    return get(fn, field, &v) ? v : dflt;
}

static const struct cali_ctl_char *ctl_char(const char *fn) {
    for (size_t i = 0; i < CALI_CTL_NCHARS; i++)
        if (strcmp(CALI_CTL_CHARS[i].function, fn) == 0) return &CALI_CTL_CHARS[i];
    return NULL;
}

/* protocol.encode(funcs[fn], vals, frame_bytes=CONTROL_FRAME_BYTES[fn]) appended to the plan; every
 * control field not in vals rides at its dictionary default, as in Python. 0 = appended. */
static int add(cali_ctl_plan_t *p, const char *fn, const vals_t *v, uint16_t delay) {
    const struct cali_ctl_char *c = ctl_char(fn);
    cali_ctl_frame_t *f = &p->f[p->n];
    size_t len = 0;
    if (p->n == CALI_CTL_MAX_FRAMES || v->overflow ||
        codec_encode(codec_func_by_name(fn), v->kv, v->n, c->frame_bytes, f->data, &len) != CODEC_OK)
        return -1;
    f->chr = c->control_short;
    f->len = (uint8_t)len;
    f->delay_ms = delay;
    p->n++;
    return 0;
}

/* control.commit_for("lighting") = LIGHT_COMMIT, FOLLOW_DELAY_S after the previous write */
static int add_commit(cali_ctl_plan_t *p) {
    cali_ctl_frame_t *f = &p->f[p->n];
    if (p->n == CALI_CTL_MAX_FRAMES) return -1;
    f->chr = ctl_char("lighting")->control_short;
    f->delay_ms = CODEC_FOLLOW_DELAY_MS;
    f->len = (uint8_t)sizeof CALI_LIGHT_COMMIT;
    memcpy(f->data, CALI_LIGHT_COMMIT, sizeof CALI_LIGHT_COMMIT);
    p->n++;
    return 0;
}

/* ---- builders (control.BUILDERS) ---- */

/* ponytail: static scratch (not reentrant): one owner task, and the 8 KB host-task stack also runs the web handler */
static vals_t s_v, s_c;

static void reset(vals_t *v) { v->n = 0; v->overflow = 0; }

/* The builders' `onoff(value) < 0 -> BAD_VALUE` checks are a second layer: gate() already answers
 * CALI_REASON_NOT_ONOFF for every on/off command (control.ONOFF_COMMANDS), so via cali_ctl_plan they are
 * unreachable; they keep a builder honest for any other caller (never garbage -> OFF, ruling R3). */

/* control._cooler: every untargeted field at the app's leave-unchanged value = the dictionary default
 * (_cooler_neutral, ruling R1), which codec_encode fills in for every field not put here. Only
 * night_on/night_off carry the current state (_cooler_values; the hour bytes are literal, #99) and
 * need a known cooler state (ruling R4; the gate refuses first). */
static int b_cooler(const char *what, const char *value, cali_ctl_get_t get, cali_ctl_plan_t *p) {
    vals_t *v = &s_v;
    long a, b;
    char k[CALI_CTL_VALUE_MAX];
    reset(v);
    if (strcmp(what, "power") == 0) {
        int on = onoff(value);
        if (on < 0) return CALI_CTL_BAD_VALUE;
        put(v, "State", (uint32_t)on);
    } else if (strcmp(what, "level") == 0) {
        if (!int_range(value, 1, 5, &a)) return CALI_CTL_BAD_VALUE;
        put(v, "Level", (uint32_t)a);
    } else if (strcmp(what, "mode") == 0) {
        norm(value, k, sizeof k);
        if (!lookup(CALI_COOLER_MODES, N_OF(CALI_COOLER_MODES), k, &a)) return CALI_CTL_NONE;
        put(v, "Mode", (uint32_t)a);
    } else if (strcmp(what, "timer_set") == 0) {
        if (!hhmm(value, &a, &b)) return CALI_CTL_BAD_VALUE;
        put(v, "TimerHour", (uint32_t)a);
        put(v, "TimerMin", (uint32_t)b);
    } else if (strcmp(what, "timer_start") == 0) {
        put(v, "TimerStart", 1);
    } else if (strcmp(what, "timer_cancel") == 0) {
        put(v, "TimerCancel", 1);
    } else if (strcmp(what, "night_on") == 0 || strcmp(what, "night_off") == 0) {
        uint32_t x;
        if (!int_range(value, 0, 23, &a)) return CALI_CTL_BAD_VALUE;
        if (!get("cooler", "State", &x)) return CALI_CTL_BAD_VALUE;   /* R4: no state -> CommandError */
        put(v, "State", st(get, "cooler", "State", 1));               /* _cooler_values */
        put(v, "Mode", st(get, "cooler", "Mode", 4));
        put(v, "Level", st(get, "cooler", "Level", 3));
        put(v, "TimerStart", CALI_SENTINEL);
        put(v, "TimerCancel", CALI_SENTINEL);
        put(v, "NightTimerSet", st(get, "cooler", "NightTimerSet", 0));
        put(v, "NightTimerHourOn", st(get, "cooler", "NightTimerHourOn", 0));
        put(v, "NightTimerHourOff", st(get, "cooler", "NightTimerHourOff", 0));
        put(v, "TimerHour", st(get, "cooler", "TimerHourSet", 0));
        put(v, "TimerMin", st(get, "cooler", "TimerMinSet", 0));
        put(v, strcmp(what, "night_on") == 0 ? "NightTimerHourOn" : "NightTimerHourOff", (uint32_t)a);
    } else {
        return CALI_CTL_NONE;
    }
    return add(p, "cooler", v, 0) ? CALI_CTL_BAD_VALUE : CALI_CTL_OK;
}

static int b_camping(const char *what, const char *value, cali_ctl_plan_t *p) {
    vals_t *v = &s_v;
    int on = onoff(value);
    if (on < 0) return CALI_CTL_BAD_VALUE;
    reset(v);
    put(v, "State", CALI_SENTINEL);                          /* camping_values */
    put(v, "UsbCharger", CALI_SENTINEL);
    put(v, "InteriorLight", CALI_SENTINEL);
    put(v, "OutsideLight", CALI_SENTINEL);
    if (strcmp(what, "lights") == 0) {                       /* inverted, both fields */
        put(v, "InteriorLight", on ? CALI_LIGHT_ON : CALI_LIGHT_OFF);
        put(v, "OutsideLight", on ? CALI_LIGHT_ON : CALI_LIGHT_OFF);
    } else if (strcmp(what, "usb") == 0) {
        put(v, "UsbCharger", (uint32_t)on);
    } else if (strcmp(what, "master") == 0) {
        put(v, "State", (uint32_t)on);
    } else {
        return CALI_CTL_NONE;
    }
    return add(p, "campingmode", v, 0) ? CALI_CTL_BAD_VALUE : CALI_CTL_OK;
}

static int b_energy(const char *what, const char *value, cali_ctl_plan_t *p) {
    vals_t *v = &s_v;
    char k[CALI_CTL_VALUE_MAX];
    long m;
    if (strcmp(what, "mode") != 0) return CALI_CTL_NONE;
    norm(value, k, sizeof k);
    if (!lookup(CALI_ENERGY_MODES, N_OF(CALI_ENERGY_MODES), k, &m)) return CALI_CTL_NONE;
    reset(v);
    put(v, "EnergyModeSet", (uint32_t)m);
    put(v, "DisplayRefresh", 0);
    return add(p, "energy", v, 0) ? CALI_CTL_BAD_VALUE : CALI_CTL_OK;
}

static int b_airheater(const char *what, const char *value, cali_ctl_plan_t *p) {
    static const char *const OFF[] = {"off", "false", "0"};
    vals_t *v = &s_v;
    char k[CALI_CTL_VALUE_MAX];
    long a, b;
    reset(v);
    put(v, "NormalOperationRequest", CALI_SENTINEL);         /* _airheater_values */
    put(v, "PermanentOperationRequest", CALI_SENTINEL);
    put(v, "PermanentOperationConfirmation", CALI_SENTINEL);
    put(v, "AirDistribution", 0);
    put(v, "OperationModeAirHeater", CALI_AIRHEATER_MODE_UNCHANGED);
    put(v, "HeatingLevel", CALI_AIRHEATER_LEVEL_UNCHANGED);
    put(v, "OperationModeCombined", 0);
    put(v, "RunningTime", CALI_AIRHEATER_RUNTIME_UNCHANGED);
    put(v, "TimerHour", CALI_AIRHEATER_TIMER_HOUR_UNCHANGED);
    put(v, "TimerMin", CALI_AIRHEATER_TIMER_MIN_UNCHANGED);
    if (strcmp(what, "power") == 0) {
        int on = onoff(value);
        if (on < 0) return CALI_CTL_BAD_VALUE;
        put(v, "NormalOperationRequest", (uint32_t)on);
    } else if (strcmp(what, "level") == 0) {
        if (!int_range(value, 1, 10, &a)) return CALI_CTL_BAD_VALUE;
        put(v, "HeatingLevel", (uint32_t)a);
    } else if (strcmp(what, "runtime") == 0) {
        if (!int_range(value, 0, CALI_AIRHEATER_MAX_RUNTIME_MIN, &a)) return CALI_CTL_BAD_VALUE;
        put(v, "RunningTime", (uint32_t)a);
    } else if (strcmp(what, "timer") == 0) {
        if (!hhmm(value, &a, &b)) return CALI_CTL_BAD_VALUE;
        put(v, "TimerHour", (uint32_t)a);
        put(v, "TimerMin", (uint32_t)b);
    } else if (strcmp(what, "timer_start") == 0) {
        put(v, "OperationModeAirHeater", CALI_AIRHEATER_MODE_TIMER_ARMED);
        put(v, "OperationModeCombined", CALI_AIRHEATER_COMBINED_AIR_HEATER);
    } else if (strcmp(what, "timer_cancel") == 0) {
        put(v, "OperationModeAirHeater", CALI_AIRHEATER_MODE_IDLE);
    } else if (strcmp(what, "permanent") == 0) {               /* OFF only */
        norm(value, k, sizeof k);
        if (!one_of(k, OFF, N_OF(OFF))) return CALI_CTL_BAD_VALUE;
        put(v, "PermanentOperationRequest", 0);
    } else {
        return CALI_CTL_NONE;
    }
    return add(p, "airheater", v, 0) ? CALI_CTL_BAD_VALUE : CALI_CTL_OK;
}

static int is_real_zone(const char *z) {
    return one_of(z, CALI_LIGHT_REAL_ZONE_FIELDS, N_OF(CALI_LIGHT_REAL_ZONE_FIELDS));
}

/* every zone = b (b = LIGHT_UNCHANGED for the profile-select frames) */
static void zones_all(vals_t *v, uint32_t b) {
    for (size_t i = 0; i < N_OF(CALI_LIGHT_ZONE_FIELDS); i++) put(v, CALI_LIGHT_ZONE_FIELDS[i], b);
}

/* control._all_real_zones */
static void zones_real(vals_t *v, uint32_t b) {
    for (size_t i = 0; i < N_OF(CALI_LIGHT_ZONE_FIELDS); i++)
        put(v, CALI_LIGHT_ZONE_FIELDS[i], is_real_zone(CALI_LIGHT_ZONE_FIELDS[i]) ? b : CALI_LIGHT_UNCHANGED);
}

/* control._saved_zones: every real zone at its reported level 0-11, else unchanged */
static void zones_saved(vals_t *v, cali_ctl_get_t get) {
    for (size_t i = 0; i < N_OF(CALI_LIGHT_ZONE_FIELDS); i++) {
        const char *z = CALI_LIGHT_ZONE_FIELDS[i];
        uint32_t x;
        put(v, z, is_real_zone(z) && get("lighting", z, &x) && x <= CALI_LIGHT_MAX_SET ? x : CALI_LIGHT_UNCHANGED);
    }
}

/* control._save_profile_args: "N" or "N <colour>" (N 1-7) -> *n, *colour (-1 = none); 0 = bad */
static int save_args(const char *value, long *n, long *colour) {
    char s[CALI_CTL_VALUE_MAX];
    char *t0, *t1, *t2;
    snprintf(s, sizeof s, "%s", value);
    t0 = strtok(s, WS);
    t1 = t0 ? strtok(NULL, WS) : NULL;
    t2 = t1 ? strtok(NULL, WS) : NULL;
    if (!t0 || t2 || !int_range(t0, 1, 7, n)) return 0;
    *colour = -1;
    if (!t1) return 1;
    for (char *c = t1; *c; c++) *c = *c == '_' ? '-' : lower(*c);
    return lookup(CALI_LIGHT_COLORS, N_OF(CALI_LIGHT_COLORS), t1, colour);
}

static const char *zone_field(const char *what) {
    for (size_t i = 0; i < N_OF(CALI_LIGHT_ZONES); i++)
        if (strcmp(CALI_LIGHT_ZONES[i].key, what) == 0) return CALI_LIGHT_ZONES[i].field;
    return what;
}

const char *const CALI_LCFG_KEYS[CALI_LCFG_N] = {"WakeupTimestamp", "WakeupLightValue", "DoorContact",
                                                 "FavouritesStored"};

#define LCFG_SET(c, k, x) ((c)->v[k] = (x), (c)->have |= 1u << (k))

/* semantics.lighting_config(None, state) over get(): the carried keys, then this frame's own config */
void cali_light_cfg(cali_ctl_get_t get, cali_light_cfg_t *out) {
    uint32_t mode, pn, lv, ts;
    int p, l;
    memset(out, 0, sizeof *out);
    for (int k = 0; k < CALI_LCFG_N; k++)
        if (get("lighting", CALI_LCFG_KEYS[k], &out->v[k])) out->have |= 1u << k;
    if (!get("lighting", "Mode", &mode)) return;
    p = get("lighting", "ProfileNumber", &pn);
    l = get("lighting", "LightValue", &lv);
    if (mode == CALI_LIGHT_MODE_WAKEUP_TIME && l && get("lighting", "Timestamp", &ts)) {
        LCFG_SET(out, CALI_LCFG_WAKE_TS, ts);
        LCFG_SET(out, CALI_LCFG_WAKE_LV, lv);
    } else if (mode == CALI_LIGHT_MODE_SET_PROFILE && p && pn == CALI_LIGHT_PROFILE_DOOR_CONTACT && l) {
        LCFG_SET(out, CALI_LCFG_DOOR, lv);
    } else if (mode == CALI_LIGHT_MODE_REQUEST_CONFIG && l && !(p && pn == 13)) {   /* 13: the request's echo */
        LCFG_SET(out, CALI_LCFG_FAVS, lv & 0x7Fu);
    } else if (mode == CALI_LIGHT_MODE_SET_BRIGHTNESS && p && pn >= 1 && pn <= 7 &&
               (out->have >> CALI_LCFG_FAVS & 1u)) {
        out->v[CALI_LCFG_FAVS] |= 1u << (pn - 1);   /* only ADD to known bits: never invent "empty" */
    }
}

/* control._lighting (wakeup: p_wakeup) + preface_for + commit_for */
static int b_lighting(const char *what, const char *value, cali_ctl_get_t get, cali_ctl_plan_t *p) {
    vals_t *v = &s_v;
    long n, colour;
    int on;
    reset(v);
    put(v, "ProfileNumber", CALI_LIGHT_BRIGHTNESS_PROFILE);
    put(v, "Mode", CALI_LIGHT_MODE_SET_BRIGHTNESS);
    put(v, "Timestamp", 0);
    put(v, "LightValue", 0);
    if (strcmp(what, "profile") == 0) {
        if (!int_range(value, 0, 13, &n) || n == CALI_LIGHT_PROFILE_DOOR_CONTACT) return CALI_CTL_BAD_VALUE;
        put(v, "Mode", CALI_LIGHT_MODE_SET_PROFILE);
        put(v, "ProfileNumber", (uint32_t)n);
        zones_all(v, CALI_LIGHT_UNCHANGED);
    } else if (strcmp(what, "save_profile") == 0) {
        if (!save_args(value, &n, &colour)) return CALI_CTL_BAD_VALUE;
        if (colour >= 0) {                                   /* preface_for: SET_COLOR first */
            reset(&s_c);
            put(&s_c, "ProfileNumber", (uint32_t)n);
            put(&s_c, "Mode", CALI_LIGHT_MODE_SET_COLOR);
            put(&s_c, "Timestamp", 0);
            put(&s_c, "LightValue", (uint32_t)colour);
            zones_saved(&s_c, get);
            if (add(p, "lighting", &s_c, 0) || add_commit(p)) return CALI_CTL_BAD_VALUE;
        }
        put(v, "ProfileNumber", (uint32_t)n);
        zones_saved(v, get);
    } else if (strcmp(what, "door_contact") == 0) {
        if ((on = onoff(value)) < 0) return CALI_CTL_BAD_VALUE;
        put(v, "Mode", CALI_LIGHT_MODE_SET_PROFILE);
        put(v, "ProfileNumber", CALI_LIGHT_PROFILE_DOOR_CONTACT);
        put(v, "LightValue", (uint32_t)on);
        zones_all(v, CALI_LIGHT_UNCHANGED);
    } else if (strcmp(what, "power") == 0) {
        if ((on = onoff(value)) < 0) return CALI_CTL_BAD_VALUE;
        put(v, "Mode", CALI_LIGHT_MODE_SET_PROFILE);
        put(v, "ProfileNumber", on ? CALI_LIGHT_PROFILE_ALL_ON : CALI_LIGHT_PROFILE_ALL_OFF);
        zones_all(v, CALI_LIGHT_UNCHANGED);
    } else if (strcmp(what, "all") == 0) {
        if (!int_range(value, 0, CALI_LIGHT_MAX_SET, &n)) return CALI_CTL_BAD_VALUE;
        zones_real(v, (uint32_t)n);
    } else {                                                 /* one zone */
        const char *field = zone_field(what);
        if (strcmp(what, "color") == 0 ||                    /* retired: the app recolours a favourite */
            !one_of(field, CALI_LIGHT_ZONE_FIELDS, N_OF(CALI_LIGHT_ZONE_FIELDS)) ||
            !int_range(value, 0, CALI_LIGHT_MAX_SET, &n))
            return CALI_CTL_BAD_VALUE;
        zones_all(v, CALI_LIGHT_UNCHANGED);
        put(v, field, (uint32_t)n);
    }
    if (add(p, "lighting", v, 0) || add_commit(p)) return CALI_CTL_BAD_VALUE;
    return CALI_CTL_OK;
}

/* ---- the wake-up light (control.wakeup_request + _lighting "wakeup", the page's clock) ---- */

typedef struct {
    long hour, minute;
    uint32_t colour, areas, brightness, ramp, enabled;   /* areas: bit a-1 = vehicle area a */
} wake_t;

static int wake_known(const cali_light_cfg_t *c) {
    return (c->have >> CALI_LCFG_WAKE_TS & 1u) && (c->have >> CALI_LCFG_WAKE_LV & 1u);
}

/* "1,3" -> bits; each 1-4 (control._int_range), "" or ",," refused like int("") */
static int wake_areas(char *s, uint32_t *areas) {
    long a;
    *areas = 0;
    for (;;) {
        char *comma = strchr(s, ',');
        if (comma) *comma = 0;
        if (!int_range(s, 1, 4, &a)) return 0;
        *areas |= 1u << (a - 1);
        if (!comma) return 1;
        s = comma + 1;
    }
}

/* control.wakeup_request over semantics.wakeup_config(cfg): 1 ok, 0 malformed (ValueError).
 * *switched: an on/off token was given (command_precondition's R5 test). */
static int wake_request(const char *value, const cali_light_cfg_t *cfg, wake_t *w, int *switched) {
    char s[CALI_CTL_VALUE_MAX], k[8], *tok, *pos[3] = {NULL, NULL, NULL};
    int npos = 0, ntok = 0, timed = 0, known = wake_known(cfg);
    long b;
    uint32_t ts = cfg->v[CALI_LCFG_WAKE_TS], lv = cfg->v[CALI_LCFG_WAKE_LV];
    if (known) {
        w->hour = (long)(ts / 3600 % 24);
        w->minute = (long)(ts / 60 % 60);
        w->colour = lv >> 12 & 0xFu;
        w->areas = lv >> 8 & 0xFu;
        w->brightness = lv >> 4 & 0xFu;
        w->ramp = ((lv & 0xFu) >> 1) * 10;
        w->enabled = lv & 1u;   /* an edit carries the switch the UNIT reported */
    } else {
        w->hour = w->minute = 0;
        w->colour = CALI_WAKEUP_DEFAULT_COLOUR;
        w->areas = CALI_WAKEUP_DEFAULT_AREAS;
        w->brightness = CALI_WAKEUP_DEFAULT_BRIGHTNESS;
        w->ramp = CALI_WAKEUP_DEFAULT_RAMP;
        w->enabled = 0;
    }
    *switched = 0;
    snprintf(s, sizeof s, "%s", value);
    for (tok = strtok(s, WS); tok; tok = strtok(NULL, WS)) {
        ntok++;
        norm(tok, k, sizeof k);
        if (strcmp(k, "on") == 0 || strcmp(k, "off") == 0) {
            w->enabled = k[1] == 'n';
            *switched = 1;
        } else if (strchr(tok, ':')) {
            if (!hhmm(tok, &w->hour, &w->minute)) return 0;
            timed = 1;
        } else {
            if (npos < 3) pos[npos] = tok;
            npos++;
        }
    }
    if (!ntok || (!known && !timed) || npos > 3) return 0;
    if (npos > 0 && !wake_areas(pos[0], &w->areas)) return 0;
    if (npos > 1) {
        if (!int_range(pos[1], 0, 10, &b)) return 0;
        w->brightness = (uint32_t)b;
    }
    if (npos > 2) {
        int ok = 0;
        if (!py_int(pos[2], &b)) return 0;
        for (size_t i = 0; i < N_OF(CALI_WAKEUP_RAMPS_MIN); i++) ok |= b == CALI_WAKEUP_RAMPS_MIN[i];
        if (!ok) return 0;
        w->ramp = (uint32_t)b;
    }
    return 1;
}

/* control.next_wakeup_epoch: the next HH:MM after now, both local wall clock read as UTC (no zone,
 * no DST: the page already applied its zone). Today if still ahead, else tomorrow. */
static int64_t next_wakeup(long h, long m, int64_t now) {
    int64_t t = now - now % 86400 + (int64_t)h * 3600 + (int64_t)m * 60;
    return t <= now ? t + 86400 : t;
}

/* command_precondition's wake-up gates, then the Mode-20 frame (dg/h.m0) + commit */
static int p_wakeup(const char *value, int64_t now, cali_ctl_get_t get, cali_ctl_plan_t *p) {
    vals_t *v = &s_v;
    cali_light_cfg_t cfg;
    wake_t w;
    int switched;
    int64_t ts;
    /* ponytail: a >= 64-byte value is BAD here even when Python would parse it (web.c caps the value at 63) */
    if (strlen(value) >= CALI_CTL_VALUE_MAX) return CALI_CTL_BAD_VALUE;
    cali_light_cfg(get, &cfg);
    if (!wake_request(value, &cfg, &w, &switched)) return CALI_CTL_BAD_VALUE;
    if (w.enabled && !w.areas) {
        p->reason = CALI_REASON_WAKEUP_NO_AREA;
        return CALI_CTL_REFUSED;
    }
    if (!switched && !wake_known(&cfg)) {   /* R5: an edit would silently disarm — pull, then refuse */
        p->reason = CALI_WAKEUP_UNKNOWN;
        p->pull = 1;
        return CALI_CTL_REFUSED;
    }
    /* protocol.encode: Timestamp is 32-bit, and the next HH:MM is after now — so a clock past it is BAD
     * before next_wakeup could overflow int64 (a huge local_now must never wrap into a made-up time) */
    if (now > (int64_t)UINT32_MAX) return CALI_CTL_BAD_VALUE;
    ts = next_wakeup(w.hour, w.minute, now);
    if (ts > (int64_t)UINT32_MAX) return CALI_CTL_BAD_VALUE;
    reset(v);
    put(v, "ProfileNumber", CALI_LIGHT_UNCHANGED);
    put(v, "Mode", CALI_LIGHT_MODE_WAKEUP_TIME);
    put(v, "Timestamp", (uint32_t)ts);
    put(v, "LightValue", w.colour << 12 | w.areas << 8 | w.brightness << 4 | (w.ramp / 10) << 1 | w.enabled);
    zones_all(v, CALI_LIGHT_UNCHANGED);
    if (add(p, "lighting", v, 0) || add_commit(p)) return CALI_CTL_BAD_VALUE;
    return CALI_CTL_OK;
}

void cali_ctl_pull_plan(cali_ctl_plan_t *out) {
    cali_ctl_frame_t *f = &out->f[0];
    memset(out, 0, sizeof *out);
    f->chr = ctl_char("lighting")->control_short;
    f->len = (uint8_t)sizeof CALI_LIGHT_REQUEST_CONFIG;
    memcpy(f->data, CALI_LIGHT_REQUEST_CONFIG, sizeof CALI_LIGHT_REQUEST_CONFIG);
    out->n = 1;
    (void)add_commit(out);
    out->rc = CALI_CTL_OK;
}

/* ---- control.command_precondition (the five functions; roof + wakeup never get here) ---- */

/* control.ONOFF_COMMANDS restricted to the ESP's functions (hand-copied: a new on/off command in
 * Python also needs a row here, else C answers BAD instead of REASON_NOT_ONOFF — never OFF either way;
 * the generated vectors catch the text divergence once the grid has the case). */
static int is_onoff_command(const char *fn, const char *what) {
    static const char *const CAMPING[] = {"master", "lights", "usb"};
    static const char *const LIGHTING[] = {"power", "door_contact"};
    if (strcmp(fn, "cooler") == 0 || strcmp(fn, "airheater") == 0) return strcmp(what, "power") == 0;
    if (strcmp(fn, "campingmode") == 0) return one_of(what, CAMPING, N_OF(CAMPING));
    if (strcmp(fn, "lighting") == 0) return one_of(what, LIGHTING, N_OF(LIGHTING));
    return 0;
}

static const char *gate(const char *fn, const char *what, const char *value, cali_ctl_get_t get) {
    uint32_t x;
    long n;
    int cooler = strcmp(fn, "cooler") == 0, lighting = strcmp(fn, "lighting") == 0;
    if (is_onoff_command(fn, what) && onoff(value) < 0) return CALI_REASON_NOT_ONOFF;   /* R3 */
    if (lighting && strcmp(what, "roof-reading") == 0 && py_int(value, &n) && n > 0 &&
        get("roof", "Position", &x))
        for (size_t i = 0; i < N_OF(CALI_ROOF_CLOSED_POSITIONS); i++)
            if (x == CALI_ROOF_CLOSED_POSITIONS[i]) return CALI_REASON_ROOF_READING;
    if (cooler && (strcmp(what, "timer_set") == 0 || strcmp(what, "timer_start") == 0) &&
        get("cooler", "State", &x) && x == 1)
        return CALI_REASON_COOLER_TIMER_NEEDS_FRIDGE_OFF;
    if (cooler && (strcmp(what, "mode") == 0 || strcmp(what, "night_on") == 0 || strcmp(what, "night_off") == 0) &&
        get("cooler", "State", &x) && x == 0)
        return CALI_REASON_QUIET_NEEDS_FRIDGE_ON;
    /* R4: `not states.get("cooler")` — a decoded cooler frame always carries State (byte 0) */
    if (cooler && (strcmp(what, "night_on") == 0 || strcmp(what, "night_off") == 0) && !get("cooler", "State", &x))
        return CALI_REASON_COOLER_STATE_UNKNOWN;
    if (strcmp(fn, "campingmode") == 0 && (strcmp(what, "lights") == 0 || strcmp(what, "usb") == 0) &&
        get("campingmode", "State", &x) && x == 0)
        return CALI_REASON_CAMPING_NEEDS_MASTER;
    if (strcmp(fn, "energy") == 0 && strcmp(what, "mode") == 0 && get("energy", "EnergyModeNotSelectable", &x) &&
        x == 1)
        return CALI_REASON_ENERGY_LOCKED;
    /* command_precondition's favourite rule over the latch (semantics.lighting_config): refuse only a
     * slot the unit positively reported empty */
    if (lighting && strcmp(what, "profile") == 0 && py_int(value, &n) && n >= 1 && n <= 7) {
        cali_light_cfg_t cfg;
        cali_light_cfg(get, &cfg);
        if ((cfg.have >> CALI_LCFG_FAVS & 1u) && !(cfg.v[CALI_LCFG_FAVS] >> (n - 1) & 1u))
            return CALI_REASON_FAVOURITE_EMPTY;
    }
    return NULL;
}

void cali_ctl_plan(const char *fn, const char *what, const char *value, int64_t local_now,
                   cali_ctl_get_t get, cali_ctl_plan_t *out) {
    memset(out, 0, sizeof *out);
    if (!ctl_char(fn)) {
        out->rc = CALI_CTL_ELSEWHERE;
        out->reason = CALI_REASON_ELSEWHERE;
        return;
    }
    if (strcmp(fn, "lighting") == 0 && strcmp(what, "wakeup") == 0) {
        if (local_now < 0) {
            out->rc = CALI_CTL_ELSEWHERE;
            out->reason = CALI_REASON_WAKEUP_CLOCK;
        } else {
            out->rc = p_wakeup(value, local_now, get, out);
        }
        if (out->rc != CALI_CTL_OK) out->n = 0;
        return;
    }
    out->reason = gate(fn, what, value, get);
    if (out->reason) {
        out->rc = CALI_CTL_REFUSED;
        return;
    }
    /* The header's precondition, after the gate like Python (command_precondition never truncates; a
     * 64+ char value cannot equal an on/off token): control.build would raise, never silently clip
     * (hhmm/save_args copy value into CALI_CTL_VALUE_MAX bytes). */
    if (strlen(value) >= CALI_CTL_VALUE_MAX) {
        out->rc = CALI_CTL_BAD_VALUE;
        return;
    }
    if (strcmp(fn, "cooler") == 0) out->rc = b_cooler(what, value, get, out);
    else if (strcmp(fn, "campingmode") == 0) out->rc = b_camping(what, value, out);
    else if (strcmp(fn, "energy") == 0) out->rc = b_energy(what, value, out);
    else if (strcmp(fn, "airheater") == 0) out->rc = b_airheater(what, value, out);
    else out->rc = b_lighting(what, value, get, out);
    if (out->rc != CALI_CTL_OK) out->n = 0;
}

int cali_ctl_write_ok(uint16_t chr, size_t len) {
    for (size_t i = 0; i < CALI_CTL_NCHARS; i++)
        if (CALI_CTL_CHARS[i].control_short == chr) return len == CALI_CTL_CHARS[i].frame_bytes;
    return 0;
}
