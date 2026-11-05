/* lora_esp.c — the RAK3172 on UART1 and the LoRa task (R_FW_LORA). Board package only (CONFIG_CALI_DISPLAY);
 * the host build and QEMU link cali_core's lora_port_none.c instead.
 *
 * The owner task hands a snapshot over every 10 s (cali_lora_port_post, a 1-deep queue: the newest wins);
 * this task packs and sends from that copy and never reads session state. It publishes its status under
 * a mutex; the owner task copies it (cali_lora_port_status). Nothing touches the pins until the first
 * snapshot with the switch on. */
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "cali_lora.h"
#include "cali_platform.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_app_desc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define RAK_UART UART_NUM_1
#define RAK_TX 43
#define RAK_RX 18
#define RAK_RST 5
#define RAK_BAUD 115200
#define TX_DONE_MS 15000
#define JOIN_WAIT_MS 30000
#define SILENT_RETRY_S 600
#define TASK_STACK 6144

static QueueHandle_t s_snap_q, s_status_q, s_event_q;
static SemaphoreHandle_t s_mu;
static cali_lora_status_t s_st = {.present = -1, .sf = 12};    /* the task's own */
static cali_lora_status_t s_pub = {.present = -1, .sf = 12};   /* the published copy (s_mu) */
static cali_lora_snap_t s_snap;   /* the task's copy */
static cali_lora_budget_t s_bud;
static cali_lora_tracker_t s_trk;
static unsigned s_silent;
static bool s_uart_up, s_in_send;
static volatile bool s_on;   /* the newest snapshot's switch, set by the owner task: a send or a join wait stops at once */

/* ---- status ---- */

static void publish(void) {
    uint32_t now = (uint32_t)(cali_uptime_ms() / 1000);
    s_st.at_unanswered = s_silent;
    s_st.airtime_ms = cali_lora_uplink_confirmed_ms(&s_bud, now);
    s_st.charged_ms = cali_lora_uplink_used_ms(&s_bud, now);
    s_st.downlinks = cali_lora_downlinks_used(&s_bud, now);
    xSemaphoreTake(s_mu, portMAX_DELAY);
    s_pub = s_st;
    xSemaphoreGive(s_mu);
}

void cali_lora_port_post(const cali_lora_snap_t *s) {
    s_on = s->enabled;
    if (s_snap_q) xQueueOverwrite(s_snap_q, s);
}

int cali_lora_port_status(cali_lora_status_t *out) {
    if (!s_mu) return 0;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    *out = s_pub;
    xSemaphoreGive(s_mu);
    return 1;
}

/* ---- the AT driver ---- */

static void log_line(const char *dir, const char *line) {
    char safe[48];   /* the keys and EUIs never reach the log, in either direction */
    cali_log("lora: %s %s", dir, cali_rak_redact(line, safe, sizeof safe) ? safe : line);
}

static bool is_status(cali_rak_kind_t k) {
    return k == CALI_RAK_OK || k == CALI_RAK_ERROR || k == CALI_RAK_BUSY || k == CALI_RAK_NOT_JOINED || k == CALI_RAK_VALUE;
}

static void reader(void *arg) {
    (void)arg;
    char line[160];
    size_t n = 0;
    for (;;) {
        uint8_t c;
        if (uart_read_bytes(RAK_UART, &c, 1, portMAX_DELAY) != 1) continue;
        if (c != '\n' && c != '\r') {
            if (n < sizeof line - 1) line[n++] = (char)c;
            continue;
        }
        if (!n) continue;
        line[n] = 0;
        n = 0;
        log_line("<<", line);
        cali_rak_event_t e = cali_rak_parse(line);
        if (e.kind != CALI_RAK_NONE) xQueueSend(is_status(e.kind) ? s_status_q : s_event_q, &e, 0);
    }
}

static void rak_reset(void) {
    gpio_set_level(RAK_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(100));
    gpio_set_level(RAK_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(1500));   /* the module's boot banner */
    xQueueReset(s_event_q);
    s_silent = 0;
}

