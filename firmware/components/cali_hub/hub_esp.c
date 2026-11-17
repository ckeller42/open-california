/* hub_esp.c — the remote hub's MQTT client (cali_hub.h, cali_hub_esp.h). Board package only; the host build
 * and QEMU link cali_core's hub_port_none.c instead.
 *
 * Tasks (ruling R16): the esp-mqtt task only moves bytes. Its event handler copies a request frame from
 * oc/<van>/req into a PSRAM buffer and posts it to s_req_q; it publishes the sealed frames the owner task
 * left in s_out_q (woken by an MQTT_USER_EVENT "kick"). The owner task (cali_hub_esp_tick, next to
 * cali_web_poll) owns the data key and the replay ring: it opens a request, runs it through
 * cali_hub_rpc_start/poll (one ask per tick: the owner is the NimBLE host task, which must never block while
 * a command waits for the unit), seals the answer to oc/<van>/res and the /api/state body to oc/<van>/state
 * (retained) after each answer and every HUB_PUB_MS. The owner never calls into esp-mqtt while the client
 * runs (its API lock is held through a TLS connect): it only posts the kick, without waiting.
 *
 * The broker URL is the config's wss:// URL, the one in the hub QR (the app connects to it too). Last will
 * oc/<van>/online = "0" retained; on connect "1" retained and the subscription. Nothing runs while the hub
 * config (KV hub_cfg) is missing. No secret (passwords, key, frames) is ever logged. */
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cali_hub.h"
#include "cali_hub_esp.h"
#include "cali_lora_setup.h" /* cali_wipe */
#include "cali_platform.h"
#include "cali_web.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "mqtt_client.h"

#define HUB_PUB_MS 30000u
#define HUB_KEEPALIVE_S 60
#define REQ_MAX 8192   /* the longest request frame taken (the RPC body cap is 2.5 KB, escaped up to 6x) */
#define RES_MAX 12288  /* the longest answer plaintext (an escaped /api/state of NET_JSON_MAX) */
#define OUT_DEPTH 4

enum { T_STATE, T_RES, T_REQ, T_ONLINE, T_N };

typedef struct {
    uint8_t *buf;
    size_t len;
} frame_t;

typedef struct {
    uint8_t *buf;
    size_t len;
    int topic;
} out_t;

static esp_mqtt_client_handle_t s_client;
static QueueHandle_t s_req_q, s_out_q;
static char s_topic[T_N][40];
static atomic_int s_online, s_kicked, s_pub_due;
static atomic_uint s_last_pub_s;
static bool s_restart, s_key_ok;
static cali_hub_crypto_t s_crypto;   /* owner task only */
static cali_hub_rpc_t s_rpc;         /* owner task only; s_rpc.buf != NULL = a request is pending */
static char *s_pt, *s_res;           /* owner task: the request and answer plaintexts (PSRAM) */
static uint64_t s_next_pub;
static frame_t s_rx;                 /* MQTT task: the request being reassembled */
static size_t s_rx_total;

