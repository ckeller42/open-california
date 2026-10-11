/* cali_ota_esp.h — the ESP side of the firmware update (R_FW_OTA): esp_https_ota + the certificate
 * bundle behind cali_ota_ops_t (cali_core/include/cali_ota.h), its own task, rollback. ESP only;
 * cali_core never includes this. */
#ifndef CALI_OTA_ESP_H
#define CALI_OTA_ESP_H

#include <stdint.h>

/* Owner task, once WiFi runs: creates the OTA task and hands cali_ota_init the HTTPS/flash ops. */
void cali_ota_esp_start(void);
/* Owner task, once after CALI_OTA_VALID_AFTER_MS of a healthy run (the tick alive, the web server
 * listening): a freshly installed app pending verification is marked valid, which cancels the
 * rollback. Before that a crash or reset boots the previous slot. Any other app: nothing. */
#define CALI_OTA_VALID_AFTER_MS 60000
void cali_ota_esp_mark_valid(void);
/* Console hook (owner task): "ota check|install|status|invalid" -> 1, any other line -> 0.
 * check/install = POST /api/ota's actions in any WiFi mode (physical access); status logs the
 * device.ota object and the running slot; invalid = esp_ota_mark_app_invalid_rollback_and_reboot
 * (the rollback test: only a pending-verify app rolls back, else it logs why not). */
int cali_ota_esp_console_line(const char *line);

#endif /* CALI_OTA_ESP_H */
