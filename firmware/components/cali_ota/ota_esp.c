/* ota_esp.c — cali_ota_ops_t on ESP-IDF (R_FW_OTA): the manifest over esp_http_client, the image
 * over esp_https_ota into the inactive slot, SHA-256 of what landed read back from flash (PSA), the
 * boot switch only through esp_https_ota_finish. TLS: the ESP x509 certificate bundle (github.com
 * redirects to GitHub's release-asset host). Contract: include/cali_ota_esp.h. */
#include "cali_ota_esp.h"

#include <stdlib.h>
#include <string.h>

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "psa/crypto.h"

#include "cali_json.h"
#include "cali_ota.h"
#include "cali_platform.h"

#define OTA_STACK 8192
#define OTA_PRIO 4            /* below the NimBLE host task (owner) */
#define HTTP_BUF 4096         /* rx and tx: GitHub's redirect Location is a ~1 KB signed URL */
#define HTTP_TIMEOUT_MS 15000
#define READ_CHUNK 4096

static TaskHandle_t s_task;
static esp_https_ota_handle_t s_ota;
static const esp_partition_t *s_slot;

typedef struct {
    char *buf;
    size_t cap, len;
    int over;
} body_t;

/* Collects the final 200 answer's body; a redirect's own body is dropped. */
static esp_err_t on_event(esp_http_client_event_t *e) {
    body_t *b = e->user_data;
    if (e->event_id != HTTP_EVENT_ON_DATA || esp_http_client_get_status_code(e->client) != 200) return ESP_OK;
    if (b->len + (size_t)e->data_len >= b->cap) {
        b->over = 1;
    } else {
        memcpy(b->buf + b->len, e->data, (size_t)e->data_len);
        b->len += (size_t)e->data_len;
    }
    return ESP_OK;
}

static int e_get(const char *url, char *buf, size_t cap) {
    body_t b = {buf, cap, 0, 0};
    esp_http_client_config_t cfg = {
        .url = url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .event_handler = on_event,
        .user_data = &b,
        .buffer_size = HTTP_BUF,
        .buffer_size_tx = HTTP_BUF,
        .timeout_ms = HTTP_TIMEOUT_MS,
    };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    esp_err_t err;
    int status;
    if (!c) return -1;
    err = esp_http_client_perform(c);   /* follows the redirect to the asset host */
    status = esp_http_client_get_status_code(c);
    esp_http_client_cleanup(c);
    if (err != ESP_OK || status != 200 || b.over) {
        cali_log("ota: GET manifest: %s, status %d%s", esp_err_to_name(err), status, b.over ? ", too long" : "");
        return -1;
    }
    buf[b.len] = 0;
    return (int)b.len;
}

static int e_download(const char *url, size_t size) {
    esp_http_client_config_t http = {
        .url = url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = HTTP_BUF,
        .buffer_size_tx = HTTP_BUF,
        .timeout_ms = HTTP_TIMEOUT_MS,
        .keep_alive_enable = true,
    };
    esp_https_ota_config_t cfg = {.http_config = &http};   /* staging NULL: the inactive slot */
    esp_err_t err;
    size_t got;
    s_slot = esp_ota_get_next_update_partition(NULL);
    err = esp_https_ota_begin(&cfg, &s_ota);
    if (err != ESP_OK) {
        s_ota = NULL;   /* begin cleans up after itself */
        cali_log("ota: download begin: %s", esp_err_to_name(err));
        return -1;
    }
    while ((err = esp_https_ota_perform(s_ota)) == ESP_ERR_HTTPS_OTA_IN_PROGRESS)
        cali_ota_progress((size_t)esp_https_ota_get_image_len_read(s_ota), size);
    got = (size_t)esp_https_ota_get_image_len_read(s_ota);
    if (err != ESP_OK || !esp_https_ota_is_complete_data_received(s_ota) || got != size) {
        cali_log("ota: download: %s, %u of %u bytes", esp_err_to_name(err), (unsigned)got, (unsigned)size);
        return -1;
    }
    cali_ota_progress(got, size);
    return 0;
}

