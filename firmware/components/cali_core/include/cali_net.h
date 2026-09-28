/* cali_net.h — everything cali_core may ask of the network: WiFi station/AP/scan, mDNS, and
 * non-blocking TCP/UDP sockets (#154).
 *
 * cali_core (the WiFi state machine, the HTTP core, the captive DNS) never includes an ESP-IDF,
 * lwIP or POSIX socket header; it reaches the network only through this table. Implementations:
 * platform/host/net_host.c (POSIX sockets on 127.0.0.1 + a scripted fake WiFi, for the host build
 * and CI) and, from Task 9, platform/esp/net_esp.c (esp_wifi + lwIP + mdns).
 *
 * WiFi calls are async: a call returns 0 when the operation was started (-1 = bad arguments or not
 * startable; nothing will follow) and its outcome arrives later as an event through the sink
 * registered with set_sink(). Events are delivered only from the platform's poll on the one task
 * that runs the tick — never from inside the call that caused them, a signal, or another thread —
 * so a sink may call back into this table.
 *
 * Addresses are IPv4 in host byte order (192.168.4.1 = 0xc0a80401u = NET_AP_ADDR_U32); 0 = none.
 * Sockets are non-blocking: every socket call returns >= 0 on success (a descriptor or a byte
 * count), -1 when it would block / there is nothing yet, -2 when the socket is closed by the peer or
 * failed (the caller then closes it). For n > 0, tcp_recv and tcp_send never return 0: > 0 bytes,
 * -1 or -2 (a POSIX recv() of 0, the peer's orderly close, is -2).
 */
#ifndef CALI_NET_H
#define CALI_NET_H

#include <stddef.h>
#include <stdint.h>

#include "net_consts.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { CALI_NET_EV_STA_GOT_IP, CALI_NET_EV_STA_LOST, CALI_NET_EV_STA_FAILED,   /* FAILED carries a reason */
               CALI_NET_EV_AP_STARTED, CALI_NET_EV_AP_STOPPED, CALI_NET_EV_SCAN_DONE } cali_net_ev_t;
typedef enum { CALI_NET_REASON_NONE = 0, CALI_NET_REASON_NOT_FOUND, CALI_NET_REASON_AUTH, CALI_NET_REASON_OTHER } cali_net_reason_t;
typedef struct { char ssid[NET_SSID_MAX + 1]; int rssi; int secure; } cali_net_ap_t;
/* ip: STA_GOT_IP only. nscan/scan: SCAN_DONE only (at most NET_SCAN_MAX entries); scan points into
 * implementation memory valid only for the duration of the sink call: copy it. */
typedef struct { cali_net_ev_t ev; cali_net_reason_t reason; uint32_t ip; int nscan; const cali_net_ap_t *scan; } cali_net_event_t;
typedef void (*cali_net_sink_t)(const cali_net_event_t *e, void *ctx);
typedef struct {
    void (*set_sink)(cali_net_sink_t sink, void *ctx);
    int  (*sta_start)(const char *ssid, const char *psk);   /* → GOT_IP or FAILED(reason) */
    int  (*sta_stop)(void);                                 /* → STA_LOST if it was up */
    int  (*ap_start)(const char *ssid, const char *psk);    /* → AP_STARTED */
    int  (*ap_stop)(void);
    int  (*scan)(void);                                     /* → SCAN_DONE with the list */
    int  (*sta_rssi)(void);                                 /* dBm or 0 */
    int  (*mdns_announce)(const char *hostname, uint16_t port);  /* host: no-op returning 0 */
    /* sockets (non-blocking; -1 = would block/none; -2 = closed/error; recv/send never 0 for n > 0) */
    int  (*tcp_listen)(uint16_t port);            int (*tcp_accept)(int lfd);
    int  (*tcp_recv)(int fd, void *buf, size_t n); int (*tcp_send)(int fd, const void *buf, size_t n);
    void (*tcp_close)(int fd);
    int  (*udp_bind)(uint16_t port);              int (*udp_recvfrom)(int fd, void *buf, size_t n, uint32_t *ip, uint16_t *port);
    int  (*udp_sendto)(int fd, const void *buf, size_t n, uint32_t ip, uint16_t port);
} cali_net_t;

#ifdef __cplusplus
}
#endif

#endif /* CALI_NET_H */
