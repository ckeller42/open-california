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
 *  - A late driver event of an OLD join never reaches the runner as the new join's outcome (join
 *    generations, fix round 1): every station event is tagged in the ring with the generation current
 *    when the handler ran; sta_start (a real replacement), sta_stop and the join watchdog bump the
 *    generation (and scrub the ring), so everything raised before is dropped whatever its reason.
 *    The one old event that can still be raised AFTER the bump — the end of the driver attempt we
 *    abandoned with esp_wifi_disconnect() — is counted (J.old_ends, at most one attempt runs in the
 *    driver) and the next DISCONNECTED is swallowed as it (for at most OLD_END_MS); a CONNECTED of the
 *    new attempt proves the old one ended (the driver runs one attempt at a time; CONNECTED comes after
 *    the 4-way handshake, so a wrong-psk attempt never produces one). The decision is the pure
 *    sta_verdict() below.
 *  - sta_start with the SAME ssid+psk as a join still in flight keeps that join (no disconnect, no
 *    new generation): the WiFi SM re-issues STA_START every 1/2/4 ... s while RETRYING, and a
 *    NO_AP_FOUND attempt scans all channels for seconds.
 *  - Every join ends (GOT_IP or FAILED): the 30 s watchdog covers a join the driver never resolves
 *    (e.g. associated but no DHCP lease, or a swallowed old end that never came — then the worst case
 *    is FAILED(other) after 30 s, never a wrong-credentials verdict). The WiFi SM has no CONNECTING
 *    timeout of its own.
 *  - STA_LOST only after GOT_IP was delivered for the current generation (J.ip_up).
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
#define OLD_END_MS 5000u  /* an abandoned attempt ends within ms of our disconnect; after this, stop waiting */
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
    uint32_t gen;              /* station kinds: the join generation when the event was queued */
    cali_net_ev_t ev;          /* K_FINAL */
    cali_net_reason_t fin_reason;
} entry_t;

/* ---- shared with the event task (s_mux) ---- */
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static entry_t s_ring[RING_MAX];
static uint32_t s_seq;
static unsigned s_dropped;     /* entries evicted by a full ring since the last poll */
static uint32_t s_gen;         /* the join generation: written by the owner, read by ring_put */

/* ---- the join: platform-free state + verdict (fix round 1) ---- */

typedef struct {
    uint32_t gen;      /* the current generation (== s_gen) */
    int joining;       /* sta_start issued in this generation; no GOT_IP/FAILED delivered yet */
    int assoc;         /* CONNECTED seen in this generation */
    int ip_up;         /* GOT_IP delivered in this generation, no STA_LOST since */
    int old_ends;      /* 0/1: the end of an abandoned driver attempt still to come (swallow it) */
} join_t;

enum { V_DROP, V_OLD_END, V_ASSOC, V_UP, V_FAIL, V_LOST };
enum { SK_CONNECTED, SK_DISCONNECTED, SK_GOT_IP, SK_LOST_IP };

/* What a station event (kind SK_*, tagged with generation ev_gen) means for the join j. Pure: no
 * driver, no globals, so the two scenarios of the Task 9 review are traced by hand against it
 * (task-9-report.md, fix round 1). */
static int sta_verdict(const join_t *j, int kind, uint32_t ev_gen) {
    if (ev_gen != j->gen) return V_DROP;                       /* raised before a replace/stop */
    switch (kind) {
    case SK_CONNECTED: return j->joining ? V_ASSOC : V_DROP;
    case SK_DISCONNECTED:
        if (j->old_ends > 0) return V_OLD_END;                 /* the abandoned attempt's end */
        if (j->ip_up) return V_LOST;
        return j->joining ? V_FAIL : V_DROP;
    case SK_GOT_IP: return j->joining && j->assoc && !j->ip_up ? V_UP : V_DROP;
    case SK_LOST_IP: return j->ip_up ? V_LOST : V_DROP;
    default: return V_DROP;
    }
}

