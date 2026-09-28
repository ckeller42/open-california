/* wifi_run.c — the WiFi runtime (#154): feeds cali_net events and ticks into the pure WiFi SM
 * (wifi_sm.c) and runs the actions it returns on cali_net, the captive DNS and the kv store.
 * Contract: include/cali_wifi_run.h. Proven by tests/firmware/test_session_fake.py (a fake cali_net,
 * next to the BLE session) and tests/firmware/test_web_e2e.py (host_main --http, net_host.c's fake
 * WiFi).
 *
 * C99, no malloc, no ESP-IDF/POSIX headers. Everything runs on the one task that runs the tick; the
 * net sink is called only from the platform's poll on that task (cali_net.h), so feeding the SM from
 * it never re-enters a step: an action's outcome arrives as an event on a later poll.
 */
#include "cali_wifi_run.h"

#include <string.h>

#include "cali_captive.h"
#include "cali_platform.h"
#include "cali_runner.h"
#include "pairing_consts.h"

#define KEY_SSID "wifi_ssid"
#define KEY_PSK "wifi_psk"
#define RSSI_EVERY_MS 1000u

static struct {
    const cali_net_t *net;           /* NULL = WiFi off (not initialised) */
    cali_wifi_state_t sm;
    char ssid[NET_SSID_MAX + 1];     /* the station credentials (a copy: callers zero theirs) */
    char psk[NET_PSK_MAX + 1];
    int have_creds;
    uint32_t ip;                     /* while ONLINE */
    int rssi;
    uint64_t rssi_at;                /* last sta_rssi() refresh on a tick; 0 = due at the next tick */
    cali_net_ap_t scan[NET_SCAN_MAX];
    int nscan;
    int scan_in_flight;              /* net->scan() started, its SCAN_DONE not seen yet */
    int scan_wanted;                 /* held back by the BLE-pairing gate: start on a later tick */
    int join_failed;                 /* a STA_START that could not start: FAILED(OTHER) next tick */
    int ap_running;                  /* ap_start() succeeded and no WACT_AP_STOP since */
    uint32_t last_fail;              /* the last WACT_LOG_REASON's reason; NONE after CREDS_SET/GOT_IP */
} W;

static void wipe_creds(void) {
    memset(W.ssid, 0, sizeof W.ssid);
    memset(W.psk, 0, sizeof W.psk);
    W.have_creds = 0;
}

/* 0 ok: ssid 1..NET_SSID_MAX, psk empty (open) or NET_PSK_MIN..NET_PSK_MAX, both copied. */
static int copy_creds(const char *ssid, size_t ssid_len, const char *psk, size_t psk_len) {
    if (ssid_len < 1 || ssid_len > NET_SSID_MAX || (psk_len && (psk_len < NET_PSK_MIN || psk_len > NET_PSK_MAX)))
        return -1;
    wipe_creds();
    memcpy(W.ssid, ssid, ssid_len);
    memcpy(W.psk, psk, psk_len);
    W.have_creds = 1;
    return 0;
}

static int ble_pairing_active(void) {
    uint8_t st = cali_runner_state()->st;
    return st != PAIR_IDLE && st != PAIR_BONDED && st != PAIR_ERROR;
}

static void start_scan(void) {
    if (W.scan_in_flight) return;          /* one at a time: its SCAN_DONE brings the list */
    if (ble_pairing_active()) {            /* R15: never while BLE pairs; the tick retries */
        W.scan_wanted = 1;
        return;
    }
    W.scan_wanted = 0;
    if (W.net->scan() == 0) W.scan_in_flight = 1;
}

static const char *reason_name(uint32_t r) {
    switch (r) {
    case CALI_NET_REASON_NOT_FOUND: return "not_found";
    case CALI_NET_REASON_AUTH: return "auth";
    default: return "other";
    }
}

