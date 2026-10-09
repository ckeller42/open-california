"""Render every web-UI screen against the mock and save PNGs for review.

This is the feedback loop that lets the GUI be reviewed WITHOUT the vehicle: it launches the
real daemon + web UI over the in-process mock (`tools.run_against_mock serve --web`, with the
mock's realistic seeded data), drives headless Chromium, and screenshots the dashboard + every
installed feature screen in light and dark themes.

    pip install playwright && python -m playwright install chromium
    python -m tools.ux_gallery                 # -> /tmp/ux/*.png
    python -m tools.ux_gallery --out ./gallery

Review the PNGs (or send them on) after any GUI change — layout, label, and data-rendering
issues show up here that a headless assertion can't judge. The hard-assertion counterpart is
`tests/e2e/test_gui.py::test_no_red_flag_text`.

It also renders the ESP32 satellite's status/setup page (#154) for the docs
(``docs/howto-esp-wifi-setup.md``): ``esp-setup-page.png`` (setup hotspot mode) and
``esp-status-page.png`` (station mode). No firmware, BLE or radio is involved: a small stdlib HTTP
stub (``EspStub``) serves the generated page bytes the firmware serves (``firmware/web/index_gen.html``)
plus canned ``/api/state`` / ``/api/wifi`` JSON (``esp_fixtures``) whose key sets
``tests/test_ux_gallery_esp_fixtures.py`` pins to what ``tests/firmware/test_web_handlers.py``
asserts of the real handlers, so the pictures cannot drift from the firmware's API shape.

    python -m tools.ux_gallery --esp --out docs/screenshots   # only the two ESP page shots

Station-like stub modes serve the gzipped calictl UI bundle at ``/`` (as ``web.c``) and the page at
``/device``; the ``satellite`` / ``calictl`` modes back ``tests/e2e/test_satellite.py``.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The Vehicle-tab feature screens, by dashboard-tile title.
SCREENS = ["Cooler", "Camping mode", "Lighting", "Air heater", "Water", "Energy", "Vehicle"]
ESP_PAGE = os.path.join(ROOT, "firmware", "web", "index_gen.html")
APP_BUNDLE_HEADER = os.path.join(ROOT, "firmware", "web", "app_bundle_gen.h")
APP_MODES = ("station", "satellite", "calictl")  # GET / = the calictl UI bundle, as web.c in station mode
# The functions the station-mode fixture shows, decoded from the mock unit's seeded state (the page
# shows every function the session holds; a few keep the figure readable).
ESP_FUNCTIONS = ("cooler", "water")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_mock_server():
    port = _free_port()
    env = dict(
        os.environ,
        CALICTL_ADDR="MO:CK:CA:MP:ER:00",
        PYTHONUNBUFFERED="1",
        CALICTL_ARM_DELAY_S="0.2",
        CALICTL_SETTLE_S="0.2",
        CALICTL_HEARTBEAT_PERIOD_S="0.1",
        CALICTL_HEARTBEAT_WARMUP_S="0",
        CALICTL_STATE_CACHE="/tmp/calictl_gallery_state.json",
        CALICTL_ENABLE_WRITES="1",
    )  # screenshots show live controls, not the read-only default
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tools.run_against_mock",
            "serve",
            "--web",
            str(port),
            "--interval",
            "1",
            "--no-influx",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:%d" % port
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            state = json.load(urllib.request.urlopen(base + "/api/state", timeout=1))
            if len([k for k, v in state.items() if isinstance(v, dict) and v.get("installed")]) >= 6:
                return proc, base
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError("mock server exited early")
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("mock server did not become ready")


def capture(out_dir, screens=SCREENS):
    """Screenshot the dashboard + each feature screen (light + dark) into out_dir. Returns paths."""
    from playwright.sync_api import sync_playwright  # tool dep; imported lazily

    os.makedirs(out_dir, exist_ok=True)
    proc, base = _start_mock_server()
    paths = []
    try:
        with sync_playwright() as pw:
            for theme in ("light", "dark"):
                browser = pw.chromium.launch()
                ctx = browser.new_context(
                    color_scheme=theme, viewport={"width": 420, "height": 900}, device_scale_factor=2
                )
                pg = ctx.new_page()
                pg.goto(base)
                pg.wait_for_timeout(1500)
                p = os.path.join(out_dir, "%s_00_dashboard.png" % theme)
                pg.screenshot(path=p, full_page=True)
                paths.append(p)
                for i, name in enumerate(screens, 1):
                    pg.goto(base)
                    pg.wait_for_timeout(400)
                    # click the dashboard TILE (not the "Vehicle" page header, which collides)
                    pg.locator(".tile", has_text=name).first.click()
                    pg.wait_for_timeout(700)
                    p = os.path.join(out_dir, "%s_%02d_%s.png" % (theme, i, name.replace(" ", "")))
                    pg.screenshot(path=p, full_page=True)
                    paths.append(p)
                browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return paths


def _esp_fn(functions=ESP_FUNCTIONS):
    """The station fixture's ``fn`` object: ``ESP_FUNCTIONS`` decoded from the mock unit's seeded
    frames with ``calictl.protocol.decode`` (what the firmware's ``codec_decode`` produces — the host
    e2e tier asserts the two agree), in ``CODEC_CHARS`` (sorted) order."""
    from calictl import overrides, protocol
    from tools.mock_unit import MockCamperUnit, _pack_state

    unit = MockCamperUnit()
    funcs = protocol.load()
    overrides.apply(funcs)
    return {
        name: protocol.decode(funcs[name], _pack_state(funcs[name], unit.state[name]))
        for name in sorted(functions)
    }


def calictl_state(fn):
    """calictl's /api/state for these decoded functions: Python semantics + a live, read-only _meta —
    the reference the satellite page must render identically (tests/e2e/test_satellite.py)."""
    from calictl import anchors, semantics
    from calictl.serve import ServeBackend

    out = {name: semantics.interpret(name, dict(f)) for name, f in fn.items()}
    semantics.apply_sw_corrections(out)
    out["_meta"] = {
        "last_seen": time.time() - 1,
        "age_s": 1,
        "online": True,
        "paired": True,
        "read_only": True,
        "session": "off",
        "session_mode": "off",
        "firmware": ServeBackend._firmware_meta(out.get("general")),
        "anchors": anchors.check(out),
    }
    return out


def esp_fixtures():
    """Canned firmware API responses for the two page modes.

    :returns: ``{"setup": {"/api/state": {...}, "/api/wifi": {...}}, "station": {...}}`` — the
        shapes ``firmware/components/cali_core/include/cali_web.h`` documents (a first boot: no
        bond, no data, the setup hotspot up; and later: joined to the home network, reading the unit).
    """
    setup_wifi = {"mode": "setup", "ssid": None, "ip": None, "rssi": None}
    from tools.mock_unit import DEFAULT_SEED

    station_wifi = {"mode": "station", "ssid": "HomeNet", "ip": "192.168.1.57", "rssi": -58}
    station_state = {
        "t": 3912400,
        "fn": _esp_fn(),
        "device": {
            "pairing": {"state": "bonded", "address": "C0:FF:EE:CA:11:F0"},
            "link": {"up": True, "last_snap_age_ms": 1200},
            "wifi": station_wifi,
            "control": {"writes": True},
            "uptime_ms": 3912400,
            "fw": "bef07f1",
            "water_held": False,
        },
    }
    fx = {
        "setup": {
            "/api/state": {
                "t": 41250,
                "fn": {},
                "device": {
                    "pairing": {"state": "idle", "address": None},
                    "link": {"up": False, "last_snap_age_ms": None},
                    "wifi": setup_wifi,
                    "control": {"writes": False},
                    "uptime_ms": 41250,
                    "fw": "bef07f1",
                    "water_held": False,
                },
            },
            "/api/wifi": dict(
                setup_wifi,
                last_error=None,
                scan=[
                    {"ssid": "HomeNet", "rssi": -52, "secure": True},
                    {"ssid": "Campsite-Guest", "rssi": -71, "secure": True},
                    {"ssid": "Neighbour-5G", "rssi": -83, "secure": True},
                ],
            ),
        },
        "station": {
            "/api/state": station_state,
            "/api/wifi": dict(station_wifi, last_error=None, scan=[]),
        },
        "satellite": {
            "/api/state": dict(station_state, fn=_esp_fn(DEFAULT_SEED)),
            "/api/wifi": dict(station_wifi, last_error=None, scan=[]),
        },
        "calictl": {
            "/api/state": calictl_state(_esp_fn(DEFAULT_SEED)),
            "/api/pairing": {"state": "bonded", "address": "C0:FF:EE:CA:11:F0"},
        },
    }
    for mode in ("setup", "station", "satellite"):  # GET /api/pairing: calictl's snapshot, as web.c
        p = fx[mode]["/api/state"]["device"]["pairing"]
        fx[mode]["/api/pairing"] = {
            "state": p["state"],
            "attempts": 0,
            "error": None,
            "address": p["address"],
            "radio_busy": False,
        }
    return json.loads(json.dumps(fx))  # independent copies: a test mutating one mode never touches another


class EspStub:
    """A stdlib HTTP stub of the firmware's web endpoints: ``GET /`` = the generated page bytes,
    ``GET /api/state`` / ``/api/wifi`` = ``esp_fixtures()[mode]``. Switch pages with ``mode``.

    ``POST /api/command`` records the body in ``commands`` and answers ``command_status`` +
    ``command_reply`` (default: the firmware's success shape, ``applied: null``) after
    ``command_delay_s``; like the ESP's single-connection core, a ``GET /api/state`` arriving
    meanwhile waits for the answer (``polls_while_pending`` counts them).

    ``POST /api/pairing`` records the body in ``pairing_posts`` and walks the fixture's snapshot like
    the firmware's runner would, compressed: start -> ``waiting_passkey`` (from idle/error), the
    passkey ``pairing_passkey`` -> ``bonded`` (any other -> ``error`` / ``pairing_failed``), cancel ->
    ``idle``, reset (``confirm: true``) -> ``idle`` without a bond; ``pairing_status`` other than 200
    answers ``{"error": pairing_error}`` instead. ``GET /app`` is the UI bundle in every mode."""

    def __init__(self, mode="setup"):
        from tools import gen_c_dict

        self.mode = mode
        self.fixtures = esp_fixtures()
        self.requests = []  # request paths, in order
        self.fail_state = 0  # the next N GET /api/state answer 503
        self.fail_pairing = 0  # the next N GET /api/pairing answer 503
        self.commands = []  # POST /api/command bodies, in order
        self.command_status = 200  # web.c: 200 ok/refused, 400/403/409/502/503/504 {"ok":false,"error":code}
        self.command_reply = None  # the JSON body; None = the success shape for the posted function
        self.command_delay_s = 0.0  # the ESP answers after the unit's ACK (<= CALI_CTL_DEADLINE_MS)
        self.polls_while_pending = 0
        self.on_command = None  # callable(body): change the fixtures as the unit would (a push)
        self.pairing_posts = []  # POST /api/pairing bodies, in order
        self.pairing_passkey = "123456"  # the code the stub "unit" shows
        self.pairing_status, self.pairing_error = 200, None  # e.g. 409, "busy"
        self.pairing_get_delay_s = 0.0  # GET /api/pairing answers this late (a stalled core)
        self.pairing_gets_max = 0  # the most GET /api/pairing ever in flight at once
        self._pairing_gets = 0
        self._count = threading.Lock()
        self._pending = threading.Lock()  # held while a command is being answered
        with open(ESP_PAGE, "rb") as f:
            page = f.read()
        with open(APP_BUNDLE_HEADER, encoding="utf-8") as f:
            bundle = gen_c_dict.header_array_bytes(f.read())
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _json(self, status, obj):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802 (http.server API)
                path = self.path.split("?", 1)[0]
                stub.requests.append(path)
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if path == "/api/pairing":
                    stub.pairing_posts.append(body)
                    if stub.pairing_status != 200:
                        self._json(stub.pairing_status, {"error": stub.pairing_error})
                        return
                    self._json(200, stub._pair(body))
                    return
                if path != "/api/command":
                    self.send_error(404)
                    return
                with stub._pending:
                    stub.commands.append(body)
                    if stub.on_command:
                        stub.on_command(body)
                    time.sleep(stub.command_delay_s)
                    reply = stub.command_reply or {
                        "ok": True,
                        "applied": None,
                        "state": None,
                        "error": None,
                        "function": body.get("function"),
                    }
                    self._json(stub.command_status, reply)

            def do_GET(self):  # noqa: N802 (http.server API)
                path = self.path.split("?", 1)[0]
                stub.requests.append(path)
                fx = stub.fixtures[stub.mode]
                enc = None
                if path == "/api/pairing" and stub.pairing_get_delay_s:
                    with stub._count:
                        stub._pairing_gets += 1
                        stub.pairing_gets_max = max(stub.pairing_gets_max, stub._pairing_gets)
                    time.sleep(stub.pairing_get_delay_s)
                    with stub._count:
                        stub._pairing_gets -= 1
                if path == "/api/state" and stub.fail_state > 0:
                    stub.fail_state -= 1
                    self.send_error(503)
                    return
                if path == "/api/pairing" and stub.fail_pairing > 0:
                    stub.fail_pairing -= 1
                    self.send_error(503)
                    return
                if path == "/api/state" and stub._pending.locked():
                    stub.polls_while_pending += 1
                    with stub._pending:  # the core serves nothing while a command pends
                        pass
                if path == "/app" or (path == "/" and stub.mode in APP_MODES):  # web.c: the UI bundle
                    body, ctype, enc = bundle, "text/html; charset=utf-8", "gzip"
                elif path in ("/", "/device"):
                    body, ctype = page, "text/html; charset=utf-8"
                elif path in fx:
                    body, ctype = json.dumps(fx[path]).encode(), "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                if enc:
                    self.send_header("Content-Encoding", enc)
                    self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _pair(self, body):
        """The compressed runner walk behind ``POST /api/pairing`` (see the class docstring)."""
        fx = self.fixtures[self.mode]
        snap, dev = fx["/api/pairing"], fx["/api/state"]["device"]["pairing"]
        action = body.get("action")
        if action == "start" and snap["state"] in ("idle", "error"):
            snap.update(state="waiting_passkey", attempts=0, error=None)
        elif action == "passkey" and snap["state"] == "waiting_passkey":
            if body.get("value") == self.pairing_passkey:
                snap.update(state="bonded", address="C0:FF:EE:CA:11:F0")
            else:
                snap.update(state="error", attempts=3, error="pairing_failed")
        elif action == "cancel":
            snap.update(state="idle", attempts=0, error=None)
        elif action == "reset" and body.get("confirm") is True:
            snap.update(state="idle", attempts=0, error=None, address=None)
        dev.update(state=snap["state"], address=snap["address"])
        return dict(snap)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def capture_esp(out_dir):
    """Screenshot the firmware page in setup and station mode (light, English) into out_dir.

    :returns: the two PNG paths (``esp-setup-page.png``, ``esp-status-page.png``)
    """
    from playwright.sync_api import sync_playwright  # tool dep; imported lazily

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    with EspStub() as stub, sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            color_scheme="light",
            locale="en-US",
            viewport={"width": 420, "height": 900},
            device_scale_factor=2,
        )
        for mode, name in (("setup", "esp-setup-page.png"), ("station", "esp-status-page.png")):
            stub.mode = mode
            pg = ctx.new_page()
            pg.goto(stub.base + ("/device" if mode == "station" else "/"))
            if mode == "setup":
                pg.wait_for_selector("#ssid option", state="attached")
                pg.fill("#psk", "example-passphrase")
                pg.wait_for_timeout(4500)  # the page's follow-up re-scan read clears "Searching…"
            else:
                pg.wait_for_selector("#functions .box")
                pg.wait_for_timeout(500)
            p = os.path.join(out_dir, name)
            pg.screenshot(path=p, full_page=True)
            paths.append(p)
            pg.close()
        browser.close()
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description="screenshot the web UI (over the mock) for review")
    ap.add_argument("--out", default="/tmp/ux", help="output directory for the PNGs")
    ap.add_argument(
        "--esp",
        action="store_true",
        help="only the ESP32 firmware page (setup + station mode, from fixtures)",
    )
    args = ap.parse_args(argv)
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise SystemExit(
            "needs playwright: pip install playwright && python -m playwright install chromium"
        ) from None
    paths = [] if args.esp else capture(args.out)
    paths += capture_esp(args.out)
    print("wrote %d screenshots to %s" % (len(paths), args.out))


if __name__ == "__main__":
    main()
