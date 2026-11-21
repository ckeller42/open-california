/* hub_esp.c — the remote hub's port on the board (cali_hub.h: cali_hub_port_*): the esp-mqtt glue under
 * cali_core's hub_run.c. Board package only; the host build and QEMU link cali_core's hub_port_none.c.
 *
 * Tasks (ruling R16): the esp-mqtt task only moves bytes. Its event handler copies a request frame from
 * oc/<van>/req into a PSRAM buffer and posts it to the client's req queue (cali_hub_port_take hands it to the
 * owner); it publishes the sealed frames the owner left in the out queue (cali_hub_port_post), woken by an
 * MQTT_USER_EVENT "kick". The owner task never calls into esp-mqtt while a client runs (its API lock is held
 * through a TLS connect and every read/write): it only posts the kick, without waiting.
 *
 * Everything one client uses (handle, queues, topics, the frame being reassembled) is in its own client_t,
 * the handler's argument. Stopping hands that client_t to a short-lived "hub_stop" task, which owns it from
 * then on: esp_mqtt_client_destroy can wait a long time (the MQTT task sleeps up to half the reconnect timeout
 * in WAIT_RECONNECT, and in INIT runs DNS + TCP + TLS + websocket + CONNECT before it looks at `run` again),
 * and the owner is the NimBLE host task. The owner blocks only for the handover (one xTaskCreate); the old
 * handler keeps touching only its own client_t, which the helper frees after the destroy. A new client starts
 * only once the helper is done (cali_hub_port_start answers -1 until then; hub_run.c retries).
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
#include "esp_tls_errors.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mqtt_client.h"

#define HUB_KEEPALIVE_S 60
#define HUB_NET_TIMEOUT_MS 3000
#define HUB_RECONNECT_MS 10000
#define REQ_DEPTH 2
#define OUT_DEPTH 4
#define STOP_STACK 4096   /* internal RAM, only while a stop runs */

enum { T_REQ = CALI_HUB_T_RES + 1, T_ONLINE, T_N };

typedef struct {
    uint8_t *buf;
    size_t len;
    int topic;
    int retain;   /* a request: the broker delivered it retained (hub_run.c drops it) */
} frame_t;

typedef struct {
    esp_mqtt_client_handle_t h;
    QueueHandle_t req_q, out_q;
    char topic[T_N][40];
    atomic_int online, kicked, why;   /* why: CALI_HUB_WHY_* of the last failed attempt */
    frame_t rx;   /* MQTT task: the request being reassembled */
} client_t;

static client_t *s_cur;       /* owner task: the running client, NULL = none */
static client_t *s_old;       /* owner task: a stopped client whose hub_stop task could not be created yet */
static atomic_int s_stopping; /* hub_stop tasks that still own an old client */
static atomic_uint s_stop_min; /* the lowest stack high-water mark (bytes) a hub_stop task left, 0 = none yet */

static void hand_over(void);

