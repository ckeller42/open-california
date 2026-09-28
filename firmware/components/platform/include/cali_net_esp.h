/* cali_net_esp.h — cali_net.h on ESP-IDF: esp_wifi + esp_netif + lwIP sockets + mdns (#154).
 * Implementation: platform/esp/net_esp.c. ESP build only (the host build has cali_net_host.h).
 *
 * Tasks: WiFi/IP events arrive on the ESP event task; its handler only copies them into a small
 * fixed ring (a critical section; when full the oldest entry is dropped and the next poll logs
 * "LOG net: event queue full, oldest dropped" once). cali_net_esp_poll(), called from the task that
 * runs the tick (app_main's owner: the NimBLE host task), drains the ring and is the ONLY place the
 * sink registered with set_sink() is called. Every cali_net_esp call below and in the table runs on
 * that task; none blocks (esp_wifi_* configure/start calls return at once, sockets are O_NONBLOCK).
 *
 * Needs, before init: nvs_flash_init(), esp_netif_init(), esp_event_loop_create_default().
 */
#ifndef CALI_NET_ESP_H
#define CALI_NET_ESP_H

#include <stdint.h>

#include "cali_net.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The cali_net table. Usable only after cali_net_esp_init() returned 0. */
extern const cali_net_t cali_net_esp;

/* Creates the STA + AP netifs (AP pinned to NET_AP_ADDR/24, DHCP server on, DNS offered = the AP),
 * initialises and starts esp_wifi in STA mode (RAM storage: the credentials live in cali_kv). 0 ok;
 * -1 when the WiFi driver is unavailable (CONFIG_CALI_WIFI=n, e.g. the QEMU image; or esp_wifi_init
 * / esp_wifi_start failed) — then nothing else here may be called. */
int cali_net_esp_init(void);

/* Delivers the queued events to the sink (at most the ones queued before this call started: a sink
 * that calls back in sees its outcome on a later poll) and runs the two watchdogs that make every
 * started operation end: a join with no outcome after 30 s -> STA_FAILED(OTHER); a scan with no
 * SCAN_DONE after 15 s -> an empty SCAN_DONE. Non-blocking. */
void cali_net_esp_poll(uint64_t now_ms);

#ifdef __cplusplus
}
#endif

#endif /* CALI_NET_ESP_H */
