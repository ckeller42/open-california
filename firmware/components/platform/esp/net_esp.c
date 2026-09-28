/* net_esp.c — cali_net.h on ESP-IDF (#154): esp_wifi + esp_netif + lwIP BSD sockets + mdns.
 * Contract: cali_core/include/cali_net.h; ESP-specific entry points: include/cali_net_esp.h. The
 * host twin is host/net_host.c (same queue-then-poll shape).
 *
 * Two tasks touch this file:
 *  - the ESP event task runs on_wifi_event/on_ip_event, which ONLY copy the event into the ring
 *    (ring_put, under s_mux) — no esp_wifi call, no sink, no log;
 *  - the owner task (app_main: the NimBLE host task, or the no-controller "core" task) makes every
 *    other call: the table's functions and cali_net_esp_poll(), which drains the ring, interprets
 *    the raw driver events against the owner-side state below, and is the only caller of the sink.
 * The owner-side state (s_*, outside the ring) is touched by the owner task alone.
 *
 * Contract items this file must hold (Task 7 review):
 *  - scan() always ends in exactly one SCAN_DONE: a scan_start error queues an empty one at once, a
 *    driver SCAN_DONE with status != 0 is delivered empty, and a scan whose SCAN_DONE never comes
 *    (dropped from a full ring, aborted by a join) gets an empty one from the poll's 15 s watchdog;
 *    a driver SCAN_DONE with no scan in flight is dropped (its records freed).
 *  - sta_start / sta_stop cancel every queued station event, and a late driver event of an OLD join
 *    is never attributed to the new one: DISCONNECTED/CONNECTED carrying another SSID are dropped,
 *    the ASSOC_LEAVE our own esp_wifi_disconnect() causes is swallowed, and GOT_IP counts only after
 *    a CONNECTED to the target SSID. Every join ends (GOT_IP or FAILED): the 30 s watchdog covers a
 *    join the driver never resolves (e.g. associated but no DHCP lease — the WiFi SM has no
 *    CONNECTING timeout of its own).
 *  - STA_LOST only after GOT_IP was delivered (s_ip_up).
 */
#include "cali_net_esp.h"

#include <string.h>

#include "sdkconfig.h"
#include "cali_platform.h"

#if !CONFIG_CALI_WIFI

/* No WiFi driver in this image (QEMU): init refuses, the table is never used. */
static void nw_set_sink(cali_net_sink_t sink, void *ctx) { (void)sink; (void)ctx; }
static int nw_2str(const char *a, const char *b) { (void)a; (void)b; return -1; }
static int nw_void(void) { return -1; }
static int nw_zero(void) { return 0; }
static int nw_mdns(const char *h, uint16_t p) { (void)h; (void)p; return -1; }
static int nw_port(uint16_t p) { (void)p; return -2; }
static int nw_fd(int fd) { (void)fd; return -2; }
static int nw_recv(int fd, void *b, size_t n) { (void)fd; (void)b; (void)n; return -2; }
static int nw_send(int fd, const void *b, size_t n) { (void)fd; (void)b; (void)n; return -2; }
static void nw_close(int fd) { (void)fd; }
static int nw_recvfrom(int fd, void *b, size_t n, uint32_t *ip, uint16_t *port) {
    (void)fd; (void)b; (void)n; (void)ip; (void)port;
    return -2;
}
static int nw_sendto(int fd, const void *b, size_t n, uint32_t ip, uint16_t port) {
    (void)fd; (void)b; (void)n; (void)ip; (void)port;
    return -2;
}

const cali_net_t cali_net_esp = {
    .set_sink = nw_set_sink, .sta_start = nw_2str, .sta_stop = nw_void, .ap_start = nw_2str,
    .ap_stop = nw_void, .scan = nw_void, .sta_rssi = nw_zero, .mdns_announce = nw_mdns,
    .tcp_listen = nw_port, .tcp_accept = nw_fd, .tcp_recv = nw_recv, .tcp_send = nw_send,
    .tcp_close = nw_close, .udp_bind = nw_port, .udp_recvfrom = nw_recvfrom, .udp_sendto = nw_sendto,
};

