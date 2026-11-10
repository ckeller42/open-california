/* lora_esp.c — the RAK3172 on UART1 and the LoRa task (R_FW_LORA). Board package only (CONFIG_CALI_DISPLAY);
 * the host build and QEMU link cali_core's lora_port_none.c instead.
 *
 * The owner task hands a snapshot over every 10 s (cali_lora_port_post, a 1-deep queue: the newest wins);
 * this task packs and sends from that copy and never reads session state. It publishes its status under
 * a mutex; the owner task copies it (cali_lora_port_status). Nothing touches the pins until the first
 * snapshot with the switch on.
 *
 * Class C: a downlink can arrive at any time, so every wait loop hands the module's events to handle_event().
 * An FPort-12 downlink is a key-transfer fragment (R_FW_LORA_SETUP); its confirmation goes out on the next
 * pass with room in the airtime budget, ahead of ports 2/3/4. The display asks for a setup through an
 * atomic flag (cali_lora_port_setup_request): only this task touches the UART and opens the setup. */
#include <ctype.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "cali_lora.h"
#include "cali_lora_aead.h"
#include "cali_lora_setup.h"
#include "cali_platform.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_app_desc.h"
#include "esp_random.h"
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
#define NO_KEYS_NOTE "no keys: open the LoRa screen"
#define JOIN_REQUEST_LEN 23u   /* PHY bytes of a LoRaWAN 1.0.x join request */
#define SILENT_RETRY_S 600
#define CONFIRM_RETRY_S 60   /* the wait after a confirmation send without +EVT:TX_DONE */
#define CONFIRM_TRIES 5      /* sends without +EVT:TX_DONE before a confirmation is dropped */
#define TASK_STACK 8192   /* PR #492 ran with 6144; the PSA AES-GCM open under send() needs more */

static QueueHandle_t s_snap_q, s_status_q, s_event_q;
static SemaphoreHandle_t s_mu;
static cali_lora_status_t s_st = {.present = -1, .sf = 12};    /* the task's own */
static cali_lora_status_t s_pub = {.present = -1, .sf = 12};   /* the published copy (s_mu) */
static cali_lora_snap_t s_snap;   /* the task's copy */
static cali_lora_budget_t s_bud;
static cali_lora_tracker_t s_trk;
static unsigned s_silent;
static bool s_uart_up, s_in_send, s_have_keys;
static volatile bool s_on;   /* the newest snapshot's switch, set by the owner task: a send or a join wait stops at once */
/* Setup requests as a counter: any task increments s_setup_req, this task sets s_setup_done to the
 * value it served only after a successful open, so a request made during an open is never lost and a
 * failed open stays pending (retried on the next pass). */
static atomic_uint s_setup_req, s_setup_done;
static atomic_bool s_forgot;   /* cali_lora_forget() ran: the next pass with the switch off erases the keys again */
#define SETUP_FAIL_LOG_MS 60000u   /* at most one "setup: store failed" line per minute while a request is pending */
static cali_lora_xfer_t s_xfer;     /* the key transfer in progress (ciphertext only) */
static uint8_t s_confirm[6];        /* the pending FPort-12 confirmation */
static size_t s_confirm_len;        /* 0 = none pending */
static uint8_t s_confirm_tk[4];     /* sha256(the transfer key it answers)[0..3]: a status 0 erases only that key */
static unsigned s_confirm_seq;      /* +1 per queued confirmation: a send sees whether it was replaced */
static unsigned s_confirm_tries;    /* sends without +EVT:TX_DONE */
static uint32_t s_confirm_retry_at; /* uptime s of the next attempt */

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

int cali_lora_port_qr(char *out, size_t max) {
    cali_lora_status_t st;
    if (!cali_lora_port_status(&st)) return 0;
    return (int)cali_lora_qr_text(out, max, &st, cali_exp_lora());
}

int cali_lora_port_setup_qr(char *out, size_t max) {
    cali_lora_status_t st;
    if (!cali_lora_port_status(&st)) return 0;
    return (int)cali_lora_setup_qr_text(out, max, &st, cali_exp_lora());
}

