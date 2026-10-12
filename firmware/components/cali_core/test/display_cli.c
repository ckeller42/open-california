/* display_cli.c — host driver for display_model.c: one stdin line per case ("key=value ..." for the
 * cali_status_t fields plus now= and lang=, the inputs lux= prox= (default -1) als= (sensor present,
 * default 0) touch=, the settings auto= pw= min= max= off= (default CALI_DISPLAY_CFG_DEFAULT); "reset"
 * restarts the model), one stdout line per case:
 *   device=<dot>|<text> ;; wifi=<dot>|<text> ;; camper=<dot>|<text> ;; setup=<0|1> ;; footer=<text> ;;
 *   bright=<pct> ;; state=<on|dim|off> ;; sensor=<ok|absent|error>
 * The model state persists across lines until "reset". "gate=<pressed><awake>" (e.g. gate=10) runs
 * cali_display_touch_gate on one persistent swallow flag and prints "deliver=<0|1>". */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cali_display_model.h"

static const char *dot_name(cali_dot_t d) {
    return d == CALI_DOT_GREEN ? "green" : d == CALI_DOT_AMBER ? "amber" : d == CALI_DOT_RED ? "red" : "grey";
}

static void put_row(const char *name, const cali_row_t *r) { printf("%s=%s|%s ;; ", name, dot_name(r->dot), r->text); }

int main(void) {
    char line[1024];
    cali_display_model_t m;
    uint8_t swallow = 0;
    cali_display_model_init(&m);
    while (fgets(line, sizeof line, stdin)) {
        cali_status_t s;
        cali_display_view_t v;
        uint64_t now = 0;
        int lang = CALI_LANG_DE;
        cali_display_in_t in = {-1, -1, 0, 0};
        cali_display_cfg_t cfg = CALI_DISPLAY_CFG_DEFAULT;
        char *tok;
        line[strcspn(line, "\r\n")] = 0;
        if (!strncmp(line, "fit=", 4)) {  /* the UTF-8-safe cut on its own: fit=<text> */
            char dst[CALI_ROW_TEXT_MAX];
            cali_display_fit_text(dst, line + 4);
            printf("fit=%s\n", dst);
            continue;
        }
        if (!strncmp(line, "gate=", 5)) {
            printf("deliver=%d\n", cali_display_touch_gate(&swallow, line[5] == '1', line[6] == '1'));
            continue;
        }
        if (!strcmp(line, "reset")) {
            cali_display_model_init(&m);
            continue;
        }
        memset(&s, 0, sizeof s);
        for (tok = strtok(line, " "); tok; tok = strtok(NULL, " ")) {
            char *eq = strchr(tok, '=');
            const char *val;
            if (!eq) continue;
            *eq = 0;
            val = eq + 1;
            if (!strcmp(tok, "pair")) s.pair_state = (uint8_t)atoi(val);
            else if (!strcmp(tok, "addr")) snprintf(s.address, sizeof s.address, "%s", val);
            else if (!strcmp(tok, "link")) s.link_up = (uint8_t)atoi(val);
            else if (!strcmp(tok, "age")) s.snap_age_ms = atoll(val);
            else if (!strcmp(tok, "wmode")) s.wifi_mode = (uint8_t)atoi(val);
            else if (!strcmp(tok, "wstate")) s.wifi_state = (uint8_t)atoi(val);
            else if (!strcmp(tok, "ssid")) snprintf(s.ssid, sizeof s.ssid, "%s", val);
            else if (!strcmp(tok, "ip")) s.ip = (uint32_t)strtoul(val, NULL, 10);
            else if (!strcmp(tok, "rssi")) s.rssi = atoi(val);
            else if (!strcmp(tok, "fail")) snprintf(s.last_fail, sizeof s.last_fail, "%s", val);
            else if (!strcmp(tok, "up")) s.uptime_ms = strtoull(val, NULL, 10);
            else if (!strcmp(tok, "now")) now = strtoull(val, NULL, 10);
            else if (!strcmp(tok, "lang")) lang = atoi(val);
            else if (!strcmp(tok, "lux")) in.lux = atoi(val);
            else if (!strcmp(tok, "prox")) in.prox = atoi(val);
            else if (!strcmp(tok, "als")) in.present = (uint8_t)atoi(val);
            else if (!strcmp(tok, "touch")) in.touched = (uint8_t)atoi(val);
            else if (!strcmp(tok, "auto")) cfg.auto_on = (uint8_t)atoi(val);
            else if (!strcmp(tok, "pw")) cfg.prox_wake = (uint8_t)atoi(val);
            else if (!strcmp(tok, "min")) cfg.min_pct = (uint8_t)atoi(val);
            else if (!strcmp(tok, "max")) cfg.max_pct = (uint8_t)atoi(val);
            else if (!strcmp(tok, "off")) cfg.offset = (int8_t)atoi(val);
        }
        cali_display_model(&m, &s, now, lang, &in, &cfg, &v);
        put_row("device", &v.device);
        put_row("wifi", &v.wifi);
        put_row("camper", &v.camper);
        printf("setup=%d ;; footer=%s ;; bright=%u ;; state=%s ;; sensor=%s\n", v.footer_setup, v.footer,
               (unsigned)v.brightness_pct, v.state == CALI_DISP_ON ? "on" : v.state == CALI_DISP_DIM ? "dim" : "off",
               v.sensor == CALI_SENSOR_OK ? "ok" : v.sensor == CALI_SENSOR_ABSENT ? "absent" : "error");
    }
    return 0;
}
