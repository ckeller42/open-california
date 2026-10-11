/* sntp_esp.c — esp_netif_sntp behind cali_time (R_FW_TIME). Contract: include/cali_sntp_esp.h. */
#include "cali_sntp_esp.h"

#include <sys/time.h>

#include "esp_event.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"

#include "cali_platform.h"
#include "cali_time.h"

#define NTP_POOL "pool.ntp.org"

static int64_t epoch_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

/* lwIP's tcpip task, after every sync (first, then every CONFIG_LWIP_SNTP_UPDATE_DELAY). */
static void on_sync(struct timeval *tv) {
    static int s_logged;
    (void)tv;
    cali_time_synced();
    if (!s_logged) {
        s_logged = 1;
        cali_log("time: sntp synced");
    }
}

/* A fresh lease: ask now instead of waiting out the retry backoff from the offline time. */
static void on_got_ip(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg, (void)base, (void)id, (void)data;
    (void)esp_netif_sntp_start();
}

void cali_sntp_esp_start(void) {
    /* Index 0 is the DHCP-offered server (lwIP writes option 42 there, dropping what was set);
     * renew_servers_after_new_IP puts the pool back at index 1 on every lease
     * (CONFIG_LWIP_SNTP_MAX_SERVERS=2, CONFIG_LWIP_DHCP_GET_NTP_SRV=y: sdkconfig.defaults). */
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG(NTP_POOL);
    cfg.server_from_dhcp = true;
    cfg.renew_servers_after_new_IP = true;
    cfg.index_of_first_server = 1;
    cfg.ip_event_to_renew = IP_EVENT_STA_GOT_IP;
    cfg.wait_for_sync = false;
    cfg.sync_cb = on_sync;
    esp_err_t e = esp_netif_sntp_init(&cfg);
    if (e != ESP_OK) {
        cali_log("time: sntp: %s", esp_err_to_name(e));
        return;
    }
    cali_time_source(epoch_ms);
    e = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_got_ip, NULL);
    if (e != ESP_OK) cali_log("time: sntp restart on new lease: %s", esp_err_to_name(e));   /* still polls */
}
