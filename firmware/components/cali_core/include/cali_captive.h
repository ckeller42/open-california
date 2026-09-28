/* cali_captive.h — captive-portal DNS responder + OS "is this a captive portal" probe-path table,
 * over cali_net UDP sockets (#154).
 *
 * A setup hotspot has no real internet behind it, so its DNS lies on purpose: every A-record query
 * gets answered with the hotspot's own address, so any host a client tries to resolve — including
 * the fixed probe host an OS uses right after joining a network to decide whether it is behind a
 * captive portal — lands on calictl's setup page instead of failing to resolve. Driven from the
 * same tick as cali_http_poll(): each cali_captive_dns_poll() call drains every datagram already
 * waiting on the UDP socket (loops on udp_recvfrom until it says "nothing more"/"error"), answers
 * each in turn, and never blocks.
 *
 * cali_captive_is_probe() is the table the HTTP handler (Task 6) consults to recognise the fixed
 * set of paths Android/iOS/macOS/Windows/Firefox request for that same captive-portal check, so the
 * setup page can answer those specially instead of a generic 404.
 *
 * C99, no malloc, no ESP-IDF/POSIX headers: reaches the network only through cali_net_t. Driven in
 * tests/firmware/test_captive_dns.py through test/captive_cli.c.
 */
#ifndef CALI_CAPTIVE_H
#define CALI_CAPTIVE_H

#include <stdint.h>

#include "cali_net.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_CAPTIVE_DNS_PORT 53u

/* Binds UDP port 53: 0 ok; -1 udp_bind failed, and cali_captive_dns_poll stays a no-op. A re-start
 * closes the previous socket first (if one was open). Every A-record (QTYPE 1) query with QCLASS IN
 * gets answered with answer_ip (host byte order, e.g. NET_AP_ADDR_U32), TTL 0, RCODE 0; any other
 * QTYPE gets an empty but still-successful answer (RCODE 0, ANCOUNT 0) — never NXDOMAIN, so a client
 * that asks for a record type we don't answer doesn't decide the network is broken. */
int cali_captive_dns_start(const cali_net_t *net, uint32_t answer_ip);

/* Drains every datagram already waiting (loops on udp_recvfrom until -1/-2), answering each in
 * turn; never blocks. A malformed or truncated query — shorter than the 12-byte header, QDCOUNT !=
 * 1, a name whose labels run past the datagram or whose wire-format length exceeds 253 bytes — or a
 * message with QR already set (a response, not a query) is dropped silently: no reply sent. A
 * no-op before cali_captive_dns_start succeeds. */
void cali_captive_dns_poll(void);

/* Closes the UDP socket, if one is bound; safe to call when not started (or already stopped). */
void cali_captive_dns_stop(void);

/* 1 when path — the request target without its query string; callers strip that — is one of the
 * fixed OS captive-portal probe targets: /generate_204, /gen_204 (Android), /hotspot-detect.html,
 * /library/test/success.html (Apple), /connecttest.txt, /ncsi.txt (Windows), /canonical.html,
 * /success.txt (Firefox); 0 otherwise. Exact match only. */
int cali_captive_is_probe(const char *path);

#ifdef __cplusplus
}
#endif

#endif /* CALI_CAPTIVE_H */
