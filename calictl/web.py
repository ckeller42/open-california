"""Stdlib HTTP server for the app-replica web UI, embedded in the serve process.

Never opens BLE: state comes from the poll cache and commands go through the injected
`backend` (which bridges to serve.on_command under the BLE lock). Serves the authored
assets in calictl/webui/ plus a small JSON API. Stdlib-only.
"""
from __future__ import annotations
import json
import os
import posixpath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

ROOF_FUNCTION = "roof"
# Safety-sensitive / not-live-verified targets that require an explicit client
# confirmation flag on the POST: roof (physically moves the pop-top) and
# airheater (fuel-burning parking heater). See CLAUDE.md "Known state".
CONFIRM_REQUIRED = {ROOF_FUNCTION, "airheater"}
_CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css",
                  ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml"}


def make_handler(backend, webui_dir):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass  # quiet; serve owns logging

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, body, ctype, status=200):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # no-cache so a redeployed UI is picked up on the next load, not a stale copy
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                try:
                    return self._send_json(backend.state())
                except Exception as e:  # log server-side only; never leak exception text to client
                    print("web: state failed: %r" % (e,), flush=True)
                    return self._send_json({"error": "state_failed"}, 500)
            if path == "/api/screens":
                return self._send_bytes(backend.screens_bytes(), "application/json")
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
                self._send_bytes(f.read(), _CONTENT_TYPES.get(ext, "application/octet-stream"))

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/api/command":
                return self._send_json({"error": "not_found"}, 404)
            ctype = self.headers.get("Content-Type", "")
            if ctype.split(";", 1)[0].strip().lower() != "application/json":
                return self._send_json({"error": "unsupported_media_type"}, 415)
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                length = -1
            if length < 0:
                return self._send_json({"error": "bad_request"}, 400)
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send_json({"error": "bad_json"}, 400)
            if backend.read_only:
                return self._send_json({"error": "read_only"}, 405)
            fn = req.get("function"); what = req.get("what")
            value = req.get("value"); confirm = bool(req.get("confirm"))
            if not fn or not what:
                return self._send_json({"error": "missing_function_or_what"}, 400)
            if fn in CONFIRM_REQUIRED and not confirm:
                return self._send_json({"error": "confirm_required"}, 400)
            try:
                result = backend.command(fn, what, value, confirm=confirm)
            except Exception as e:  # log server-side only; never leak exception text to client
                print("web: command failed: %r" % (e,), flush=True)
                return self._send_json({"error": "command_failed"}, 500)
            return self._send_json(result)

    return Handler


def serve_http(backend, webui_dir, host="0.0.0.0", port=8080):
    """Create a ThreadingHTTPServer and start serving in a daemon thread. Returns it."""
    httpd = ThreadingHTTPServer((host, port), make_handler(backend, webui_dir))
    print("web UI is UNAUTHENTICATED — expose only on a trusted LAN", flush=True)
    Thread(target=httpd.serve_forever, name="calictl-web", daemon=True).start()
    return httpd
