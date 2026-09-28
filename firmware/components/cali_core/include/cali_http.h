/* cali_http.h — a portable single-connection HTTP/1.1 responder over cali_net sockets (#154).
 *
 * Serves the web status/setup page (web.c) on any platform: it reaches the network only through
 * the cali_net_t socket table and takes time only from cali_http_poll(now_ms) — no clock, no
 * malloc, no ESP-IDF/POSIX headers.
 *
 * One connection at a time, one request per connection: poll accepts a connection, reads into a
 * NET_HTTP_REQ_MAX buffer until "\r\n\r\n" plus Content-Length body bytes, calls the handler once,
 * sends the response with "Connection: close" and closes. Bytes after the first request (a
 * pipelined second request) are discarded. The core answers by itself:
 *   400  malformed request line or Content-Length
 *   404  the handler returned 0 (not handled)
 *   413  Content-Length larger than the buffer space left after the headers
 *   431  no "\r\n\r\n" within NET_HTTP_REQ_MAX bytes
 * A connection idle for more than CALI_HTTP_IDLE_MS (measured from its accept or its last
 * received byte) before its request is complete is closed without a response, as is one the peer
 * closes mid-request. Every response carries Content-Type, Content-Length and Connection: close;
 * a response with a location (302) also carries Location.
 *
 * Request views (method, path, query, body) are NUL-terminated and point into the core's request
 * buffer, valid only during the handler call; query is "" when the target has no '?'. Response
 * strings/body are owned by the handler and must stay valid until cali_http_poll returns.
 * The send of one response completes within the poll (bounded retries on would-block), so the
 * body may live in the handler's own scratch memory.
 */
#ifndef CALI_HTTP_H
#define CALI_HTTP_H

#include <stddef.h>
#include <stdint.h>

#include "cali_net.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CALI_HTTP_IDLE_MS 5000u

typedef struct { const char *method; const char *path; const char *query; const char *body; size_t body_len; } cali_http_req_t;
/* Pre-filled before the handler runs: status 200, content_type "text/plain", no body, no location. */
typedef struct { int status; const char *content_type; const char *body; size_t body_len; const char *location; } cali_http_resp_t;
typedef int (*cali_http_handler_t)(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx);   /* 1 = handled */

/* Listens on port. A listen failure is remembered and makes cali_http_poll a no-op. */
void cali_http_init(const cali_net_t *net, uint16_t port, cali_http_handler_t handler, void *ctx);
void cali_http_poll(uint64_t now_ms);   /* accept one connection, read until "\r\n\r\n" (+ Content-Length), dispatch, send, close */
void cali_http_stop(void);              /* closes the open connection (if any) and the listener */

#ifdef __cplusplus
}
#endif

#endif /* CALI_HTTP_H */