static void run_action(const cali_wifi_action_t *a) {
    switch (a->act) {
    case WACT_AP_START:
        /* Idempotent: a forget/replace while the hotspot already runs (the SM emits AP_START on every
         * CREDS_FORGET) keeps it — no second ap_start, DNS re-bind or "setup hotspot up". A start
         * that failed is retried by the next AP_START. */
        if (!W.ap_running) {
            if (W.net->ap_start(NET_AP_SSID, NET_AP_PSK) != 0) cali_log("wifi: setup hotspot failed to start");
            else W.ap_running = 1;
            if (cali_captive_dns_start(W.net, NET_AP_ADDR_U32) != 0) cali_log("wifi: captive DNS unavailable");
        }
        start_scan();                      /* the setup page lists networks from the first GET */
        break;
    case WACT_AP_STOP:
        W.ap_running = 0;
        W.net->ap_stop();
        cali_captive_dns_stop();
        cali_log("wifi: setup hotspot closed");
        break;
    case WACT_STA_START:
        if (!W.have_creds) {
            W.join_failed = 1;
            break;
        }
        cali_log("wifi: joining %s", W.ssid);
        if (W.net->sta_start(W.ssid, W.psk) != 0) W.join_failed = 1;
        break;
    case WACT_STA_STOP:
        W.net->sta_stop();
        W.ip = 0;
        W.rssi = 0;
        break;
    case WACT_MDNS:
        W.net->mdns_announce(NET_HOSTNAME, NET_HTTP_PORT);
        break;
    case WACT_CLEAR_CREDS: {
        /* both erases always run; a missing key is not an error (cali_kv_erase) */
        int ok = cali_kv_erase(KEY_SSID) == 0;
        ok = cali_kv_erase(KEY_PSK) == 0 && ok;
        wipe_creds();
        cali_log(ok ? "wifi: credentials cleared" : "wifi: credential erase failed");
        break;
    }
    case WACT_LOG_REASON:
        W.last_fail = a->arg ? a->arg : CALI_NET_REASON_OTHER;   /* the page shows why (R23) */
        cali_log("wifi: failed %s", reason_name(a->arg));
        break;
    default:
        break;
    }
}

/* One SM step; `skip` = an action kind not to run (-1: run all). */
static void feed_skip(uint8_t ev, uint64_t arg, int skip) {
    cali_wifi_action_t acts[WIFI_MAX_ACTIONS];
    int n = cali_wifi_step(&W.sm, ev, arg, acts);
    for (int i = 0; i < n; i++)
        if (acts[i].act != skip) run_action(&acts[i]);
}

static void feed(uint8_t ev, uint64_t arg) { feed_skip(ev, arg, -1); }

static void on_net(const cali_net_event_t *e, void *ctx) {
    (void)ctx;
    switch (e->ev) {
    case CALI_NET_EV_STA_GOT_IP:
        feed(WEV_GOT_IP, 0);
        if (W.sm.st == WIFI_ONLINE) {
            W.last_fail = CALI_NET_REASON_NONE;
            W.ip = e->ip;
            W.rssi = W.net->sta_rssi();
            W.rssi_at = 0;
            cali_log("wifi: online %u.%u.%u.%u", (unsigned)(e->ip >> 24), (unsigned)(e->ip >> 16 & 0xffu),
                     (unsigned)(e->ip >> 8 & 0xffu), (unsigned)(e->ip & 0xffu));
        }
        break;
    case CALI_NET_EV_STA_LOST: {
        if (W.sm.st == WIFI_ONLINE) {    /* logged before the SM's STA_STOP runs */
            W.ip = 0;
            W.rssi = 0;
            cali_log("wifi: lost");
        }
        feed(WEV_LOST, 0);
        break;
    }
    case CALI_NET_EV_STA_FAILED:
        feed(WEV_FAILED, (uint64_t)e->reason);
        break;
    case CALI_NET_EV_AP_STARTED:
        feed(WEV_AP_STARTED, 0);
        cali_log("wifi: setup hotspot up (%s)", NET_AP_SSID);
        break;
    case CALI_NET_EV_SCAN_DONE: {
        int n = e->scan && e->nscan > 0 ? e->nscan : 0;
        if (n > NET_SCAN_MAX) n = NET_SCAN_MAX;
        if (n) memcpy(W.scan, e->scan, (size_t)n * sizeof *W.scan);
        for (int i = 0; i < n; i++) W.scan[i].ssid[NET_SSID_MAX] = '\0';
        W.nscan = n;
        W.scan_in_flight = 0;
        break;
    }
    case CALI_NET_EV_AP_STOPPED:
        /* the hotspot is down (asked for, or the platform lost it): the next WACT_AP_START must
         * really start it again, not trust the idempotence bookkeeping */
        W.ap_running = 0;
        break;
    default:
        break;
    }
}