static bool uart_up(void) {
    if (s_uart_up) return true;
    const uart_config_t cfg = {.baud_rate = RAK_BAUD, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
                               .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
                               .source_clk = UART_SCLK_DEFAULT};
    if (uart_driver_install(RAK_UART, 1024, 0, 0, NULL, 0) != ESP_OK) return false;
    if (uart_param_config(RAK_UART, &cfg) != ESP_OK ||
        uart_set_pin(RAK_UART, RAK_TX, RAK_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK ||
        xTaskCreate(reader, "cali_lora_rx", 3072, NULL, 5, NULL) != pdPASS) {
        uart_driver_delete(RAK_UART);   /* else the next try fails to install: "no UART" for good */
        return false;
    }
    gpio_set_direction(RAK_RST, GPIO_MODE_OUTPUT);
    s_uart_up = true;
    return true;
}

static cali_rak_event_t at(const char *cmd, uint32_t timeout_ms) {
    cali_rak_event_t e, value = {.kind = CALI_RAK_NONE};
    xQueueReset(s_status_q);
    log_line(">>", cmd);
    uart_write_bytes(RAK_UART, cmd, strlen(cmd));
    uart_write_bytes(RAK_UART, "\r\n", 2);
    TickType_t end = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    while (xQueueReceive(s_status_q, &e, end > xTaskGetTickCount() ? end - xTaskGetTickCount() : 0) == pdTRUE) {
        if (e.kind == CALI_RAK_VALUE) {
            value = e;
            continue;
        }
        s_silent = 0;
        return e.kind == CALI_RAK_OK && value.kind == CALI_RAK_VALUE ? value : e;
    }
    s_silent++;
    cali_log("lora: timeout");
    e.kind = CALI_RAK_NONE;
    return e;
}

/* ---- the link ---- */

static void handle_event(const cali_rak_event_t *e) {
    if (e->kind == CALI_RAK_RX) {
        /* class A: a downlink (an ADR command, a link-check answer) costs one of the day's downlinks */
        (void)cali_lora_downlink_take(&s_bud, (uint32_t)(cali_uptime_ms() / 1000));
    } else if (e->kind == CALI_RAK_JOINED) {
        s_st.joined = true;
    } else if (e->kind == CALI_RAK_TX_DONE && !s_in_send) {
        cali_log("lora: TX_DONE outside a send");
    }
}

static void read_sf(void) {
    cali_rak_event_t e = at("AT+DR=?", 1000);   /* EU868: DR0 = SF12 .. DR5 = SF7 */
    if (e.kind == CALI_RAK_VALUE && e.value[0] >= '0' && e.value[0] <= '5') s_st.sf = 12 - (e.value[0] - '0');
}

static void set_if_differs(const char *name, const char *want) {
    char q[24], cmd[64];
    snprintf(q, sizeof q, "AT+%s=?", name);
    cali_rak_event_t e = at(q, 2000);
    if (e.kind == CALI_RAK_VALUE && strcasecmp(e.value, want) == 0) return;
    snprintf(cmd, sizeof cmd, "AT+%s=%s", name, want);
    if (at(cmd, 2000).kind != CALI_RAK_OK) cali_log("lora: AT+%s rejected", name);
}

static void hexs(char *out, const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) sprintf(out + 2 * i, "%02X", b[i]);
}

/* The stored DevEUI/JoinEUI/AppKey into the module, each only when it differs; before every join
 * attempt, so `lora keys` takes effect without a reboot. */
static void apply_keys(void) {
    uint8_t k[32];
    size_t n = sizeof k;
    if (cali_kv_get(CALI_LORA_KEYS_KEY, k, &n) == CALI_KV_OK && n == sizeof k) {
        char h[33];
        hexs(h, k, 8);
        set_if_differs("DEVEUI", h);
        hexs(h, k + 8, 8);
        set_if_differs("APPEUI", h);
        hexs(h, k + 16, 16);
        set_if_differs("APPKEY", h);
        memset(h, 0, sizeof h);
    } else {
        s_st.note = "no keys: console lora keys";
    }
    memset(k, 0, sizeof k);
}

