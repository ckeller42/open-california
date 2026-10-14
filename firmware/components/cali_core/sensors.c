/* sensors.c — external BLE sensors (R_FW_SENSORS): type table, decoders, seen/configured tables,
 * readings, JSON, ext records and the window cadence. Contract: include/cali_sensors.h. */
#include "cali_sensors.h"

#include <stdio.h>
#include <string.h>

#include "cali_diag.h"
#include "cali_platform.h"

#define SENS_MAX 16
#define SENS_WINDOW_MS 5000u
#define SENS_PERIOD_MS 60000u
#define SENS_KICK_MIN_MS 15000u
#define SENS_REC_MIN_MS 60000u
#define SENS_OFFLINE_S 600 /* seen rows and "value" age past this: dropped / null */
#define SENS_LIST_KEY "sens_list"

typedef struct {
    const char *id;
    uint16_t mfr;
    int needs_key;
    const char *default_alias;
    const char *const *fields;
    uint8_t nfields;
    int (*match)(const uint8_t *adv, size_t len);   /* NULL: the mfr id alone decides */
    int (*decode)(const uint8_t *adv, size_t len, const uint8_t key[16], cali_sensor_reading_t *out);
} sens_type_t;

/* Govee 0xEC88 (govee_reader.py parse_govee): adv = company id + data; data[1..] carries the values. */
static int decode_govee(const uint8_t *adv, size_t len, const uint8_t key[16], cali_sensor_reading_t *out) {
    const uint8_t *d = adv + 2;
    (void)key;
    if (len < 2 + 5) return -1;
    size_t n = len - 2;
    if (n >= 7) {   /* H5074: int16 temp / 100, uint16 humidity / 100, battery % */
        out->f[0] = (float)(int16_t)(d[1] | d[2] << 8) / 100.0f;
        out->f[1] = (float)(uint16_t)(d[3] | d[4] << 8) / 100.0f;
        out->f[2] = d[5];
    } else {        /* H5075: 24 bits, sign in bit 23, temp*10000 + humidity*10 */
        uint32_t base = (uint32_t)d[1] << 16 | (uint32_t)d[2] << 8 | d[3];
        uint32_t t = base & 0x7FFFFFu;
        out->f[0] = (float)(t / 1000u) / 10.0f * (base & 0x800000u ? -1.0f : 1.0f);
        out->f[1] = (float)(t % 1000u) / 10.0f;
        out->f[2] = d[4] & 0x7F;
    }
    out->n = 3;
    return 0;
}

static const char *const GOVEE_F[] = {"temp_c", "humidity", "battery"};
static const sens_type_t TYPES[] = {
    {"govee", 0xEC88, 0, "govee", GOVEE_F, 3, NULL, decode_govee},   /* Task 5 adds the Victron rows */
};
#define NTYPES (sizeof TYPES / sizeof TYPES[0])

typedef struct { uint8_t type, addr_type, addr[6], key[16]; char alias[17]; } sens_cfg_t;   /* = the NVS entry */
typedef struct { uint8_t n; sens_cfg_t e[SENS_MAX]; } sens_list_t;                         /* the sens_list blob */

static sens_list_t s_cfg;
static struct {
    cali_sensor_reading_t r;   /* r.n == 0: no reading yet */
    uint64_t next_rec_ms;      /* the next ext record is due at */
} s_val[SENS_MAX];
static struct {
    uint16_t id;               /* 0: free row */
    uint8_t type, addr_type, addr[6];
    int8_t rssi;
    char model[24];
    uint64_t at_ms;
} s_seen[SENS_MAX];
static uint64_t s_last_window_ms;
static int s_kick, s_paused;
static cali_sensors_aes_fn s_aes;   /* the Victron decoders' AES (Task 5) */

/* The boot-local id: never 0, the same for one address all boot. */
static uint16_t sens_id(const uint8_t addr[6]) { return (uint16_t)((cali_crc32(0, addr, 6) & 0xffffu) | 1u); }

/* The name up to its last '_' (Govee "GVH5075_1A2B" -> "GVH5075"), or all of it. */
static void model_of(const char *name, char out[24]) {
    const char *u = strrchr(name, '_');
    size_t n = u ? (size_t)(u - name) : strlen(name);
    if (n > 23) n = 23;
    memcpy(out, name, n);
    out[n] = 0;
}

