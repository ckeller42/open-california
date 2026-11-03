/* energy.c — house load + Wh counters since boot (R_FW_ENERGY_COUNTERS). See cali_energy.h. */
#include "cali_energy.h"

#include <math.h>
#include <string.h>

#include "cali_sensors.h"
#include "cali_session.h"
#include "cali_time.h"
#include "codec.h"
#include "codec_chars.h"

static double sgn(uint32_t v, int width) { return v >= (1u << (width - 1)) ? (double)v - (double)(1u << width) : (double)v; }

static int field(const codec_kv_t *kv, int n, const char *name, uint32_t *v) {
    for (int k = 0; k < n; k++)
        if (strcmp(kv[k].name, name) == 0) {
            *v = kv[k].value;
            return 1;
        }
    return 0;
}

void cali_energy_from_frame(const uint8_t *frame, size_t len, const char *amb_sw, cali_energy_in_t *out) {
    static codec_kv_t kv[CODEC_KV_MAX];   /* static: the 8 KB host-task stack */
    int n = codec_decode(codec_func_by_name("energy"), frame, len, kv);
    uint32_t u, i, inst, a;
    memset(out, 0, sizeof *out);
    out->sources_ok = 1;
    if (!field(kv, n, "UTwoBattBemAfs", &u) || !field(kv, n, "ITwoBattBemAfs", &i)) return;
    out->valid = 1;
    out->batt2_v = u / 10.0;
    out->batt2_a = sgn(i, 16) / 10.0;
    /* the installed sources, as semantics.energy(): DC-DC signed A (no /10), shore and solar unsigned /10 */
    if (field(kv, n, "DcdcInstalled", &inst) && inst) {
        if (field(kv, n, "IDcdcAfs", &a)) {
            double d = sgn(a, 16);
            if (amb_sw && (strcmp(amb_sw, "0409") == 0 || strcmp(amb_sw, "0410") == 0)) d += 2;
            out->sources_a += d;
        } else out->sources_ok = 0;
    }
    if (field(kv, n, "LadInstalled", &inst) && inst) {
        if (field(kv, n, "ILandAfs", &a)) out->sources_a += a / 10.0;
        else out->sources_ok = 0;
    }
    if (field(kv, n, "PvInstalled", &inst) && inst) {
        if (field(kv, n, "IPvAfs", &a)) {
            out->sources_a += a / 10.0;
            out->unit_solar_a = a / 10.0;
        } else out->sources_ok = 0;
    }
}

int cali_energy_amb_sw(const uint8_t *frame, size_t len, char out[5]) {
    static codec_kv_t kv[CODEC_KV_MAX];
    int n = codec_decode(codec_func_by_name("general"), frame, len, kv);
    uint32_t v;
    if (!field(kv, n, "AmbSwVersion", &v)) return -1;
    for (int k = 0; k < 4; k++) {
        char c = (char)(v >> (24 - 8 * k) & 0xFF);
        if (c < 0x20 || c > 0x7E) return -1;
        out[k] = c;
    }
    out[4] = 0;
    return 0;
}

int cali_energy_house_load(const cali_energy_in_t *in, int mppt_ok, double mppt_a, double *out_a) {
    if (!in->valid || !in->sources_ok || !mppt_ok) return -1;
    double a = mppt_a + in->sources_a - in->batt2_a;
    if (a < -0.5) return -1;   /* a measurement mismatch, not a load */
    *out_a = a < 0 ? 0 : a;
    return 0;
}

static int32_t take(double *frac, double dwh) {
    *frac += dwh;
    double whole = trunc(*frac + (*frac >= 0 ? 1e-9 : -1e-9));   /* 0.999999... is a whole one */
    *frac -= whole;
    return (int32_t)whole;
}

void cali_energy_step(cali_energy_ctr_t *c, double dt_s, const cali_energy_in_t *in, int mppt_ok, double mppt_a) {
    if (!in->valid) return;
    double k = in->batt2_v * dt_s / 3600.0 * 10.0;   /* A -> deci-Wh over dt */
    double load;
    if (mppt_ok) {
        double solar = (mppt_a > 0 ? mppt_a : 0) + in->unit_solar_a;
        c->solar_dwh = (c->solar_dwh + (uint32_t)take(&c->frac[0], solar * k)) & CALI_ENERGY_MASK;
    }
    if (cali_energy_house_load(in, mppt_ok, mppt_a, &load) == 0)
        c->load_dwh = (c->load_dwh + (uint32_t)take(&c->frac[1], load * k)) & CALI_ENERGY_MASK;
    int32_t b = c->batt_net_dwh + take(&c->frac[2], in->batt2_a * k);
    b = (int32_t)((uint32_t)b & CALI_ENERGY_MASK);
    if (b & 0x800000) b -= 0x1000000;   /* signed 24 bit */
    c->batt_net_dwh = b;
}

static cali_energy_ctr_t s_ctr;
static uint64_t s_next, s_last;
static int s_have_load;
static double s_load_a;

void cali_energy_init(void) {
    memset(&s_ctr, 0, sizeof s_ctr);
    s_next = s_last = 0;
    s_have_load = 0;
}

static int frame_of(const char *fn, int live, const uint8_t **frame, size_t *len) {
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        if (strcmp(CODEC_CHARS[i].function, fn) == 0)
            return live ? cali_session_frame_live(i, frame, len) : cali_session_frame(i, frame, len);
    return 0;
}

void cali_energy_tick(uint64_t now_ms) {
    if (now_ms < s_next) return;
    s_next = now_ms + CALI_ENERGY_TICK_MS;
    uint64_t last = s_last;
    s_last = now_ms;
    s_have_load = 0;
    const uint8_t *frame, *gen;
    size_t len, glen;
    uint64_t upd = cali_session_last_update_ms();
    if (!frame_of("energy", 1, &frame, &len) || !upd || now_ms - upd > CALI_ENERGY_MAX_AGE_MS) return;
    char sw[5];
    const char *amb = frame_of("general", 0, &gen, &glen) && cali_energy_amb_sw(gen, glen, sw) == 0 ? sw : NULL;
    cali_energy_in_t in;
    cali_energy_from_frame(frame, len, amb, &in);
    double mppt_a = 0;
    int mppt_ok = cali_sensors_type_sum("victron_mppt", 0, now_ms, CALI_ENERGY_MAX_AGE_MS, &mppt_a) >= 0;
    s_have_load = cali_energy_house_load(&in, mppt_ok, mppt_a, &s_load_a) == 0;
    if (!last || now_ms - last > CALI_ENERGY_MAX_GAP_MS) return;
    cali_energy_step(&s_ctr, (double)(now_ms - last) / 1000.0, &in, mppt_ok, mppt_a);
}

const cali_energy_ctr_t *cali_energy_counters(void) { return &s_ctr; }

void cali_energy_json(cali_json_t *j) {
    cali_json_obj_begin(j);
    cali_json_key(j, "boot");
    cali_json_int(j, cali_time_boot());
    cali_json_key(j, "solar_wh");
    cali_json_num(j, s_ctr.solar_dwh / 10.0);
    cali_json_key(j, "load_wh");
    cali_json_num(j, s_ctr.load_dwh / 10.0);
    cali_json_key(j, "batt_net_wh");
    cali_json_num(j, s_ctr.batt_net_dwh / 10.0);
    cali_json_key(j, "load_a");
    if (s_have_load) cali_json_num(j, round(s_load_a * 10) / 10);
    else cali_json_null(j);
    cali_json_obj_end(j);
}
