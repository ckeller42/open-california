/* captive_dns.c — captive-portal DNS responder over cali_net UDP sockets; see cali_captive.h. */
#include "cali_captive.h"

#include <string.h>

#define MAX_NAME_WIRE 253u /* wire-format bytes (length octets + labels + the terminating 0) */
#define RECV_MAX 512u      /* a query this large has no business on a captive portal; drop it */
#define REPLY_MAX 512u
#define DRAIN_MAX 16 /* datagrams answered per cali_captive_dns_poll() */
#define ANSWER_RR_LEN 16u  /* pointer(2) + TYPE(2) + CLASS(2) + TTL(4) + RDLENGTH(2) + RDATA(4) */

static struct {
    const cali_net_t *net;
    int fd; /* -1: not bound */
    uint32_t answer_ip;
} S = {.fd = -1};

/* The fixed OS captive-portal probe targets (Android, Apple, Windows, Firefox); see cali_captive.h.
 * A static table, not a switch, so Task 6 can walk it too (e.g. to document it on the setup page). */
static const char *const PROBE_PATHS[] = {
    "/generate_204",       "/gen_204",           "/hotspot-detect.html", "/library/test/success.html",
    "/connecttest.txt",    "/ncsi.txt",          "/canonical.html",      "/success.txt",
};
#define PROBE_PATHS_N (sizeof PROBE_PATHS / sizeof PROBE_PATHS[0])

int cali_captive_is_probe(const char *path) {
    size_t i;
    for (i = 0; i < PROBE_PATHS_N; i++)
        if (strcmp(path, PROBE_PATHS[i]) == 0) return 1;
    return 0;
}

/* Walks the QNAME starting at buf[*pos], within buf[0..len); advances *pos past it (including the
 * terminating 0-length label). 0 ok; -1 = a label's length runs past the datagram, a compression
 * pointer (not valid in a query name), or the wire-format name exceeds MAX_NAME_WIRE bytes — every
 * case the caller must treat as a malformed query to drop. */
static int skip_qname(const uint8_t *buf, size_t len, size_t *pos) {
    size_t i = *pos;
    size_t wire = 0;
    for (;;) {
        uint8_t label_len;
        if (i >= len) return -1;
        label_len = buf[i];
        if (label_len & 0xC0u) return -1; /* a compression pointer: never valid in a query's QNAME */
        i += 1;
        wire += 1;
        if (label_len == 0) break;
        if (i + label_len > len) return -1;
        i += label_len;
        wire += label_len;
        if (wire > MAX_NAME_WIRE) return -1;
    }
    *pos = i;
    return 0;
}

/* Builds the reply for the query buf[0..len) into out (capacity REPLY_MAX); returns its length, or
 * -1 to drop the query silently (malformed, a response rather than a query, or a reply that would
 * not fit REPLY_MAX). */
static int build_reply(const uint8_t *buf, size_t len, uint8_t *out) {
    size_t qname_start = 12, qname_end, qsec_len, reply_len;
    uint16_t flags, qdcount, qtype, qclass, rflags;
    int answer;

    if (len < 12) return -1;
    flags = (uint16_t)((buf[2] << 8) | buf[3]);
    if (flags & 0x8000u) return -1; /* QR=1: a response, not a query — ignore it */
    qdcount = (uint16_t)((buf[4] << 8) | buf[5]);
    if (qdcount != 1) return -1;

    qname_end = qname_start;
    if (skip_qname(buf, len, &qname_end) != 0) return -1;
    if (qname_end + 4 > len) return -1; /* QTYPE(2) + QCLASS(2) must follow the name */
    qtype = (uint16_t)((buf[qname_end] << 8) | buf[qname_end + 1]);
    qclass = (uint16_t)((buf[qname_end + 2] << 8) | buf[qname_end + 3]);
    qsec_len = qname_end + 4 - qname_start; /* QNAME + QTYPE + QCLASS, echoed verbatim */

    answer = (qtype == 1 && qclass == 1); /* A/IN: answer it; anything else: empty (still RCODE 0) */
    reply_len = 12 + qsec_len + (answer ? ANSWER_RR_LEN : 0);
    if (reply_len > REPLY_MAX) return -1;

    out[0] = buf[0];
    out[1] = buf[1]; /* ID, echoed */
    rflags = 0x8000u                    /* QR=1 (response) */
             | (uint16_t)(flags & 0x7800u) /* OPCODE, bits 14-11, kept from the query */
             | 0x0400u                    /* AA=1 */
             | (uint16_t)(flags & 0x0100u); /* RD, bit 8, kept from the query; RA/TC/Z/RCODE all 0 */
    out[2] = (uint8_t)(rflags >> 8);
    out[3] = (uint8_t)rflags;
    out[4] = 0;
    out[5] = 1; /* QDCOUNT = 1 */
    out[6] = 0;
    out[7] = (uint8_t)(answer ? 1 : 0); /* ANCOUNT */
    out[8] = out[9] = out[10] = out[11] = 0; /* NSCOUNT, ARCOUNT = 0 */
    memcpy(out + 12, buf + qname_start, qsec_len);
    if (answer) {
        uint8_t *a = out + 12 + qsec_len;
        a[0] = 0xC0;
        a[1] = 0x0C; /* NAME: a compression pointer back to the question name at offset 12 */
        a[2] = 0;
        a[3] = 1; /* TYPE = A */
        a[4] = 0;
        a[5] = 1; /* CLASS = IN */
        a[6] = a[7] = a[8] = a[9] = 0; /* TTL = 0 */
        a[10] = 0;
        a[11] = 4; /* RDLENGTH = 4 */
        a[12] = (uint8_t)(S.answer_ip >> 24);
        a[13] = (uint8_t)(S.answer_ip >> 16);
        a[14] = (uint8_t)(S.answer_ip >> 8);
        a[15] = (uint8_t)S.answer_ip;
    }
    return (int)reply_len;
}

void cali_captive_dns_stop(void) {
    if (S.fd >= 0) S.net->tcp_close(S.fd); /* cali_net has one generic fd-close, shared by tcp/udp */
    S.fd = -1;
}

int cali_captive_dns_start(const cali_net_t *net, uint32_t answer_ip) {
    int fd;
    cali_captive_dns_stop(); /* a re-start drops the old socket (if any) first */
    S.net = net;
    S.answer_ip = answer_ip;
    fd = net->udp_bind(CALI_CAPTIVE_DNS_PORT);
    S.fd = fd >= 0 ? fd : -1;
    return S.fd >= 0 ? 0 : -1;
}

void cali_captive_dns_poll(void) {
    uint8_t buf[RECV_MAX];
    uint8_t reply[REPLY_MAX];

    if (S.fd < 0) return;
    /* at most DRAIN_MAX datagrams per poll: a flood cannot starve the rest of the tick */
    for (int k = 0; k < DRAIN_MAX; k++) {
        uint32_t from_ip;
        uint16_t from_port;
        int n = S.net->udp_recvfrom(S.fd, buf, sizeof buf, &from_ip, &from_port);
        int reply_len;
        if (n <= 0) break; /* -1 nothing more this poll, -2 error: either way, stop draining */
        reply_len = build_reply(buf, (size_t)n, reply);
        if (reply_len > 0) S.net->udp_sendto(S.fd, reply, (size_t)reply_len, from_ip, from_port);
    }
}
