/**
 * @file cali_http.h
 * @brief A portable single-connection HTTP/1.1 responder over cali_net sockets.
 *
 * Serves the web UI, recovery page and API (web.c, see cali_web.h) on any platform: it reaches the
 * network only through the cali_net_t socket table and takes time only from cali_http_poll(now_ms).
 * No clock, no malloc, no ESP-IDF/POSIX headers.
 *
 * **Connection model.** One connection at a time, one request per connection. Each poll accepts a
 * connection and reads into a NET_HTTP_REQ_MAX buffer until `\r\n\r\n` plus Content-Length body
 * bytes. It then calls the handler once and sends the response with `Connection: close` across as
 * many polls as the socket needs (tcp_send -1 = retry next poll, never a spin; -2 = close). It
 * closes the connection once everything is sent. Bytes after the first request (a pipelined second
 * request) are discarded.
 *
 * **Answers of the core itself**
 *
 * - 400: malformed request line or Content-Length, a repeated Content-Length, or any
 *   Transfer-Encoding (chunked bodies are not supported).
 * - 404: the handler returned 0 (not handled).
 * - 413: Content-Length larger than the buffer space left after the headers.
 * - 431: no `\r\n\r\n` within NET_HTTP_REQ_MAX bytes.
 * - 500: the handler's Content-Type and Location do not fit the response header buffer, or the
 *   handler answered a 3xx redirect (not 304) without a location (a handler bug).
 * - 504: a request still pending CALI_HTTP_PENDING_MAX_MS after it arrived (text/plain).
 *
 * **Timeouts.** A connection that makes no progress for more than CALI_HTTP_IDLE_MS is closed.
 * While reading, the time counts from its last received byte (closed without a response). A
 * connection that has sent nothing yet gets only CALI_HTTP_FIRST_BYTE_MS from its accept, so a
 * browser's speculative preconnect or a captive-portal probe cannot hold the single connection for
 * 5 s. While sending, the time counts from the last byte the socket took (the response is cut
 * short). A connection the peer closes mid-request is closed without a response.
 *
 * **Headers.** Every response carries Content-Type, Content-Length and `Connection: close`. A
 * response with a location (302) also carries Location, and a 302 MUST set location (see 500
 * above). The content_encoding, cache_control and etag members of cali_http_resp_t add further
 * headers.
 *
 * **Lifetime of views.** Request views (method, path, query, body) are NUL-terminated and point
 * into the core's request buffer; query is "" when the target has no `?`. The buffer is not
 * touched again until the connection closes, so a handler may answer with a request view. The
 * response's body, content_type and location are owned by the handler and MUST stay valid until
 * the core closes the connection (possibly several polls later). The core is single-connection, so
 * the handler is never called again before that close (web.c's static page and its JSON buffer
 * satisfy this).
 *
 * **Pending answers.** A handler may return CALI_HTTP_PENDING. The core then keeps the connection,
 * reads nothing more and accepts no other. It calls the handler again with the same request views
 * and resume = 1 on every poll until it returns 1 (answer) or 0 (404). No idle timeout applies
 * while waiting, because the handler guarantees an answer (web.c's command: within
 * CALI_CTL_DEADLINE_MS). The core has a bound of its own, CALI_HTTP_PENDING_MAX_MS, so a handler
 * that never answers cannot wedge the single connection. cali_http_stop() drops a waiting
 * connection like any other.
 */
#ifndef CALI_HTTP_H
#define CALI_HTTP_H

#include <stddef.h>
#include <stdint.h>

#include "cali_net.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Idle budget of a connection that makes no progress (ms). */
#define CALI_HTTP_IDLE_MS 5000u
/** Idle budget before a connection's first byte (ms). */
#define CALI_HTTP_FIRST_BYTE_MS 1000u
/** Handler return value: not answered yet, re-ask me (req->resume = 1) on every poll. */
#define CALI_HTTP_PENDING 2
/** Longest a request may stay pending before the core answers 504 (ms): 2 x CALI_CTL_DEADLINE_MS, never reached by a well-behaved handler. */
#define CALI_HTTP_PENDING_MAX_MS 8000u

/** @brief A parsed request. All string views are NUL-terminated and valid until the connection closes. */
typedef struct cali_http_req {
    const char *method;         /**< The request method, e.g. "GET". */
    const char *path;           /**< The request path without the query. */
    const char *query;          /**< The query string without `?`, "" when the target has none. */
    const char *body;           /**< The request body (Content-Length bytes). */
    size_t body_len;            /**< Length of body in bytes. */
    int resume;                 /**< 0 on the first call for a request, 1 on every re-ask after CALI_HTTP_PENDING. */
    const char *if_none_match;  /**< The If-None-Match header's value, NULL when absent. */
} cali_http_req_t;

/**
 * @brief The response a handler fills in.
 *
 * Pre-filled before the handler runs: status 200, content_type "text/plain", no body, no
 * location, no content_encoding. A content_encoding (e.g. "gzip" for a pre-compressed static
 * body) adds `Content-Encoding: <it>` and `Cache-Control: no-cache` (an encoded body is an asset
 * baked into the image: a reflash must show on the next load). Without it the headers are
 * unchanged. A cache_control (e.g. "max-age=86400" for a static icon) is sent as
 * `Cache-Control: <it>`, replacing that no-cache. An etag (a quoted strong validator, e.g.
 * `"\"0123456789abcdef\""`) adds `ETag: <it>`. When the answer is 200 and the request's
 * If-None-Match is "*" or lists the etag, the core sends 304 Not Modified instead: no body, no
 * Content-Type or Content-Length, the ETag and Cache-Control kept.
 */
typedef struct cali_http_resp {
    int status;                       /**< HTTP status code. */
    const char *content_type;         /**< Content-Type header value. */
    const char *body;                 /**< Response body, owned by the handler. */
    size_t body_len;                  /**< Length of body in bytes. */
    const char *location;             /**< Location header value; required for a 302. */
    const char *content_encoding;     /**< Content-Encoding header value, or NULL. */
    const char *cache_control;        /**< Cache-Control header value, or NULL. */
    const char *etag;                 /**< ETag header value (quoted), or NULL. */
} cali_http_resp_t;

/**
 * @brief The request handler type.
 * @param req  The request.
 * @param resp The response to fill in.
 * @param ctx  The context given to cali_http_init.
 * @return 1 = handled, 0 = not handled (404), CALI_HTTP_PENDING = ask again on the next poll.
 */
typedef int (*cali_http_handler_t)(const cali_http_req_t *req, cali_http_resp_t *resp, void *ctx);

/**
 * @brief Listens on a port.
 * @param net     The socket table.
 * @param port    The TCP port.
 * @param handler Called once per request.
 * @param ctx     Passed to the handler.
 * @return 0 ok; -1 if tcp_listen failed (cali_http_poll then stays a no-op).
 */
int cali_http_init(const cali_net_t *net, uint16_t port, cali_http_handler_t handler, void *ctx);

/**
 * @brief Accepts one connection, reads until the end of the headers (plus Content-Length bytes),
 *        dispatches, sends across polls and closes.
 * @param now_ms The current time in milliseconds.
 */
void cali_http_poll(uint64_t now_ms);

/** @brief Closes the open connection (if any) and the listener. */
void cali_http_stop(void);

#ifdef __cplusplus
}
#endif

#endif /* CALI_HTTP_H */
