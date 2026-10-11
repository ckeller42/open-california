/* cali_sdcard_esp.h — the ESP side of the SD log (R_FW_SD_LOG): microSD power + mount + the SD task
 * that writes cali_sdlog's records to /sdcard/log. ESP only; cali_core never includes this. */
#ifndef CALI_SDCARD_ESP_H
#define CALI_SDCARD_ESP_H

/* Owner task, once, after cali_display_init succeeded (the SPI3 bus and the board package's I2C
 * chips are up): the record queue, cali_sdlog_init, and the SD task (8 KB stack), which mounts the
 * card (retrying every 5 min while absent) and appends + fsyncs. Never aborts: no card = no log. */
void cali_sdcard_esp_start(void);

#endif /* CALI_SDCARD_ESP_H */
