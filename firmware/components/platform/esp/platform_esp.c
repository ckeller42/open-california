/* platform_esp.c — cali_platform.h on ESP-IDF (#154): kv-store on NVS, esp_timer clock, printf log.
 *
 * kv-store: one NVS blob per key in namespace "cali", holding exactly the host build's record
 * (host/platform_host.c): 4-byte little-endian length, the value bytes, 4-byte little-endian CRC32
 * over length + bytes. NVS already writes each entry atomically; the wrapper makes a record that
 * NVS returns intact but that is not ours (or bit-rotted) read back as corrupt (-2), exactly as on
 * the host. Called only from the task that owns cali_core (the NimBLE host task), like the host.
 */
#include "cali_platform.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_app_desc.h"
#include "esp_timer.h"
#include "nvs.h"
#if CONFIG_PM_ENABLE
#include "esp_pm.h"
#endif

#define KV_NAMESPACE "cali"
#define KV_RECORD_MAX (64u * 1024u) /* same bound as the host: bigger = not ours = corrupt */

static nvs_handle_t s_nvs;
static int s_nvs_open;
static int64_t s_t0_us;

uint64_t cali_uptime_ms(void) {
    return (uint64_t)(esp_timer_get_time() - s_t0_us) / 1000u;
}

void cali_log(const char *fmt, ...) {
    va_list ap;
    fputs("LOG ", stdout);
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    fputc('\n', stdout);
    fflush(stdout);
}

/* The app description's version: PROJECT_VER, which firmware/CMakeLists.txt sets from `git describe`
 * at configure time (esp_app_desc_t.version, 32 bytes, NUL-terminated). */
const char *cali_fw_version(void) {
    return esp_app_get_description()->version;
}

/* The live esp_pm config, never a flag of our own: device.display.dfs must say whether DFS is really
 * active in this boot (#346: two "DFS on" boots differed 45x in touch NACKs). */
int cali_pm_dfs(int on, char *pm, size_t cap) {
#if CONFIG_PM_ENABLE
    esp_pm_config_t c = {.max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ, .light_sleep_enable = false};
    if (on >= 0) {
        c.min_freq_mhz = on ? CONFIG_XTAL_FREQ : CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ;
        if (esp_pm_configure(&c) != ESP_OK) cali_log("pm: configure failed");
    }
    if (esp_pm_get_configuration(&c) != ESP_OK) return -1;
    if (pm) snprintf(pm, cap, "%d-%d", c.min_freq_mhz, c.max_freq_mhz);
    return c.min_freq_mhz < c.max_freq_mhz;
#else
    (void)on;
    (void)pm;
    (void)cap;
    return CALI_PM_NONE;
#endif
}

/* Open drain only: the Pi's pin 5 has its own 1.8 k pull-up to 3.3 V; this side may sink it, never
 * source it (push-pull high would fight a Pi that holds the line, e.g. as I2C SCL). */
#define PI_WAKE_GPIO GPIO_NUM_9
#define PI_HALT_GPIO GPIO_NUM_8   /* the same rules: the Pi's pin 11 has its gpio-shutdown pull-up */

static void pi_line(gpio_num_t pin, int *cfg, int low) {
    if (!*cfg) {
        const gpio_config_t c = {.pin_bit_mask = 1ULL << pin, .mode = GPIO_MODE_OUTPUT_OD,
                                 .pull_up_en = GPIO_PULLUP_DISABLE, .pull_down_en = GPIO_PULLDOWN_DISABLE,
                                 .intr_type = GPIO_INTR_DISABLE};
        gpio_set_level(pin, 1);   /* released first: becoming an output must not pulse the Pi */
        if (gpio_config(&c) != ESP_OK) {
            cali_log("pi-wake: gpio%d config failed", (int)pin);
            return;
        }
        *cfg = 1;
    }
    gpio_set_level(pin, low ? 0 : 1);
}

void cali_pi_wake_line(int low) {
    static int s_cfg;
    pi_line(PI_WAKE_GPIO, &s_cfg, low);
}

void cali_pi_halt_line(int low) {
    static int s_cfg;
    pi_line(PI_HALT_GPIO, &s_cfg, low);
}

/* store_dir is the host's; on the ESP the store is NVS (nvs_flash_init() must have run). */
int cali_platform_init(const char *store_dir) {
    (void)store_dir;
    s_t0_us = esp_timer_get_time();
    if (!s_nvs_open) {
        if (nvs_open(KV_NAMESPACE, NVS_READWRITE, &s_nvs) != ESP_OK) return -1;
        s_nvs_open = 1;
    }
    return 0;
}

/* The host's key rule (keys are file names there): 1..15 chars of [A-Za-z0-9_-]. */
static int key_ok(const char *key) {
    size_t n = key ? strlen(key) : 0;
    if (n == 0 || n > CALI_KV_KEY_MAX) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = key[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '_' || c == '-'))
            return 0;
    }
    return 1;
}

static void put_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t get_le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int cali_kv_get(const char *key, void *buf, size_t *len) {
    size_t size = 0;
    uint8_t *raw;
    uint32_t n;
    int rc = CALI_KV_CORRUPT;
    esp_err_t err;

    if (!s_nvs_open || !key_ok(key) || len == NULL) return CALI_KV_MISSING;
    err = nvs_get_blob(s_nvs, key, NULL, &size);
    if (err == ESP_ERR_NVS_NOT_FOUND) return CALI_KV_MISSING;
    if (err != ESP_OK || size < 8 || size > KV_RECORD_MAX) return CALI_KV_CORRUPT;
    raw = malloc(size);
    if (raw != NULL && nvs_get_blob(s_nvs, key, raw, &size) == ESP_OK && size >= 8) {
        n = get_le32(raw);
        if ((uint64_t)n + 8u == (uint64_t)size &&
            cali_crc32(0, raw, 4u + n) == get_le32(raw + 4 + n) && n <= *len) {
            if (n) memcpy(buf, raw + 4, n);
            *len = n;
            rc = CALI_KV_OK;
        }
    }
    free(raw);
    return rc;
}

int cali_kv_set(const char *key, const void *buf, size_t len) {
    uint8_t *raw;
    uint32_t crc;
    int rc = -1;

    if (!s_nvs_open || !key_ok(key) || len > KV_RECORD_MAX - 8 || (len && buf == NULL)) return -1;
    raw = malloc(len + 8);
    if (raw == NULL) return -1;
    put_le32(raw, (uint32_t)len);
    if (len) memcpy(raw + 4, buf, len);
    crc = cali_crc32(0, raw, 4u + len);
    put_le32(raw + 4 + len, crc);
    if (nvs_set_blob(s_nvs, key, raw, len + 8) == ESP_OK && nvs_commit(s_nvs) == ESP_OK) rc = 0;
    free(raw);
    return rc;
}

/* Needed now, not only from Task 9: console.c links the WiFi runner (wifi_run.c), whose credential
 * clear calls this, into every build that has the console. */
int cali_kv_erase(const char *key) {
    esp_err_t err;
    if (!s_nvs_open || !key_ok(key)) return -1;
    err = nvs_erase_key(s_nvs, key);
    if (err == ESP_ERR_NVS_NOT_FOUND) return 0;
    return err == ESP_OK && nvs_commit(s_nvs) == ESP_OK ? 0 : -1;
}

int cali_kv_erase_all(void) {
    if (!s_nvs_open) return -1;
    return nvs_erase_all(s_nvs) == ESP_OK && nvs_commit(s_nvs) == ESP_OK ? 0 : -1;
}
