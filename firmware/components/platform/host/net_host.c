/* net_host.c — cali_net.h for the host build: POSIX sockets on 127.0.0.1 + a scripted fake WiFi
 * (#154). The script format and the host-only entry points are in cali_net_host.h.
 *
 * Every WiFi operation only QUEUES its outcome; cali_net_host_poll(now_ms), called from the tick,
 * delivers it — the same shape as the ESP implementation, where WiFi events arrive on the event
 * task and are re-posted to the host task. A poll delivers only events queued before it started,
 * so a sink that calls back in (e.g. retries a failed join) sees the result on the next poll, and
 * a script can never spin a poll forever.
 */
#if defined(__APPLE__) && !defined(_DARWIN_C_SOURCE)
#define _DARWIN_C_SOURCE /* SO_NOSIGPIPE */
#endif
#if !defined(_GNU_SOURCE) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L
#endif

#include "cali_net_host.h"
#include "cali_platform.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0 /* macOS: SO_NOSIGPIPE on the socket instead */
#endif

#define JOIN_MAX 16
#define QUEUE_MAX 16
#define OK_AFTER_DEFAULT_IP 0xc0a80164u /* 192.168.1.100 */

typedef enum { JOIN_OK, JOIN_FAIL, JOIN_OK_AFTER } join_kind_t;

typedef struct {
    char ssid[NET_SSID_MAX + 1];
    join_kind_t kind;
    cali_net_reason_t reason; /* JOIN_FAIL */
    uint32_t ip;              /* JOIN_OK, JOIN_OK_AFTER */
    unsigned fails_left;      /* JOIN_OK_AFTER: attempts still to fail with OTHER */
} join_rule_t;

typedef struct {
    int used;
    uint32_t seq;
    int timed;          /* 0 = due at the next poll; 1 = due at due_ms */
    uint64_t due_ms;
    cali_net_ev_t ev;
    cali_net_reason_t reason;
    uint32_t ip;
    int rssi;           /* STA_GOT_IP: the joined network's rssi (for sta_rssi) */
} pending_t;

static cali_net_sink_t s_sink;
static void *s_sink_ctx;

static cali_net_ap_t s_aps[NET_SCAN_MAX];
static int s_naps;
static join_rule_t s_joins[JOIN_MAX];
static int s_njoins;
static uint64_t s_drop_after_ms; /* 0 = never drop */

static pending_t s_queue[QUEUE_MAX];
static uint32_t s_seq;

static int s_sta_up;
static int s_ap_up; /* an AP_STARTED was queued since the last ap_stop */
static int s_sta_rssi;

/* ---- the fake-WiFi script ---- */

static int parse_ip(const char *s, uint32_t *ip) {
    unsigned a, b, c, d;
    char tail;
    if (sscanf(s, "%u.%u.%u.%u%c", &a, &b, &c, &d, &tail) != 4 || a > 255 || b > 255 || c > 255 ||
        d > 255)
        return -1;
    *ip = (uint32_t)a << 24 | (uint32_t)b << 16 | (uint32_t)c << 8 | (uint32_t)d;
    return *ip != 0 ? 0 : -1;
}

static int parse_reason(const char *s, cali_net_reason_t *r) {
    if (strcmp(s, "not_found") == 0) *r = CALI_NET_REASON_NOT_FOUND;
    else if (strcmp(s, "auth") == 0) *r = CALI_NET_REASON_AUTH;
    else if (strcmp(s, "other") == 0) *r = CALI_NET_REASON_OTHER;
    else return -1;
    return 0;
}

/* Parse a whole decimal token (optional leading '-' when `neg_ok`) → 0 ok, -1 malformed. */
static int parse_long(const char *s, int neg_ok, long *out) {
    char *end;
    if (s[0] == '\0' || (s[0] == '-' && !neg_ok)) return -1;
    errno = 0;
    *out = strtol(s, &end, 10);
    return *end == '\0' && errno == 0 ? 0 : -1;
}

#define TOK_MAX 6 /* the longest rule, "join SSID ok-after N IP", has 5 tokens: 6 = one too many */

/* One line → 0 ok (a rule, a comment, or blank), -1 malformed. The line is split in place into
 * whitespace-separated tokens (no scanf field widths); every SSID token is checked against
 * NET_SSID_MAX from the generated net_consts.h, so a longer SSID is refused, never truncated. */
