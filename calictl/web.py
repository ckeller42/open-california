"""Stdlib HTTP server for the app-replica web UI, embedded in the serve process.

Never opens BLE: state comes from the poll cache and commands go through the injected
`backend` (which bridges to serve.on_command under the BLE lock). Serves the authored
assets in calictl/webui/ plus a small JSON API. Stdlib-only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import posixpath
import re
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from . import control
from . import log as _log

log = _log.get(__name__)


class _NoResolveHTTPServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that does NOT reverse-resolve its bind address.

    The stdlib ``HTTPServer.server_bind`` calls ``socket.getfqdn(host)`` to set ``server_name``.
    On a host with slow or absent reverse-DNS (macOS dev machines, some CI runners, a van with no
    uplink) that lookup can **block for tens of seconds or hang indefinitely** — stalling the daemon
    before the web UI ever binds (root-caused 2026-08-28 via a faulthandler stack dump). ``server_name``
    is only used by the CGI machinery, which this server never serves, so binding the socket and
    setting ``server_name`` to the literal host is sufficient and instant.

    .. req:: The web server binds without a reverse-DNS lookup
       :id: R_WEB_NO_REVERSE_DNS
       :status: implemented
       :tags: web, robustness

       ``serve_http`` shall bind its listening socket without calling ``socket.getfqdn`` (or any
       reverse-DNS lookup), so a slow or absent resolver cannot stall the daemon before the web UI
       comes up.
    """

    # socketserver's default listen backlog is 5: a page load (HTML + scripts + icons + the first
    # /api/state) plus the previous page's pagehide usage beacon can overflow it, and a refused
    # connection fails a script load. 64 is free.
    request_queue_size = 64

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)  # bind the socket; skip the getfqdn() reverse lookup
        host, port = self.server_address[:2]
        self.server_name = str(host)  # server_address may be bytes on some Python versions
        self.server_port = port


ROOF_FUNCTION = "roof"
# Safety-sensitive / not-live-verified targets that require an explicit client
# confirmation flag on the POST: roof (physically moves the pop-top) and
# airheater (fuel-burning parking heater). See AGENTS.md "Known state".
CONFIRM_REQUIRED = {ROOF_FUNCTION, "airheater"}
_MAX_BODY = 64 * 1024  # command POSTs are tiny JSON; reject anything larger unread
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "text/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
    ".ico": "image/x-icon",
}


# ---- UX telemetry (POST /api/ux, R_UX_TELEMETRY) -----------------------------------------------
UX_MAX_BODY = 2048  # the satellite's whole request buffer is 2 KB (NET_HTTP_REQ_MAX); same cap here
UX_MAX_EVENTS = 16
UX_MSG_MAX = 200
UX_ENUMS = {"ua_class": ("ios", "android", "mac", "other"), "origin": ("lan", "busnet", "buspi")}
# every key an event of that type carries (all required, nothing else): ids / non-negative ints / msg
UX_EVENTS = {
    "view": ("screen",),
    "control": ("fn", "control"),
    "timing": ("ttfb", "dcl", "tiles"),
    "error": ("msg",),
}
UX_OPTIONAL = {"control": ("value",)}  # keys an event of that type may also carry
UX_VALUE_MAX = 32
_UX_ID = re.compile(r"[a-z0-9_.-]{1,32}\Z")
_UX_SID = re.compile(r"[0-9a-f]{8,32}\Z")
_UX_MSG = re.compile(r"[ -~]{1,%d}\Z" % UX_MSG_MAX)  # printable ASCII: the page sanitizes first
_UX_VALUE = re.compile(r"[ -~]{1,%d}\Z" % UX_VALUE_MAX)  # a control's value: printable ASCII
_UX_INT_MAX = 600000  # ms: a timing beyond 10 min is no page load


def _ux_value(key, v):
    if key == "msg":
        return isinstance(v, str) and bool(_UX_MSG.match(v))
    if key == "value":
        return isinstance(v, str) and bool(_UX_VALUE.match(v))
    if key in ("ttfb", "dcl", "tiles"):
        return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= _UX_INT_MAX
    return isinstance(v, str) and bool(_UX_ID.match(v))


