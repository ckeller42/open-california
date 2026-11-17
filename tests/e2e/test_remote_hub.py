"""Browser e2e of the remote app over the remote hub (R_UI_REMOTE_APP, hub.js): the BUILT remote site
(``tools/build_remote.py --hub-origin``, under its own CSP) talks MQTT over a WebSocket to a small in-test broker,
here with a fake ESP that seals the van's state (dir 1) and answers requests, and a fake bridge that seals LoRa
uplinks (dir 3). LoRa commands go to ``tests/e2e/fake_ttn.py``. No real broker or TTS server is contacted.

The broker speaks the MQTT 3.1.1 subset MQTT.js uses here (CONNECT, SUBSCRIBE with ``+``, PUBLISH QoS 0 with
retain, PINGREQ, DISCONNECT) over ``websockets`` (subprotocol ``mqtt``). OPT-IN like test_gui.py: skipped without
Playwright + Chromium.

.. test:: The remote app shows the van live over the hub, marks LoRa mode and stale values, answers commands over
   the hub and falls back to one LoRa command when the van is offline
   :id: T_UI_REMOTE_HUB
   :links: R_UI_REMOTE_APP
"""

import functools
import json
import os
import re
import struct
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from calictl import overrides, protocol
from tools import build_remote, gen_lora_dict
from tools.mock_unit import MockCamperUnit, _pack_state

sync_api = pytest.importorskip("playwright.sync_api")
ws_server = pytest.importorskip("websockets.sync.server")
AESGCM = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead").AESGCM
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

E2E = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E))
from conftest import _error_gated_page  # noqa: E402
from fake_ttn import FakeTtn  # noqa: E402

KEY = bytes(range(16))
EUI = "70B3D57ED0000001"
VAN = "van1"


# ---- the broker -----------------------------------------------------------------------------------------------


def _remaining(n):
    out = b""
    while True:
        n, b = n >> 7, n & 0x7F
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _packet(kind, body):
    return bytes([kind]) + _remaining(len(body)) + body


def _publish_packet(topic, payload, retain):
    t = topic.encode()
    return _packet(0x30 | (1 if retain else 0), struct.pack(">H", len(t)) + t + payload)


def _matches(filt, topic):
    f, t = filt.split("/"), topic.split("/")
    return len(f) == len(t) and all(a in ("+", b) for a, b in zip(f, t))


class FakeBroker:
    """MQTT 3.1.1 over WebSocket on 127.0.0.1; ``published`` logs every client publish, ``on_publish(topic,
    payload)`` lets the test answer (the fake ESP)."""

    def __init__(self):
        self.retained, self.subs, self.published, self.logins = {}, [], [], []
        self.lock = threading.Lock()
        self.on_publish = None
        self.server = ws_server.serve(self._conn, "127.0.0.1", 0, subprotocols=["mqtt"])
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    def publish(self, topic, payload, retain=False):
        """Deliver to every matching subscriber; keep it when ``retain``."""
        with self.lock:
            if retain:
                self.retained[topic] = payload
            subs = list(self.subs)
        for conn, filt in subs:
            if _matches(filt, topic):
                try:
                    conn.send(_publish_packet(topic, payload, False))
                except Exception:  # noqa: BLE001 — a closed page
                    pass

    def _conn(self, conn):
        buf = b""
        try:
            for msg in conn:
                buf += msg if isinstance(msg, bytes) else msg.encode()
                while len(buf) >= 2:
                    n, mul, i = 0, 1, 1
                    while True:
                        if i >= len(buf):
                            break
                        n += (buf[i] & 0x7F) * mul
                        mul *= 128
                        i += 1
                        if not buf[i - 1] & 0x80:
                            break
                    if len(buf) < i + n:
                        break
                    kind, body, buf = buf[0], buf[i : i + n], buf[i + n :]
                    self._handle(conn, kind, body)
        finally:
            with self.lock:
                self.subs = [s for s in self.subs if s[0] is not conn]

    def _handle(self, conn, kind, body):
        t = kind >> 4
        if t == 1:  # CONNECT: the login (username, password) is logged, never checked here
            flags, i = body[7], 10
            cid = struct.unpack(">H", body[i : i + 2])[0]
            i += 2 + cid
            fields = []
            while i < len(body):
                n = struct.unpack(">H", body[i : i + 2])[0]
                fields.append(body[i + 2 : i + 2 + n].decode())
                i += 2 + n
            self.logins.append((fields, flags))
            conn.send(_packet(0x20, b"\x00\x00"))
        elif t == 8:  # SUBSCRIBE
            pid, i, granted, filters = body[:2], 2, b"", []
            while i < len(body):
                n = struct.unpack(">H", body[i : i + 2])[0]
                filters.append(body[i + 2 : i + 2 + n].decode())
                i += 3 + n
                granted += b"\x00"
            with self.lock:
                self.subs += [(conn, f) for f in filters]
                ret = list(self.retained.items())
            conn.send(_packet(0x90, pid + granted))
            for topic, payload in ret:
                if any(_matches(f, topic) for f in filters):
                    conn.send(_publish_packet(topic, payload, True))
        elif t == 3:  # PUBLISH, QoS 0
            n = struct.unpack(">H", body[:2])[0]
            topic, payload = body[2 : 2 + n].decode(), body[2 + n :]
            self.published.append((topic, payload))
            if self.on_publish:
                self.on_publish(topic, payload)
            self.publish(topic, payload, bool(kind & 1))
        elif t == 12:  # PINGREQ
            conn.send(b"\xd0\x00")
        elif t == 14:  # DISCONNECT
            conn.close()