static int parse_line(char *line) {
    char *tok[TOK_MAX];
    int n = 0;
    for (char *p = strtok(line, " \t\r\n"); p != NULL; p = strtok(NULL, " \t\r\n")) {
        if (n == TOK_MAX) return -1;
        tok[n++] = p;
    }
    if (n == 0 || tok[0][0] == '#') return 0;

    if (strcmp(tok[0], "ap") == 0) {
        long rssi, secure;
        if (n != 4 || strlen(tok[1]) > NET_SSID_MAX || s_naps >= NET_SCAN_MAX ||
            parse_long(tok[2], 1, &rssi) != 0 || parse_long(tok[3], 0, &secure) != 0)
            return -1;
        strcpy(s_aps[s_naps].ssid, tok[1]);
        s_aps[s_naps].rssi = (int)rssi;
        s_aps[s_naps].secure = secure != 0;
        s_naps++;
        return 0;
    }
    if (strcmp(tok[0], "join") == 0) {
        join_rule_t *j = &s_joins[s_njoins];
        /* join SSID KIND ARG [IP]: the optional address only for ok-after */
        if (n < 4 || n > 5 || strlen(tok[1]) > NET_SSID_MAX || s_njoins >= JOIN_MAX) return -1;
        memset(j, 0, sizeof *j);
        strcpy(j->ssid, tok[1]);
        if (strcmp(tok[2], "ok") == 0 && n == 4) {
            j->kind = JOIN_OK;
            if (parse_ip(tok[3], &j->ip) != 0) return -1;
        } else if (strcmp(tok[2], "fail") == 0 && n == 4) {
            j->kind = JOIN_FAIL;
            if (parse_reason(tok[3], &j->reason) != 0) return -1;
        } else if (strcmp(tok[2], "ok-after") == 0) {
            long fails;
            if (parse_long(tok[3], 0, &fails) != 0 || fails > 1000000L) return -1;
            j->kind = JOIN_OK_AFTER;
            j->fails_left = (unsigned)fails;
            j->ip = OK_AFTER_DEFAULT_IP;
            if (n == 5 && parse_ip(tok[4], &j->ip) != 0) return -1;
        } else {
            return -1;
        }
        s_njoins++;
        return 0;
    }
    if (strcmp(tok[0], "drop-after") == 0) {
        long ms;
        if (n != 2 || parse_long(tok[1], 0, &ms) != 0 || ms == 0) return -1;
        s_drop_after_ms = (uint64_t)ms;
        return 0;
    }
    return -1;
}

int cali_net_host_init(const char *script) {
    char line[256];
    FILE *f;
    int lineno = 0, rc = 0;

    s_naps = s_njoins = 0;
    s_drop_after_ms = 0;
    memset(s_queue, 0, sizeof s_queue);
    s_sta_up = 0;
    s_sta_rssi = 0;
    s_ap_up = 0;
    if (script == NULL || (f = fopen(script, "r")) == NULL) return 0; /* R2: no networks */
    while (fgets(line, sizeof line, f)) {
        lineno++;
        if (parse_line(line) != 0) {
            cali_log("net: fake-wifi %s:%d: bad rule", script, lineno);
            rc = -1;
            break;
        }
    }
    fclose(f);
    return rc;
}

/* ---- the event queue ---- */

static pending_t *enqueue(cali_net_ev_t ev) {
    for (int i = 0; i < QUEUE_MAX; i++) {
        if (!s_queue[i].used) {
            memset(&s_queue[i], 0, sizeof s_queue[i]);
            s_queue[i].used = 1;
            s_queue[i].seq = s_seq++;
            s_queue[i].ev = ev;
            return &s_queue[i];
        }
    }
    return NULL;
}

static int queue_free(void) {
    int n = 0;
    for (int i = 0; i < QUEUE_MAX; i++) n += !s_queue[i].used;
    return n;
}

/* Forget every queued station outcome (a pending join result or a scheduled drop). */
static void cancel_sta(void) {
    for (int i = 0; i < QUEUE_MAX; i++) {
        cali_net_ev_t ev = s_queue[i].ev;
        if (s_queue[i].used &&
            (ev == CALI_NET_EV_STA_GOT_IP || ev == CALI_NET_EV_STA_FAILED || ev == CALI_NET_EV_STA_LOST))
            s_queue[i].used = 0;
    }
}

