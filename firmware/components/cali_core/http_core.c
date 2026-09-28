/* http_core.c — single-connection HTTP/1.1 responder over cali_net sockets; see cali_http.h. */
#include "cali_http.h"

#include <stdio.h>
#include <string.h>

#define HEAD_OUT_MAX 512 /* status line + response headers, Location included */

static struct {
    const cali_net_t *net;
    cali_http_handler_t handler;
    void *ctx;
    int lfd;      /* -1: not listening (never initialised, listen failed, stopped) */
    int fd;       /* -1: no connection */
    uint64_t last_ms; /* accept, last received byte, or last send progress */
    int sending;      /* 1 once a response is queued: no more reading, only sending */
    size_t len;      /* bytes in buf */
    size_t head_len; /* 0 until "\r\n\r\n" was seen: request line + headers + blank line */
    size_t body_len; /* Content-Length, valid once head_len != 0 */
    char buf[NET_HTTP_REQ_MAX + 1]; /* +1: room for the body's NUL */
    char out_head[HEAD_OUT_MAX];    /* the queued response's status line + headers */
    size_t out_head_len;
    const char *out_body; /* the handler's body (or a static reason): valid until close */
    size_t out_body_len;
    size_t out_off; /* bytes of out_head + out_body already sent */
} S = {.lfd = -1, .fd = -1};

