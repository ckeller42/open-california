/* thermostat_run_cli.c — line-protocol driver for thermostat_run.c (the runner), tests/firmware/
 * test_thermostat_run.py ONLY; not part of the ESP build. The real runner, decision (thermostat.c),
 * codec, diag and JSON writer; the session, the control sequencer, the sensors and the ignition are
 * scripted stubs. argv[1] = the kv store directory. stdin, one command per line:
 *   init                     cali_thermo_run_init()
 *   now <ms>                 the clock of tick / frame / sensor
 *   tick                     cali_thermo_run_tick(now)
 *   frame <fn> <hex>         the session's frame of fn (stored and live) at now
 *   ready 0|1                cali_session_ready()
 *   ign <-1|0|1>             cali_ac_ignition()
 *   sensor <alias> <centi|-> cali_sensors_value(alias): that value read at now ("-" = no reading)
 *   rc <code>                the next cali_ctl_submit returns this CALI_CTL_* code at once (default PENDING)
 *   done <code>              the pending command ends with this CALI_CTL_* code
 *   on 0|1 | auto | stop     cali_thermo_set_enabled / cali_thermo_request("AUTO" | "OFF")
 *   ref|backup <alias|->     the configured thermometers
 *   target <centi> | set <key> <value>
 *   state                    "THERMO <cali_thermo_state_json>"
 * Output: "SUBMIT <what> <value>" per submit, "HOLD <0|1>" per cali_session_hold change, "REC <body>"
 * per diagnostics record. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_auto_camper.h"
#include "cali_control.h"
#include "cali_diag.h"
#include "cali_platform.h"
#include "cali_sensors.h"
#include "cali_session.h"
#include "cali_thermostat.h"
#include "codec.h"
#include "codec_chars.h"

static uint64_t s_now;
static uint8_t s_fr[CODEC_NCHARS][CODEC_FRAME_MAX];
static size_t s_len[CODEC_NCHARS];
static uint64_t s_at[CODEC_NCHARS];
static int s_ready, s_ign = -1, s_hold = -1, s_rc = CALI_CTL_PENDING;
static cali_ctl_done_t s_done;
static struct {
    char alias[24];
    int have;
    int32_t v;
    uint64_t at;
} s_sens[4];

int cali_session_frame(size_t i, const uint8_t **frame, size_t *len) {
    if (i >= CODEC_NCHARS || !s_len[i]) return 0;
    *frame = s_fr[i];
    *len = s_len[i];
    return 1;
}
int cali_session_frame_live(size_t i, const uint8_t **frame, size_t *len) { return cali_session_frame(i, frame, len); }
uint64_t cali_session_frame_ms(size_t i) { return i < CODEC_NCHARS ? s_at[i] : 0; }
int cali_session_ready(void) { return s_ready; }
void cali_session_hold(int on) {
    if (on != s_hold) printf("HOLD %d\n", on);
    s_hold = on;
}
int cali_ac_ignition(void) { return s_ign; }
int cali_ctl_submit(const char *fn, const char *what, const char *value, int64_t local_now, cali_ctl_done_t done,
                    const char **reason, const char *src) {
    int rc = s_rc;
    (void)fn, (void)local_now, (void)src;
    *reason = NULL;
    s_rc = CALI_CTL_PENDING;
    printf("SUBMIT %s %s\n", what, value);
    if (rc == CALI_CTL_PENDING) s_done = done;
    return rc;
}
const char *cali_sensors_type(const char *alias) { (void)alias; return NULL; }   /* the API is not driven here */
int cali_sensors_value(const char *alias, int field, int32_t *centi, uint64_t *at_ms) {
    (void)field;
    for (int i = 0; i < 4; i++)
        if (s_sens[i].have && strcmp(s_sens[i].alias, alias) == 0) {
            *centi = s_sens[i].v;
            *at_ms = s_sens[i].at;
            return 0;
        }
    return -1;
}

static int rec(const char *body) {
    printf("REC %s\n", body);
    return 0;
}

static void set_sensor(const char *alias, const char *v) {
    int k = 0;
    while (k < 3 && s_sens[k].alias[0] && strcmp(s_sens[k].alias, alias) != 0) k++;
    snprintf(s_sens[k].alias, sizeof s_sens[k].alias, "%s", alias);
    s_sens[k].have = strcmp(v, "-") != 0;
    s_sens[k].v = (int32_t)atol(v);
    s_sens[k].at = s_now;
}

int main(int argc, char **argv) {
    char line[512];
    if (argc < 2 || cali_platform_init(argv[1]) != 0) return 2;
    setvbuf(stdout, NULL, _IOLBF, 0);
    cali_diag_sink = rec;
    while (fgets(line, sizeof line, stdin)) {
        char cmd[16] = "", a[200] = "", b[200] = "";
        int n = sscanf(line, "%15s %199s %199s", cmd, a, b);
        cali_thermo_run_cfg_t *c = cali_thermo_run_cfg();
        if (n < 1) continue;
        if (!strcmp(cmd, "init")) cali_thermo_run_init();
        else if (!strcmp(cmd, "now")) s_now = strtoull(a, NULL, 10);
        else if (!strcmp(cmd, "tick")) cali_thermo_run_tick(s_now);
        else if (!strcmp(cmd, "frame") && n == 3) {
            for (size_t i = 0; i < CODEC_NCHARS; i++) {
                if (strcmp(CODEC_CHARS[i].function, a) != 0) continue;
                s_len[i] = 0;
                while (s_len[i] < CODEC_FRAME_MAX && b[2 * s_len[i]] && sscanf(b + 2 * s_len[i], "%2hhx", &s_fr[i][s_len[i]]) == 1)
                    s_len[i]++;
                s_at[i] = s_now;
            }
        } else if (!strcmp(cmd, "ready")) s_ready = atoi(a);
        else if (!strcmp(cmd, "ign")) s_ign = atoi(a);
        else if (!strcmp(cmd, "sensor") && n == 3) set_sensor(a, b);
        else if (!strcmp(cmd, "rc")) s_rc = atoi(a);
        else if (!strcmp(cmd, "done") && s_done) {
            cali_ctl_done_t d = s_done;
            s_done = NULL;
            d(atoi(a), NULL);
        } else if (!strcmp(cmd, "on")) (void)cali_thermo_set_enabled(atoi(a));
        else if (!strcmp(cmd, "auto")) (void)cali_thermo_request("AUTO");
        else if (!strcmp(cmd, "stop")) (void)cali_thermo_request("OFF");
        else if (!strcmp(cmd, "ref")) snprintf(c->ref, sizeof c->ref, "%.16s", strcmp(a, "-") ? a : "");
        else if (!strcmp(cmd, "backup")) snprintf(c->backup, sizeof c->backup, "%.16s", strcmp(a, "-") ? a : "");
        else if (!strcmp(cmd, "target")) c->target = (int32_t)atol(a);
        else if (!strcmp(cmd, "set") && n == 3) (void)cali_thermo_cfg_set(a, (int32_t)atol(b));
        else if (!strcmp(cmd, "state")) {
            char buf[512];
            cali_json_t j;
            cali_json_begin(&j, buf, sizeof buf);
            cali_json_key(&j, "thermostat");
            cali_thermo_state_json(&j);
            printf("THERMO %s\n", cali_json_end(&j) < 0 ? "OVERFLOW" : buf);
        } else printf("UNKNOWN %s\n", cmd);
    }
    return 0;
}