/* SHA-256 of the slot's first size bytes, read back from flash: what will boot, not what was sent. */
static int e_slot_sha256(size_t size, uint8_t out[32]) {
    psa_hash_operation_t op = PSA_HASH_OPERATION_INIT;
    uint8_t *buf = malloc(READ_CHUNK);
    size_t n = 0;
    int ok = buf && s_slot && psa_crypto_init() == PSA_SUCCESS && psa_hash_setup(&op, PSA_ALG_SHA_256) == PSA_SUCCESS;
    for (size_t off = 0; ok && off < size; off += n) {
        n = size - off < READ_CHUNK ? size - off : READ_CHUNK;
        ok = esp_partition_read(s_slot, off, buf, n) == ESP_OK && psa_hash_update(&op, buf, n) == PSA_SUCCESS;
    }
    ok = ok && psa_hash_finish(&op, out, 32, &n) == PSA_SUCCESS && n == 32;
    if (!ok) psa_hash_abort(&op);
    free(buf);
    return ok ? 0 : -1;
}

static int e_commit(void) {
    esp_err_t err = esp_https_ota_finish(s_ota);   /* validates the image, sets the boot partition */
    s_ota = NULL;
    if (err != ESP_OK) cali_log("ota: finish: %s", esp_err_to_name(err));
    return err == ESP_OK ? 0 : -1;
}

static void e_abort(void) {
    if (s_ota) esp_https_ota_abort(s_ota);
    s_ota = NULL;
}

static void e_restart(void) {
    vTaskDelay(pdMS_TO_TICKS(1000));   /* the page's next poll sees "rebooting" */
    esp_restart();
}

static void e_wake(void) { xTaskNotifyGive(s_task); }

static const cali_ota_ops_t s_ops = {
    .get = e_get, .download = e_download, .slot_sha256 = e_slot_sha256, .commit = e_commit,
    .abort = e_abort, .restart = e_restart, .wake = e_wake,
};

static void ota_task(void *arg) {
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        cali_ota_work();
    }
}

void cali_ota_esp_start(void) {
    if (xTaskCreate(ota_task, "cali_ota", OTA_STACK, NULL, OTA_PRIO, &s_task) != pdPASS) {
        cali_log("ota: no task, updates off");
        return;
    }
    cali_ota_init(&s_ops);
}

void cali_ota_esp_mark_valid(void) {
    const esp_partition_t *run = esp_ota_get_running_partition();
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(run, &st) != ESP_OK || st != ESP_OTA_IMG_PENDING_VERIFY) return;
    cali_log("ota: %s healthy for %d s, marked valid: %s", run->label, CALI_OTA_VALID_AFTER_MS / 1000,
             esp_err_to_name(esp_ota_mark_app_valid_cancel_rollback()));
}

int cali_ota_esp_console_line(const char *line) {
    if (strncmp(line, "ota", 3) != 0 || (line[3] != ' ' && line[3] != 0)) return 0;
    const char *sub = line + 3;
    while (*sub == ' ') sub++;
    if (strcmp(sub, "check") == 0 || strcmp(sub, "install") == 0) {
        int rc = cali_ota_request(sub[0] == 'i');
        cali_log("ota: %s", rc == 0 ? "started" : rc == -2 ? "busy" : "unavailable");
    } else if (strcmp(sub, "status") == 0) {
        char buf[200];
        cali_json_t j;
        const esp_partition_t *run = esp_ota_get_running_partition();
        esp_ota_img_states_t st = ESP_OTA_IMG_UNDEFINED;
        (void)esp_ota_get_state_partition(run, &st);
        cali_json_begin(&j, buf, sizeof buf);
        cali_json_key(&j, "ota");
        cali_ota_json(&j);
        if (cali_json_end(&j) < 0) buf[0] = 0;
        cali_log("ota: fw=%s slot=%s img_state=%d %s", cali_fw_version(), run->label, (int)st, buf);
    } else if (strcmp(sub, "invalid") == 0) {
        cali_log("ota: mark invalid + reboot: %s", esp_err_to_name(esp_ota_mark_app_invalid_rollback_and_reboot()));
    } else {
        cali_log("ota: usage: ota check|install|status|invalid");
    }
    return 1;
}
