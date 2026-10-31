/**
 * @file cali_captive.h
 * @brief Captive-portal DNS responder and the OS "is this a captive portal" probe-path table.
 *
 * A setup hotspot has no real internet behind it, so its DNS lies on purpose: every A-record query
 * gets answered with the hotspot's own address, so any host a client tries to resolve (including
 * the fixed probe host an OS uses right after joining a network to decide whether it is behind a
 * captive portal) lands on the web UI, on its WiFi-setup screen, instead of failing to resolve.
 * Driven from the same tick as `cali_http_poll()`: each cali_captive_dns_poll() call drains the
 * datagrams already waiting on the UDP socket and never blocks.
 *
 * cali_captive_is_probe() is the table the HTTP handler consults to recognise the fixed set of paths
 * Android, iOS, macOS, Windows and Firefox request for that same captive-portal check, so the web
 * handler can redirect those to the UI instead of a generic 404.
 *
 * C99, no malloc, no ESP-IDF or POSIX headers: it reaches the network only through cali_net_t.
 */
#ifndef CALI_CAPTIVE_H
#define CALI_CAPTIVE_H

#include <stdint.h>

#include "cali_net.h"

#ifdef __cplusplus
extern "C" {
#endif

/** The UDP port the DNS responder binds. */
#define CALI_CAPTIVE_DNS_PORT 53u

/**
 * @brief Bind UDP port 53 and start answering DNS queries.
 *
 * A start while a socket is open closes the previous socket first. Every A-record (QTYPE 1) query
 * with QCLASS IN gets answered with `answer_ip`, TTL 0, RCODE 0. Any other QTYPE gets an empty but
 * still successful answer (RCODE 0, ANCOUNT 0), never NXDOMAIN, so a client that asks for a record
 * type the responder does not answer does not decide the network is broken.
 *
 * @param net       The network table; its UDP calls are used.
 * @param answer_ip The address to answer with, IPv4 in host byte order (e.g. NET_AP_ADDR_U32).
 * @return 0 on success; -1 when `udp_bind` failed, and cali_captive_dns_poll() stays a no-op.
 */
int cali_captive_dns_start(const cali_net_t *net, uint32_t answer_ip);

/**
 * @brief Answer the DNS datagrams already waiting; never blocks.
 *
 * Reads with `udp_recvfrom` until it reports nothing more or an error, at most 16 datagrams per
 * call so a flood cannot starve the tick; the rest wait for the next poll. A malformed or truncated
 * query is dropped silently, with no reply: one shorter than the 12-byte header, with QDCOUNT other
 * than 1, with a name whose labels run past the datagram or whose wire-format length exceeds 253
 * bytes, or a message with QR already set (a response, not a query). A no-op before
 * cali_captive_dns_start() succeeds.
 */
void cali_captive_dns_poll(void);

/** @brief Close the UDP socket, if one is bound. Safe to call when not started or already stopped. */
void cali_captive_dns_stop(void);

/**
 * @brief Tell whether a request path is an OS captive-portal probe.
 *
 * The probe targets are `/generate_204` and `/gen_204` (Android), `/hotspot-detect.html` and
 * `/library/test/success.html` (Apple), `/connecttest.txt` and `/ncsi.txt` (Windows), and
 * `/canonical.html` and `/success.txt` (Firefox). The match is exact.
 *
 * @param path The request target without its query string; callers strip that.
 * @return 1 when `path` is a probe target, else 0.
 */
int cali_captive_is_probe(const char *path);

#ifdef __cplusplus
}
#endif

#endif /* CALI_CAPTIVE_H */
