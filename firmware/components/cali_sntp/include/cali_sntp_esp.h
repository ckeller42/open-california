/* cali_sntp_esp.h — the ESP side of the log clock (R_FW_TIME): SNTP via esp_netif_sntp, servers
 * pool.ntp.org plus the one the DHCP lease offers (option 42), restarted on every station
 * IP_EVENT_STA_GOT_IP, i.e. whenever the WiFi station is up. ESP only; cali_core never includes this. */
#ifndef CALI_SNTP_ESP_H
#define CALI_SNTP_ESP_H

/* Owner task, once the WiFi driver runs (after esp_netif_init + the default event loop): starts
 * SNTP and hands cali_time_source the system clock. A failure is logged; ts then stays null. */
void cali_sntp_esp_start(void);

#endif /* CALI_SNTP_ESP_H */
