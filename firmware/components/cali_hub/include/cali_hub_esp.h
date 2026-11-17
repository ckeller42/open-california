/**
 * @file cali_hub_esp.h
 * @brief The remote hub's MQTT client on the device (cali_hub.h): started once from app_main, ticked on the
 *        owner task. Nothing runs while the hub config (KV hub_cfg) is missing.
 */
#ifndef CALI_HUB_ESP_H
#define CALI_HUB_ESP_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Sets the cipher, loads the config and the data key, starts the client. Owner task, once. */
void cali_hub_esp_start(void);
/** @brief Owner task, every tick: runs queued requests, publishes the state, applies a new config. */
void cali_hub_esp_tick(uint64_t now_ms);

#ifdef __cplusplus
}
#endif

#endif
