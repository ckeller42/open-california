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

/* One non-comment line → 0 ok, -1 malformed. The SSID buffers hold NET_SSID_MAX + 1 characters so
 * an over-long SSID is caught (sscanf stops one past the limit) instead of silently truncated. */
static int parse_line(const char *line) {
    char word[16], ssid[NET_SSID_MAX + 2], a1[24], a2[24], extra[2];
    int n = sscanf(line, "%15s", word);
    if (n != 1 || word[0] == '#') return 0;

    if (strcmp(word, "ap") == 0) {
        int rssi, secure;
        if (sscanf(line, "%*s %33s %d %d %1s", ssid, &rssi, &secure, extra) != 3) return -1;
        if (strlen(ssid) > NET_SSID_MAX || s_naps >= NET_SCAN_MAX) return -1;
        strcpy(s_aps[s_naps].ssid, ssid);
        s_aps[s_naps].rssi = rssi;
        s_aps[s_naps].secure = secure != 0;
        s_naps++;
        return 0;
    }
    if (strcmp(word, "join") == 0) {
        join_rule_t *j = &s_joins[s_njoins];
        char ip[24] = "";
        /* ssid, kind, argument, and the optional ok-after address; anything more is malformed */
        int k = sscanf(line, "%*s %33s %23s %23s %23s %1s", ssid, a1, a2, ip, extra);
        if (k < 3 || k > 4 || strlen(ssid) > NET_SSID_MAX || s_njoins >= JOIN_MAX) return -1;
        memset(j, 0, sizeof *j);
        strcpy(j->ssid, ssid);
        if (strcmp(a1, "ok") == 0 && k == 3) {
            j->kind = JOIN_OK;
            if (parse_ip(a2, &j->ip) != 0) return -1;
        } else if (strcmp(a1, "fail") == 0 && k == 3) {
            j->kind = JOIN_FAIL;
            if (parse_reason(a2, &j->reason) != 0) return -1;
        } else if (strcmp(a1, "ok-after") == 0) {
            unsigned long fails;
            char *end;
            errno = 0;
            fails = strtoul(a2, &end, 10);
            if (*end != '\0' || a2[0] == '-' || errno != 0 || fails > 1000000ul) return -1;
            j->kind = JOIN_OK_AFTER;
            j->fails_left = (unsigned)fails;
            j->ip = OK_AFTER_DEFAULT_IP;
            if (k == 4 && parse_ip(ip, &j->ip) != 0) return -1;
        } else {
            return -1;
        }
        s_njoins++;
        return 0;
    }
    if (strcmp(word, "drop-after") == 0) {
        unsigned long ms;
        if (sscanf(line, "%*s %lu %1s", &ms, extra) != 1 || ms == 0) return -1;
        s_drop_after_ms = ms;
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
