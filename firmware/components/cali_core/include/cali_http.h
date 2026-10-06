/* cali_http.h — a portable single-connection HTTP/1.1 responder over cali_net sockets (#154).
 *
 * Serves the web status/setup page (web.c) on any platform: it reaches the network only through
 * the cali_net_t socket table and takes time only from cali_http_poll(now_ms) — no clock, no
 * malloc, no ESP-IDF/POSIX headers.
 *
 * One connection at a time, one request per connection: poll accepts a connection, reads into a
 * NET_HTTP_REQ_MAX buffer until "\r\n\r\n" plus Content-Length body bytes, calls the handler once,
 * then sends the response with "Connection: close" across as many polls as the socket needs
 * (tcp_send -1 = retry next poll, never a spin; -2 = close) and closes once it is all sent. Bytes
 * after the first request (a pipelined second request) are discarded. The core answers by itself:
 *   400  malformed request line or Content-Length, a repeated Content-Length, or any
 *        Transfer-Encoding (chunked bodies are not supported)
 *   404  the handler returned 0 (not handled)
 *   413  Content-Length larger than the buffer space left after the headers
 *   431  no "\r\n\r\n" within NET_HTTP_REQ_MAX bytes
 *   500  the handler's Content-Type + Location do not fit the response header buffer, or the
 *        handler answered a 3xx redirect (not 304) without a location — a handler bug
 * A connection that makes no progress for more than CALI_HTTP_IDLE_MS is closed: while reading,
 * counted from its accept or its last received byte (closed without a response); while sending,
 * from the last byte the socket took (the response is cut short). A connection the peer closes
 * mid-request is closed without a response. Every response carries Content-Type, Content-Length and
 * Connection: close; a response with a location (302) also carries Location. A 302 MUST set
 * location (see 500 above).
 * A response with a content_encoding also carries Content-Encoding and Cache-Control: no-cache.
 *
 * Request views (method, path, query, body) are NUL-terminated and point into the core's request
 * buffer; query is "" when the target has no '?'. The buffer is not touched again until the
 * connection closes, so a handler may answer with a request view. The response's body, content_type
 * and location are owned by the handler and MUST stay valid until the core closes the connection
 * (possibly several polls later); the core is single-connection, so the handler is never called
 * again before that close (web.c's static page and its JSON buffer satisfy this).
 *
 * A handler may return CALI_HTTP_PENDING: the core then keeps the connection, reads nothing more
 * and accepts no other, and calls the handler again with the same request views and resume = 1 on
 * every poll until it returns 1 (answer) or 0 (404). No idle timeout applies while waiting — the
 * handler guarantees an answer (web.c's command: within CALI_CTL_DEADLINE_MS). cali_http_stop()
 * drops a waiting connection like any other.
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
#define CALI_HTTP_PENDING 2   /* handler: not answered yet — re-ask me (req->resume = 1) on every poll */

/* resume: 0 on the first call for a request, 1 on every re-ask after CALI_HTTP_PENDING. */
typedef struct { const char *method; const char *path; const char *query; const char *body; size_t body_len; int resume; } cali_http_req_t;
/* Pre-filled before the handler runs: status 200, content_type "text/plain", no body, no location,
 * no content_encoding. A content_encoding (e.g. "gzip" for a pre-compressed static body) adds
 * "Content-Encoding: <it>" and "Cache-Control: no-cache" (an encoded body is an asset baked into the
 * image: a reflash must show on the next load); without it the headers are unchanged. */
typedef struct { int status; const char *content_type; const char *body; size_t body_len; const char *location; const char *content_encoding; } cali_http_resp_t;
typedef int (*cali_http_handler_t)(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx);   /* 1 = handled, 0 = 404, CALI_HTTP_PENDING */

/* Listens on port: 0 ok; -1 tcp_listen failed, and cali_http_poll stays a no-op. */
int cali_http_init(const cali_net_t *net, uint16_t port, cali_http_handler_t handler, void *ctx);
void cali_http_poll(uint64_t now_ms);   /* accept one connection, read until "\r\n\r\n" (+ Content-Length), dispatch, send (across polls), close */
void cali_http_stop(void);              /* closes the open connection (if any) and the listener */

#ifdef __cplusplus
}
#endif

#endif /* CALI_HTTP_H */
