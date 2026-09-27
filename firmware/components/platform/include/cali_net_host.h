/* cali_net_host.h — the host build's cali_net (platform/host/net_host.c): POSIX sockets bound to
 * 127.0.0.1 and a scripted fake WiFi, so the WiFi state machine and the setup flow run in CI
 * without radios (#154). Host build and tests only; the ESP build never sees this header.
 *
 * Fake-WiFi script (host_main --fake-wifi PATH), one rule per line, '#' comments and blank lines
 * ignored; an SSID is one whitespace-free token of 1..NET_SSID_MAX characters:
 *   ap <ssid> <rssi> <secure>          a visible network (scan lists them in file order, at most
 *                                      NET_SCAN_MAX)
 *   join <ssid> ok <a.b.c.d>           sta_start(ssid) → STA_GOT_IP with that address
 *   join <ssid> fail <not_found|auth|other>   sta_start(ssid) → STA_FAILED(reason)
 *   join <ssid> ok-after <n> [a.b.c.d] the first n sta_start(ssid) fail with OTHER, then GOT_IP
 *                                      (address default 192.168.1.100)
 *   drop-after <ms>                    STA_LOST <ms> after each STA_GOT_IP delivery
 * An SSID without a join rule fails NOT_FOUND. The PSK is validated (empty = open, else
 * NET_PSK_MIN..NET_PSK_MAX characters) but otherwise ignored — the rule decides.
 */
#ifndef CALI_NET_HOST_H
#define CALI_NET_HOST_H

#include <stdint.h>

#include "cali_net.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Reset all fake-WiFi state and load `script` (NULL, a missing file, or an empty file = no networks
 * visible, every join fails NOT_FOUND — ruling R2). Returns 0 ok, -1 on a malformed line (logged
 * through cali_log with its line number). Sockets are untouched. */
int cali_net_host_init(const char *script);

/* The operations table. Valid before and after cali_net_host_init(). */
const cali_net_t *cali_net_host(void);

/* Deliver every due event to the sink, in order. Call from the tick with the current uptime (a
 * non-decreasing clock); event timing (drop-after) is measured on it. Events that the sink's own
 * calls queue wait for the next poll. */
void cali_net_host_poll(uint64_t now_ms);

/* The local port a socket from tcp_listen/udp_bind is bound to (for port 0 = "any free"), or -1. */
int cali_net_host_bound_port(int fd);

#ifdef __cplusplus
}
#endif

#endif /* CALI_NET_HOST_H */
