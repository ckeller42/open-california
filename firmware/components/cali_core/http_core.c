/* http_core.c — single-connection HTTP/1.1 responder over cali_net sockets; see cali_http.h. */
#include "cali_http.h"

#include <stdio.h>
#include <string.h>

#define SEND_SPIN_MAX 10000 /* would-block retries for one response before giving up on the peer */
#define HEAD_OUT_MAX 512    /* status line + response headers, Location included */

static struct {
    const cali_net_t *net;
    cali_http_handler_t handler;
    void *ctx;
    int lfd;      /* -1: not listening (never initialised, listen failed, stopped) */
    int fd;       /* -1: no connection */
    uint64_t last_ms;
    size_t len;      /* bytes in buf */
    size_t head_len; /* 0 until "\r\n\r\n" was seen: request line + headers + blank line */
    size_t body_len; /* Content-Length, valid once head_len != 0 */
    char buf[NET_HTTP_REQ_MAX + 1]; /* +1: room for the body's NUL */
} S = {NULL, NULL, NULL, -1, -1, 0, 0, 0, 0, {0}};

static void close_conn(void) {
    S.net->tcp_close(S.fd);
    S.fd = -1;
}

static int send_all(const char *p, size_t n) {
    int spins = 0;
    while (n > 0) {
        int r = S.net->tcp_send(S.fd, p, n);
        if (r == -2 || (r == -1 && ++spins > SEND_SPIN_MAX)) return -1;
        if (r > 0) {
            p += r;
            n -= (size_t)r;
        }
    }
    return 0;
}

static const char *reason(int status) {
    switch (status) {
    case 200: return "OK";
    case 204: return "No Content";
    case 302: return "Found";
    case 303: return "See Other";
    case 400: return "Bad Request";
    case 404: return "Not Found";
    case 405: return "Method Not Allowed";
    case 413: return "Content Too Large";
    case 431: return "Request Header Fields Too Large";
    case 500: return "Internal Server Error";
    case 503: return "Service Unavailable";
    default: return "Unknown";
    }
}

/* Sends the whole response, then closes the connection. */
static void respond(const cali_http_resp_t *r) {
    char head[HEAD_OUT_MAX];
    const char *body = r->body ? r->body : "";
    size_t body_len = r->body ? r->body_len : 0;
    int n = snprintf(head, sizeof head, "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %lu\r\n%s%s%s"
                     "Connection: close\r\n\r\n",
                     r->status, reason(r->status), r->content_type ? r->content_type : "text/plain",
                     (unsigned long)body_len, r->location ? "Location: " : "", r->location ? r->location : "",
                     r->location ? "\r\n" : "");
    if (n > 0 && (size_t)n < sizeof head && send_all(head, (size_t)n) == 0) send_all(body, body_len);
    close_conn();
}

static void respond_error(int status) {
    cali_http_resp_t r = {status, "text/plain", reason(status), strlen(reason(status)), NULL};
    respond(&r);
}

/* "\r\n\r\n" in buf[0..len), or 0. */
static size_t find_head_end(void) {
    size_t i;
    for (i = 0; i + 4 <= S.len; i++)
        if (memcmp(S.buf + i, "\r\n\r\n", 4) == 0) return i + 4;
    return 0;
}

static int lower(int c) {
    return c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c;
}

/* The Content-Length header in the header lines buf[from..to), 0 when absent; -1 malformed.
 * Saturates above NET_HTTP_REQ_MAX (anything that large is a 413 anyway). */
static long content_length(size_t from, size_t to) {
    static const char name[] = "content-length:";
    long v = 0;
    while (from < to) {
        size_t eol = from, k;
        while (eol + 1 < to && !(S.buf[eol] == '\r' && S.buf[eol + 1] == '\n')) eol++;
        for (k = 0; k < sizeof name - 1 && from + k < eol && lower(S.buf[from + k]) == name[k]; k++) {}
        if (k == sizeof name - 1) {
            size_t i = from + k, digits = 0;
            while (i < eol && (S.buf[i] == ' ' || S.buf[i] == '\t')) i++;
            for (v = 0; i < eol && S.buf[i] >= '0' && S.buf[i] <= '9'; i++, digits++)
                if (v <= NET_HTTP_REQ_MAX) v = v * 10 + (S.buf[i] - '0');
            while (i < eol && (S.buf[i] == ' ' || S.buf[i] == '\t')) i++;
            if (digits == 0 || i != eol) return -1;
        }
        from = eol + 2;
    }
    return v;
}