void cali_net_host_poll(uint64_t now_ms) {
    uint32_t limit = s_seq; /* events queued by the sink during this poll wait for the next one */
    for (;;) {
        pending_t *next = NULL;
        for (int i = 0; i < QUEUE_MAX; i++) {
            pending_t *p = &s_queue[i];
            if (!p->used || (uint32_t)(p->seq - limit) < 0x80000000u) continue; /* seq >= limit */
            if (p->timed && p->due_ms > now_ms) continue;
            if (next == NULL || (uint32_t)(p->seq - next->seq) >= 0x80000000u) next = p;
        }
        if (next == NULL) return;

        cali_net_event_t e;
        memset(&e, 0, sizeof e);
        e.ev = next->ev;
        e.reason = next->reason;
        e.ip = next->ip;
        next->used = 0;
        if (e.ev == CALI_NET_EV_STA_GOT_IP) {
            s_sta_up = 1;
            s_sta_rssi = next->rssi;
            if (s_drop_after_ms) {
                pending_t *drop = enqueue(CALI_NET_EV_STA_LOST);
                if (drop) {
                    drop->timed = 1;
                    drop->due_ms = now_ms + s_drop_after_ms;
                }
            }
        } else if (e.ev == CALI_NET_EV_STA_LOST) {
            s_sta_up = 0;
            s_sta_rssi = 0;
        } else if (e.ev == CALI_NET_EV_SCAN_DONE) {
            e.nscan = s_naps;
            e.scan = s_aps;
        }
        if (s_sink) s_sink(&e, s_sink_ctx);
    }
}

/* ---- WiFi operations ---- */

static int ssid_ok(const char *ssid) {
    size_t n = ssid ? strlen(ssid) : 0;
    return n >= 1 && n <= NET_SSID_MAX;
}

static int psk_ok(const char *psk) {
    size_t n = psk ? strlen(psk) : 0;
    return n == 0 || (n >= NET_PSK_MIN && n <= NET_PSK_MAX);
}

static void set_sink(cali_net_sink_t sink, void *ctx) {
    s_sink = sink;
    s_sink_ctx = ctx;
}

static int sta_stop(void) {
    cancel_sta();
    if (s_sta_up) {
        s_sta_up = 0;
        s_sta_rssi = 0;
        if (enqueue(CALI_NET_EV_STA_LOST) == NULL) return -1;
    }
    return 0;
}

static int sta_start(const char *ssid, const char *psk) {
    if (!ssid_ok(ssid) || !psk_ok(psk) || queue_free() < 2) return -1;
    sta_stop(); /* a new join replaces the old one: LOST first if it was up */

    pending_t *p = enqueue(CALI_NET_EV_STA_FAILED);
    p->reason = CALI_NET_REASON_NOT_FOUND;
    for (int i = 0; i < s_njoins; i++) {
        join_rule_t *j = &s_joins[i];
        if (strcmp(j->ssid, ssid) != 0) continue;
        if (j->kind == JOIN_FAIL) {
            p->reason = j->reason;
        } else if (j->kind == JOIN_OK_AFTER && j->fails_left > 0) {
            j->fails_left--;
            p->reason = CALI_NET_REASON_OTHER;
        } else {
            p->ev = CALI_NET_EV_STA_GOT_IP;
            p->reason = CALI_NET_REASON_NONE;
            p->ip = j->ip;
            for (int a = 0; a < s_naps; a++)
                if (strcmp(s_aps[a].ssid, ssid) == 0) p->rssi = s_aps[a].rssi;
        }
        break;
    }
    return 0;
}

static int ap_start(const char *ssid, const char *psk) {
    if (!ssid_ok(ssid) || !psk_ok(psk)) return -1;
    if (enqueue(CALI_NET_EV_AP_STARTED) == NULL) return -1;
    s_ap_up = 1;
    return 0;
}

static int ap_stop(void) {
    if (!s_ap_up) return 0; /* nothing to stop: no event */
    if (enqueue(CALI_NET_EV_AP_STOPPED) == NULL) return -1;
    s_ap_up = 0;
    return 0;
}

static int scan(void) {
    cali_log("net: fake scan");   /* tests count scans: each one costs the real hotspot's channel */
    return enqueue(CALI_NET_EV_SCAN_DONE) ? 0 : -1;
}