void cali_wifi_run_init(const cali_net_t *net) {
    memset(&W, 0, sizeof W);
    W.net = net;
    if (net) net->set_sink(on_net, NULL);
}

void cali_wifi_run_boot(void) {
    char ssid[NET_SSID_MAX + 1], psk[NET_PSK_MAX + 1];
    size_t ssid_len = sizeof ssid - 1, psk_len = sizeof psk - 1;
    int ok;
    if (!W.net) return;
    ok = cali_kv_get(KEY_SSID, ssid, &ssid_len) == CALI_KV_OK && cali_kv_get(KEY_PSK, psk, &psk_len) == CALI_KV_OK &&
         copy_creds(ssid, ssid_len, psk, psk_len) == 0;
    memset(psk, 0, sizeof psk);
    feed(ok ? WEV_BOOT_WITH_CREDS : WEV_BOOT_NO_CREDS, 0);
}

void cali_wifi_run_tick(uint64_t now_ms) {
    if (!W.net) return;
    if (W.join_failed) {                   /* a join that never started fails like a real one */
        W.join_failed = 0;
        feed(WEV_FAILED, CALI_NET_REASON_OTHER);
    }
    feed(WEV_TICK, now_ms);
    if (W.scan_wanted && !ble_pairing_active()) start_scan();
    if (W.sm.st == WIFI_ONLINE && (W.rssi_at == 0 || now_ms - W.rssi_at >= RSSI_EVERY_MS)) {
        W.rssi = W.net->sta_rssi();
        W.rssi_at = now_ms;
    }
}

/* WEV_CREDS_SET: a new attempt — the previous attempt's failure reason no longer applies. */
static void feed_creds_set(void) {
    W.last_fail = CALI_NET_REASON_NONE;
    feed(WEV_CREDS_SET, 0);
}

void cali_wifi_run_set_creds(const char *ssid, const char *psk) {
    uint8_t st = W.sm.st;
    if (!W.net || !ssid || !psk || copy_creds(ssid, strlen(ssid), psk, strlen(psk)) != 0) return;
    if (st == WIFI_SETUP_AP || st == WIFI_SETUP_AP_RETRYING) {
        feed_creds_set();
    } else if (st != WIFI_UNPROVISIONED) {
        /* Online, retrying or mid-join: the SM takes new creds only in setup. Go there the way a
         * forget does — station down, hotspot up — but keep the new creds (the caller stored them;
         * no WACT_CLEAR_CREDS), then join them as typed-in-setup creds (a failure clears them and
         * stays in setup, like any setup-flow typo). */
        cali_log("wifi: credentials replaced, reconnecting");
        W.ip = 0;
        W.rssi = 0;
        feed_skip(WEV_CREDS_FORGET, 0, WACT_CLEAR_CREDS);
        feed_creds_set();
    }
}

void cali_wifi_run_forget(void) {
    if (!W.net) return;
    W.ip = 0;
    W.rssi = 0;
    feed(WEV_CREDS_FORGET, 0);
}

const cali_wifi_state_t *cali_wifi_run_state(void) { return &W.sm; }

void cali_wifi_run_scan(void) {
    if (W.net) start_scan();
}

const char *cali_wifi_run_ssid(void) { return W.have_creds ? W.ssid : NULL; }

uint32_t cali_wifi_run_ip(void) { return W.sm.st == WIFI_ONLINE ? W.ip : 0; }

int cali_wifi_run_rssi(void) { return W.sm.st == WIFI_ONLINE ? W.rssi : 0; }

const char *cali_wifi_run_last_fail(void) {
    return W.last_fail == CALI_NET_REASON_NONE ? NULL : reason_name(W.last_fail);
}

int cali_wifi_run_scan_list(const cali_net_ap_t **out) {
    if (out) *out = W.scan;
    return W.nscan;
}
