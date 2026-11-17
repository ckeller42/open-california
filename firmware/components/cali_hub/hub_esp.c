/* hub_esp.c — the remote hub's port on the board (cali_hub.h: cali_hub_port_*): the esp-mqtt glue under
 * cali_core's hub_run.c. Board package only; the host build and QEMU link cali_core's hub_port_none.c.
 *
 * Tasks (ruling R16): the esp-mqtt task only moves bytes. Its event handler copies a request frame from
 * oc/<van>/req into a PSRAM buffer and posts it to s_req_q (cali_hub_port_take hands it to the owner); it
 * publishes the sealed frames the owner left in s_out_q (cali_hub_port_post), woken by an MQTT_USER_EVENT
 * "kick". The owner task never calls into esp-mqtt while the client runs (its API lock is held through a TLS
 * connect and every read/write): it only posts the kick, without waiting. Only start/stop call esp-mqtt.
 *
 * The broker URL is the config's wss:// URL, the one in the hub QR (ruling R20). Last will oc/<van>/online =
 * "0" retained; on connect "1" retained and the subscription. No secret (passwords, key, frames) is logged. */
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cali_hub.h"
#include "cali_platform.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "mqtt_client.h"

#define HUB_KEEPALIVE_S 60
/* Network timeouts: the owner task (the NimBLE host task) runs cali_hub_port_stop on a config change, and
 * esp_mqtt_client_destroy waits for the MQTT task to leave its loop. That task can be inside a TCP/TLS
 * connect or a write (HUB_NET_TIMEOUT_MS) or its read poll (MQTT_POLL_READ_TIMEOUT_MS, 1 s): the owner
 * blocks at most about 3 s + 1 s there, once per config change. */
#define HUB_NET_TIMEOUT_MS 3000
#define HUB_RECONNECT_MS 10000
#define REQ_DEPTH 2
#define OUT_DEPTH 4

enum { T_REQ = CALI_HUB_T_RES + 1, T_ONLINE, T_N };

typedef struct {
    uint8_t *buf;
    size_t len;
    int topic;
} frame_t;

static esp_mqtt_client_handle_t s_client;
static QueueHandle_t s_req_q, s_out_q;
static char s_topic[T_N][40];
static atomic_int s_online, s_kicked;
static frame_t s_rx;                 /* MQTT task: the request being reassembled */