/* Find and configure the module. false = it does not answer AT. */
static bool module_setup(void) {
    int present = 0;
    for (int i = 0; i < 5 && !present; i++) present = at("AT", 1000).kind == CALI_RAK_OK;
    s_st.present = present;
    s_st.joined = false;
    if (!present) return false;
    cali_rak_event_t nwm = at("AT+NWM=?", 1000);
    if (nwm.kind != CALI_RAK_VALUE || strcmp(nwm.value, "1") != 0) {
        at("AT+NWM=1", 2000);   /* LoRaWAN mode; the module restarts */
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
    at("AT+BAND=4", 1000);   /* EU868 */
    at("AT+NJM=1", 1000);    /* OTAA */
    at("AT+CFM=0", 1000);    /* unconfirmed uplinks */
    at("AT+CLASS=A", 1000);
    at("AT+ADR=1", 1000);
    return true;
}

static void join(void) {
    unsigned attempt = 0;
    while (!s_st.joined && s_on && s_silent < CALI_LORA_SILENT_MAX) {
        s_st.note = "joining";
        publish();
        apply_keys();
        if (s_on && at("AT+JOIN=1:0:8:1", 2000).kind == CALI_RAK_OK) {
            cali_rak_event_t e;
            TickType_t end = xTaskGetTickCount() + pdMS_TO_TICKS(JOIN_WAIT_MS);
            while (xTaskGetTickCount() < end && s_on) {
                if (xQueueReceive(s_event_q, &e, pdMS_TO_TICKS(1000)) != pdTRUE) continue;
                handle_event(&e);
                if (e.kind == CALI_RAK_JOINED || e.kind == CALI_RAK_JOIN_FAILED) break;
            }
        }
        if (s_st.joined) {
            (void)cali_lora_downlink_take(&s_bud, (uint32_t)(cali_uptime_ms() / 1000));   /* the join-accept */
            s_st.note = NULL;
            return;
        }
        if (s_silent >= CALI_LORA_SILENT_MAX) return;   /* the watchdog in the task loop resets */
        s_st.note = "join failed";
        publish();
        /* the backoff: one second at a time, taking each new snapshot (the 1-deep queue is always full,
         * so a peek would return at once and spin) */
        uint32_t wait = cali_lora_join_backoff_s(attempt++);
        for (uint32_t t = 0; t < wait && s_on; t++) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            (void)xQueueReceive(s_snap_q, &s_snap, 0);
        }
    }
}

/* One AT+SEND, only while the switch is on and within both budgets (at the SF the module reports now);
 * true only when +EVT:TX_DONE confirmed it, so a caller keeps what it would otherwise mark as sent. */
static bool send(uint8_t port, const uint8_t *buf, size_t len) {
    char cmd[16 + 2 * 222];
    int n = snprintf(cmd, sizeof cmd, "AT+SEND=%u:", port);
    for (size_t i = 0; i < len && n < (int)sizeof cmd - 3; i++) n += snprintf(cmd + n, sizeof cmd - (size_t)n, "%02X", buf[i]);
    if (!s_on) return false;
    read_sf();
    uint32_t t = (uint32_t)(cali_uptime_ms() / 1000);
    /* class A: every uplink opens receive windows, so the downlink budget gates uplinks (a join does not) */
    if (cali_lora_uplink_room_ms(&s_bud, t) < cali_lora_airtime_ms((unsigned)len + CALI_LORA_OVERHEAD, (unsigned)s_st.sf) ||
        cali_lora_downlinks_used(&s_bud, t) >= CALI_LORA_DOWNLINKS)
        return false;
    cali_rak_event_t e = at(cmd, 3000);
    if (e.kind == CALI_RAK_NOT_JOINED) {
        s_st.joined = false;
        return false;
    }
    cali_rak_kind_t status = e.kind;
    if (!cali_lora_outcome(status, 0).charged) return false;   /* refused: nothing went out */
    bool tx_done = false;
    s_in_send = true;
    TickType_t end = xTaskGetTickCount() + pdMS_TO_TICKS(TX_DONE_MS);
    while (xTaskGetTickCount() < end) {
        if (xQueueReceive(s_event_q, &e, pdMS_TO_TICKS(500)) != pdTRUE) continue;
        handle_event(&e);
        if (e.kind == CALI_RAK_TX_DONE) {
            tx_done = true;
            break;
        }
    }
    while (xQueueReceive(s_event_q, &e, pdMS_TO_TICKS(2500)) == pdTRUE) handle_event(&e);   /* RX windows */
    s_in_send = false;
    cali_lora_outcome_t o = cali_lora_outcome(status, tx_done);
    uint32_t now = (uint32_t)(cali_uptime_ms() / 1000);
    cali_lora_uplink_add(&s_bud, now, cali_lora_airtime_ms((unsigned)len + CALI_LORA_OVERHEAD, (unsigned)s_st.sf), o.confirmed);
    if (o.confirmed) s_st.last_confirmed_s = now;
    else s_st.unconfirmed++;
    publish();
    return o.confirmed;
}