# ---- the fake ESP and bridge ----------------------------------------------------------------------------------


class Sealer:
    """Seals hub frames as cali_hub_seal does: ver | dir | nonce(dir | session(7) | ctr(4)) | ct | tag."""

    def __init__(self):
        self.sess, self.ctr = os.urandom(7), 0

    def seal(self, d, obj):
        self.ctr += 1
        head = bytes([1, d, d]) + self.sess + self.ctr.to_bytes(4, "big")
        return head + AESGCM(KEY).encrypt(head[2:], json.dumps(obj).encode(), head)


def open_req(frame):
    """The app's request (dir 2) as the ESP opens it."""
    assert frame[:3] == b"\x01\x02\x02"
    return json.loads(AESGCM(KEY).decrypt(frame[2:14], frame[14:], frame[:14]))


def raw_state(**over):
    """The ESP's /api/state?hub=1 body: the mock unit's raw fields, writes allowed, LoRa on, hub_t = now."""
    unit = MockCamperUnit()
    fn = {name: dict(unit.state[name]) for name in ("cooler", "airheater", "campingmode", "water")}
    return {
        "hub_t": int(time.time() * 1000),
        "lora_ctr": 41,
        "fn": fn,
        "device": {
            "control": {"writes": True},
            "pairing": {"state": "paired", "address": "00:11:22:33:44:55"},
            "link": {"up": True, "ready": True, "last_snap_age_ms": 1000},
            "lora": {"dev_eui": EUI},
        },
        **over,
    }


def port3_cooler_on():
    """A LoRa port-3 uplink with the cooler switched on, as the bridge forwards it."""
    funcs = protocol.load()
    overrides.apply(funcs)
    frame = _pack_state(funcs["cooler"], {**MockCamperUnit().state["cooler"], "State": 1})
    idx = [f["name"] for f in gen_lora_dict.functions()].index("cooler")
    return (bytes([idx, len(frame)]) + frame).hex()


# ---- the page ---------------------------------------------------------------------------------------------------


def serve(root, csp_extra):
    """The built site; ``csp_extra`` joins connect-src (the fake TTN is plain http on 127.0.0.1: the one test seam,
    as in tests/test_build_remote.py). The broker is in the built CSP itself (``--hub-origin``)."""

    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.split("?")[0] in ("/", "/index.html"):
                body = (root / "index.html").read_text(encoding="utf-8")
                body = body.replace("connect-src 'self'", "connect-src 'self' " + csp_extra).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def world(tmp_path):
    broker = FakeBroker()
    origin = "ws://127.0.0.1:%d" % broker.port
    build_remote.build(tmp_path / "site", hub_origin=origin)
    with FakeTtn() as ttn:
        _, cmd = ttn.make_key("cmd")
        rc = {
            "cluster": "eu1.cloud.thethings.network",
            "appId": ttn.app,
            "devId": "van-000001",
            "readKey": cmd,
        }
        rc |= {"cmdKey": cmd, "base": ttn.base}
        hub = {"van": VAN, "url": origin, "user": "app", "pass": "app-pass", "key": KEY.hex()}
        httpd = serve(tmp_path / "site", csp_extra="http://127.0.0.1:*")  # the fake TTN only
        init = (
            "".join(
                "localStorage.getItem(%s) || localStorage.setItem(%s, %s);"
                % (json.dumps(k), json.dumps(k), json.dumps(json.dumps(v)))
                for k, v in (("oc.remote.cfg", rc), ("oc.hub.cfg", hub))
            )
            + "window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.blockedURI));"
        )
        try:
            yield broker, ttn, "http://127.0.0.1:%d/" % httpd.server_address[1], init
        finally:
            httpd.shutdown()
            httpd.server_close()
            broker.close()