static bool setup_pending(void) { return atomic_load(&s_setup_req) != atomic_load(&s_setup_done); }

int cali_lora_port_setup_pending(void) { return setup_pending(); }

void cali_lora_port_setup_request(void) {
    atomic_fetch_add(&s_setup_req, 1);
}

void cali_lora_port_forgotten(void) { atomic_store(&s_forgot, true); }

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

static int unhex8(const char *h, uint8_t *out) {
    if (strlen(h) != 16) return -1;
    for (int i = 0; i < 8; i++) {
        unsigned v;
        if (sscanf(h + 2 * i, "%2x", &v) != 1) return -1;
        out[i] = (uint8_t)v;
    }
    return 0;
}

static void confirm_set(const uint8_t *c, size_t n, const uint8_t *tk_id) {
    memcpy(s_confirm, c, n);
    s_confirm_len = n;
    if (tk_id) memcpy(s_confirm_tk, tk_id, sizeof s_confirm_tk);
    else cali_wipe(s_confirm_tk, sizeof s_confirm_tk);
    s_confirm_seq++;
    s_confirm_tries = 0;
    s_confirm_retry_at = 0;
}

static void confirm_clear(void) {
    cali_wipe(s_confirm, sizeof s_confirm);
    cali_wipe(s_confirm_tk, sizeof s_confirm_tk);
    s_confirm_len = 0;
    s_confirm_seq++;
}

/* One key-transfer fragment. The transfer key and the plaintext are wiped here; no payload byte is logged. */
static void xfer_rx(const cali_rak_event_t *e) {
    static uint8_t plain[CALI_LORA_XFER_MAX];   /* static: off the stack under send() */
    uint8_t tk[16], eui[8], confirm[6];
    size_t tl = sizeof tk, plain_len = 0, confirm_len = 0;
    if (unhex8(s_st.dev_eui, eui) != 0) {
        cali_log("lora: transfer fragment before the DevEUI is known: dropped");
        return;
    }
    if (cali_kv_get(CALI_LORA_TK_KEY, tk, &tl) != CALI_KV_OK || tl != sizeof tk) {
        /* 00|sha256(lora_ttn) when the keys are here (the app lost the status-0 confirmation), else 03 */
        confirm_len = cali_lora_confirm_no_tk(confirm);
        cali_lora_xfer_reset(&s_xfer);
        confirm_set(confirm, confirm_len, NULL);
        cali_log("lora: transfer fragment without a transfer key: status %u", (unsigned)confirm[0]);
        cali_wipe(tk, sizeof tk);
        return;
    }
    uint8_t tk_id[4];
    cali_lora_sha256_4(tk, sizeof tk, tk_id);
    if (s_confirm_len && s_confirm[0] == 0 && memcmp(s_confirm_tk, tk_id, sizeof tk_id) == 0) {
        /* this transfer key's transfer is done and its status 0 still waits to go out: a fragment now is the
         * app's probe or resend of it, never a new transfer (the app sends a new one only with a new key) */
        cali_log("lora: transfer fragment after the transfer finished: status 0 kept");
        cali_wipe(tk, sizeof tk);
        cali_wipe(tk_id, sizeof tk_id);
        return;
    }
    int r = cali_lora_xfer_rx(&s_xfer, e->payload, e->payload_len, tk, eui, cali_lora_aead_open_psa, plain,
                              &plain_len, confirm, &confirm_len);
    cali_wipe(tk, sizeof tk);
    if (r == 1 && confirm[0] == 0 && !cali_lora_ttn_ok(plain, plain_len)) {
        confirm[0] = 1;   /* authentic but not a plaintext the repair QR can show: store nothing */
        confirm_len = 1;
        cali_log("lora: transfer: plaintext not usable");
    }
    if (r == 1 && confirm[0] == 0 && cali_kv_set(CALI_LORA_TTN_KEY, plain, plain_len) != 0) {
        cali_log("lora: transfer: storing the TTN keys failed");
        r = 0;   /* no confirmation: the app sends again */
    }
    cali_wipe(plain, sizeof plain);
    if (r == 1 || r == 2) {
        confirm_set(confirm, confirm_len, tk_id);
        cali_log("lora: transfer: confirmation status %u queued", (unsigned)confirm[0]);
    } else if (r < 0) {
        cali_log("lora: transfer fragment rejected (%u bytes)", e->payload_len);
    }
}