static void lora_task(void *arg) {
    (void)arg;
    uint32_t next_energy = 0, daily_at = 0;
    bool daily_due = true, ready = false;
    char fw[9] = "";
    memcpy(fw, esp_app_get_description()->version, sizeof fw - 1);   /* the first 8 characters */
    cali_lora_budget_init(&s_bud);
    cali_lora_tracker_init(&s_trk);
    for (;;) {
        (void)xQueueReceive(s_snap_q, &s_snap, pdMS_TO_TICKS(1000));   /* the newest snapshot, or the last one */
        if (!s_snap.enabled) continue;   /* off: no pin is touched, nothing is sent */
        if (!ready || s_silent >= CALI_LORA_SILENT_MAX) {
            /* first use, or a module that stopped answering: a timed-out AT+SEND still counts as charged,
             * so without this a hung module would look joined and keep using up the budget */
            if (!uart_up()) {
                s_st.present = 0, s_st.note = "no UART";
                publish();
                vTaskDelay(pdMS_TO_TICKS(SILENT_RETRY_S * 1000));
                continue;
            }
            if (ready) cali_log("lora: module silent: reset");
            rak_reset();
            ready = module_setup();
            if (!ready) {
                s_silent = CALI_LORA_SILENT_MAX;   /* the next pass resets and checks again */
                s_st.note = "module silent";
                publish();
                for (int t = 0; t < SILENT_RETRY_S && s_snap.enabled; t++)
                    (void)xQueueReceive(s_snap_q, &s_snap, pdMS_TO_TICKS(1000));
                continue;
            }
        }
        if (!s_st.joined) {
            join();
            daily_due = true;
            continue;
        }
        uint32_t now = (uint32_t)(cali_uptime_ms() / 1000);
        if (now >= next_energy) {
            uint8_t buf[222];
            size_t n = cali_lora_pack_energy(buf, &s_snap);
            if (send(CALI_LORA_PORT_ENERGY, buf, n)) s_st.uplinks[0]++;
            /* then the changed functions; consumed only after a confirmed send */
            uint32_t picked;
            cali_lora_tracker_update(&s_trk, &s_snap, now);
            n = cali_lora_pack_changed(&s_trk, &s_snap, buf, cali_lora_max_payload((unsigned)s_st.sf), &picked);
            if (n && send(CALI_LORA_PORT_CHANGED, buf, n)) {
                cali_lora_tracker_commit(&s_trk, &s_snap, picked, now);
                s_st.uplinks[1]++;
            }
            if (daily_due || now - daily_at >= CALI_LORA_DAY_S) {
                n = cali_lora_pack_daily(buf, &s_snap, fw);
                if (send(CALI_LORA_PORT_DAILY, buf, n)) s_st.uplinks[2]++, daily_at = now, daily_due = false;
            }
            now = (uint32_t)(cali_uptime_ms() / 1000);
            next_energy = now + cali_lora_next_interval_s(&s_bud, now, (unsigned)s_st.sf, CALI_LORA_ENERGY_LEN,
                                                         CALI_LORA_ENERGY_SHARE);
        }
        cali_rak_event_t e;
        while (xQueueReceive(s_event_q, &e, 0) == pdTRUE) handle_event(&e);
        publish();
    }
}

void cali_lora_esp_start(void) {
    s_mu = xSemaphoreCreateMutex();
    s_snap_q = xQueueCreate(1, sizeof(cali_lora_snap_t));
    s_status_q = xQueueCreate(8, sizeof(cali_rak_event_t));
    s_event_q = xQueueCreate(16, sizeof(cali_rak_event_t));
    if (!s_mu || !s_snap_q || !s_status_q || !s_event_q ||
        xTaskCreate(lora_task, "cali_lora", TASK_STACK, NULL, 3, NULL) != pdPASS) {
        cali_log("lora: no LoRa task (out of memory)");
        s_mu = NULL;
    }
}
