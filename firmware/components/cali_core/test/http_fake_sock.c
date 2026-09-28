/* http_fake_sock.c — line-protocol driver for tests/firmware/test_http_core.py ONLY; not part of the
 * ESP build.
 *
 * A cali_net_t whose tcp ops run over an in-memory script, plus a trivial handler:
 * GET /hello -> 200 text/plain "hi"; POST /echo -> 200 text/plain <request body>; else unhandled.
 *
 * Reads lines from stdin, executed in order:
 *   conn          the next tcp_accept returns a connection (once)
 *   frag <text>   queue a fragment the peer sends (escapes: \r \n \0 \\); tcp_recv hands out at most
 *                 one fragment per poll, then -1 (a fragment larger than the read is split)
 *   eof           after the queued fragments, tcp_recv returns -2 (peer closed)
 *   tick <ms>     advance the clock by <ms> and call cali_http_poll(now)
 * tcp_send accepts at most SEND_CHUNK bytes per call (exercises partial sends) and writes them to
 * stdout raw; tcp_close prints "<closed>". At end of input it prints "<stopped>" and calls
 * cali_http_stop(), so a "<closed>" before "<stopped>" is the core's own close.
 */
#include <stdio.h>
#include <string.h>

#include "cali_http.h"

#define LFD 3
#define CFD 7
#define SEND_CHUNK 64
#define MAX_FRAGS 64

static char frags[MAX_FRAGS][4096];
static size_t frag_len[MAX_FRAGS], nfrags, head, head_off;
static int pending_conn, eof_queued, recv_this_poll;

static int f_listen(uint16_t port) {
    (void)port;
    return LFD;
}

static int f_accept(int lfd) {
    if (lfd != LFD || !pending_conn) return -1;
    pending_conn = 0;
    return CFD;
}

static int f_recv(int fd, void *buf, size_t n) {
    size_t k;
    if (fd != CFD) return -2;
    if (head == nfrags) return eof_queued ? -2 : -1;
    if (recv_this_poll) return -1;
    recv_this_poll = 1;
    k = frag_len[head] - head_off;
    if (k > n) k = n;
    memcpy(buf, frags[head] + head_off, k);
    head_off += k;
    if (head_off == frag_len[head]) {
        head++;
        head_off = 0;
    }
    return (int)k;
}

static int f_send(int fd, const void *buf, size_t n) {
    if (fd != CFD) return -2;
    if (n > SEND_CHUNK) n = SEND_CHUNK;
    fwrite(buf, 1, n, stdout);
    return (int)n;
}

static void f_close(int fd) {
    if (fd == CFD) printf("<closed>");
}

static const cali_net_t fake_net = {
    .tcp_listen = f_listen, .tcp_accept = f_accept, .tcp_recv = f_recv, .tcp_send = f_send,
    .tcp_close = f_close,
};

static int handler(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx) {
    (void)ctx;
    resp->status = 200;
    resp->content_type = "text/plain";
    if (strcmp(req->method, "GET") == 0 && strcmp(req->path, "/hello") == 0) {
        resp->body = "hi";
        resp->body_len = 2;
        return 1;
    }
    if (strcmp(req->method, "POST") == 0 && strcmp(req->path, "/echo") == 0) {
        resp->body = req->body;
        resp->body_len = req->body_len;
        return 1;
    }
    return 0;
}

static size_t unescape(const char *s, char *out) {
    size_t n = 0;
    for (; *s && *s != '\n'; s++) {
        if (*s == '\\' && s[1]) {
            s++;
            out[n++] = *s == 'r' ? '\r' : *s == 'n' ? '\n' : *s == '0' ? '\0' : *s;
        } else {
            out[n++] = *s;
        }
    }
    return n;
}

int main(void) {
    static char line[8192];
    unsigned long long now = 1000;

    cali_http_init(&fake_net, NET_HTTP_PORT, handler, NULL);
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long ms;
        if (strncmp(line, "conn", 4) == 0) {
            pending_conn = 1;
        } else if (strncmp(line, "frag ", 5) == 0 && nfrags < MAX_FRAGS) {
            frag_len[nfrags] = unescape(line + 5, frags[nfrags]);
            nfrags++;
        } else if (strncmp(line, "eof", 3) == 0) {
            eof_queued = 1;
        } else if (sscanf(line, "tick %llu", &ms) == 1) {
            now += ms;
            recv_this_poll = 0;
            cali_http_poll((uint64_t)now);
        }
    }
    printf("<stopped>");
    cali_http_stop();
    fflush(stdout);
    return 0;
}