int cali_net_esp_init(void) { return -1; }

void cali_net_esp_poll(uint64_t now_ms) { (void)now_ms; }

#else /* CONFIG_CALI_WIFI */

#include <errno.h>
#include <fcntl.h>
#include <unistd.h>

#include "esp_err.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "dhcpserver/dhcpserver.h"
#include "freertos/FreeRTOS.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "mdns.h"

#define RING_MAX 16
#define JOIN_TIMEOUT_MS 30000u
#define SCAN_TIMEOUT_MS 15000u
#define AP_CHANNEL 1
#define AP_MAX_CLIENTS 4

/* Ring entry kinds: raw driver events (interpreted by the poll) and final events (delivered as-is). */
enum { K_STA_CONNECTED, K_STA_DISCONNECTED, K_GOT_IP, K_LOST_IP, K_AP_START, K_AP_STOP, K_SCAN_DONE, K_FINAL };

typedef struct {
    uint8_t used;
    uint8_t kind;
    uint8_t reason;            /* K_STA_DISCONNECTED: wifi_err_reason_t; K_SCAN_DONE: status */
    uint8_t ssid_len;          /* K_STA_CONNECTED / K_STA_DISCONNECTED */
    uint8_t ssid[32];
    uint32_t ip;               /* K_GOT_IP: host byte order */
    uint32_t seq;
    cali_net_ev_t ev;          /* K_FINAL */
    cali_net_reason_t fin_reason;
} entry_t;

/* ---- shared with the event task (s_mux) ---- */
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static entry_t s_ring[RING_MAX];
static uint32_t s_seq;
static unsigned s_dropped;     /* entries evicted by a full ring since the last poll */

/* ---- owner task only ---- */
static cali_net_sink_t s_sink;
static void *s_sink_ctx;
static esp_netif_t *s_sta_if, *s_ap_if;
static int s_ready;            /* init succeeded */
static char s_target[NET_SSID_MAX + 1];   /* the SSID of the current join ("" = none) */
static int s_joining;          /* sta_start issued; no GOT_IP/FAILED delivered yet */
static int s_assoc;            /* CONNECTED to s_target seen during this join */
static int s_ip_up;            /* GOT_IP delivered, no STA_LOST since */
static int s_sta_active;       /* esp_wifi_connect issued, no DISCONNECTED for it processed yet */
static int s_leave_pending;    /* our esp_wifi_disconnect's ASSOC_LEAVE not seen yet */
static uint64_t s_join_ms;
static int s_ap_on;            /* ap_start succeeded, no ap_stop since */
static int s_ap_start_due;     /* AP_STARTED not delivered yet for this ap_start */
static int s_ap_stop_due;      /* AP_STOPPED not delivered yet for this ap_stop */
static int s_scan_busy;
static uint64_t s_scan_ms;
static int s_overflow_logged;
static int s_mdns_up;
static wifi_ap_record_t s_rec[NET_SCAN_MAX];
static cali_net_ap_t s_aps[NET_SCAN_MAX];

/* ---- the ring ---- */

/* Any task. Full: evict the oldest entry (counted; the poll logs it once). */
static void ring_put(const entry_t *e) {
    taskENTER_CRITICAL(&s_mux);
    int slot = -1, oldest = -1;
    for (int i = 0; i < RING_MAX; i++) {
        if (!s_ring[i].used) {
            slot = i;
            break;
        }
        if (oldest < 0 || (uint32_t)(s_ring[i].seq - s_ring[oldest].seq) >= 0x80000000u) oldest = i;
    }
    if (slot < 0) {
        slot = oldest;
        s_dropped++;
    }
    s_ring[slot] = *e;
    s_ring[slot].used = 1;
    s_ring[slot].seq = s_seq++;
    taskEXIT_CRITICAL(&s_mux);
}