static void handle_event(const cali_rak_event_t *e) {
    if (e->kind == CALI_RAK_RX) {
        /* every downlink (ADR, link check, a command, a transfer fragment) costs one of the day's downlinks */
        (void)cali_lora_downlink_take(&s_bud, (uint32_t)(cali_uptime_ms() / 1000));
        if (e->port == CALI_LORA_XFER_PORT) xfer_rx(e);
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
    if (!(e.kind == CALI_RAK_VALUE && strcasecmp(e.value, want) == 0)) {
        snprintf(cmd, sizeof cmd, "AT+%s=%s", name, want);
        if (at(cmd, 2000).kind != CALI_RAK_OK) cali_log("lora: AT+%s rejected", name);
    }
    cali_wipe(cmd, sizeof cmd);   /* AT+APPKEY=... */
    cali_wipe(&e, sizeof e);      /* the module's answer to AT+APPKEY=? */
}

static void hexs(char *out, const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) sprintf(out + 2 * i, "%02X", b[i]);
}

/* The stored DevEUI/JoinEUI/AppKey into the module, each only when it differs; before every join
 * attempt, so `lora keys` or a new setup takes effect without a reboot: the setup's keys first, else the
 * bench keys. false = no stored keys (no join then). */
static bool apply_keys(void) {
    uint8_t k[32];
    char h[33];
    size_t n = 24, nb = sizeof k;
    s_have_keys = true;
    if (cali_kv_get(CALI_LORA_DEV_KEY, k, &n) == CALI_KV_OK && n == 24) {
        /* the setup's keys: AppKey 16 + JoinEUI 8; the module keeps its own DevEUI */
        hexs(h, k + 16, 8);
        set_if_differs("APPEUI", h);
        hexs(h, k, 16);
        set_if_differs("APPKEY", h);
    } else if (cali_kv_get(CALI_LORA_KEYS_KEY, k, &nb) == CALI_KV_OK && nb == sizeof k) {
        /* the bench path: `lora keys <deveui> <joineui> <appkey>` */
        hexs(h, k, 8);
        set_if_differs("DEVEUI", h);
        hexs(h, k + 8, 8);
        set_if_differs("APPEUI", h);
        hexs(h, k + 16, 16);
        set_if_differs("APPKEY", h);
    } else {
        s_have_keys = false;
        s_st.note = NO_KEYS_NOTE;
    }
    cali_wipe(h, sizeof h);
    cali_wipe(k, sizeof k);
    return s_have_keys;
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
    at("AT+CLASS=C", 1000);   /* permanently: the registration says supports_class_c */
    at("AT+ADR=1", 1000);
    (void)apply_keys();
    cali_rak_event_t e = at("AT+DEVEUI=?", 1000);   /* an identifier, not a secret: shown in the setup QR */
    s_st.dev_eui[0] = 0;
    if (e.kind == CALI_RAK_VALUE && strlen(e.value) == 16) {
        for (int i = 0; i < 16; i++) s_st.dev_eui[i] = (char)toupper((unsigned char)e.value[i]);
        s_st.dev_eui[16] = 0;
    }
    return true;
}

/* A module still silent after the RST pulse: release TX and RST for 5 s, then reset and set it up again. No
 * power is switched. ponytail: the cause of the hang after a host power event is not known (not reproduced on
 * the bench); a driven pin that keeps a browned-out STM32 latched is the guess this tests. */
