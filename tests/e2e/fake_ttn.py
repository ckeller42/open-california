"""A fake The Things Stack (HTTP API v3) for the remote app's tests: one in-memory application, with the
verified 2026-11-04 behaviour of ``calictl/webui/ttn.js``'s calls.

The four registries (IS, JS, NS, AS) collapse into one device record that keeps each call it received. API keys
are generated here (random, never literals) in four kinds: ``user`` (``auth_info`` shows ``user_ids``), ``all``
(``RIGHT_APPLICATION_ALL``), ``read`` and ``cmd`` (the phone keys' rights). A key may carry ``all`` or the exact
right a call needs; a missing right answers 403, an unknown (or deleted) key 401. Every request is answered with
CORS ``*`` (preflights included), as TTS does. ``calls`` logs ``(method, path, key id)`` and never a secret.

Use ``FakeTtn()`` as a context manager, or ``start()`` / ``stop()``.
"""

import base64
import json
import random
import re
import secrets
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

READ = ["RIGHT_APPLICATION_INFO", "RIGHT_APPLICATION_DEVICES_READ", "RIGHT_APPLICATION_TRAFFIC_READ"]
CMD = [*READ, "RIGHT_APPLICATION_TRAFFIC_DOWN_WRITE"]
KIND_RIGHTS = {
    "all": ["RIGHT_APPLICATION_ALL"],
    "read": READ,
    "cmd": CMD,
    "user": ["RIGHT_USER_APPLICATIONS_LIST"],
}
B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_APP = r"/api/v3/(?:(?:js|ns|as)/)?applications/([^/]+)"


def _tkey(t):
    """RFC3339Nano text to a sortable ``(seconds, nanoseconds)``."""
    sec, _, frac = t.rstrip("Z").partition(".")
    return datetime.fromisoformat(sec + "+00:00"), int((frac + "000000000")[:9])


def _pick(ed, paths):
    """The fields of ``ed`` that ``paths`` (dotted) name, as TTS stores only the masked fields."""
    out = {}
    for path in paths:
        if path.startswith("ids."):
            continue
        src, dst = ed, out
        parts = path.split(".")
        for part in parts[:-1]:
            src = src.get(part, {})
            dst = dst.setdefault(part, {})
        if parts[-1] in src:
            dst[parts[-1]] = src[parts[-1]]
    return out


def _secret():
    """A random key in TTS form ``NNSXS.<39 b32>.<52 b32>``."""
    r = random.SystemRandom()
    return (
        "NNSXS." + "".join(r.choice(B32) for _ in range(39)) + "." + "".join(r.choice(B32) for _ in range(52))
    )


class FakeTtn:
    """The fake server; ``base`` is its URL, ``calls`` the log, ``devices`` the registry."""

    def __init__(self, app="oc-test-app"):
        self.app = app
        self.keys = {}  # secret -> {"id", "kind", "name", "rights", "app"}
        self.devices = {}  # dev id -> {"ids", "calls": [registry], "app_key", "storage"...}
        self.associations = {}  # port -> package name
        self.uplinks = {}  # dev id -> [{"received_at", "f_port", "frm_payload"}]
        self.downlinks = []  # (dev id, f_port, bytes, key id)
        self.storage_body = None  # test hook: bytes to answer every storage read with
        self.refuse_key_delete = False  # test hook: deleting a full-rights ("all") key answers 500
        self.create_gate = None  # test hook: a threading.Event the device create waits for (max 15 s)
        self.stall_reads = 0  # test hook: this many next storage reads wait for ``release`` (max 30 s)
        self.release = threading.Event()
        self.after_inclusive = False  # test hook: ``after`` also returns the uplink AT that time
        self.calls = []  # (method, path, key id)
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = None

    def start(self):
        """Serve in a daemon thread."""
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        """Stop serving (a stalled read ends first: the server joins its threads)."""
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def make_key(self, kind, name=""):
        """Issue a key of ``kind`` (user / all / read / cmd) and return ``(key id, secret)``."""
        return self._add_key(kind, name, KIND_RIGHTS[kind])

    def _add_key(self, kind, name, rights):
        secret, kid = _secret(), secrets.token_hex(8).upper()
        with self.lock:
            self.keys[secret] = {
                "id": kid,
                "kind": kind,
                "name": name,
                "rights": list(rights),
                "app": self.app,
            }
        return kid, secret

    def add_uplink(self, dev, port, payload, when=None, ns=0):
        """Test hook: store an uplink of ``dev`` on ``port`` with ``payload`` bytes at ``when`` (a ``datetime``).

        ``received_at`` is RFC3339Nano as TTS sends it (9 fraction digits): the microseconds of ``when`` plus the
        last three digits ``ns`` (0-999).
        """
        when = when or datetime.now(UTC)
        rec = {
            "received_at": when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "%03dZ" % ns,
            "f_port": port,
            "frm_payload": base64.b64encode(bytes(payload)).decode(),
        }
        with self.lock:
            self.uplinks.setdefault(dev, []).append(rec)

    def writes(self):
        """The logged calls that are not GET."""
        return [c for c in self.calls if c[0] != "GET"]