/* Owner task: the oldest entry with seq < limit, removed from the ring into *out. 1 = got one. */
static int ring_take(uint32_t limit, entry_t *out) {
    int got = 0;
    taskENTER_CRITICAL(&s_mux);
    int next = -1;
    for (int i = 0; i < RING_MAX; i++) {
        if (!s_ring[i].used || (uint32_t)(s_ring[i].seq - limit) < 0x80000000u) continue; /* seq >= limit */
        if (next < 0 || (uint32_t)(s_ring[i].seq - s_ring[next].seq) >= 0x80000000u) next = i;
    }
    if (next >= 0) {
        *out = s_ring[next];
        s_ring[next].used = 0;
        got = 1;
    }
    taskEXIT_CRITICAL(&s_mux);
    return got;
}

static int is_sta_kind(const entry_t *e) {
    if (e->kind == K_FINAL)
        return e->ev == CALI_NET_EV_STA_GOT_IP || e->ev == CALI_NET_EV_STA_FAILED || e->ev == CALI_NET_EV_STA_LOST;
    return e->kind == K_STA_CONNECTED || e->kind == K_STA_DISCONNECTED || e->kind == K_GOT_IP || e->kind == K_LOST_IP;
}

/* Owner task: forget every queued station event (raw or final). */
static void cancel_sta(void) {
    taskENTER_CRITICAL(&s_mux);
    for (int i = 0; i < RING_MAX; i++)
        if (s_ring[i].used && is_sta_kind(&s_ring[i])) s_ring[i].used = 0;
    taskEXIT_CRITICAL(&s_mux);
}

static void put_final(cali_net_ev_t ev, cali_net_reason_t reason) {
    entry_t e;
    memset(&e, 0, sizeof e);
    e.kind = K_FINAL;
    e.ev = ev;
    e.fin_reason = reason;
    ring_put(&e);
}

/* ---- the event task side ---- */

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    entry_t e;
    (void)arg;
    (void)base;
    memset(&e, 0, sizeof e);
    switch (id) {
    case WIFI_EVENT_STA_CONNECTED: {
        const wifi_event_sta_connected_t *c = data;
        e.kind = K_STA_CONNECTED;
        e.ssid_len = c->ssid_len <= 32 ? c->ssid_len : 32;
        memcpy(e.ssid, c->ssid, e.ssid_len);
        break;
    }
    case WIFI_EVENT_STA_DISCONNECTED: {
        const wifi_event_sta_disconnected_t *d = data;
        e.kind = K_STA_DISCONNECTED;
        e.reason = d->reason;
        e.ssid_len = d->ssid_len <= 32 ? d->ssid_len : 32;
        memcpy(e.ssid, d->ssid, e.ssid_len);
        break;
    }
    case WIFI_EVENT_AP_START: e.kind = K_AP_START; break;
    case WIFI_EVENT_AP_STOP: e.kind = K_AP_STOP; break;
    case WIFI_EVENT_SCAN_DONE: {
        const wifi_event_sta_scan_done_t *s = data;
        e.kind = K_SCAN_DONE;
        e.reason = s->status != 0;
        break;
    }
    default:
        return;
    }
    ring_put(&e);
}

static void on_ip_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    entry_t e;
    (void)arg;
    (void)base;
    memset(&e, 0, sizeof e);
    if (id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *g = data;
        e.kind = K_GOT_IP;
        e.ip = ntohl(g->ip_info.ip.addr);
    } else if (id == IP_EVENT_STA_LOST_IP) {
        e.kind = K_LOST_IP;
    } else {
        return;
    }
    ring_put(&e);
}

/* ---- WiFi operations (owner task) ---- */

static int ssid_ok(const char *ssid) {
    size_t n = ssid ? strlen(ssid) : 0;
    return n >= 1 && n <= NET_SSID_MAX;
}

static int psk_ok(const char *psk) {
    size_t n = psk ? strlen(psk) : 0;
    return n == 0 || (n >= NET_PSK_MIN && n <= NET_PSK_MAX);
}

static int ssid_is(const entry_t *e, const char *want) {
    return e->ssid_len == strlen(want) && memcmp(e->ssid, want, e->ssid_len) == 0;
}

static void set_sink(cali_net_sink_t sink, void *ctx) {
    s_sink = sink;
    s_sink_ctx = ctx;
}

/* Drop the station: cancel its queued events, disconnect a live/pending association (its
 * ASSOC_LEAVE is then swallowed), queue STA_LOST if it was up. */
static int sta_stop(void) {
    cancel_sta();
    if (s_sta_active) {
        esp_err_t err = esp_wifi_disconnect();
        if (err == ESP_OK) s_leave_pending = 1;
        else cali_log("net: esp_wifi_disconnect: %s", esp_err_to_name(err));
        s_sta_active = 0;
    }
    s_joining = 0;
    s_assoc = 0;
    s_target[0] = '\0';
    if (s_ip_up) {
        s_ip_up = 0;
        put_final(CALI_NET_EV_STA_LOST, CALI_NET_REASON_NONE);
    }
    return 0;
}

static int sta_start(const char *ssid, const char *psk) {
    wifi_config_t cfg;
    esp_err_t err;
    if (!s_ready || !ssid_ok(ssid) || !psk_ok(psk)) return -1;
    sta_stop(); /* a new join replaces the old one: LOST first if it was up */

    memset(&cfg, 0, sizeof cfg);
    memcpy(cfg.sta.ssid, ssid, strlen(ssid));          /* 1..32 bytes, no NUL needed at 32 */
    memcpy(cfg.sta.password, psk, strlen(psk));        /* 0 or 8..63 bytes: NUL-terminated */
    cfg.sta.threshold.authmode = psk[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    cfg.sta.pmf_cfg.capable = true;
    cfg.sta.pmf_cfg.required = false;
    err = esp_wifi_set_config(WIFI_IF_STA, &cfg);
    memset(&cfg, 0, sizeof cfg);                       /* the passphrase copy */
    if (err != ESP_OK) {
        cali_log("net: esp_wifi_set_config(sta): %s", esp_err_to_name(err));
        return -1;
    }
    err = esp_wifi_connect();
    if (err != ESP_OK) {
        cali_log("net: esp_wifi_connect: %s", esp_err_to_name(err));
        return -1;
    }
    strcpy(s_target, ssid);
    s_joining = 1;
    s_assoc = 0;
    s_sta_active = 1;
    s_join_ms = cali_uptime_ms();
    return 0;
}

/* The AP's config: WPA2-PSK (open when psk is empty), channel 1, 4 clients. The AP interface must
 * be enabled (APSTA) for esp_wifi_set_config(WIFI_IF_AP). */
static esp_err_t set_ap_config(const char *ssid, const char *psk) {
    wifi_config_t cfg;
    esp_err_t err;
    memset(&cfg, 0, sizeof cfg);
    memcpy(cfg.ap.ssid, ssid, strlen(ssid));
    cfg.ap.ssid_len = (uint8_t)strlen(ssid);
    memcpy(cfg.ap.password, psk, strlen(psk));
    cfg.ap.channel = AP_CHANNEL;
    cfg.ap.authmode = psk[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    cfg.ap.max_connection = AP_MAX_CLIENTS;
    cfg.ap.pmf_cfg.required = false;
    err = esp_wifi_set_config(WIFI_IF_AP, &cfg);
    memset(&cfg, 0, sizeof cfg);
    return err;
}

static char s_ap_ssid[NET_SSID_MAX + 1], s_ap_psk[NET_PSK_MAX + 1];   /* the configured AP */

static int ap_start(const char *ssid, const char *psk) {
    esp_err_t err;
    if (!s_ready || !ssid_ok(ssid) || !psk_ok(psk)) return -1;
    if (s_ap_on) {                         /* already up: confirm, as the host does */
        put_final(CALI_NET_EV_AP_STARTED, CALI_NET_REASON_NONE);
        return 0;
    }
    err = esp_wifi_set_mode(WIFI_MODE_APSTA);   /* the AP comes up with the config set at init */
    if (err == ESP_OK && (strcmp(ssid, s_ap_ssid) != 0 || strcmp(psk, s_ap_psk) != 0)) {
        err = set_ap_config(ssid, psk);
        if (err == ESP_OK) {
            strcpy(s_ap_ssid, ssid);
            strcpy(s_ap_psk, psk);
        }
    }
    if (err != ESP_OK) {
        cali_log("net: setup AP start: %s", esp_err_to_name(err));
        esp_err_t back = esp_wifi_set_mode(WIFI_MODE_STA);
        if (back != ESP_OK) cali_log("net: esp_wifi_set_mode(sta): %s", esp_err_to_name(back));
        return -1;
    }
    s_ap_on = 1;
    s_ap_start_due = 1;
    s_ap_stop_due = 0;
    return 0;
}

static int ap_stop(void) {
    esp_err_t err;
    if (!s_ap_on) return 0; /* nothing to stop: no event */
    err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err != ESP_OK) {
        cali_log("net: esp_wifi_set_mode(sta): %s", esp_err_to_name(err));
        return -1;
    }
    s_ap_on = 0;
    s_ap_start_due = 0;
    s_ap_stop_due = 1;
    return 0;
}

static int scan(void) {
    esp_err_t err;
    if (!s_ready || s_scan_busy) return -1;   /* one at a time: the one in flight ends in SCAN_DONE */
    s_scan_busy = 1;
    s_scan_ms = cali_uptime_ms();
    err = esp_wifi_scan_start(NULL, false);    /* async: WIFI_EVENT_SCAN_DONE */
    if (err != ESP_OK) {                       /* e.g. ESP_ERR_WIFI_STATE while a join runs */
        entry_t e;
        cali_log("net: esp_wifi_scan_start: %s", esp_err_to_name(err));
        memset(&e, 0, sizeof e);
        e.kind = K_SCAN_DONE;                  /* this scan's one SCAN_DONE, delivered empty */
        e.reason = 1;
        ring_put(&e);
    }
    return 0;
}

static int sta_rssi(void) {
    wifi_ap_record_t ap;
    if (!s_ip_up || esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return 0;
    return ap.rssi;
}

static int mdns_announce(const char *hostname, uint16_t port) {
    esp_err_t err;
    if (!s_ready) return -1;
    if (!s_mdns_up) {
        err = mdns_init();
        if (err != ESP_OK) {
            cali_log("net: mdns_init: %s", esp_err_to_name(err));
            return -1;
        }
        s_mdns_up = 1;
    }
    err = mdns_hostname_set(hostname);
    if (err == ESP_OK) {
        if (mdns_service_exists("_http", "_tcp", NULL)) err = mdns_service_port_set("_http", "_tcp", port);
        else err = mdns_service_add(NULL, "_http", "_tcp", port, NULL, 0);
    }
    if (err != ESP_OK) {
        cali_log("net: mdns announce: %s", esp_err_to_name(err));
        return -1;
    }
    return 0;
}

/* ---- the poll ---- */

static cali_net_reason_t map_reason(uint8_t r) {
    switch (r) {
    case WIFI_REASON_NO_AP_FOUND: return CALI_NET_REASON_NOT_FOUND;
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT: return CALI_NET_REASON_AUTH;
    default: return CALI_NET_REASON_OTHER;
    }
}

static void deliver(cali_net_ev_t ev, cali_net_reason_t reason, uint32_t ip, int nscan) {
    cali_net_event_t e;
    memset(&e, 0, sizeof e);
    e.ev = ev;
    e.reason = reason;
    e.ip = ip;
    if (ev == CALI_NET_EV_SCAN_DONE) {
        e.nscan = nscan;
        e.scan = s_aps;
    }
    if (s_sink) s_sink(&e, s_sink_ctx);
}

/* The join ended without GOT_IP: FAILED(reason) to the runner. */
static void join_failed(cali_net_reason_t reason) {
    s_joining = 0;
    s_assoc = 0;
    deliver(CALI_NET_EV_STA_FAILED, reason, 0, 0);
}

static int scan_records(int ok) {
    uint16_t n = NET_SCAN_MAX;
    int out = 0;
    if (!ok || esp_wifi_scan_get_ap_records(&n, s_rec) != ESP_OK) {
        esp_err_t err = esp_wifi_clear_ap_list();   /* free whatever the driver still holds */
        if (err != ESP_OK && err != ESP_ERR_WIFI_NOT_STARTED) cali_log("net: esp_wifi_clear_ap_list: %s", esp_err_to_name(err));
        return 0;
    }
    for (uint16_t i = 0; i < n && out < NET_SCAN_MAX; i++) {
        size_t len = strnlen((const char *)s_rec[i].ssid, sizeof s_rec[i].ssid);
        if (len == 0 || len > NET_SSID_MAX) continue;  /* hidden network: nothing to pick */
        memcpy(s_aps[out].ssid, s_rec[i].ssid, len);
        s_aps[out].ssid[len] = '\0';
        s_aps[out].rssi = s_rec[i].rssi;
        s_aps[out].secure = s_rec[i].authmode != WIFI_AUTH_OPEN;
        out++;
    }
    return out;
}

static void handle(const entry_t *e) {
    switch (e->kind) {
    case K_FINAL:
        if (e->ev == CALI_NET_EV_SCAN_DONE) s_scan_busy = 0;
        deliver(e->ev, e->fin_reason, 0, 0);
        break;
    case K_STA_CONNECTED:
        if (s_joining && ssid_is(e, s_target)) s_assoc = 1;
        break;
    case K_GOT_IP:
        if (s_joining && s_assoc && !s_ip_up) {
            s_joining = 0;
            s_ip_up = 1;
            s_leave_pending = 0;
            deliver(CALI_NET_EV_STA_GOT_IP, CALI_NET_REASON_NONE, e->ip, 0);
        }
        break;
    case K_STA_DISCONNECTED:
        if (s_leave_pending && e->reason == WIFI_REASON_ASSOC_LEAVE) {
            s_leave_pending = 0;                   /* our own disconnect of the old association */
            break;
        }
        if (e->ssid_len > 0 && !ssid_is(e, s_target)) break;   /* another (old) SSID */
        if (s_ip_up) {
            s_ip_up = 0;
            s_sta_active = 0;
            s_target[0] = '\0';
            deliver(CALI_NET_EV_STA_LOST, CALI_NET_REASON_NONE, 0, 0);
        } else if (s_joining) {
            s_sta_active = 0;
            join_failed(map_reason(e->reason));
        }
        break;
    case K_LOST_IP:
        if (s_ip_up) {                             /* the lease is gone while associated */
            s_ip_up = 0;
            deliver(CALI_NET_EV_STA_LOST, CALI_NET_REASON_NONE, 0, 0);
        }
        break;
    case K_AP_START:
        if (s_ap_on && s_ap_start_due) {
            s_ap_start_due = 0;
            deliver(CALI_NET_EV_AP_STARTED, CALI_NET_REASON_NONE, 0, 0);
        }
        break;
    case K_AP_STOP:
        if (!s_ap_on && s_ap_stop_due) {           /* a stop we asked for; a driver restart is not */
            s_ap_stop_due = 0;
            deliver(CALI_NET_EV_AP_STOPPED, CALI_NET_REASON_NONE, 0, 0);
        }
        break;
    case K_SCAN_DONE:
        if (!s_scan_busy) {                        /* not ours (the watchdog already answered) */
            (void)scan_records(0);
            break;
        }
        s_scan_busy = 0;
        deliver(CALI_NET_EV_SCAN_DONE, CALI_NET_REASON_NONE, 0, scan_records(e->reason == 0));
        break;
    default:
        break;
    }
}

void cali_net_esp_poll(uint64_t now_ms) {
    uint32_t limit;
    unsigned dropped;
    entry_t e;
    if (!s_ready) return;
    taskENTER_CRITICAL(&s_mux);
    limit = s_seq;                     /* events queued during this poll wait for the next one */
    dropped = s_dropped;
    s_dropped = 0;
    taskEXIT_CRITICAL(&s_mux);
    if (dropped && !s_overflow_logged) {
        s_overflow_logged = 1;
        cali_log("net: event queue full, oldest dropped");
    }
    while (ring_take(limit, &e)) handle(&e);

    if (s_joining && now_ms - s_join_ms >= JOIN_TIMEOUT_MS) {   /* no outcome from the driver */
        esp_err_t err = esp_wifi_disconnect();
        if (err == ESP_OK) s_leave_pending = 1;
        else cali_log("net: esp_wifi_disconnect: %s", esp_err_to_name(err));
        s_sta_active = 0;
        cancel_sta();
        join_failed(CALI_NET_REASON_OTHER);
    }
    if (s_scan_busy && now_ms - s_scan_ms >= SCAN_TIMEOUT_MS) {  /* SCAN_DONE never came */
        s_scan_busy = 0;
        deliver(CALI_NET_EV_SCAN_DONE, CALI_NET_REASON_NONE, 0, 0);
    }
}

/* ---- sockets: lwIP BSD, IPv4, non-blocking ---- */

static int would_block(void) {
    return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR;
}

static int set_nonblock(int fd) {
    int fl = fcntl(fd, F_GETFL, 0);
    return fl >= 0 && fcntl(fd, F_SETFL, fl | O_NONBLOCK) == 0 ? 0 : -1;
}

static int open_bound(int type, uint16_t port) {
    struct sockaddr_in a;
    int one = 1;
    int fd = socket(AF_INET, type, type == SOCK_STREAM ? IPPROTO_TCP : IPPROTO_UDP);
    if (fd < 0) return -2;
    if (type == SOCK_STREAM && setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) != 0) {
        close(fd);
        return -2;
    }
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (set_nonblock(fd) != 0 || bind(fd, (struct sockaddr *)&a, sizeof a) != 0 ||
        (type == SOCK_STREAM && listen(fd, 2) != 0)) {
        close(fd);
        return -2;
    }
    return fd;
}

static int tcp_listen(uint16_t port) {
    return open_bound(SOCK_STREAM, port);
}

/* A failed accept is never the listener's end (http_core keeps it): -1 = nothing now. */
static int tcp_accept(int lfd) {
    int one = 1;
    int fd = accept(lfd, NULL, NULL);
    if (fd < 0) return -1;             /* EWOULDBLOCK, ECONNABORTED, ENFILE (socket budget) ... */
    if (set_nonblock(fd) != 0 || setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one) != 0) {
        close(fd);
        return -1;                     /* this connection is lost; the listener is fine */
    }
    return fd;
}