static void close_conn(void) {
    S.net->tcp_close(S.fd);
    S.fd = -1;
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

/* Sends as much of the queued response as the socket takes; closes once it is all sent, on a send
 * error, or after CALI_HTTP_IDLE_MS without progress. */
static void pump(uint64_t now_ms) {
    size_t total = S.out_head_len + S.out_body_len;
    while (S.out_off < total) {
        const char *p = S.out_off < S.out_head_len ? S.out_head + S.out_off : S.out_body + (S.out_off - S.out_head_len);
        size_t n = S.out_off < S.out_head_len ? S.out_head_len - S.out_off : total - S.out_off;
        int r = S.net->tcp_send(S.fd, p, n);
        if (r == -2) break;
        if (r <= 0) { /* -1 would block; 0 (a contract breach) likewise: retry next poll */
            if (now_ms - S.last_ms > CALI_HTTP_IDLE_MS) break;
            return;
        }
        S.out_off += (size_t)r;
        S.last_ms = now_ms;
    }
    close_conn();
}

/* Queues the response (headers rendered now, body by reference); pump() sends it. A header set too
 * long for HEAD_OUT_MAX (an oversized Location or Content-Type), or a 3xx redirect (not 304)
 * without a location, becomes a 500. */
static void respond(const cali_http_resp_t *r, uint64_t now_ms) {
    const char *body = r->body ? r->body : "";
    size_t body_len = r->body ? r->body_len : 0;
    int n = -1; /* a redirect without a Location is the handler's error: 500 */
    if (!(r->status >= 300 && r->status < 400 && r->status != 304 && !r->location))
        n = snprintf(S.out_head, sizeof S.out_head,
                     "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %lu\r\n%s%s%s"
                     "Connection: close\r\n\r\n",
                     r->status, reason(r->status), r->content_type ? r->content_type : "text/plain",
                     (unsigned long)body_len, r->location ? "Location: " : "", r->location ? r->location : "",
                     r->location ? "\r\n" : "");
    if (n < 0 || (size_t)n >= sizeof S.out_head) {
        body = reason(500);
        body_len = strlen(body);
        n = snprintf(S.out_head, sizeof S.out_head,
                     "HTTP/1.1 500 %s\r\nContent-Type: text/plain\r\nContent-Length: %lu\r\n"
                     "Connection: close\r\n\r\n", body, (unsigned long)body_len);
    }
    S.out_head_len = (size_t)n;
    S.out_body = body;
    S.out_body_len = body_len;
    S.out_off = 0;
    S.sending = 1;
    S.last_ms = now_ms;
    pump(now_ms);
}

static void respond_error(int status, uint64_t now_ms) {
    cali_http_resp_t r = {status, "text/plain", reason(status), strlen(reason(status)), NULL};
    respond(&r, now_ms);
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

/* Index just past a case-insensitive header name (with its ':') at the start of the line
 * buf[from..eol), or 0 when the line is another header. */
static size_t header_value(size_t from, size_t eol, const char *name) {
    size_t k;
    for (k = 0; name[k] && from + k < eol && lower(S.buf[from + k]) == name[k]; k++) {}
    return name[k] ? 0 : from + k;
}

/* The Content-Length in the header lines buf[from..to), 0 when absent; -1 = 400: malformed or
 * repeated Content-Length, or any Transfer-Encoding (chunked bodies are not supported).
 * Saturates above NET_HTTP_REQ_MAX (anything that large is a 413 anyway). */
static long content_length(size_t from, size_t to) {
    long v = 0;
    int seen = 0;
    while (from < to) {
        size_t eol = from, i;
        while (eol + 1 < to && !(S.buf[eol] == '\r' && S.buf[eol + 1] == '\n')) eol++;
        if (header_value(from, eol, "transfer-encoding:")) return -1;
        if ((i = header_value(from, eol, "content-length:")) != 0) {
            size_t digits = 0;
            if (seen++) return -1;
            while (i < eol && (S.buf[i] == ' ' || S.buf[i] == '\t')) i++;
            for (; i < eol && S.buf[i] >= '0' && S.buf[i] <= '9'; i++, digits++)
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

static void dispatch(uint64_t now_ms) {
    cali_http_req_t req;
    cali_http_resp_t resp = {200, "text/plain", NULL, 0, NULL};
    if (parse_request_line(request_line_end(), &req) != 0) {
        respond_error(400, now_ms);
        return;
    }
    S.buf[S.head_len + S.body_len] = '\0';
    req.body = S.buf + S.head_len;
    req.body_len = S.body_len;
    if (!S.handler(&req, &resp, S.ctx)) {
        respond_error(404, now_ms);
        return;
    }
    respond(&resp, now_ms);
}

void cali_http_stop(void) {
    if (S.fd >= 0) close_conn();
    if (S.lfd >= 0) S.net->tcp_close(S.lfd);
    S.lfd = -1;
}

int cali_http_init(const cali_net_t *net, uint16_t port, cali_http_handler_t handler, void *ctx) {
    int lfd;
    cali_http_stop(); /* a re-init drops the open connection and the old listener */
    lfd = net->tcp_listen(port);
    S.net = net;
    S.handler = handler;
    S.ctx = ctx;
    S.lfd = lfd >= 0 ? lfd : -1;
    return S.lfd >= 0 ? 0 : -1;
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
        S.sending = 0;
    }
    if (S.sending) {
        pump(now_ms);
        return;
    }

    while (S.len < NET_HTTP_REQ_MAX) {
        int r = S.net->tcp_recv(S.fd, S.buf + S.len, NET_HTTP_REQ_MAX - S.len);
        if (r == -1 || r == 0) break; /* 0 breaks cali_net's contract: read it as would-block, never spin */
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
            respond_error(431, now_ms);
            return;
        }
        if (S.head_len != 0) {
            long cl = content_length(request_line_end() + 2, S.head_len - 2);
            if (cl < 0) {
                respond_error(400, now_ms);
                return;
            }
            if ((size_t)cl > NET_HTTP_REQ_MAX - S.head_len) {
                respond_error(413, now_ms);
                return;
            }
            S.body_len = (size_t)cl;
        }
    }

    if (S.head_len != 0 && S.len >= S.head_len + S.body_len) {
        dispatch(now_ms); /* bytes past this request (pipelining) are dropped with the connection */
        return;
    }
    if (peer_gone || now_ms - S.last_ms > CALI_HTTP_IDLE_MS) close_conn();
}