static void *ps_alloc(size_t n) {
    return heap_caps_malloc_prefer(n, 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

/* frees a client nothing runs on any more (its MQTT task is gone or never started) */
static void client_free(client_t *c) {
    frame_t f;
    heap_caps_free(c->rx.buf);
    if (c->out_q) {
        while (xQueueReceive(c->out_q, &f, 0) == pdTRUE) heap_caps_free(f.buf);
        vQueueDeleteWithCaps(c->out_q);
    }
    if (c->req_q) {
        while (xQueueReceive(c->req_q, &f, 0) == pdTRUE) heap_caps_free(f.buf);
        vQueueDeleteWithCaps(c->req_q);
    }
    heap_caps_free(c);
}

/* ---- the MQTT task (one client_t each) ---- */

static void publish_out(client_t *c) {
    frame_t o;
    atomic_store(&c->kicked, 0);   /* before the drain: a frame posted during it kicks again */
    while (xQueueReceive(c->out_q, &o, 0) == pdTRUE) {
        int id = -1;
        if (atomic_load(&c->online))
            id = esp_mqtt_client_publish(c->h, c->topic[o.topic], (const char *)o.buf, (int)o.len, 0,
                                         o.topic == CALI_HUB_T_STATE);
        if (id < 0) cali_log("hub: publish failed (%u bytes)", (unsigned)o.len);
        else if (o.topic == CALI_HUB_T_STATE) cali_hub_published();   /* last_pub_s: really sent */
        heap_caps_free(o.buf);
    }
}

/* one MQTT_EVENT_DATA: a whole request frame (esp-mqtt splits one longer than its buffer) goes to req_q */
static void rx_data(client_t *c, const esp_mqtt_event_t *e) {
    if (e->current_data_offset == 0) {
        heap_caps_free(c->rx.buf);
        c->rx.buf = NULL;
        if (e->topic_len != (int)strlen(c->topic[T_REQ]) || memcmp(e->topic, c->topic[T_REQ], (size_t)e->topic_len)) return;
        if (e->total_data_len <= 0 || e->total_data_len > CALI_HUB_REQ_MAX) {
            cali_log("hub: request of %d bytes dropped", e->total_data_len);
            return;
        }
        c->rx.len = (size_t)e->total_data_len;
        c->rx.retain = e->retain;
        c->rx.buf = ps_alloc(c->rx.len);
        if (!c->rx.buf) {
            cali_log("hub: request dropped: no memory");
            return;
        }
    }
    if (!c->rx.buf || e->data_len < 0 || (size_t)e->current_data_offset + (size_t)e->data_len > c->rx.len) return;
    memcpy(c->rx.buf + e->current_data_offset, e->data, (size_t)e->data_len);
    if ((size_t)e->current_data_offset + (size_t)e->data_len < c->rx.len) return;
    if (xQueueSend(c->req_q, &c->rx, 0) != pdTRUE) {
        cali_log("hub: request queue full: dropped");
        heap_caps_free(c->rx.buf);
    }
    c->rx.buf = NULL;
}

/* MQTT_EVENT_ERROR -> CALI_HUB_WHY_* (ruling R45): the login refused, the name not found, TLS, else no connection */
static int why_of(const esp_mqtt_error_codes_t *e) {
    if (!e) return CALI_HUB_WHY_UNREACHABLE;
    if (e->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED)
        return e->connect_return_code == MQTT_CONNECTION_REFUSE_BAD_USERNAME ||
                       e->connect_return_code == MQTT_CONNECTION_REFUSE_NOT_AUTHORIZED
                   ? CALI_HUB_WHY_AUTH
                   : CALI_HUB_WHY_UNREACHABLE;
    if (e->esp_tls_last_esp_err == ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME) return CALI_HUB_WHY_DNS;
    if (e->esp_tls_cert_verify_flags || e->esp_tls_last_esp_err == ESP_ERR_MBEDTLS_SSL_HANDSHAKE_FAILED)
        return CALI_HUB_WHY_TLS;
    return CALI_HUB_WHY_UNREACHABLE;
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    client_t *c = arg;
    esp_mqtt_event_handle_t e = data;
    (void)base;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        atomic_store(&c->online, 1);
        atomic_store(&c->why, CALI_HUB_WHY_NONE);
        esp_mqtt_client_publish(c->h, c->topic[T_ONLINE], "1", 1, 1, 1);
        esp_mqtt_client_subscribe(c->h, c->topic[T_REQ], 0);
        cali_log("hub: connected");
        break;
    case MQTT_EVENT_DISCONNECTED:
        if (atomic_exchange(&c->online, 0)) cali_log("hub: disconnected");
        break;
    case MQTT_EVENT_DATA:
        rx_data(c, e);
        break;
    case MQTT_EVENT_ERROR: {
        int w = why_of(e->error_handle);
        if (atomic_exchange(&c->why, w) != w) cali_log("hub: not connected (reason %d)", w);   /* no URL, no login */
        break;
    }
    case MQTT_USER_EVENT:
        publish_out(c);
        break;
    default:
        break;
    }
}

/* ---- the hub_stop task: owns the old client from the handover on ---- */

static void stop_task(void *arg) {
    client_t *c = arg;
    esp_mqtt_client_destroy(c->h);   /* stops its MQTT task first: the handler is done with c after this */
    client_free(c);
    atomic_fetch_sub(&s_stopping, 1);
    unsigned hwm = (unsigned)uxTaskGetStackHighWaterMark(NULL), old = atomic_load(&s_stop_min);   /* IDF: bytes */
    while ((!old || hwm < old) && !atomic_compare_exchange_weak(&s_stop_min, &old, hwm)) {}
    vTaskDelete(NULL);
}

/* Overrides console.c's weak 0: the console STATE line's "hub_stop_stack_min" (bench gate: STOP_STACK margin). */
unsigned cali_hub_stop_stack_min(void) { return atomic_load(&s_stop_min); }

/* ---- the port (owner task) ---- */

static void kick(client_t *c) {
    if (!atomic_exchange(&c->kicked, 1)) {
        esp_mqtt_event_t k = {.event_id = MQTT_USER_EVENT};
        if (esp_mqtt_dispatch_custom_event(c->h, &k) != ESP_OK) atomic_store(&c->kicked, 0);   /* the tick retries */
    }
}

int cali_hub_port_start(const cali_hub_cfg_t *cfg) {
    static const char *const SUF[T_N] = {"state", "res", "req", "online"};
    client_t *c;
    if (s_old) hand_over();
    if (s_cur || s_old || atomic_load(&s_stopping)) return -1;   /* the old client is still being stopped: retried */
    c = ps_alloc(sizeof *c);
    if (!c) return -1;
    memset(c, 0, sizeof *c);
    c->req_q = xQueueCreateWithCaps(REQ_DEPTH, sizeof(frame_t), MALLOC_CAP_SPIRAM);
    c->out_q = xQueueCreateWithCaps(OUT_DEPTH, sizeof(frame_t), MALLOC_CAP_SPIRAM);
    if (!c->req_q || !c->out_q) {
        cali_log("hub: no memory: not started");
        client_free(c);
        return -1;
    }
    for (int i = 0; i < T_N; i++) snprintf(c->topic[i], sizeof c->topic[i], "oc/%s/%s", cfg->van, SUF[i]);
    const esp_mqtt_client_config_t mc = {
        .broker.address.uri = cfg->url,
        .broker.verification.crt_bundle_attach = esp_crt_bundle_attach,
        .credentials.username = cfg->user,
        .credentials.authentication.password = cfg->pass,
        .session.keepalive = HUB_KEEPALIVE_S,
        .session.last_will = {.topic = c->topic[T_ONLINE], .msg = "0", .msg_len = 1, .qos = 1, .retain = 1},
        .network.timeout_ms = HUB_NET_TIMEOUT_MS,
        .network.reconnect_timeout_ms = HUB_RECONNECT_MS,
    };
    c->h = esp_mqtt_client_init(&mc);   /* copies the strings */
    if (!c->h) {
        cali_log("hub: client init failed");
        client_free(c);
        return -1;
    }
    esp_mqtt_client_register_event(c->h, MQTT_EVENT_ANY, on_event, c);
    if (esp_mqtt_client_start(c->h) != ESP_OK) {
        cali_log("hub: client start failed");
        esp_mqtt_client_destroy(c->h);   /* never started: no task to wait for */
        client_free(c);
        return -1;
    }
    s_cur = c;
    cali_log("hub: started");
    return 0;
}

/* hands s_old to a hub_stop task; without RAM for its stack it stays in s_old (still running, its frames
 * unread) and cali_hub_port_start tries again: the owner never waits for a destroy */
static void hand_over(void) {
    atomic_fetch_add(&s_stopping, 1);
    if (xTaskCreate(stop_task, "hub_stop", STOP_STACK, s_old, 4, NULL) == pdPASS) {
        s_old = NULL;
    } else {
        atomic_fetch_sub(&s_stopping, 1);
        cali_log("hub: stop task not created: retried");
    }
}

/* The owner blocks only for the handover (one xTaskCreate); the hub_stop task waits for the destroy. */
void cali_hub_port_stop(void) {
    if (!s_cur) return;
    s_old = s_cur;
    s_cur = NULL;
    hand_over();
}

int cali_hub_port_online(void) { return s_cur && atomic_load(&s_cur->online); }

int cali_hub_port_why(void) { return s_cur ? atomic_load(&s_cur->why) : CALI_HUB_WHY_NONE; }

size_t cali_hub_port_take(uint8_t *buf, size_t max, int *retained) {
    frame_t f;
    size_t n = 0;
    *retained = 0;
    if (!s_cur || xQueueReceive(s_cur->req_q, &f, 0) != pdTRUE) return 0;
    if (f.len <= max) memcpy(buf, f.buf, n = f.len);
    *retained = f.retain;
    heap_caps_free(f.buf);
    return n;
}

int cali_hub_port_post(int topic, const uint8_t *frame, size_t n) {
    frame_t o = {NULL, n, topic, 0};
    if (!s_cur || !(o.buf = ps_alloc(n))) return -1;
    memcpy(o.buf, frame, n);
    if (xQueueSend(s_cur->out_q, &o, 0) != pdTRUE) {
        heap_caps_free(o.buf);
        return -1;
    }
    kick(s_cur);
    return 0;
}

/* a kick that could not be queued (the event loop's queue full) is retried while frames wait */
void cali_hub_port_tick(void) {
    if (s_cur && uxQueueMessagesWaiting(s_cur->out_q)) kick(s_cur);
}