/* Splits the request line buf[0..eol) in place into NUL-terminated method/path/query;
 * 0 ok, -1 malformed ("METHOD SP /target SP HTTP/1.x", method A-Z, no NUL, exactly two SPs). */
static int parse_request_line(size_t eol, cali_http_req_t *req) {
    char *line = S.buf, *sp1, *sp2, *q;
    if (memchr(line, '\0', eol)) return -1;
    line[eol] = '\0';
    sp1 = strchr(line, ' ');
    if (!sp1 || sp1 == line) return -1;
    for (q = line; q < sp1; q++)
        if (*q < 'A' || *q > 'Z') return -1;
    sp2 = strchr(sp1 + 1, ' ');
    if (!sp2 || sp1[1] != '/' || (strcmp(sp2 + 1, "HTTP/1.1") != 0 && strcmp(sp2 + 1, "HTTP/1.0") != 0))
        return -1;
    *sp1 = *sp2 = '\0';
    req->method = line;
    req->path = sp1 + 1;
    q = strchr(sp1 + 1, '?');
    if (q) *q = '\0';
    req->query = q ? q + 1 : "";
    return 0;
}

/* First "\r\n" in buf; the head ends in "\r\n\r\n", so there is one before head_len. */
static size_t request_line_end(void) {
    size_t i = 0;
    while (memcmp(S.buf + i, "\r\n", 2) != 0) i++;
    return i;
}

static void dispatch(void) {
    cali_http_req_t req;
    cali_http_resp_t resp = {200, "text/plain", NULL, 0, NULL};
    if (parse_request_line(request_line_end(), &req) != 0) {
        respond_error(400);
        return;
    }
    S.buf[S.head_len + S.body_len] = '\0';
    req.body = S.buf + S.head_len;
    req.body_len = S.body_len;
    if (!S.handler(&req, &resp, S.ctx)) {
        respond_error(404);
        return;
    }
    respond(&resp);
}

void cali_http_init(const cali_net_t *net, uint16_t port, cali_http_handler_t handler, void *ctx) {
    int lfd = net->tcp_listen(port);
    S.net = net;
    S.handler = handler;
    S.ctx = ctx;
    S.lfd = lfd >= 0 ? lfd : -1;
    S.fd = -1;
}

void cali_http_poll(uint64_t now_ms) {
    int peer_gone = 0;
    if (S.lfd < 0) return;
    if (S.fd < 0) {
        int fd = S.net->tcp_accept(S.lfd);
        if (fd < 0) return;
        S.fd = fd;
        S.last_ms = now_ms;
        S.len = S.head_len = S.body_len = 0;
    }

    while (S.len < NET_HTTP_REQ_MAX) {
        int r = S.net->tcp_recv(S.fd, S.buf + S.len, NET_HTTP_REQ_MAX - S.len);
        if (r == -1) break;
        if (r < 0) {
            peer_gone = 1;
            break;
        }
        S.len += (size_t)r;
        S.last_ms = now_ms;
    }

    if (S.head_len == 0) {
        S.head_len = find_head_end();
        if (S.head_len == 0 && S.len >= NET_HTTP_REQ_MAX) {
            respond_error(431);
            return;
        }
        if (S.head_len != 0) {
            long cl = content_length(request_line_end() + 2, S.head_len - 2);
            if (cl < 0) {
                respond_error(400);
                return;
            }
            if ((size_t)cl > NET_HTTP_REQ_MAX - S.head_len) {
                respond_error(413);
                return;
            }
            S.body_len = (size_t)cl;
        }
    }

    if (S.head_len != 0 && S.len >= S.head_len + S.body_len) {
        dispatch(); /* bytes past this request (pipelining) are dropped with the connection */
        return;
    }
    if (peer_gone || now_ms - S.last_ms > CALI_HTTP_IDLE_MS) close_conn();
}

void cali_http_stop(void) {
    if (S.fd >= 0) close_conn();
    if (S.lfd >= 0) S.net->tcp_close(S.lfd);
    S.lfd = -1;
}