static void *ps_alloc(size_t n) {
    return heap_caps_malloc_prefer(n, 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

/* ---- the MQTT task ---- */

static void publish_out(void) {
    out_t o;
    atomic_store(&s_kicked, 0);   /* before the drain: a frame posted during it kicks again */
    while (xQueueReceive(s_out_q, &o, 0) == pdTRUE) {
        if (atomic_load(&s_online) &&
            esp_mqtt_client_publish(s_client, s_topic[o.topic], (const char *)o.buf, (int)o.len, 0, o.topic == T_STATE) < 0)
            cali_log("hub: publish failed (%u bytes)", (unsigned)o.len);
        heap_caps_free(o.buf);
    }
}

/* one MQTT_EVENT_DATA: a whole request frame (esp-mqtt splits one longer than its buffer) goes to s_req_q */
static void rx_data(const esp_mqtt_event_t *e) {
    if (e->current_data_offset == 0) {
        heap_caps_free(s_rx.buf);
        s_rx.buf = NULL;
        if (e->topic_len != (int)strlen(s_topic[T_REQ]) || memcmp(e->topic, s_topic[T_REQ], (size_t)e->topic_len)) return;
        if (e->total_data_len <= 0 || e->total_data_len > REQ_MAX) {
            cali_log("hub: request of %d bytes dropped", e->total_data_len);
            return;
        }
        s_rx_total = (size_t)e->total_data_len;
        s_rx.buf = ps_alloc(s_rx_total);
        if (!s_rx.buf) {
            cali_log("hub: request dropped: no memory");
            return;
        }
    }
    if (!s_rx.buf || e->data_len < 0 || (size_t)e->current_data_offset + (size_t)e->data_len > s_rx_total) return;
    memcpy(s_rx.buf + e->current_data_offset, e->data, (size_t)e->data_len);
    if ((size_t)e->current_data_offset + (size_t)e->data_len < s_rx_total) return;
    s_rx.len = s_rx_total;
    if (xQueueSend(s_req_q, &s_rx, 0) != pdTRUE) {
        cali_log("hub: request queue full: dropped");
        heap_caps_free(s_rx.buf);
    }
    s_rx.buf = NULL;
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    esp_mqtt_event_handle_t e = data;
    (void)arg, (void)base;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        atomic_store(&s_online, 1);
        esp_mqtt_client_publish(s_client, s_topic[T_ONLINE], "1", 1, 1, 1);
        esp_mqtt_client_subscribe(s_client, s_topic[T_REQ], 0);
        atomic_store(&s_pub_due, 1);
        cali_log("hub: connected");
        break;
    case MQTT_EVENT_DISCONNECTED:
        if (atomic_exchange(&s_online, 0)) cali_log("hub: disconnected");
        break;
    case MQTT_EVENT_DATA:
        rx_data(e);
        break;
    case MQTT_USER_EVENT:
        publish_out();
        break;
    default:
        break;
    }
}

/* ---- the owner task ---- */

static void out_free_all(void) {
    out_t o;
    frame_t f;
    while (s_out_q && xQueueReceive(s_out_q, &o, 0) == pdTRUE) heap_caps_free(o.buf);
    while (s_req_q && xQueueReceive(s_req_q, &f, 0) == pdTRUE) heap_caps_free(f.buf);
}

static void stop(void) {
    if (!s_client) return;
    esp_mqtt_client_destroy(s_client);   /* stops the task: nothing below races it */
    s_client = NULL;
    atomic_store(&s_online, 0);
    atomic_store(&s_kicked, 0);
    heap_caps_free(s_rx.buf);
    s_rx.buf = NULL;
    out_free_all();
}

static void start(void) {
    cali_hub_cfg_t cfg;
    if (cali_hub_cfg_load(&cfg) != 0) return;   /* no config: nothing runs */
    if (!s_key_ok) {
        if (cali_hub_key_load_or_make(&s_crypto) != 0) {   /* -1: the context is not used */
            cali_log("hub: data key cannot be stored: not started");
            cali_wipe(&cfg, sizeof cfg);
            return;
        }
        s_key_ok = true;
    }
    static const char *const SUF[T_N] = {"state", "res", "req", "online"};
    for (int i = 0; i < T_N; i++) snprintf(s_topic[i], sizeof s_topic[i], "oc/%s/%s", cfg.van, SUF[i]);
    const esp_mqtt_client_config_t mc = {
        .broker.address.uri = cfg.url,
        .broker.verification.crt_bundle_attach = esp_crt_bundle_attach,
        .credentials.username = cfg.user,
        .credentials.authentication.password = cfg.pass,
        .session.keepalive = HUB_KEEPALIVE_S,
        .session.last_will = {.topic = s_topic[T_ONLINE], .msg = "0", .msg_len = 1, .qos = 1, .retain = 1},
    };
    s_client = esp_mqtt_client_init(&mc);   /* copies the strings */
    cali_wipe(&cfg, sizeof cfg);
    if (!s_client) {
        cali_log("hub: client init failed");
        return;
    }
    esp_mqtt_client_register_event(s_client, MQTT_EVENT_ANY, on_event, NULL);
    if (esp_mqtt_client_start(s_client) != ESP_OK) {
        cali_log("hub: client start failed");
        esp_mqtt_client_destroy(s_client);
        s_client = NULL;
        return;
    }
    cali_log("hub: started");
}

/* seals `pt` for the MQTT task to publish on `topic`; never waits */
static void post(int topic, const char *pt, size_t n) {
    out_t o = {ps_alloc(n + CALI_HUB_OVERHEAD), 0, topic};
    if (!o.buf) return;
    o.len = cali_hub_seal(&s_crypto, CALI_HUB_DIR_ESP, (const uint8_t *)pt, n, o.buf, n + CALI_HUB_OVERHEAD);
    if (!o.len || xQueueSend(s_out_q, &o, 0) != pdTRUE) {
        cali_log("hub: %s not sent", topic == T_STATE ? "state" : "answer");
        heap_caps_free(o.buf);
        return;
    }
    if (!atomic_exchange(&s_kicked, 1)) {
        esp_mqtt_event_t k = {.event_id = MQTT_USER_EVENT};
        if (esp_mqtt_dispatch_custom_event(s_client, &k) != ESP_OK) atomic_store(&s_kicked, 0);   /* next post retries */
    }
}

/* the /api/state body ("?hub=1": not a viewer) */
static void publish_state(uint64_t now_ms) {
    cali_http_req_t rq = {"GET", "/api/state", "hub=1", "", 0, 0, NULL};
    cali_http_resp_t rs = {200, "text/plain", NULL, 0, NULL, NULL, NULL, NULL};
    s_next_pub = now_ms + HUB_PUB_MS;
    if (cali_web_handle(&rq, &rs, NULL) != 1 || rs.status != 200 || !rs.body) return;
    post(T_STATE, rs.body, rs.body_len);
    uint32_t s = (uint32_t)(now_ms / 1000u);
    atomic_store(&s_last_pub_s, s ? s : 1u);
}

static void answered(uint64_t now_ms) {
    if (atomic_load(&s_online)) {
        post(T_RES, s_res, strlen(s_res));
        publish_state(now_ms);
    }
    cali_wipe(s_res, RES_MAX);
}

void cali_hub_esp_start(void) {
    s_req_q = xQueueCreateWithCaps(2, sizeof(frame_t), MALLOC_CAP_SPIRAM);
    s_out_q = xQueueCreateWithCaps(OUT_DEPTH, sizeof(out_t), MALLOC_CAP_SPIRAM);
    s_pt = ps_alloc(REQ_MAX + 1);
    s_res = ps_alloc(RES_MAX);
    if (!s_req_q || !s_out_q || !s_pt || !s_res) {
        cali_log("hub: no memory: not started");
        return;
    }
    s_restart = true;   /* the first tick starts the client (or finds no config) */
}

void cali_hub_esp_tick(uint64_t now_ms) {
    frame_t f;
    if (!s_res) return;
    if (s_restart) {
        s_restart = false;
        stop();
        start();
    }
    /* the HTTP core may still be sending a body out of the router's static buffer: no router call now */
    if (!s_client || cali_http_busy()) return;
    if (s_rpc.buf) {   /* a pending request: one more ask */
        if (cali_hub_rpc_poll(&s_rpc, s_res, RES_MAX)) answered(now_ms);
    } else if (xQueueReceive(s_req_q, &f, 0) == pdTRUE) {
        int n = cali_hub_open(&s_crypto, f.buf, f.len, (uint8_t *)s_pt, REQ_MAX, 1u << CALI_HUB_DIR_APP, NULL);
        heap_caps_free(f.buf);
        if (n < 0) {
            cali_log("hub: request rejected (%u bytes)", (unsigned)f.len);   /* bad tag, replay, wrong dir */
        } else {
            s_pt[n] = 0;
            if (cali_hub_rpc_start(&s_rpc, s_pt, s_res, RES_MAX)) answered(now_ms);
            cali_wipe(s_pt, (size_t)n);
        }
    }
    if (atomic_load(&s_online) && (atomic_exchange(&s_pub_due, 0) || now_ms >= s_next_pub)) publish_state(now_ms);
}

/* ---- the port (cali_hub.h) ---- */

void cali_hub_port_status(int *online, uint32_t *last_pub_s) {
    *online = atomic_load(&s_online);
    *last_pub_s = atomic_load(&s_last_pub_s);
}

void cali_hub_port_cfg_changed(void) { s_restart = s_res != NULL; }

size_t cali_hub_port_qr(char *out, size_t max) {
    cali_hub_cfg_t cfg;
    size_t n = 0;
    if (s_key_ok && cali_hub_cfg_load(&cfg) == 0) n = cali_hub_qr_text(out, max, &cfg, s_crypto.key);
    cali_wipe(&cfg, sizeof cfg);
    return n;
}

int cali_hub_port_rekey(void) {
    if (!s_key_ok) return -1;
    if (cali_hub_rekey(&s_crypto) != 0) return -1;
    atomic_store(&s_pub_due, 1);   /* the state under the new key at once */
    cali_log("hub: new data key");
    return 0;
}