/* "AA:BB:CC:DD:EE:FF" -> 6 bytes, as written. 0 ok. */
static int parse_addr(const char *s, uint8_t a[6]) {
    unsigned v[6];
    if (sscanf(s, "%2x:%2x:%2x:%2x:%2x:%2x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6) return -1;
    for (int i = 0; i < 6; i++) a[i] = (uint8_t)v[i];
    return 0;
}

/* The type of a manufacturer-data payload, or -1. */
static int type_of(const uint8_t *adv, size_t len) {
    if (len < 2) return -1;
    uint16_t mfr = (uint16_t)(adv[0] | adv[1] << 8);
    for (size_t i = 0; i < NTYPES; i++)
        if (TYPES[i].mfr == mfr && (!TYPES[i].match || TYPES[i].match(adv, len))) return (int)i;
    return -1;
}

/* The configured index with this id, or -1. */
static int cfg_by_id(uint16_t id) {
    for (int i = 0; i < s_cfg.n; i++)
        if (sens_id(s_cfg.e[i].addr) == id) return i;
    return -1;
}

/* 0 when alias is [a-z0-9_-]{1,16} and no configured sensor but `self` uses it. */
static int alias_ok(const char *alias, int self) {
    size_t n = strlen(alias);
    if (n < 1 || n > 16 || strspn(alias, "abcdefghijklmnopqrstuvwxyz0123456789_-") != n) return -1;
    for (int i = 0; i < s_cfg.n; i++)
        if (i != self && strcmp(s_cfg.e[i].alias, alias) == 0) return -1;
    return 0;
}

/* The list -> NVS, whole. */
static int store(void) { return cali_kv_set(SENS_LIST_KEY, &s_cfg, sizeof s_cfg) == 0 ? 0 : CALI_SENS_E_STORE; }

/* NVS -> the list; missing or damaged = empty. */
static void load(void) {
    size_t n = sizeof s_cfg;
    if (cali_kv_get(SENS_LIST_KEY, &s_cfg, &n) != CALI_KV_OK || n != sizeof s_cfg || s_cfg.n > SENS_MAX)
        memset(&s_cfg, 0, sizeof s_cfg);
    int k = 0;   /* ponytail: a type this build lacks (a rolled-back OTA) is dropped, and lost on the next edit */
    for (int i = 0; i < s_cfg.n; i++) {
        if (s_cfg.e[i].type >= NTYPES) continue;
        s_cfg.e[k] = s_cfg.e[i];
        s_cfg.e[k++].alias[16] = 0;   /* NUL-terminated, whatever NVS held */
    }
    s_cfg.n = (uint8_t)k;
    memset(s_val, 0, sizeof s_val);
}

/* v as "%.2f" without trailing zeros / "." (cali_json_num's rule), for the record text. */
static int fmt_num(char *out, size_t cap, double v) {
    int n = snprintf(out, cap, "%.2f", v);
    if (n < 0 || (size_t)n >= cap) return -1;
    while (out[n - 1] == '0') n--;
    if (out[n - 1] == '.') n--;
    out[n] = 0;
    return n;
}

/* ,"<field>":<num> for every non-NaN field of r into out. */
static void rec_fields(char *out, size_t cap, const sens_type_t *t, const cali_sensor_reading_t *r) {
    size_t o = 0;
    out[0] = 0;
    for (uint8_t i = 0; i < t->nfields && i < r->n && o < cap; i++) {
        char v[24];
        if (r->f[i] != r->f[i] || fmt_num(v, sizeof v, r->f[i]) < 0) continue;
        int k = snprintf(out + o, cap - o, ",\"%s\":%s", t->fields[i], v);
        if (k < 0 || (size_t)k >= cap - o) return;   /* cut: vrec refuses the record */
        o += (size_t)k;
    }
}

/* The fields object (or members with null when no reading) for configured sensor i. */
static void fields_json(cali_json_t *j, int i) {
    const sens_type_t *t = &TYPES[s_cfg.e[i].type];
    for (uint8_t k = 0; k < t->nfields; k++) {
        cali_json_key(j, t->fields[k]);
        if (k < s_val[i].r.n) cali_json_num(j, s_val[i].r.f[k]);
        else cali_json_null(j);
    }
}

void cali_sensors_init(void) {
    load();
    memset(s_seen, 0, sizeof s_seen);
    s_last_window_ms = 0;
    s_kick = s_paused = 0;
}

void cali_sensors_tick(uint64_t now_ms, const cali_transport_t *t, int enabled, int pairing_active) {
    s_paused = pairing_active;
    if (!enabled || pairing_active) return;
    int kick = s_kick;
    s_kick = 0;   /* a kick is for the next tick only: a window that ran recently answers it */
    if (s_last_window_ms && now_ms - s_last_window_ms < (kick ? SENS_KICK_MIN_MS : SENS_PERIOD_MS)) return;
    if (t->scan_window(SENS_WINDOW_MS) == 0) s_last_window_ms = now_ms;   /* busy: the next tick retries */
}

void cali_sensors_on_adv(const cali_tevent_t *e, uint64_t now_ms) {
    uint8_t addr[6];
    int ty = type_of(e->data, e->len);
    if (ty < 0 || parse_addr(e->addr, addr) != 0) return;
    for (int i = 0; i < s_cfg.n; i++) {
        sens_cfg_t *c = &s_cfg.e[i];
        if (c->type != ty || c->addr_type != (uint8_t)e->status || memcmp(c->addr, addr, 6) != 0) continue;
        cali_sensor_reading_t r;
        memset(&r, 0, sizeof r);
        if (TYPES[ty].decode(e->data, e->len, c->key, &r) != 0) return;
        r.at_ms = now_ms;
        s_val[i].r = r;
        if (now_ms >= s_val[i].next_rec_ms) {
            char f[CALI_DIAG_BODY_MAX];
            rec_fields(f, sizeof f, &TYPES[ty], &r);
            (void)cali_diag_rec("ext", "\"dev\":\"%s\",\"type\":\"%s\"%s", c->alias, TYPES[ty].id, f);
            s_val[i].next_rec_ms = now_ms + SENS_REC_MIN_MS;
        }
        return;
    }
    int row = 0;   /* this address's row, else a free one, else the oldest */
    for (int i = 0; i < SENS_MAX; i++) {
        if (s_seen[i].id && s_seen[i].addr_type == (uint8_t)e->status && memcmp(s_seen[i].addr, addr, 6) == 0) {
            row = i;
            break;
        }
        if (s_seen[row].id && (!s_seen[i].id || s_seen[i].at_ms < s_seen[row].at_ms)) row = i;
    }
    s_seen[row].id = sens_id(addr);
    s_seen[row].type = (uint8_t)ty;
    s_seen[row].addr_type = (uint8_t)e->status;
    memcpy(s_seen[row].addr, addr, 6);
    s_seen[row].rssi = e->rssi;
    model_of(e->name, s_seen[row].model);
    s_seen[row].at_ms = now_ms;
}

void cali_sensors_kick(void) { s_kick = 1; }

void cali_sensors_state_json(cali_json_t *j, uint64_t now_ms) {
    for (int i = 0; i < s_cfg.n; i++) {
        cali_json_key(j, s_cfg.e[i].alias);
        cali_json_obj_begin(j);
        fields_json(j, i);
        cali_json_key(j, "age_s");
        if (s_val[i].r.n) cali_json_int(j, (long long)((now_ms - s_val[i].r.at_ms) / 1000u));
        else cali_json_null(j);
        cali_json_obj_end(j);
    }
}

void cali_sensors_api_json(cali_json_t *j, uint64_t now_ms) {
    cali_json_key(j, "configured");
    cali_json_arr_begin(j);
    for (int i = 0; i < s_cfg.n; i++) {
        const sens_cfg_t *c = &s_cfg.e[i];
        int have = s_val[i].r.n != 0;
        uint64_t age_s = have ? (now_ms - s_val[i].r.at_ms) / 1000u : 0;
        cali_json_obj_begin(j);
        cali_json_key(j, "id");
        cali_json_int(j, sens_id(c->addr));
        cali_json_key(j, "type");
        cali_json_str(j, TYPES[c->type].id);
        cali_json_key(j, "alias");
        cali_json_str(j, c->alias);
        cali_json_key(j, "last_seen_s");
        if (have) cali_json_int(j, (long long)age_s);
        else cali_json_null(j);
        cali_json_key(j, "value");
        if (have && age_s < SENS_OFFLINE_S) {
            cali_json_obj_begin(j);
            fields_json(j, i);
            cali_json_obj_end(j);
        } else {
            cali_json_null(j);
        }
        cali_json_key(j, "has_key");
        cali_json_bool(j, TYPES[c->type].needs_key);
        cali_json_obj_end(j);
    }
    cali_json_arr_end(j);
    cali_json_key(j, "seen");
    cali_json_arr_begin(j);
    for (int i = 0; i < SENS_MAX; i++) {
        if (!s_seen[i].id || now_ms - s_seen[i].at_ms >= SENS_OFFLINE_S * 1000u) continue;
        cali_json_obj_begin(j);
        cali_json_key(j, "id");
        cali_json_int(j, s_seen[i].id);
        cali_json_key(j, "type");
        cali_json_str(j, TYPES[s_seen[i].type].id);
        cali_json_key(j, "model");
        cali_json_str(j, s_seen[i].model);
        cali_json_key(j, "rssi");
        cali_json_int(j, s_seen[i].rssi);
        cali_json_obj_end(j);
    }
    cali_json_arr_end(j);
    cali_json_key(j, "scan");
    cali_json_str(j, cali_sensors_scan_state());
}

int cali_sensors_add(uint16_t id, const char *alias, const char *key_hex) {
    int row = -1;
    for (int i = 0; i < SENS_MAX && row < 0; i++)
        if (s_seen[i].id && s_seen[i].id == id) row = i;
    if (row < 0) return CALI_SENS_E_UNKNOWN;
    const sens_type_t *t = &TYPES[s_seen[row].type];
    if (!alias) {   /* the default alias only for the first sensor of its type */
        for (int i = 0; i < s_cfg.n; i++)
            if (s_cfg.e[i].type == s_seen[row].type) return CALI_SENS_E_ALIAS;
        alias = t->default_alias;
    }
    if (alias_ok(alias, -1) != 0) return CALI_SENS_E_ALIAS;
    sens_cfg_t c;
    memset(&c, 0, sizeof c);
    if (t->needs_key) {   /* the key-check byte against the advert: Task 5 */
        if (!key_hex || strlen(key_hex) != 32 || strspn(key_hex, "0123456789abcdefABCDEF") != 32) return CALI_SENS_E_KEY;
        for (int i = 0; i < 16; i++) {
            unsigned b;
            sscanf(key_hex + 2 * i, "%2x", &b);
            c.key[i] = (uint8_t)b;
        }
    }
    if (s_cfg.n == SENS_MAX) return CALI_SENS_E_FULL;
    c.type = s_seen[row].type;
    c.addr_type = s_seen[row].addr_type;
    memcpy(c.addr, s_seen[row].addr, 6);
    strcpy(c.alias, alias);   /* alias_ok: <= 16 */
    s_cfg.e[s_cfg.n++] = c;
    memset(&c, 0, sizeof c);   /* no key copy left on the stack */
    if (store() != 0) {
        memset(&s_cfg.e[--s_cfg.n], 0, sizeof s_cfg.e[0]);
        return CALI_SENS_E_STORE;
    }
    memset(&s_val[s_cfg.n - 1], 0, sizeof s_val[0]);
    s_seen[row].id = 0;   /* configured now: no longer "seen" */
    return 0;
}

int cali_sensors_remove(uint16_t id) {
    int i = cfg_by_id(id);
    if (i < 0) return CALI_SENS_E_UNKNOWN;
    s_cfg.n--;
    memmove(&s_cfg.e[i], &s_cfg.e[i + 1], (size_t)(s_cfg.n - i) * sizeof s_cfg.e[0]);
    memmove(&s_val[i], &s_val[i + 1], (size_t)(s_cfg.n - i) * sizeof s_val[0]);
    memset(&s_cfg.e[s_cfg.n], 0, sizeof s_cfg.e[0]);
    if (store() != 0) {   /* back to what NVS holds (readings restart) */
        load();
        return CALI_SENS_E_STORE;
    }
    return 0;
}

int cali_sensors_rename(uint16_t id, const char *alias) {
    char old[17];
    int i = cfg_by_id(id);
    if (i < 0) return CALI_SENS_E_UNKNOWN;
    if (!alias || alias_ok(alias, i) != 0) return CALI_SENS_E_ALIAS;
    memcpy(old, s_cfg.e[i].alias, sizeof old);
    strcpy(s_cfg.e[i].alias, alias);
    if (store() != 0) {
        memcpy(s_cfg.e[i].alias, old, sizeof old);
        return CALI_SENS_E_STORE;
    }
    return 0;
}

const char *cali_sensors_scan_state(void) { return s_paused ? "paused" : "ok"; }

void cali_sensors_set_aes(cali_sensors_aes_fn fn) { s_aes = fn; }

int cali_sensors_max_body_len(void) {
    int max = 0;
    for (size_t t = 0; t < NTYPES; t++) {
        cali_sensor_reading_t r;
        char f[CALI_DIAG_BODY_MAX * 2];
        memset(&r, 0, sizeof r);
        r.n = TYPES[t].nfields;
        for (uint8_t i = 0; i < r.n; i++) r.f[i] = -9999.99f;
        rec_fields(f, sizeof f, &TYPES[t], &r);
        int n = snprintf(NULL, 0, "\"ev\":\"ext\",\"dev\":\"%s\",\"type\":\"%s\"%s", "abcdefghijklmnop", TYPES[t].id, f);
        if (n > max) max = n;
    }
    return max;
}