def ux_batch(obj):
    """Validate one ``POST /api/ux`` beacon from the web UI; ``None`` when it is not exactly the shape.

    ``{"sid": <8-32 hex>, "ua_class": ios|android|mac|other, "standalone": <bool>,
    "origin": lan|busnet|buspi, "events": [<1-16 events>]}``, each event ``{"type": …}`` plus exactly
    its :data:`UX_EVENTS` keys (a ``control`` may also carry ``value``, printable ASCII 1-32 chars): ids ``[a-z0-9_.-]{1,32}``, timings ints 0..600000 ms, ``msg`` 1-200
    printable ASCII chars. Values only of controls (never a password or PIN: those use their
    own endpoints), no other free text than an error message. The firmware's
    ``web.c`` ``api_ux`` holds the same rules (``tests/firmware/test_web_handlers.py``).

    .. req:: UX telemetry: the web UI's usage beacons, no values, with an off switch
       :id: R_UX_TELEMETRY
       :status: implemented
       :tags: web, firmware, telemetry

       The web UI shall batch usage events (screen views, control ids and values (never a password or PIN: those never pass through a control) —
       load timings, script errors truncated to 200 chars) with a random per-tab id into at most
       one ``navigator.sendBeacon('/api/ux')`` per 10 s plus one on ``pagehide``, and only while
       ``_meta.ux`` is true; a missing endpoint shall never break the page. calictl shall validate
       a beacon (``ux_batch``), log it at INFO and write measurement ``ui_ux`` to Influx; the ESP32
       satellite shall accept it in station mode only (body <= 2 KB), write one ``{"ev":"ux"}`` SD
       record per event, and answer 204 without a record while its ``ux_on`` setting (Satellite settings
       "Send usage data") is off. No client IPs, no user identity are recorded.

    :param obj: the decoded JSON body.
    :returns: the batch (the same dict) or ``None``.
    """
    if not isinstance(obj, dict) or set(obj) != {"sid", "ua_class", "standalone", "origin", "events"}:
        return None
    if not (isinstance(obj["sid"], str) and _UX_SID.match(obj["sid"])) or not isinstance(
        obj["standalone"], bool
    ):
        return None
    if any(obj[k] not in allowed for k, allowed in UX_ENUMS.items()):
        return None
    evs = obj["events"]
    if not isinstance(evs, list) or not 1 <= len(evs) <= UX_MAX_EVENTS:
        return None
    for e in evs:
        keys = UX_EVENTS.get(str(e.get("type"))) if isinstance(e, dict) else None
        if keys is None:
            return None
        opt = UX_OPTIONAL.get(e["type"], ())
        if not {"type", *keys} <= set(e) <= {"type", *keys, *opt} or not all(
            _ux_value(k, e[k]) for k in set(e) - {"type"}
        ):
            return None
    return obj


# The /api/state members that tick on their own (ages, countdowns, uptime, the satellite battery's
# jitter), left out of its ETag (R_FW_STATE_ETAG) so an unchanged state answers 304; the page counts
# them on from its own clock. Scalar values only. Same set as the firmware's STATE_TICKING (web.c;
# tests/test_state_etag_parity.py).
STATE_TICKING = frozenset(
    {"uptime_ms", "last_snap_age_ms", "rest_next_s", "halt_last_s", "halt_in_s", "last_s", "age_s"}
    | {"water_held_age_ms", "remaining_ms", "mv", "pct", "runtime_min"}
)


def _steady(o):
    """``o`` without its ticking scalar members (STATE_TICKING), recursively."""
    if isinstance(o, dict):
        return {
            k: _steady(v)
            for k, v in o.items()
            if not (k in STATE_TICKING and not isinstance(v, (dict, list)))
        }
    if isinstance(o, list):
        return [_steady(v) for v in o]
    return o


def state_etag(state):
    """The weak ETag of a /api/state body: a hash of its content without the ticking members.

    .. req:: An unchanged state poll answers 304; JSON is gzipped on request
       :id: R_FW_STATE_ETAG
       :status: implemented
       :tags: web, esp32, data

       ``GET /api/state`` (calictl and the satellite's plain reply) shall carry a weak ETag over its
       content without the members that tick on their own (``STATE_TICKING``), with
       ``Cache-Control: no-store``, and answer a matching ``If-None-Match`` with a body-less 304.
       calictl shall gzip its JSON API replies for a client that accepts it.

    :param state: the state dict as served
    :returns: ``W/"<16 hex>"``
    """
    body = json.dumps(_steady(state), sort_keys=True, separators=(",", ":")).encode()
    return 'W/"%s"' % hashlib.sha256(body).hexdigest()[:16]


def make_handler(backend, webui_dir):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass  # quiet; serve owns logging

        def _send_json(self, obj, status=200, etag=None, cache="no-cache"):
            if etag and status == 200 and self._inm_matches(etag):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", cache)
                self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                return
            body = json.dumps(obj).encode()
            gz = "gzip" in self.headers.get("Accept-Encoding", "")
            if gz:
                body = gzip.compress(body, compresslevel=6, mtime=0)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Vary", "Accept-Encoding")
            if gz:
                self.send_header("Content-Encoding", "gzip")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)

        def _inm_matches(self, etag):
            inm = self.headers.get("If-None-Match", "")
            return inm.strip() == "*" or etag in inm

        def _send_bytes(self, body, ctype, status=200, etag=None):
            if etag and self._inm_matches(etag):  # the client's copy is current
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # no-cache so a redeployed UI is picked up on the next load, not a stale copy (an
            # unchanged one costs a 304 when the ETag matches)
            self.send_header("Cache-Control", "no-cache")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                try:
                    st = backend.state()
                    return self._send_json(st, etag=state_etag(st), cache="no-store")
                except Exception as e:  # log server-side only; never leak exception text to client
                    log.warning("web: state failed: %r" % (e,))
                    return self._send_json({"error": "state_failed"}, 500)
            if path == "/api/screens":
                return self._send_bytes(backend.screens_bytes(), "application/json")
            if path == "/api/pairing":
                try:
                    return self._send_json(backend.pairing_snapshot())
                except Exception as e:  # log server-side only; never leak exception text to client
                    log.warning("web: pairing snapshot failed: %r" % (e,))
                    return self._send_json({"error": "pairing_failed"}, 500)
            return self._serve_static(path)

        def _serve_static(self, path):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            # prevent path traversal
            safe = posixpath.normpath(rel)
            if safe.startswith("..") or safe.startswith("/"):
                return self._send_json({"error": "not_found"}, 404)
            full = os.path.join(webui_dir, safe)
            if not os.path.isfile(full):
                return self._send_json({"error": "not_found"}, 404)
            ext = os.path.splitext(full)[1]
            with open(full, "rb") as f:
                body = f.read()
            etag = '"%s"' % hashlib.sha256(body).hexdigest()[:16]  # strong: changes with the bytes
            self._send_bytes(body, _CONTENT_TYPES.get(ext, "application/octet-stream"), etag=etag)

        def _ux(self):
            """``POST /api/ux`` (R_UX_TELEMETRY): any Content-Type (``sendBeacon`` sends text/plain),
            204 on success and while switched off (``CALICTL_UX=0``: nothing recorded)."""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0:
                return self._send_json({"error": "bad_request"}, 400)
            if length > UX_MAX_BODY:
                return self._send_json({"error": "too_large"}, 413)
            raw = self.rfile.read(length)
            if not getattr(backend, "ux_enabled", False):
                return self._send_bytes(b"", "text/plain", 204)
            try:
                batch = ux_batch(json.loads(raw))
            except ValueError:
                batch = None
            if batch is None:
                return self._send_json({"error": "bad_ux"}, 400)
            try:
                backend.ux(batch)
            except Exception as e:  # telemetry must never fail the page
                log.warning("web: ux failed: %r" % (e,))
            return self._send_bytes(b"", "text/plain", 204)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path not in (
                "/api/command",
                "/api/session",
                "/api/roof",
                "/api/auto_camper",
                "/api/pairing",
                "/api/ux",
            ):
                return self._send_json({"error": "not_found"}, 404)
            if path == "/api/ux":  # before the Content-Type check: sendBeacon posts text/plain
                return self._ux()
            ctype = self.headers.get("Content-Type", "")
            if ctype.split(";", 1)[0].strip().lower() != "application/json":
                return self._send_json({"error": "unsupported_media_type"}, 415)
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                length = -1
            if length < 0 or length > _MAX_BODY:  # cap: commands are tiny JSON objects
                return self._send_json({"error": "bad_request"}, 400)
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send_json({"error": "bad_json"}, 400)
            if path == "/api/session":
                # connection toggle: manage the BLE session, not a vehicle write, so it is allowed
                # even in read-only mode (it never actuates).
                action = req.get("action")
                if action not in ("connect", "disconnect"):
                    return self._send_json({"error": "bad_action"}, 400)
                try:
                    return self._send_json(backend.set_session(action))
                except Exception as e:
                    log.warning("web: set_session failed: %r" % (e,))
                    return self._send_json({"error": "session_failed"}, 500)
            if path == "/api/roof":
                # the roof page is open / was left: the daemon streams the roof SafetyCounter
                # (STOP frames) while viewing, like the app's roof screen — a vehicle write, so
                # refused in read-only mode
                action = req.get("action")
                if action not in ("view", "leave"):
                    return self._send_json({"error": "bad_action"}, 400)
                if backend.read_only:
                    return self._send_json({"error": "read_only"}, 405)
                try:
                    return self._send_json(backend.roof_view(action))
                except Exception as e:
                    log.warning("web: roof view failed: %r" % (e,))
                    return self._send_json({"error": "roof_view_failed"}, 500)
            if path == "/api/auto_camper":
                # a SETTING toggle (persisted), not a vehicle write — allowed even in read-only;
                # the feature's own actuation still checks read-only before it acts.
                if not isinstance(req.get("enabled"), bool):
                    return self._send_json({"error": "bad_enabled"}, 400)
                try:
                    return self._send_json(backend.set_auto_camper(req["enabled"]))
                except Exception as e:
                    log.warning("web: set_auto_camper failed: %r" % (e,))
                    return self._send_json({"error": "auto_camper_failed"}, 500)
            if path == "/api/pairing":
                # connection management (like /api/session), not a vehicle write — allowed even in
                # read-only mode.
                action = req.get("action")
                if action not in ("start", "passkey", "cancel", "reset"):
                    return self._send_json({"error": "bad_action"}, 400)
                if action == "reset" and not req.get("confirm"):
                    return self._send_json({"error": "confirm_required"}, 400)
                value = req.get("value")
                if action == "passkey" and not (
                    isinstance(value, str) and len(value) == 6 and value.isdigit()
                ):
                    return self._send_json({"error": "bad_passkey"}, 400)
                try:
                    return self._send_json(backend.pairing_command(action, value))
                except Exception as e:
                    log.warning("web: pairing command failed: %r" % (e,))
                    return self._send_json({"error": "pairing_failed"}, 500)
            if backend.read_only:
                return self._send_json({"error": "read_only"}, 405)
            fn = req.get("function")
            what = req.get("what")
            value = req.get("value")
            confirm = bool(req.get("confirm"))
            if not fn or not what:
                return self._send_json({"error": "missing_function_or_what"}, 400)
            if fn in CONFIRM_REQUIRED and not confirm:
                return self._send_json({"error": "confirm_required"}, 400)
            try:
                result = backend.command(fn, what, value, confirm=confirm)
            except control.CommandError as e:  # calictl's own grammar/range message: show it
                return self._send_json({"error": str(e)}, 400)
            except Exception as e:  # log server-side only; never leak exception text to client
                log.warning("web: command failed: %r" % (e,))
                return self._send_json({"error": "command_failed"}, 500)
            return self._send_json(result)

    return Handler


def serve_http(backend, webui_dir, host="0.0.0.0", port=8080):
    """Create the web server and start serving in a daemon thread. Returns it.

    Uses :class:`_NoResolveHTTPServer` so binding never blocks on a reverse-DNS lookup — a slow/absent
    resolver would otherwise hang the whole daemon before the web UI comes up.
    """
    httpd = _NoResolveHTTPServer((host, port), make_handler(backend, webui_dir))
    log.warning("web UI is UNAUTHENTICATED — expose only on a trusted LAN")
    Thread(target=httpd.serve_forever, name="calictl-web", daemon=True).start()
    return httpd