static void *ps_alloc(size_t n) {
    return heap_caps_malloc_prefer(n, 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

/* ---- the MQTT task ---- */

static void publish_out(void) {
    frame_t o;
    atomic_store(&s_kicked, 0);   /* before the drain: a frame posted during it kicks again */
    while (xQueueReceive(s_out_q, &o, 0) == pdTRUE) {
        int id = -1;
        if (atomic_load(&s_online))
            id = esp_mqtt_client_publish(s_client, s_topic[o.topic], (const char *)o.buf, (int)o.len, 0,
                                         o.topic == CALI_HUB_T_STATE);
        if (id < 0) cali_log("hub: publish failed (%u bytes)", (unsigned)o.len);
        else if (o.topic == CALI_HUB_T_STATE) cali_hub_published();   /* last_pub_s: really sent */
        heap_caps_free(o.buf);
    }
}

/* one MQTT_EVENT_DATA: a whole request frame (esp-mqtt splits one longer than its buffer) goes to s_req_q */
static void rx_data(const esp_mqtt_event_t *e) {
    if (e->current_data_offset == 0) {
        heap_caps_free(s_rx.buf);
        s_rx.buf = NULL;
        if (e->topic_len != (int)strlen(s_topic[T_REQ]) || memcmp(e->topic, s_topic[T_REQ], (size_t)e->topic_len)) return;
        if (e->total_data_len <= 0 || e->total_data_len > CALI_HUB_REQ_MAX) {
            cali_log("hub: request of %d bytes dropped", e->total_data_len);
            return;
        }
        s_rx.len = (size_t)e->total_data_len;
        s_rx.buf = ps_alloc(s_rx.len);
        if (!s_rx.buf) {
            cali_log("hub: request dropped: no memory");
            return;
        }
    }
    if (!s_rx.buf || e->data_len < 0 || (size_t)e->current_data_offset + (size_t)e->data_len > s_rx.len) return;
    memcpy(s_rx.buf + e->current_data_offset, e->data, (size_t)e->data_len);
    if ((size_t)e->current_data_offset + (size_t)e->data_len < s_rx.len) return;
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

/* ---- the port (owner task) ---- */

static void kick(void) {
    if (!atomic_exchange(&s_kicked, 1)) {
        esp_mqtt_event_t k = {.event_id = MQTT_USER_EVENT};
        if (esp_mqtt_dispatch_custom_event(s_client, &k) != ESP_OK) atomic_store(&s_kicked, 0);   /* the tick retries */
    }
}

int cali_hub_port_start(const cali_hub_cfg_t *cfg) {
    static const char *const SUF[T_N] = {"state", "res", "req", "online"};
    if (!s_req_q) s_req_q = xQueueCreateWithCaps(REQ_DEPTH, sizeof(frame_t), MALLOC_CAP_SPIRAM);
    if (!s_out_q) s_out_q = xQueueCreateWithCaps(OUT_DEPTH, sizeof(frame_t), MALLOC_CAP_SPIRAM);
    if (!s_req_q || !s_out_q) {
        cali_log("hub: no memory: not started");
        return -1;
    }
    for (int i = 0; i < T_N; i++) snprintf(s_topic[i], sizeof s_topic[i], "oc/%s/%s", cfg->van, SUF[i]);
    const esp_mqtt_client_config_t mc = {
        .broker.address.uri = cfg->url,
        .broker.verification.crt_bundle_attach = esp_crt_bundle_attach,
        .credentials.username = cfg->user,
        .credentials.authentication.password = cfg->pass,
        .session.keepalive = HUB_KEEPALIVE_S,
        .session.last_will = {.topic = s_topic[T_ONLINE], .msg = "0", .msg_len = 1, .qos = 1, .retain = 1},
        .network.timeout_ms = HUB_NET_TIMEOUT_MS,
        .network.reconnect_timeout_ms = HUB_RECONNECT_MS,
    };
    s_client = esp_mqtt_client_init(&mc);   /* copies the strings */
    if (!s_client) {
        cali_log("hub: client init failed");
        return -1;
    }
    esp_mqtt_client_register_event(s_client, MQTT_EVENT_ANY, on_event, NULL);
    if (esp_mqtt_client_start(s_client) != ESP_OK) {
        cali_log("hub: client start failed");
        esp_mqtt_client_destroy(s_client);
        s_client = NULL;
        return -1;
    }
    cali_log("hub: started");
    return 0;
}

void cali_hub_port_stop(void) {
    frame_t f;
    if (!s_client) return;
    esp_mqtt_client_destroy(s_client);   /* stops the task (see HUB_NET_TIMEOUT_MS): nothing below races it */
    s_client = NULL;
    atomic_store(&s_online, 0);
    atomic_store(&s_kicked, 0);
    heap_caps_free(s_rx.buf);
    s_rx.buf = NULL;
    while (xQueueReceive(s_out_q, &f, 0) == pdTRUE) heap_caps_free(f.buf);
    while (xQueueReceive(s_req_q, &f, 0) == pdTRUE) heap_caps_free(f.buf);
}

int cali_hub_port_online(void) { return s_client && atomic_load(&s_online); }

size_t cali_hub_port_take(uint8_t *buf, size_t max) {
    frame_t f;
    size_t n = 0;
    if (!s_client || xQueueReceive(s_req_q, &f, 0) != pdTRUE) return 0;
    if (f.len <= max) memcpy(buf, f.buf, n = f.len);
    heap_caps_free(f.buf);
    return n;
}

int cali_hub_port_post(int topic, const uint8_t *frame, size_t n) {
    frame_t o = {ps_alloc(n), n, topic};
    if (!s_client || !o.buf) {
        heap_caps_free(o.buf);
        return -1;
    }
    memcpy(o.buf, frame, n);
    if (xQueueSend(s_out_q, &o, 0) != pdTRUE) {
        heap_caps_free(o.buf);
        return -1;
    }
    kick();
    return 0;
}

/* a kick that could not be queued (the event loop's queue full) is retried while frames wait */
void cali_hub_port_tick(void) {
    if (s_client && uxQueueMessagesWaiting(s_out_q)) kick();
}