static int tcp_recv(int fd, void *buf, size_t n) {
    ssize_t r;
    if (n == 0) return 0;
    r = recv(fd, buf, n, MSG_DONTWAIT);
    if (r > 0) return (int)r;
    if (r == 0) return -2; /* orderly close */
    return would_block() ? -1 : -2;
}

static int tcp_send(int fd, const void *buf, size_t n) {
    ssize_t r;
    if (n == 0) return 0;
    r = send(fd, buf, n, MSG_DONTWAIT);
    if (r > 0) return (int)r;
    if (r == 0) return -1;             /* nothing taken: try again (never 0 for n > 0) */
    return would_block() ? -1 : -2;
}

static void tcp_close(int fd) {
    if (fd >= 0) close(fd);
}

static int udp_bind(uint16_t port) {
    return open_bound(SOCK_DGRAM, port);
}

static int udp_recvfrom(int fd, void *buf, size_t n, uint32_t *ip, uint16_t *port) {
    struct sockaddr_in a;
    socklen_t alen = sizeof a;
    ssize_t r = recvfrom(fd, buf, n, MSG_DONTWAIT, (struct sockaddr *)&a, &alen);
    if (r < 0) return would_block() ? -1 : -2;
    if (ip) *ip = ntohl(a.sin_addr.s_addr);
    if (port) *port = ntohs(a.sin_port);
    return (int)r;
}