/* ---- owner task only ---- */
static cali_net_sink_t s_sink;
static void *s_sink_ctx;
static esp_netif_t *s_sta_if, *s_ap_if;
static int s_ready;            /* init succeeded */
static join_t J;
static char s_ssid[NET_SSID_MAX + 1], s_psk[NET_PSK_MAX + 1];   /* the current join's credentials */
static int s_drv_active;       /* esp_wifi_connect issued in this generation, its end not seen yet */
static uint64_t s_join_ms;
static uint64_t s_old_ms;      /* when J.old_ends was set */
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
    s_ring[slot].gen = s_gen;
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

/* Owner task: a new generation; everything tagged before is stale from now on. */
static void bump_gen(void) {
    taskENTER_CRITICAL(&s_mux);
    J.gen = ++s_gen;
    taskEXIT_CRITICAL(&s_mux);
}

/* Owner task: is the driver still running this generation's attempt? Not when its DISCONNECTED is
 * already waiting in the ring (the attempt ended; a disconnect now would produce no event). */
static int drv_active_now(void) {
    int ended = 0;
    if (!s_drv_active) return 0;
    taskENTER_CRITICAL(&s_mux);
    for (int i = 0; i < RING_MAX; i++)
        if (s_ring[i].used && s_ring[i].kind == K_STA_DISCONNECTED && s_ring[i].gen == J.gen) ended = 1;
    taskEXIT_CRITICAL(&s_mux);
    return !ended;
}

/* Owner task: end the current join/association: new generation, ring scrubbed (cancel_sta BEFORE
 * any esp_wifi_disconnect, so the event it causes can never be scrubbed), a running driver attempt
 * disconnected and its end counted in old_ends. Returns 1 if GOT_IP had been delivered. */