static int sta_rssi(void) {
    return s_sta_up ? s_sta_rssi : 0;
}

static int mdns_announce(const char *hostname, uint16_t port) {
    (void)hostname;
    (void)port;
    return 0;
}

/* ---- sockets: IPv4 on 127.0.0.1, non-blocking ---- */

static int would_block(void) {
    return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR;
}

static int open_bound(int type, uint16_t port) {
    struct sockaddr_in a;
    int one = 1;
    int fd = socket(AF_INET, type, 0);
    if (fd < 0) return -2;
    if (type == SOCK_STREAM) setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
#ifdef SO_NOSIGPIPE
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#endif
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) | O_NONBLOCK) != 0 ||
        fcntl(fd, F_SETFD, FD_CLOEXEC) != 0 || bind(fd, (struct sockaddr *)&a, sizeof a) != 0 ||
        (type == SOCK_STREAM && listen(fd, 4) != 0)) {
        close(fd);
        return -2;
    }
    return fd;
}

static int tcp_listen(uint16_t port) {
    return open_bound(SOCK_STREAM, port);
}

static int tcp_accept(int lfd) {
    int one = 1;
    int fd = accept(lfd, NULL, NULL);
    if (fd < 0) return would_block() || errno == ECONNABORTED ? -1 : -2;
#ifdef SO_NOSIGPIPE
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#else
    (void)one;
#endif
    if (fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) | O_NONBLOCK) != 0 ||
        fcntl(fd, F_SETFD, FD_CLOEXEC) != 0) {
        close(fd);
        return -1; /* this connection is lost; the listener is fine */
    }
    return fd;
}

static int tcp_recv(int fd, void *buf, size_t n) {
    ssize_t r;
    if (n == 0) return 0;
    r = recv(fd, buf, n, 0);
    if (r > 0) return (int)r;
    if (r == 0) return -2; /* orderly close */
    return would_block() ? -1 : -2;
}

static int tcp_send(int fd, const void *buf, size_t n) {
    ssize_t r;
    if (n == 0) return 0;
    r = send(fd, buf, n, MSG_NOSIGNAL);
    if (r >= 0) return (int)r;
    return would_block() ? -1 : -2;
}

static void tcp_close(int fd) {
    if (fd >= 0) close(fd);
}

static int udp_bind(uint16_t port) {
    return open_bound(SOCK_DGRAM, port);
}

static int udp_recvfrom(int fd, void *buf, size_t n, uint32_t *ip, uint16_t *port) {
    struct sockaddr_in a;
    socklen_t alen = sizeof a;
    ssize_t r = recvfrom(fd, buf, n, 0, (struct sockaddr *)&a, &alen);
    if (r < 0) return would_block() ? -1 : -2;
    if (ip) *ip = ntohl(a.sin_addr.s_addr);
    if (port) *port = ntohs(a.sin_port);
    return (int)r;
}

static int udp_sendto(int fd, const void *buf, size_t n, uint32_t ip, uint16_t port) {
    struct sockaddr_in a;
    ssize_t r;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(ip);
    r = sendto(fd, buf, n, MSG_NOSIGNAL, (struct sockaddr *)&a, sizeof a);
    if (r >= 0) return (int)r;
    return would_block() ? -1 : -2;
}

int cali_net_host_bound_port(int fd) {
    struct sockaddr_in a;
    socklen_t alen = sizeof a;
    if (getsockname(fd, (struct sockaddr *)&a, &alen) != 0 || a.sin_family != AF_INET) return -1;
    return ntohs(a.sin_port);
}

static const cali_net_t s_net = {
    .set_sink = set_sink,
    .sta_start = sta_start,
    .sta_stop = sta_stop,
    .ap_start = ap_start,
    .ap_stop = ap_stop,
    .scan = scan,
    .sta_rssi = sta_rssi,
    .mdns_announce = mdns_announce,
    .tcp_listen = tcp_listen,
    .tcp_accept = tcp_accept,
    .tcp_recv = tcp_recv,
    .tcp_send = tcp_send,
    .tcp_close = tcp_close,
    .udp_bind = udp_bind,
    .udp_recvfrom = udp_recvfrom,
    .udp_sendto = udp_sendto,
};

const cali_net_t *cali_net_host(void) {
    return &s_net;
}
