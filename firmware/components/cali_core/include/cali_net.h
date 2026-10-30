/**
 * @file cali_net.h
 * @brief Everything cali_core may ask of the network: WiFi station, AP and scan, mDNS, and
 *        non-blocking TCP and UDP sockets.
 *
 * cali_core (the WiFi state machine, the HTTP core, the captive DNS) never includes an ESP-IDF,
 * lwIP or POSIX socket header; it reaches the network only through the cali_net_t table.
 * Implementations: `platform/host/net_host.c` (POSIX sockets on 127.0.0.1 plus a scripted fake
 * WiFi, for the host build and CI) and `platform/esp/net_esp.c` (esp_wifi, lwIP and mdns).
 *
 * WiFi calls are async: a call returns 0 when the operation was started (-1 = bad arguments or not
 * startable; nothing will follow) and its outcome arrives later as an event through the sink
 * registered with `set_sink()`. Events are delivered only from the platform's poll on the one task
 * that runs the tick, never from inside the call that caused them, a signal, or another thread, so
 * a sink may call back into this table.
 *
 * Addresses are IPv4 in host byte order (192.168.4.1 = 0xc0a80401u = NET_AP_ADDR_U32); 0 = none.
 * Sockets are non-blocking: every socket call returns >= 0 on success (a descriptor or a byte
 * count), -1 when it would block or there is nothing yet, and -2 when the socket is closed by the
 * peer or failed (the caller then closes it). For n > 0, tcp_recv and tcp_send never return 0:
 * they return > 0 bytes, -1 or -2 (a POSIX recv() of 0, the peer's orderly close, is -2).
 */
#ifndef CALI_NET_H
#define CALI_NET_H

#include <stddef.h>
#include <stdint.h>

#include "net_consts.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief The kind of a network event. */
typedef enum {
    CALI_NET_EV_STA_GOT_IP,   /**< The station joined and has an address (`ip` set). */
    CALI_NET_EV_STA_LOST,     /**< The station link went down. */
    CALI_NET_EV_STA_FAILED,   /**< A join failed (`reason` set). */
    CALI_NET_EV_AP_STARTED,   /**< The setup hotspot is up. */
    CALI_NET_EV_AP_STOPPED,   /**< The setup hotspot is down. */
    CALI_NET_EV_SCAN_DONE     /**< A scan ended (`nscan` and `scan` set). */
} cali_net_ev_t;

/** @brief Why a station join failed. */
typedef enum {
    CALI_NET_REASON_NONE = 0,   /**< No reason (events other than STA_FAILED). */
    CALI_NET_REASON_NOT_FOUND,  /**< The network was not seen. */
    CALI_NET_REASON_AUTH,       /**< The password was refused. */
    CALI_NET_REASON_OTHER       /**< Any other failure. */
} cali_net_reason_t;

/** @brief One network from a scan. */
typedef struct {
    char ssid[NET_SSID_MAX + 1];  /**< The network name. */
    int rssi;                     /**< Signal strength in dBm. */
    int secure;                   /**< 1 = needs a password, 0 = open. */
} cali_net_ap_t;

/**
 * @brief One network event, as delivered to the sink.
 *
 * `ip` is set for STA_GOT_IP only. `nscan` and `scan` are set for SCAN_DONE only (at most
 * NET_SCAN_MAX entries); `scan` points into implementation memory valid only for the duration of
 * the sink call: copy it. `nscan` < 0 means the scan produced no list (refused or aborted, for
 * example by a station join, or timed out); 0 means it ran and saw no network. Every `scan()` that
 * returned 0 still ends in exactly one SCAN_DONE.
 */
typedef struct {
    cali_net_ev_t ev;            /**< The event kind. */
    cali_net_reason_t reason;    /**< STA_FAILED: why the join failed. */
    uint32_t ip;                 /**< STA_GOT_IP: the station address, host byte order. */
    int nscan;                   /**< SCAN_DONE: the number of entries in `scan`, or < 0. */
    const cali_net_ap_t *scan;   /**< SCAN_DONE: the networks found. */
} cali_net_event_t;

/**
 * @brief Receiver of network events.
 * @param e   The event; valid only during the call.
 * @param ctx The pointer passed to `set_sink()`.
 */
typedef void (*cali_net_sink_t)(const cali_net_event_t *e, void *ctx);

/**
 * @brief The network as cali_core sees it: WiFi operations and non-blocking sockets.
 *
 * The WiFi members return 0 when started and -1 when they cannot start. The socket members use the
 * return convention in the file description: -1 = would block or nothing yet, -2 = closed or failed.
 */
typedef struct {
    /** Register the event sink. @param sink The receiver. @param ctx Passed back with each event. */
    void (*set_sink)(cali_net_sink_t sink, void *ctx);
    /** Join a network as a station; GOT_IP or FAILED(reason) follows. */
    int  (*sta_start)(const char *ssid, const char *psk);
    /** Stop the station; STA_LOST follows if it was up. */
    int  (*sta_stop)(void);
    /** Start the WPA2 setup hotspot; AP_STARTED follows. */
    int  (*ap_start)(const char *ssid, const char *psk);
    /** Stop the setup hotspot. */
    int  (*ap_stop)(void);
    /** Scan for networks; SCAN_DONE with the list follows. */
    int  (*scan)(void);
    /** @return The station signal strength in dBm, or 0 when none. */
    int  (*sta_rssi)(void);
    /** Announce the device by mDNS as `hostname`.local with a service on `port`. The host build
     *  does nothing and returns 0. */
    int  (*mdns_announce)(const char *hostname, uint16_t port);
    /** Listen for TCP connections on `port`. @return A listening descriptor, or -1. */
    int  (*tcp_listen)(uint16_t port);
    /** Accept a connection on the listening descriptor `lfd`. @return A descriptor, -1 or -2. */
    int  (*tcp_accept)(int lfd);
    /** Receive up to `n` bytes. @return The byte count (> 0), -1 or -2. */
    int  (*tcp_recv)(int fd, void *buf, size_t n);
    /** Send up to `n` bytes. @return The byte count (> 0), -1 or -2. */
    int  (*tcp_send)(int fd, const void *buf, size_t n);
    /** Close a TCP descriptor. */
    void (*tcp_close)(int fd);
    /** Bind a UDP socket to `port`. @return A descriptor, or -1. */
    int  (*udp_bind)(uint16_t port);
    /** Receive one datagram of up to `n` bytes; the sender's address and port come back in `ip`
     *  and `port`. @return The byte count, -1 (nothing yet) or -2. */
    int  (*udp_recvfrom)(int fd, void *buf, size_t n, uint32_t *ip, uint16_t *port);
    /** Send one datagram to `ip`:`port`. @return The byte count, -1 or -2. */
    int  (*udp_sendto)(int fd, const void *buf, size_t n, uint32_t ip, uint16_t port);
} cali_net_t;

#ifdef __cplusplus
}
#endif

#endif /* CALI_NET_H */