static int end_join(void) {
    int active = drv_active_now(), was_up = J.ip_up;
    bump_gen();
    cancel_sta();
    if (active) {
        esp_err_t err = esp_wifi_disconnect();
        if (err == ESP_OK) {
            J.old_ends = 1;
            s_old_ms = cali_uptime_ms();
        } else {
            cali_log("net: esp_wifi_disconnect: %s", esp_err_to_name(err));
        }
    }
    s_drv_active = 0;
    J.joining = J.assoc = J.ip_up = 0;
    return was_up;
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

static void set_sink(cali_net_sink_t sink, void *ctx) {
    s_sink = sink;
    s_sink_ctx = ctx;
}

/* Drop the station (a new generation: nothing pending is ever reported), queue STA_LOST if GOT_IP
 * had been delivered for the stopped generation. */
static int sta_stop(void) {
    if (!s_ready) return -1;
    if (end_join()) put_final(CALI_NET_EV_STA_LOST, CALI_NET_REASON_NONE);
    memset(s_ssid, 0, sizeof s_ssid);
    memset(s_psk, 0, sizeof s_psk);
    return 0;
}

static int sta_start(const char *ssid, const char *psk) {
    wifi_config_t cfg;
    esp_err_t err;
    if (!s_ready || !ssid_ok(ssid) || !psk_ok(psk)) return -1;
    if (J.joining && strcmp(ssid, s_ssid) == 0 && strcmp(psk, s_psk) == 0)
        return 0;                                      /* the same join is in flight: keep it */
    sta_stop(); /* a real replacement: new generation, LOST first if it was up */

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
    strcpy(s_ssid, ssid);
    strcpy(s_psk, psk);
    J.joining = 1;
    s_drv_active = 1;
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
    /* The softAP shares the one radio: go back to its channel between scanned channels, short active
     * dwell per channel, so a phone on the setup hotspot stays associated (bench walk #154: with the
     * defaults under BLE coexistence it dropped for ~12 s and its Connect POST failed). */
    wifi_scan_config_t cfg = {
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time.active.max = NET_SCAN_CHAN_MAX_MS,
        .home_chan_dwell_time = NET_SCAN_HOME_DWELL_MS,
        .coex_background_scan = true,          /* return home under coexistence too */
    };
    esp_err_t err;
    if (!s_ready || s_scan_busy) return -1;   /* one at a time: the one in flight ends in SCAN_DONE */
    s_scan_busy = 1;
    s_scan_ms = cali_uptime_ms();
    err = esp_wifi_scan_start(&cfg, false);    /* async: WIFI_EVENT_SCAN_DONE */
    if (err != ESP_OK) {                       /* e.g. ESP_ERR_WIFI_STATE while a join runs */
        entry_t e;
        cali_log("net: esp_wifi_scan_start: %s", esp_err_to_name(err));
        memset(&e, 0, sizeof e);
        e.kind = K_SCAN_DONE;                  /* this scan's one SCAN_DONE, delivered as failed (-1) */
        e.reason = 1;
        ring_put(&e);
    }
    return 0;
}

static int sta_rssi(void) {
    wifi_ap_record_t ap;
    if (!J.ip_up || esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return 0;
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

/* The scan's list into s_aps: its count, or -1 = no list (a refused/aborted scan; cali_net.h). */
static int scan_records(int ok) {
    uint16_t n = NET_SCAN_MAX;
    int out = 0;
    if (!ok || esp_wifi_scan_get_ap_records(&n, s_rec) != ESP_OK) {
        esp_err_t err = esp_wifi_clear_ap_list();   /* free whatever the driver still holds */
        if (err != ESP_OK && err != ESP_ERR_WIFI_NOT_STARTED) cali_log("net: esp_wifi_clear_ap_list: %s", esp_err_to_name(err));
        return -1;
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

static void handle_sta(const entry_t *e) {
    int kind = e->kind == K_STA_CONNECTED ? SK_CONNECTED : e->kind == K_STA_DISCONNECTED ? SK_DISCONNECTED
             : e->kind == K_GOT_IP ? SK_GOT_IP : SK_LOST_IP;
    switch (sta_verdict(&J, kind, e->gen)) {
    case V_OLD_END: J.old_ends = 0; break;
    case V_ASSOC:
        J.assoc = 1;
        J.old_ends = 0;                            /* a new association: the old attempt is over */
        break;
    case V_UP:
        J.joining = 0;
        J.ip_up = 1;
        deliver(CALI_NET_EV_STA_GOT_IP, CALI_NET_REASON_NONE, e->ip, 0);
        break;
    case V_FAIL:
        J.joining = J.assoc = 0;
        s_drv_active = 0;
        deliver(CALI_NET_EV_STA_FAILED, map_reason(e->reason), 0, 0);
        break;
    case V_LOST:
        if (kind == SK_DISCONNECTED) s_drv_active = 0;   /* LOST_IP: still associated */
        J.ip_up = 0;
        deliver(CALI_NET_EV_STA_LOST, CALI_NET_REASON_NONE, 0, 0);
        break;
    default:
        break;
    }
}

static void handle(const entry_t *e) {
    switch (e->kind) {
    case K_FINAL:
        if (e->ev == CALI_NET_EV_SCAN_DONE) s_scan_busy = 0;
        deliver(e->ev, e->fin_reason, 0, 0);
        break;
    case K_STA_CONNECTED:
    case K_STA_DISCONNECTED:
    case K_GOT_IP:
    case K_LOST_IP:
        handle_sta(e);
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
    /* An old end that never came (esp_wifi_disconnect of an attempt that had just ended emits
     * nothing) must not swallow a genuine outcome forever. */
    if (J.old_ends && now_ms >= s_old_ms && now_ms - s_old_ms >= OLD_END_MS) J.old_ends = 0;
    while (ring_take(limit, &e)) handle(&e);

    /* now_ms >= start: an op started from inside the sink during this drain is stamped after now_ms */
    if (J.joining && now_ms >= s_join_ms && now_ms - s_join_ms >= JOIN_TIMEOUT_MS) {
        end_join();                    /* this generation's late outcome is dropped from now on */
        memset(s_psk, 0, sizeof s_psk);
        s_ssid[0] = '\0';
        deliver(CALI_NET_EV_STA_FAILED, CALI_NET_REASON_OTHER, 0, 0);
    }
    if (s_scan_busy && now_ms >= s_scan_ms && now_ms - s_scan_ms >= SCAN_TIMEOUT_MS) {  /* SCAN_DONE never came */
        s_scan_busy = 0;
        deliver(CALI_NET_EV_SCAN_DONE, CALI_NET_REASON_NONE, 0, -1);   /* no list: keep the last one */
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

/* Undo a failed init (M3): handlers, driver, netifs; the no-driver path and a retry start clean. */
static void init_undo(int wifi_inited) {
    esp_err_t err;
    if ((err = esp_event_handler_unregister(IP_EVENT, IP_EVENT_STA_LOST_IP, on_ip_event)) != ESP_OK &&
        err != ESP_ERR_NOT_FOUND && err != ESP_ERR_INVALID_ARG)
        cali_log("net: unregister: %s", esp_err_to_name(err));
    (void)esp_event_handler_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, on_ip_event);
    (void)esp_event_handler_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event);
    if (wifi_inited && (err = esp_wifi_deinit()) != ESP_OK) cali_log("net: esp_wifi_deinit: %s", esp_err_to_name(err));
    if (s_sta_if) esp_netif_destroy_default_wifi(s_sta_if);
    if (s_ap_if) esp_netif_destroy_default_wifi(s_ap_if);
    s_sta_if = s_ap_if = NULL;
}

#define STEP(call)                                                           \
    do {                                                                     \
        esp_err_t err_ = (call);                                             \
        if (err_ != ESP_OK) {                                                \
            cali_log("net: %s: %s", #call, esp_err_to_name(err_));           \
            goto fail;                                                       \
        }                                                                    \
    } while (0)

int cali_net_esp_init(void) {
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    int wifi_inited = 0;
    if (s_ready) return 0;
    s_sta_if = esp_netif_create_default_wifi_sta();
    s_ap_if = esp_netif_create_default_wifi_ap();
    if (s_sta_if == NULL || s_ap_if == NULL) {
        cali_log("net: esp_netif_create_default_wifi_*: failed");
        goto fail;
    }
    /* DHCP client/AP hostname = the generated NET_HOSTNAME (csrc/net_consts.h), never a hand-typed
     * copy in sdkconfig (review I2). */
    STEP(esp_netif_set_hostname(s_sta_if, NET_HOSTNAME));
    STEP(esp_netif_set_hostname(s_ap_if, NET_HOSTNAME));
    if (ap_netif_setup() != 0) goto fail;
    STEP(esp_wifi_init(&cfg));
    wifi_inited = 1;
    STEP(esp_wifi_set_storage(WIFI_STORAGE_RAM));          /* credentials live in cali_kv */
    STEP(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL));
    STEP(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_ip_event, NULL));
    STEP(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_LOST_IP, on_ip_event, NULL));
    /* Configure the setup AP while it is still down (set_config needs the AP interface enabled),
     * so ap_start()'s mode switch brings it up with our SSID/PSK, never the driver's open default. */
    STEP(esp_wifi_set_mode(WIFI_MODE_APSTA));
    STEP(set_ap_config(NET_AP_SSID, NET_AP_PSK));
    strcpy(s_ap_ssid, NET_AP_SSID);
    strcpy(s_ap_psk, NET_AP_PSK);
    STEP(esp_wifi_set_mode(WIFI_MODE_STA));
    STEP(esp_wifi_start());
    s_ready = 1;
    return 0;
fail:
    init_undo(wifi_inited);
    return -1;
}

#endif /* CONFIG_CALI_WIFI */
