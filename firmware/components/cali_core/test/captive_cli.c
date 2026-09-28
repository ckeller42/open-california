/* captive_cli.c — line-protocol driver for tests/firmware/test_captive_dns.py ONLY; not part of the
 * ESP build.
 *
 * A cali_net_t whose UDP ops replay a script of datagrams, all "received" from one fixed fake
 * source address, and record every reply the core sends back to it.
 *
 * Reads lines from stdin, executed in order:
 *   dgram <hex>   queue one UDP datagram (hex-encoded bytes)
 *   poll          call cali_captive_dns_poll() once. Per the core's own contract this drains every
 *                 datagram queued so far in one call, so this line prints one output line per
 *                 queued-but-not-yet-reported datagram, in order: "REPLY <hex>" (the bytes the core
 *                 sent back) or "DROP" (no reply sent for it)
 *   stop          call cali_captive_dns_stop()
 *   probe <path>  call cali_captive_is_probe(path); prints "PROBE 0" or "PROBE 1"
 * Argument "nobind": udp_bind fails, so cali_captive_dns_start returns -1 and poll stays a no-op —
 * queued datagrams are then never even handed to the core (no recvfrom call happens at all).
 * cali_captive_dns_start's return value goes to stderr as "init=<rc>".
 *
 * A datagram's outcome (REPLY/DROP) can only be known once the core asks for the *next* one (or
 * gives up for this poll) — the core never tells the net layer "I dropped that" directly, only
 * "did you want to send something back". So this driver finalises datagram i's line lazily, from
 * inside the recvfrom call that hands out datagram i+1 (or that finds nothing left): whatever
 * udp_sendto recorded since the last finalisation is that verdict.
 */
#include <stdio.h>
#include <string.h>

#include "cali_captive.h"
#include "net_consts.h"

#define FD 5
#define MAX_DGRAMS 32
#define DGRAM_MAX 600
#define REPLY_MAX_HEX (DGRAM_MAX * 2 + 1)

static uint8_t dgrams[MAX_DGRAMS][DGRAM_MAX];
static size_t dgram_len[MAX_DGRAMS];
static size_t nqueued, head;
static int no_bind;

static const uint32_t SRC_IP = 0x0a000005u; /* 10.0.0.5: arbitrary, fixed for every datagram */
static const uint16_t SRC_PORT = 34567;

static int have_unfinalized; /* a datagram was handed to the core; its outcome isn't printed yet */
static int have_sent;        /* the core called udp_sendto for that datagram */
static char sent_hex[REPLY_MAX_HEX];

static int f_bind(uint16_t port) {
    (void)port;
    return no_bind ? -2 : FD;
}

/* Prints the pending datagram's verdict (if any) and clears it for the next one. */
static void finalize(void) {
    if (!have_unfinalized) return;
    if (have_sent) printf("REPLY %s\n", sent_hex);
    else printf("DROP\n");
    have_unfinalized = 0;
    have_sent = 0;
}

static int f_recvfrom(int fd, void *buf, size_t n, uint32_t *ip, uint16_t *port) {
    size_t take;
    if (fd != FD) return -2;
    finalize(); /* whatever came out of the PREVIOUS datagram is decided by now */
    if (head == nqueued) return -1; /* nothing queued: "nothing more this poll" */
    take = dgram_len[head];
    if (take > n) return -2; /* a script bug: the datagram doesn't fit the core's receive buffer */
    memcpy(buf, dgrams[head], take);
    if (ip) *ip = SRC_IP;
    if (port) *port = SRC_PORT;
    head++;
    have_unfinalized = 1;
    return (int)take;
}

static int f_sendto(int fd, const void *buf, size_t n, uint32_t ip, uint16_t port) {
    size_t i;
    char *p = sent_hex;
    if (fd != FD || ip != SRC_IP || port != SRC_PORT || n * 2 >= sizeof sent_hex) return -2;
    have_sent = 1;
    for (i = 0; i < n; i++, p += 2) sprintf(p, "%02x", ((const uint8_t *)buf)[i]);
    *p = '\0';
    return (int)n;
}

static void f_close(int fd) {
    (void)fd; /* nothing to release: FD is just a fixed script tag, not a real descriptor */
}

static const cali_net_t fake_net = {
    .udp_bind = f_bind,
    .udp_recvfrom = f_recvfrom,
    .udp_sendto = f_sendto,
    .tcp_close = f_close,
};

static size_t hex_decode(const char *s, uint8_t *out, size_t cap) {
    size_t n = 0;
    unsigned v;
    while (n < cap && sscanf(s, "%2x", &v) == 1) {
        out[n++] = (uint8_t)v;
        s += 2;
    }
    return n;
}

int main(int argc, char **argv) {
    static char line[4096];

    no_bind = argc > 1 && strcmp(argv[1], "nobind") == 0;
    fprintf(stderr, "init=%d\n", cali_captive_dns_start(&fake_net, NET_AP_ADDR_U32));
    while (fgets(line, sizeof line, stdin)) {
        char arg[600];
        if (strncmp(line, "dgram ", 6) == 0 && nqueued < MAX_DGRAMS) {
            dgram_len[nqueued] = hex_decode(line + 6, dgrams[nqueued], DGRAM_MAX);
            nqueued++;
        } else if (strncmp(line, "poll", 4) == 0) {
            cali_captive_dns_poll();
            finalize(); /* belt and braces: the last datagram of this poll is normally finalised by
                         * the drain loop's own final (empty) recvfrom call, above */
        } else if (strncmp(line, "stop", 4) == 0) {
            cali_captive_dns_stop();
        } else if (sscanf(line, "probe %599s", arg) == 1) {
            printf("PROBE %d\n", cali_captive_is_probe(arg));
        }
    }
    cali_captive_dns_stop();
    return 0;
}