static int udp_sendto(int fd, const void *buf, size_t n, uint32_t ip, uint16_t port) {
    struct sockaddr_in a;
    ssize_t r;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(ip);
    r = sendto(fd, buf, n, MSG_DONTWAIT, (struct sockaddr *)&a, sizeof a);
    if (r >= 0) return (int)r;
    return would_block() ? -1 : -2;
}

const cali_net_t cali_net_esp = {
    .set_sink = set_sink,
    .sta_start = sta_start,
    .sta_stop = sta_stop,
    .ap_start = ap_start,
    .ap_stop = ap_stop,
    .scan = scan,
    .sta_rssi = sta_rssi,
    .mdns_announce = mdns_announce,
    .tcp_listen = tcp_listen,
    .tcp_accept = tcp_accept,
    .tcp_recv = tcp_recv,
    .tcp_send = tcp_send,
    .tcp_close = tcp_close,
    .udp_bind = udp_bind,
    .udp_recvfrom = udp_recvfrom,
    .udp_sendto = udp_sendto,
};

/* ---- init ---- */

#define TRY(call)                                                            \
    do {                                                                     \
        esp_err_t err_ = (call);                                             \
        if (err_ != ESP_OK) {                                                \
            cali_log("net: %s: %s", #call, esp_err_to_name(err_));           \
            return -1;                                                       \
        }                                                                    \
    } while (0)

/* The AP netif: NET_AP_ADDR/24 (the esp_netif default is the same 192.168.4.1; pinned so the
 * captive DNS answer and the page's redirect can never disagree with it), the DHCP server handing
 * out 192.168.4.x and offering the AP itself as the DNS server (the captive DNS). */
static int ap_netif_setup(void) {
    esp_netif_ip_info_t ip;
    esp_netif_dns_info_t dns;
    dhcps_offer_t offer_dns = OFFER_DNS;
    esp_err_t err;
    memset(&ip, 0, sizeof ip);
    ip.ip.addr = htonl(NET_AP_ADDR_U32);
    ip.gw.addr = htonl(NET_AP_ADDR_U32);
    ip.netmask.addr = htonl(0xffffff00u);
    err = esp_netif_dhcps_stop(s_ap_if);
    if (err != ESP_OK && err != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
        cali_log("net: esp_netif_dhcps_stop: %s", esp_err_to_name(err));
        return -1;
    }
    TRY(esp_netif_set_ip_info(s_ap_if, &ip));
    memset(&dns, 0, sizeof dns);
    dns.ip.type = ESP_IPADDR_TYPE_V4;
    dns.ip.u_addr.ip4.addr = htonl(NET_AP_ADDR_U32);
    TRY(esp_netif_set_dns_info(s_ap_if, ESP_NETIF_DNS_MAIN, &dns));
    TRY(esp_netif_dhcps_option(s_ap_if, ESP_NETIF_OP_SET, ESP_NETIF_DOMAIN_NAME_SERVER, &offer_dns,
                               sizeof offer_dns));
    TRY(esp_netif_dhcps_start(s_ap_if));
    return 0;
}

int cali_net_esp_init(void) {
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t err;
    if (s_ready) return 0;
    s_sta_if = esp_netif_create_default_wifi_sta();
    s_ap_if = esp_netif_create_default_wifi_ap();
    if (s_sta_if == NULL || s_ap_if == NULL) {
        cali_log("net: esp_netif_create_default_wifi_*: failed");
        return -1;
    }
    if (ap_netif_setup() != 0) return -1;
    err = esp_wifi_init(&cfg);
    if (err != ESP_OK) {
        cali_log("net: esp_wifi_init: %s", esp_err_to_name(err));
        return -1;
    }
    TRY(esp_wifi_set_storage(WIFI_STORAGE_RAM));           /* credentials live in cali_kv */
    TRY(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL));
    TRY(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_ip_event, NULL));
    TRY(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_LOST_IP, on_ip_event, NULL));
    /* Configure the setup AP while it is still down (set_config needs the AP interface enabled),
     * so ap_start()'s mode switch brings it up with our SSID/PSK, never the driver's open default. */
    TRY(esp_wifi_set_mode(WIFI_MODE_APSTA));
    TRY(set_ap_config(NET_AP_SSID, NET_AP_PSK));
    strcpy(s_ap_ssid, NET_AP_SSID);
    strcpy(s_ap_psk, NET_AP_PSK);
    TRY(esp_wifi_set_mode(WIFI_MODE_STA));
    TRY(esp_wifi_start());
    s_ready = 1;
    return 0;
}

#endif /* CONFIG_CALI_WIFI */
