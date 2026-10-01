/* status.c — the shared status snapshot (include/cali_status.h). */
#include "cali_status.h"

#include <stdio.h>
#include <string.h>

#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_snapshot.h"
#include "cali_wifi_run.h"

static void copy(char *dst, size_t cap, const char *src) {
    if (!src) {
        dst[0] = 0;
        return;
    }
    snprintf(dst, cap, "%s", src);
}

void cali_status_get(cali_status_t *s, const cali_transport_t *t, uint64_t now_ms) {
    const cali_wifi_state_t *w = cali_wifi_run_state();
    uint64_t last = cali_session_last_update_ms();
    s->pair_state = cali_runner_state()->st;
    copy(s->address, sizeof s->address, cali_snapshot_pair_address(t));
    s->link_up = (uint8_t)(cali_session_link_up() != 0);
    s->snap_age_ms = last ? (int64_t)(now_ms >= last ? now_ms - last : 0) : -1;
    s->wifi_mode = (uint8_t)cali_wifi_mode(w);
    s->wifi_state = w->st;
    copy(s->ssid, sizeof s->ssid, cali_wifi_run_ssid());
    s->ip = cali_wifi_run_ip();
    s->rssi = cali_wifi_run_rssi();
    copy(s->last_fail, sizeof s->last_fail, cali_wifi_run_last_fail());
    s->uptime_ms = now_ms;
    s->fw = cali_fw_version();
}
