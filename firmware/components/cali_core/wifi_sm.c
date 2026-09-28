/* wifi_sm.c — line-for-line transcription of tools/wifi_sm_ref.py's step() / _tick_retrying().
 * Keep both in lock-step; the parity test (tests/firmware/test_wifi_sm_parity.py) replays the golden
 * vectors the Python twin generates (tests/vectors/wifi_sm.json, tools/gen_wifi_vectors.py).
 */
#include "cali_wifi_sm.h"

static int emit(cali_wifi_action_t *o, int n, uint8_t act, uint32_t arg) {
    o[n].act = act;
    o[n].arg = arg;
    return n + 1;
}

static void set(cali_wifi_state_t *s, uint8_t st, uint8_t ap_up, uint8_t joined, uint32_t retry,
                uint64_t since, uint64_t next) {
    s->st = st;
    s->ap_up = ap_up;
    s->joined_once = joined;
    s->retry_ms = retry;
    s->since_ms = since;
    s->next_try_ms = next;
}

/* (SETUP_AP_)RETRYING + TICK(now): stamp, retry on schedule, open the AP after NET_SETUP_AFTER_MS. */
static int tick_retrying(cali_wifi_state_t *s, uint64_t now, cali_wifi_action_t *o) {
    int n = 0;
    if (s->since_ms == 0) {
        s->since_ms = now;
        s->next_try_ms = now + s->retry_ms;
        return 0;
    }
    if (now >= s->next_try_ms) {
        n = emit(o, n, WACT_STA_START, 0);
        s->retry_ms = s->retry_ms * 2u < (uint32_t)NET_RETRY_MAX_MS ? s->retry_ms * 2u
                                                                    : (uint32_t)NET_RETRY_MAX_MS;
        s->next_try_ms = now + s->retry_ms;
    }
    if (s->st == WIFI_RETRYING && now - s->since_ms >= (uint64_t)NET_SETUP_AFTER_MS) {
        s->st = WIFI_SETUP_AP_RETRYING;
        s->ap_up = 1;
        n = emit(o, n, WACT_AP_START, 0);
    }
    return n;
}

int cali_wifi_step(cali_wifi_state_t *s, uint8_t ev, uint64_t arg, cali_wifi_action_t o[WIFI_MAX_ACTIONS]) {
    uint8_t st = s->st;
    if (ev == WEV_CREDS_FORGET) {
        set(s, WIFI_SETUP_AP, 1, 0, 0, 0, 0);
        return emit(o, emit(o, emit(o, 0, WACT_STA_STOP, 0), WACT_CLEAR_CREDS, 0), WACT_AP_START, 0);
    }
    if (st == WIFI_UNPROVISIONED) {
        if (ev == WEV_BOOT_NO_CREDS) { set(s, WIFI_SETUP_AP, 1, 0, 0, 0, 0); return emit(o, 0, WACT_AP_START, 0); }
        if (ev == WEV_BOOT_WITH_CREDS) { set(s, WIFI_CONNECTING, s->ap_up, 0, 0, 0, 0); return emit(o, 0, WACT_STA_START, 0); }
    } else if (st == WIFI_SETUP_AP) {
        if (ev == WEV_CREDS_SET) { s->st = WIFI_CONNECTING; return emit(o, 0, WACT_STA_START, 0); }
    } else if (st == WIFI_CONNECTING) {
        if (ev == WEV_GOT_IP) { set(s, WIFI_ONLINE, s->ap_up, 1, 0, 0, 0); return emit(o, 0, WACT_MDNS, 0); }
        if (ev == WEV_FAILED) {
            uint32_t reason = (uint32_t)arg;
            if (!s->joined_once) { /* a typo never bricks setup: back to the AP, bad creds gone */
                int n = emit(o, emit(o, 0, WACT_LOG_REASON, reason), WACT_CLEAR_CREDS, 0);
                if (!s->ap_up) n = emit(o, n, WACT_AP_START, 0); /* booted with creds: no AP yet */
                set(s, WIFI_SETUP_AP, 1, 0, 0, 0, 0);
                return n;
            }
            set(s, WIFI_RETRYING, s->ap_up, 1, NET_RETRY_MIN_MS, 0, 0);
            return emit(o, 0, WACT_LOG_REASON, reason);
        }
    } else if (st == WIFI_ONLINE) {
        if (ev == WEV_LOST) { set(s, WIFI_RETRYING, s->ap_up, s->joined_once, NET_RETRY_MIN_MS, 0, 0); return emit(o, 0, WACT_STA_STOP, 0); }
        if (ev == WEV_TICK) {
            if (s->since_ms == 0) { s->since_ms = arg; return 0; }
            if (s->ap_up && s->since_ms + NET_AP_CLOSE_MS <= arg) { s->ap_up = 0; return emit(o, 0, WACT_AP_STOP, 0); }
        }
    } else if (st == WIFI_RETRYING || st == WIFI_SETUP_AP_RETRYING) {
        if (ev == WEV_GOT_IP) { set(s, WIFI_ONLINE, s->ap_up, 1, 0, 0, 0); return emit(o, 0, WACT_MDNS, 0); }
        if (ev == WEV_CREDS_SET && st == WIFI_SETUP_AP_RETRYING) { set(s, WIFI_CONNECTING, s->ap_up, 0, 0, 0, 0); return emit(o, 0, WACT_STA_START, 0); }
        if (ev == WEV_TICK) return tick_retrying(s, arg, o);
    }
    return 0;
}
