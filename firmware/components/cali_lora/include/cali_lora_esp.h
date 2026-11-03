/**
 * @file cali_lora_esp.h
 * @brief The LoRa task on the device (R_FW_LORA, see cali_lora.h): started once from app_main; it touches
 *        no pin until the experimental switch is on.
 */
#ifndef CALI_LORA_ESP_H
#define CALI_LORA_ESP_H

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Create the LoRa task and its queues (board package only). */
void cali_lora_esp_start(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_LORA_ESP_H */
