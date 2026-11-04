/* status.c — the shared status snapshot (include/cali_status.h). */
#include "cali_status.h"

#include <stdio.h>
#include <string.h>

#include "cali_ap_identity.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "cali_session.h"
#include "cali_snapshot.h"
#include "cali_web.h"
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
    s->link_ready = (uint8_t)(cali_session_ready() != 0);
    s->link_rest = (uint8_t)(cali_session_rest() != 0);
    s->link_redial = (uint8_t)(cali_session_redial() != 0);
    s->rest_next_ms = cali_session_rest_next_ms();
    s->snap_age_ms = last ? (int64_t)(now_ms >= last ? now_ms - last : 0) : -1;
    s->wifi_mode = (uint8_t)cali_wifi_mode(w);
    s->wifi_state = w->st;
    copy(s->ssid, sizeof s->ssid, cali_wifi_run_ssid());
    s->ip = cali_wifi_run_ip();
    s->rssi = cali_wifi_run_rssi();
    s->ap_ssid[0] = 0;
    if (s->wifi_mode == CALI_WIFI_MODE_SETUP) {
        char psk[NET_PSK_MAX + 1];
        if (cali_ap_identity(s->ap_ssid, psk) < 0) s->ap_ssid[0] = 0;
        memset(psk, 0, sizeof psk);
    }
    uint8_t b = 0;
    size_t n = sizeof b;
    s->setup_pending = (uint8_t)(cali_kv_get(CALI_SETUP_QR_KEY, &b, &n) == CALI_KV_OK && n == 1 && b != 0);
    copy(s->last_fail, sizeof s->last_fail, cali_wifi_run_last_fail());
    s->uptime_ms = now_ms;
    s->fw = cali_fw_version();
}