static bool pins_release_retry(void) {
    gpio_reset_pin(RAK_TX);
    gpio_set_direction(RAK_TX, GPIO_MODE_INPUT);
    gpio_reset_pin(RAK_RST);
    gpio_set_direction(RAK_RST, GPIO_MODE_INPUT);
    vTaskDelay(pdMS_TO_TICKS(5000));
    uart_set_pin(RAK_UART, RAK_TX, RAK_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    gpio_set_direction(RAK_RST, GPIO_MODE_OUTPUT);
    rak_reset();
    return module_setup();
}

/* The display's long press: a new transfer key (and the AppKey the first time). The module needs a new
 * AppKey and a new join only when the AppKey changed. -1 = the store failed, 0 = opened, 1 = opened with
 * a new AppKey. */
static int setup_open(void) {
    uint8_t before[24], after[24];
    size_t nb = sizeof before, na = sizeof after;
    int had = cali_kv_get(CALI_LORA_DEV_KEY, before, &nb) == CALI_KV_OK && nb == sizeof before;
    static uint64_t s_fail_log_ms;   /* the last "store failed" line, 0 = none since the last good open */
    static bool s_new_appkey;        /* a failed open stored a new AppKey already: the open that succeeds rejoins */
    int changed = -1, rc = cali_lora_setup_open(esp_fill_random);
    bool differs = cali_kv_get(CALI_LORA_DEV_KEY, after, &na) == CALI_KV_OK && na == sizeof after &&
                   (!had || memcmp(before, after, 16) != 0);
    if (rc != 0) {
        s_new_appkey |= differs;
        uint64_t now = cali_uptime_ms();   /* retried every pass (~1 s) while pending: one line per SETUP_FAIL_LOG_MS */
        if (!s_fail_log_ms || now - s_fail_log_ms >= SETUP_FAIL_LOG_MS) {
            cali_log("lora: setup: store failed");
            s_fail_log_ms = now ? now : 1;
        }
    } else {
        s_fail_log_ms = 0;
        changed = differs || s_new_appkey;
        s_new_appkey = false;
        cali_log("lora: setup opened%s", changed ? " (new AppKey: join again)" : "");
    }
    cali_wipe(before, sizeof before);
    cali_wipe(after, sizeof after);
    cali_lora_xfer_reset(&s_xfer);   /* fragments sealed with the old transfer key are useless */
    /* so is a status-2 request for more of them; a pending status 0 still goes out (the app waits for it, and
     * erase_tk_if keeps the new key), as do 1 and 3 (they ask the app for the new scan) */
    if (s_confirm_len && s_confirm[0] == 2) confirm_clear();
    return changed;
}

static void join(void) {
    unsigned attempt = 0;
    while (!s_st.joined && s_on && s_silent < CALI_LORA_SILENT_MAX && !setup_pending()) {
        s_st.note = "joining";
        publish();
        bool keys = apply_keys();
        read_sf();
        /* a join request is an uplink too: 23 B on air (MHDR, JoinEUI, DevEUI, DevNonce, MIC), charged
         * to the airtime budget like any send; no room = no attempt, the backoff below waits */
        uint32_t join_ms = cali_lora_airtime_ms(JOIN_REQUEST_LEN, (unsigned)s_st.sf);
        bool room = cali_lora_uplink_room_ms(&s_bud, (uint32_t)(cali_uptime_ms() / 1000)) >= join_ms;
        if (keys && room && s_on && at("AT+JOIN=1:0:8:1", 2000).kind == CALI_RAK_OK) {
            cali_lora_uplink_add(&s_bud, (uint32_t)(cali_uptime_ms() / 1000), join_ms, 0);
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
        s_st.note = !keys ? NO_KEYS_NOTE : !room ? "join waits: airtime budget" : "join failed";
        publish();
        /* the backoff: one second at a time, taking each new snapshot (the 1-deep queue is always full,
         * so a peek would return at once and spin) */
        uint32_t wait = cali_lora_join_backoff_s(attempt++);
        for (uint32_t t = 0; t < wait && s_on && !setup_pending(); t++) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            (void)xQueueReceive(s_snap_q, &s_snap, 0);
        }
    }
}

/* One AT+SEND: 0 refused, 1 taken without +EVT:TX_DONE, 2 confirmed by +EVT:TX_DONE. Only while the switch is on and
 * within both budgets (at the SF the module reports now); airtime counts as on air only after +EVT:TX_DONE. */
static int send(uint8_t port, const uint8_t *buf, size_t len) {
    char cmd[16 + 2 * 222];
    int n = snprintf(cmd, sizeof cmd, "AT+SEND=%u:", port);
    for (size_t i = 0; i < len && n < (int)sizeof cmd - 3; i++) n += snprintf(cmd + n, sizeof cmd - (size_t)n, "%02X", buf[i]);
    if (!s_on) return 0;
    read_sf();
    uint32_t t = (uint32_t)(cali_uptime_ms() / 1000);
    /* every uplink can draw a downlink, so the downlink budget gates uplinks (a join does not) */
    if (cali_lora_uplink_room_ms(&s_bud, t) < cali_lora_airtime_ms((unsigned)len + CALI_LORA_OVERHEAD, (unsigned)s_st.sf) ||
        cali_lora_downlinks_used(&s_bud, t) >= CALI_LORA_DOWNLINKS)
        return 0;
    cali_rak_event_t e = at(cmd, 3000);
    if (e.kind == CALI_RAK_NOT_JOINED) {
        s_st.joined = false;
        return 0;
    }
    cali_rak_kind_t status = e.kind;
    if (!cali_lora_outcome(status, 0).charged) return 0;   /* refused: nothing went out */
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
    while (xQueueReceive(s_event_q, &e, pdMS_TO_TICKS(2500)) == pdTRUE) handle_event(&e);   /* RX windows, class C */
    s_in_send = false;
    cali_lora_outcome_t o = cali_lora_outcome(status, tx_done);
    uint32_t now = (uint32_t)(cali_uptime_ms() / 1000);
    cali_lora_uplink_add(&s_bud, now, cali_lora_airtime_ms((unsigned)len + CALI_LORA_OVERHEAD, (unsigned)s_st.sf), o.confirmed);
    if (o.confirmed) s_st.last_confirmed_s = now;
    else s_st.unconfirmed++;
    publish();
    return o.confirmed ? 2 : status == CALI_RAK_OK;
}

/* Erase lora_tk only when it is still the key the confirmed transfer used (a long press may have made a new one). */
static void erase_tk_if(const uint8_t *tk_id) {
    uint8_t tk[16], id[4];
    size_t tl = sizeof tk;
    if (cali_kv_get(CALI_LORA_TK_KEY, tk, &tl) == CALI_KV_OK && tl == sizeof tk) {
        cali_lora_sha256_4(tk, sizeof tk, id);
        if (memcmp(id, tk_id, sizeof id) != 0) cali_log("lora: transfer done: a newer transfer key is kept");
        else if (cali_kv_erase(CALI_LORA_TK_KEY) != 0) cali_log("lora: transfer: erasing the transfer key failed");
        else cali_log("lora: transfer done: transfer key erased");
    }
    cali_wipe(tk, sizeof tk);
    cali_wipe(id, sizeof id);
}

/* The pending key-transfer confirmation: on every pass while the 24-h airtime and downlink budgets have room,
 * ahead of ports 2/3/4. A send without +EVT:TX_DONE waits CONFIRM_RETRY_S; after CONFIRM_TRIES of them the
 * confirmation is dropped and the transfer key kept (the app sends again). After a confirmed status 0
 * the transfer key it answers is erased. */
static void send_confirm(uint32_t now) {
    uint8_t c[6], tk_id[4];
    size_t n = s_confirm_len;
    unsigned seq = s_confirm_seq;
    /* no room in either budget: wait, it is no send attempt (the confirmation stays until there is room) */
    if (!n || now < s_confirm_retry_at ||
        cali_lora_uplink_room_ms(&s_bud, now) < cali_lora_airtime_ms((unsigned)n + CALI_LORA_OVERHEAD, (unsigned)s_st.sf) ||
        cali_lora_downlinks_used(&s_bud, now) >= CALI_LORA_DOWNLINKS)
        return;
    memcpy(c, s_confirm, n);
    memcpy(tk_id, s_confirm_tk, sizeof tk_id);
    int r = send(CALI_LORA_XFER_PORT, c, n);
    if (seq == s_confirm_seq) {   /* not replaced by a fragment during the send */
        if (r == 2) {
            confirm_clear();
            if (c[0] == 0) erase_tk_if(tk_id);
        } else if (++s_confirm_tries >= CONFIRM_TRIES) {
            cali_log("lora: transfer: confirmation status %u dropped after %u sends without TX_DONE", (unsigned)c[0],
                     s_confirm_tries);
            confirm_clear();
        } else {
            s_confirm_retry_at = (uint32_t)(cali_uptime_ms() / 1000) + CONFIRM_RETRY_S;
        }
    }
    cali_wipe(tk_id, sizeof tk_id);
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
        if (!s_snap.enabled) {   /* off: no pin is touched, nothing is sent */
            atomic_store(&s_setup_done, atomic_load(&s_setup_req));   /* a request made while off never rotates the key later */
            /* forgotten keys, or a module changed meanwhile: on again starts like the first use */
            ready = false;
            s_st.joined = false;
            s_have_keys = false;
            cali_lora_xfer_reset(&s_xfer);
            if (s_confirm_len) confirm_clear();
            /* this task is the only key writer: a write of its own that raced the forget is gone after this */
            if (atomic_exchange(&s_forgot, false) && cali_lora_erase_keys() != 0) cali_log("lora: forget: store failed");
            continue;
        }
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
            if (!ready && s_snap.enabled) {
                cali_log("lora: module silent after reset: pins released 5 s");
                ready = pins_release_retry();
                if (ready) cali_log("lora: module answers after the pin release");
            }
            if (!ready) {
                s_silent = CALI_LORA_SILENT_MAX;   /* the next pass resets and checks again */
                s_st.note = "module silent";
                publish();
                for (int t = 0; t < SILENT_RETRY_S && s_snap.enabled; t++)
                    (void)xQueueReceive(s_snap_q, &s_snap, pdMS_TO_TICKS(1000));
                continue;
            }
        }
        unsigned want = atomic_load(&s_setup_req);
        if (want != atomic_load(&s_setup_done)) {   /* done only once the new transfer key is stored (the display waits on it) */
            int fresh = setup_open();
            if (fresh >= 0) atomic_store(&s_setup_done, want);   /* a newer request stays pending */
            if (fresh == 1) {
                ready = false;   /* a new AppKey: reset, set up and join like the first use */
                continue;
            }
        }
        if (!s_st.joined) {
            /* nothing to join with until the LoRa screen opens the setup or `lora keys` stores bench keys;
             * the store is read again each pass, so either takes effect without a restart */
            if (!s_have_keys && !apply_keys()) {
                publish();
                continue;
            }
            join();
            daily_due = true;
            continue;
        }
        uint32_t now = (uint32_t)(cali_uptime_ms() / 1000);
        send_confirm(now);
        now = (uint32_t)(cali_uptime_ms() / 1000);
        if (now >= next_energy) {
            uint8_t buf[222];
            size_t n = cali_lora_pack_energy(buf, &s_snap);
            if (send(CALI_LORA_PORT_ENERGY, buf, n) == 2) s_st.uplinks[0]++;
            /* then the changed functions; consumed only after a confirmed send */
            uint32_t picked;
            cali_lora_tracker_update(&s_trk, &s_snap, now);
            n = cali_lora_pack_changed(&s_trk, &s_snap, buf, cali_lora_max_payload((unsigned)s_st.sf), &picked);
            if (n && send(CALI_LORA_PORT_CHANGED, buf, n) == 2) {
                cali_lora_tracker_commit(&s_trk, &s_snap, picked, now);
                s_st.uplinks[1]++;
            }
            if (daily_due || now - daily_at >= CALI_LORA_DAY_S) {
                n = cali_lora_pack_daily(buf, &s_snap, fw);
                if (send(CALI_LORA_PORT_DAILY, buf, n) == 2) s_st.uplinks[2]++, daily_at = now, daily_due = false;
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
