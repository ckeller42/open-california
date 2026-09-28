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
 *   sendmax <n>   tcp_send accepts at most <n> bytes per poll, then returns -1 until the next tick
 *                 (default: unlimited); every call accepts at most SEND_CHUNK (partial sends)
 *   sendblock <k> the next <k> tcp_send calls return -1 (would block); k < 0 = forever
 *   big <n>       GET /big answers <n> bytes (i % 26 + 'a'), at most BIG_MAX
 * Argument "nolisten": tcp_listen fails. cali_http_init's return value goes to stderr as "init=<rc>".
 * tcp_send writes the accepted bytes to stdout raw; tcp_close prints "<closed>". At end of input it prints "<stopped>" and calls
 * cali_http_stop(), so a "<closed>" before "<stopped>" is the core's own close.
 */
#include <stdio.h>
#include <string.h>

#include "cali_http.h"

#define LFD 3
#define CFD 7
#define MAX_FRAGS 64
#define SEND_CHUNK 64
#define BIG_MAX 32768

static char frags[MAX_FRAGS][4096];
static size_t frag_len[MAX_FRAGS], nfrags, head, head_off;
static int pending_conn, eof_queued, recv_this_poll, no_listen;
static long send_max = -1, send_block;
static size_t sent_this_poll;
static char big[BIG_MAX];
static size_t big_len;

static int f_listen(uint16_t port) {
    (void)port;
    return no_listen ? -2 : LFD;
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
    if (send_block != 0) {
        if (send_block > 0) send_block--;
        return -1;
    }
    if (send_max >= 0 && sent_this_poll + n > (size_t)send_max) n = (size_t)send_max - sent_this_poll;
    if (n == 0) return -1;
    if (n > SEND_CHUNK) n = SEND_CHUNK;
    sent_this_poll += n;
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
    if (strcmp(req->method, "GET") == 0 && strcmp(req->path, "/big") == 0) {
        resp->body = big;
        resp->body_len = big_len;
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

int main(int argc, char **argv) {
    static char line[8192];
    unsigned long long now = 1000;
    size_t i;

    for (i = 0; i < BIG_MAX; i++) big[i] = (char)('a' + i % 26);
    no_listen = argc > 1 && strcmp(argv[1], "nolisten") == 0;
    fprintf(stderr, "init=%d\n", cali_http_init(&fake_net, NET_HTTP_PORT, handler, NULL));
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long ms;
        long v;
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
            sent_this_poll = 0;
            cali_http_poll((uint64_t)now);
        } else if (sscanf(line, "sendmax %ld", &v) == 1 && v > 0) {
            send_max = v;
        } else if (sscanf(line, "sendblock %ld", &v) == 1) {
            send_block = v;
        } else if (sscanf(line, "big %ld", &v) == 1 && v >= 0 && v <= BIG_MAX) {
            big_len = (size_t)v;
        }
    }
    printf("<stopped>");
    cali_http_stop();
    fflush(stdout);
    return 0;
}
