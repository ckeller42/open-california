/* energy_cli.c — host driver for energy.c (R_FW_ENERGY_COUNTERS), one command per stdin line:
 *   in <energy hex> <amb_sw|->            -> "<valid> <sources_ok> <batt2_v> <batt2_a> <sources_a> <unit_solar_a>"
 *   load <energy hex> <amb|-> <ok> <a>    -> the house load in A, or "null"
 *   amb <general hex>                     -> "0410", or "null"
 *   step <energy hex> <amb|-> <ok> <a> <dt_s> <count>   -> integrates count steps into a local counter set
 *   ctr                                   -> "<solar_dwh> <load_dwh> <batt_net_dwh>" of that set
 * and the owner-task tick over fakes of the session, the sensors and the clock:
 *   fake <energy hex|-> <general hex|-> <live 0/1> <last_update_ms|now> <mppt_rc> <mppt_a>
 *     ("now": the session stores a frame on every tick)
 *   tick <now_ms>                         -> nothing; "json" prints device.energy
 * Driven by tests/firmware/test_energy.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_energy.h"
#include "cali_json.h"
#include "cali_sensors.h"
#include "cali_session.h"
#include "cali_time.h"
#include "codec_chars.h"

static uint8_t s_en[64], s_gen[64];
static size_t s_en_len, s_gen_len;
static int s_live, s_mppt_rc, s_follow;
static uint64_t s_upd;
static double s_mppt_a;

static size_t unhex(const char *h, uint8_t *out, size_t cap) {
    size_t n = 0;
    if (!strcmp(h, "-")) return 0;
    while (h[0] && h[1] && n < cap) {
        char b[3] = {h[0], h[1], 0};
        out[n++] = (uint8_t)strtoul(b, NULL, 16);
        h += 2;
    }
    return n;
}

static int idx(const char *fn) {
    for (size_t i = 0; i < CODEC_NCHARS; i++)
        if (!strcmp(CODEC_CHARS[i].function, fn)) return (int)i;
    return -1;
}

/* fakes */
int cali_session_frame(size_t i, const uint8_t **frame, size_t *len) {
    if ((int)i == idx("energy") && s_en_len) return *frame = s_en, *len = s_en_len, 1;
    if ((int)i == idx("general") && s_gen_len) return *frame = s_gen, *len = s_gen_len, 1;
    return 0;
}
int cali_session_frame_live(size_t i, const uint8_t **frame, size_t *len) { return s_live && cali_session_frame(i, frame, len); }
uint64_t cali_session_last_update_ms(void) { return s_upd; }
int cali_sensors_type_sum(const char *type, int field, uint64_t now_ms, uint64_t max_age_ms, double *sum) {
    (void)type, (void)field, (void)now_ms, (void)max_age_ms;
    *sum = s_mppt_rc >= 0 ? s_mppt_a : 0;
    return s_mppt_rc;
}
uint32_t cali_time_boot(void) { return 7; }

int main(void) {
    char line[512], cmd[32], a[160], b[160];
    cali_energy_ctr_t c = {0};
    while (fgets(line, sizeof line, stdin)) {
        uint8_t f[64];
        size_t n;
        int ok;
        double amps, dt;
        unsigned count;
        unsigned long long now, upd;
        if (sscanf(line, "%31s", cmd) != 1) continue;
        if (!strcmp(cmd, "in") && sscanf(line, "%*s %159s %159s", a, b) == 2) {
            cali_energy_in_t in;
            n = unhex(a, f, sizeof f);
            cali_energy_from_frame(f, n, strcmp(b, "-") ? b : NULL, &in);
            printf("%d %d %.3f %.3f %.3f %.3f\n", in.valid, in.sources_ok, in.batt2_v, in.batt2_a, in.sources_a, in.unit_solar_a);
        } else if (!strcmp(cmd, "load") && sscanf(line, "%*s %159s %159s %d %lf", a, b, &ok, &amps) == 4) {
            cali_energy_in_t in;
            double out;
            n = unhex(a, f, sizeof f);
            cali_energy_from_frame(f, n, strcmp(b, "-") ? b : NULL, &in);
            if (cali_energy_house_load(&in, ok, amps, &out) == 0) printf("%.3f\n", out);
            else puts("null");
        } else if (!strcmp(cmd, "amb") && sscanf(line, "%*s %159s", a) == 1) {
            char sw[5];
            n = unhex(a, f, sizeof f);
            puts(cali_energy_amb_sw(f, n, sw) == 0 ? sw : "null");
        } else if (!strcmp(cmd, "step") && sscanf(line, "%*s %159s %159s %d %lf %lf %u", a, b, &ok, &amps, &dt, &count) == 6) {
            cali_energy_in_t in;
            n = unhex(a, f, sizeof f);
            cali_energy_from_frame(f, n, strcmp(b, "-") ? b : NULL, &in);
            for (unsigned k = 0; k < count; k++) cali_energy_step(&c, dt, &in, ok, amps);
        } else if (!strcmp(cmd, "ctr")) {
            printf("%lu %lu %ld\n", (unsigned long)c.solar_dwh, (unsigned long)c.load_dwh, (long)c.batt_net_dwh);
        } else if (!strcmp(cmd, "fake") &&
                   sscanf(line, "%*s %159s %159s %d %31s %d %lf", a, b, &s_live, cmd, &s_mppt_rc, &s_mppt_a) == 6) {
            s_en_len = unhex(a, s_en, sizeof s_en);
            s_gen_len = unhex(b, s_gen, sizeof s_gen);
            s_follow = !strcmp(cmd, "now");
            upd = strtoull(cmd, NULL, 10);
            s_upd = upd;
        } else if (!strcmp(cmd, "tick") && sscanf(line, "%*s %llu", &now) == 1) {
            if (s_follow) s_upd = now;
            cali_energy_tick(now);
        } else if (!strcmp(cmd, "json")) {
            char buf[256];
            cali_json_t j;
            cali_json_begin(&j, buf, sizeof buf);
            cali_json_key(&j, "energy");
            cali_energy_json(&j);
            if (cali_json_end(&j) >= 0) puts(buf);
        } else if (!strcmp(cmd, "reset")) {
            cali_energy_init();
        }
        fflush(stdout);
    }
    return 0;
}