def _tile(pg, title):
    return pg.locator(".tile", has=pg.locator(".tt", has_text=re.compile("^%s$" % title)))


def test_live_then_lora_and_a_command_by_lora(world):
    broker, ttn, url, init = world
    esp, bridge = Sealer(), Sealer()
    broker.publish("oc/%s/online" % VAN, b"1", retain=True)
    broker.publish("oc/%s/state" % VAN, esp.seal(1, raw_state()), retain=True)
    answers = []

    def on_publish(topic, payload):
        """The fake ESP: answers a request while `answers` holds a reply."""
        if topic == "oc/%s/req" % VAN and answers:
            q = open_req(payload)
            broker.publish("oc/%s/res" % VAN, esp.seal(1, {"id": q["id"], "s": 200, "b": answers.pop(0)}))

    broker.on_publish = on_publish
    with _error_gated_page(
        url, init_script=init, service_workers="block", allow_errors=("Executing inline",)
    ) as pg:
        pg.clock.install()
        expect(pg.locator("#status")).to_have_text("WLAN · just now")
        expect(_tile(pg, "Cooler")).to_be_visible()
        assert pg.locator(".tile.stale").count() == 0
        assert broker.logins[0][0][-2:] == ["app", "app-pass"]

        # a command over WiFi: the ESP answers it
        answers.append('{"ok":true,"applied":true}')
        pg.evaluate("command('cooler', 'power', 'on')")
        expect(pg.get_by_text("✓ Applied")).to_be_visible()
        q = open_req(broker.published[-1][1])
        assert (q["m"], q["p"], json.loads(q["b"])["function"]) == ("POST", "/api/command", "cooler")

        # the van goes offline: no LoRa data yet -> "offline"; a LoRa uplink -> "LoRa", the rest greyed
        broker.publish("oc/%s/online" % VAN, b"0", retain=True)
        pg.clock.fast_forward(2500)
        expect(pg.locator("#status")).to_have_text("offline")
        t = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        broker.publish(
            "oc/%s/lora/3" % VAN, bridge.seal(3, {"fcnt": 1, "t": t, "hex": port3_cooler_on()}), retain=True
        )
        pg.clock.fast_forward(2500)
        expect(pg.locator("#status")).to_have_text(re.compile("^LoRa · "))
        expect(_tile(pg, "Air heater")).to_have_class(re.compile(r"\bstale\b"))
        expect(_tile(pg, "Cooler")).not_to_have_class(re.compile(r"\bstale\b"))

        # the heater: no answer in 30 s and the van offline -> one LoRa command (FPort 20) to the fake TTN
        n = len(broker.published)
        pg.evaluate("command('airheater', 'power', 'on')")
        for _ in range(100):  # the request is out before the 30 s run
            if len(broker.published) > n:
                break
            time.sleep(0.05)
        pg.clock.fast_forward(31000)
        expect(pg.get_by_text("Sent over LoRa.")).to_be_visible()
        assert [open_req(p)["p"] for tp, p in broker.published[n:] if tp.endswith("/req")] == ["/api/command"]
        assert [(d[1], len(d[2]), d[2][4:6]) for d in ttn.downlinks] == [(20, 15, (42).to_bytes(2, "big"))]
        assert pg.evaluate("window.__csp") == []


def test_key_changed(world):
    broker, _, url, init = world
    other = Sealer()
    good = Sealer()
    broker.publish("oc/%s/online" % VAN, b"1", retain=True)
    broker.publish("oc/%s/state" % VAN, good.seal(1, raw_state()), retain=True)
    with _error_gated_page(url, init_script=init, service_workers="block") as pg:
        expect(pg.locator("#status")).to_have_text("WLAN · just now")
        head = bytes([1, 1, 1]) + other.sess + (1).to_bytes(4, "big")  # a fresh frame under another key
        broker.publish("oc/%s/state" % VAN, head + AESGCM(b"\xff" * 16).encrypt(head[2:], b"{}", head))
        expect(pg.get_by_text("Key changed: scan the hub QR")).to_be_visible()