def _make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj=None, raw=None):
            body = raw if raw is not None else json.dumps(obj if obj is not None else {}).encode()
            self.send_response(code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _handle(self, method):
            url = urlsplit(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            with fake.lock:
                key = fake.keys.get(token)
                fake.calls.append((method, self.path, key["id"] if key else None))
            if not key:
                return self._send(401, {"code": 16, "message": "error:pkg/auth:no_credentials"})
            code, out = self._route(method, url, body, key)
            if isinstance(out, bytes):
                return self._send(code, raw=out)
            self._send(code, out)

        def _need(self, key, app, right):
            ok = key["app"] == app and ("RIGHT_APPLICATION_ALL" in key["rights"] or right in key["rights"])
            return None if ok else (403, {"code": 7, "message": "no right: " + right})

        def _route(self, method, url, body, key):
            p, q = url.path, parse_qs(url.query)
            if p == "/api/v3/auth_info" and method == "GET":
                ak = {"id": key["id"], "rights": key["rights"]}
                if key["kind"] == "user":
                    return 200, {"api_key": {"entity_ids": {"user_ids": {"user_id": "owner"}}, "api_key": ak}}
                ids = {"application_ids": {"application_id": key["app"]}}
                return 200, {"api_key": {"entity_ids": ids, "api_key": ak}}
            m = re.fullmatch(_APP + r"/devices", p)
            if m and method == "POST":
                if fake.create_gate is not None:
                    fake.create_gate.wait(15)
                if err := self._need(key, m[1], "RIGHT_APPLICATION_DEVICES_WRITE"):
                    return err
                dev = body["end_device"]["ids"]["device_id"]
                if dev in fake.devices:
                    return 409, {"code": 6, "message": "already exists"}
                fake.devices[dev] = {"ids": body["end_device"]["ids"], "registry": ["is"], "storage": False}
                return 200, body["end_device"]
            m = re.fullmatch(r"/api/v3/(js|ns|as)/applications/([^/]+)/devices/([^/]+)", p)
            if m and method == "PUT":
                reg, app, dev = m.groups()
                right = (
                    "RIGHT_APPLICATION_DEVICES_WRITE_KEYS"
                    if reg == "js"
                    else "RIGHT_APPLICATION_DEVICES_WRITE"
                )
                if err := self._need(key, app, right):
                    return err
                if dev not in fake.devices:
                    return 404, {"code": 5, "message": "device not found"}
                d, ed = fake.devices[dev], body["end_device"]
                d["registry"].append(reg)
                d[reg] = _pick(ed, body["field_mask"]["paths"])
                d["masks"] = {**d.get("masks", {}), reg: body["field_mask"]["paths"]}
                return 200, ed
            m = re.fullmatch(_APP + r"/packages/associations/(\d+)", p)
            if m and method == "PUT" and "/as/" in p:
                if err := self._need(key, m[1], "RIGHT_APPLICATION_SETTINGS_PACKAGES"):
                    return err
                fake.associations[int(m[2])] = body["default"]["package_name"]
                return 200, body
            m = re.fullmatch(_APP + r"/api-keys", p)
            if m and method in ("GET", "POST"):
                if err := self._need(key, m[1], "RIGHT_APPLICATION_SETTINGS_API_KEYS"):
                    return err
                if method == "GET":
                    ks = [
                        {k: v[k] for k in ("id", "name", "rights")}
                        for v in fake.keys.values()
                        if v["app"] == m[1]
                    ]
                    return 200, {"api_keys": ks}
                if "RIGHT_APPLICATION_ALL" not in key["rights"] and not set(body["rights"]) <= set(
                    key["rights"]
                ):
                    return 403, {"code": 7, "message": "cannot grant rights the key does not hold"}
                kid, secret = fake._add_key("created", body["name"], body["rights"])
                return 200, {"id": kid, "key": secret, "name": body["name"], "rights": body["rights"]}
            m = re.fullmatch(_APP + r"/api-keys/([^/]+)", p)
            if m and method == "DELETE":
                if err := self._need(key, m[1], "RIGHT_APPLICATION_SETTINGS_API_KEYS"):
                    return err
                with fake.lock:
                    gone = [s for s, v in fake.keys.items() if v["id"] == m[2]]
                    if fake.refuse_key_delete and any(fake.keys[s]["kind"] == "all" for s in gone):
                        return 500, {"code": 13, "message": "internal"}
                    for s in gone:
                        del fake.keys[s]
                return (200, {}) if gone else (404, {"code": 5, "message": "key not found"})
            m = re.fullmatch(_APP + r"/devices/([^/]+)/packages/storage/uplink_message", p)
            if m and method == "GET":
                if err := self._need(key, m[1], "RIGHT_APPLICATION_TRAFFIC_READ"):
                    return err
                with fake.lock:
                    stall = fake.stall_reads > 0
                    fake.stall_reads -= stall
                if stall:
                    fake.release.wait(30)
                if fake.storage_body is not None:
                    return 200, fake.storage_body
                recs = sorted(fake.uplinks.get(m[2], []), key=lambda r: _tkey(r["received_at"]))
                if "after" in q:
                    cut = _tkey(q["after"][0])
                    recs = [
                        r
                        for r in recs
                        if _tkey(r["received_at"]) > cut
                        or (fake.after_inclusive and _tkey(r["received_at"]) == cut)
                    ]
                if q.get("order") == ["-received_at"]:
                    recs.reverse()
                recs = recs[: int(q["limit"][0])] if "limit" in q else recs
                lines = [{"result": {"received_at": r["received_at"], "uplink_message": {
                    "f_port": r["f_port"], "frm_payload": r["frm_payload"]}}} for r in recs]  # fmt: skip
                return 200, "".join(json.dumps(x) + "\n" for x in lines).encode()
            m = re.fullmatch(_APP + r"/devices/([^/]+)/down/push", p)
            if m and method == "POST":
                if err := self._need(key, m[1], "RIGHT_APPLICATION_TRAFFIC_DOWN_WRITE"):
                    return err
                for d in body["downlinks"]:
                    fake.downlinks.append((m[2], d["f_port"], base64.b64decode(d["frm_payload"]), key["id"]))
                return 200, {}
            return 404, {"code": 5, "message": "no route"}

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler
