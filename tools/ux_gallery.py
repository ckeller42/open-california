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

It also renders the ESP32 satellite's WiFi setup screen (#154) for the docs
(``docs/howto-esp-wifi-setup.md``): ``esp-setup-page.png`` (the web UI over the setup hotspot) and
``esp-recovery-page.png`` (``/device``). No firmware, BLE or radio is involved: a small stdlib HTTP
stub (``EspStub``) serves the bytes the firmware serves (the gzipped UI bundle at ``/`` and ``/app``
in every mode, as ``web.c``; the recovery page ``firmware/web/index_gen.html`` at ``/device``)
plus canned ``/api/state`` / ``/api/wifi`` JSON (``esp_fixtures``) whose key sets
``tests/test_ux_gallery_esp_fixtures.py`` pins to what ``tests/firmware/test_web_handlers.py``
asserts of the real handlers, so the pictures cannot drift from the firmware's API shape.

    python -m tools.ux_gallery --esp --out docs/screenshots   # only the ESP shots

The ``satellite`` / ``calictl`` modes back ``tests/e2e/test_satellite.py``.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
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


OTA_IDLE = {
    "state": "idle",
    "available_sha": None,
    "progress_pct": 0,
    "error": None,
    "channel": "stable",
}  # device.ota
# device.display (R_FW_DISPLAY_WAKE): a lit screen in an evening van, default settings
DISPLAY_ON = {
    "state": "on",
    "lux": 42,
    "prox": 12,
    "motion": 3,
    "brightness_pct": 68,
    "sensor": "ok",
    "auto": True,
    "prox_wake": True,
    "motion_wake": True,
    "min": 8,
    "max": 100,
    "offset": 0,
    "dfs": True,
    "pm": "40-240",
}

# device.pi_wake (R_FW_PI_WAKE + R_FW_PI_HALT): never pulsed, parked 18 min of the default 30
PI_WAKE = {
    "enabled": True,
    "last_s": None,
    "reason": None,
    "halt_after_min": 30,
    "halt_soc_pct": 30,
    "halt_in_s": 720,
    "halted": False,
    "halt_last_s": None,
    "halt_reason": None,
    "batt_low": False,
}


# /api/state ext while exp_sensors is on (R_FW_SENSORS): one sensor of each type, by default alias (= its type id)
EXT_SENSORS = {
    "govee": {"type": "govee", "temp_c": 16.5, "humidity": 58.6, "battery": 49, "age_s": 6},
    "victron_mppt": {
        "type": "victron_mppt",
        "battery_current": 0,
        "battery_voltage": 13.21,
        "charge_state": 0,
        "error_code": 0,
        "load_current": 0,
        "pv_power": 0,
        "yield_today_kwh": 0.19,
        "age_s": 7,
    },
    "victron_battery": {
        "type": "victron_battery",
        "battery_voltage": 13.32,
        "temperature_c": 17.25,
        "age_s": 7,
    },
}
MPPT_100_20 = {  # a second solar charger (a two-MPPT van)
    "type": "victron_mppt",
    "battery_current": 2.3,
    "battery_voltage": 13.52,
    "charge_state": 3,
    "error_code": 0,
    "load_current": None,
    "pv_power": 87,
    "yield_today_kwh": 0.42,
    "age_s": 9,
}
# The ext combinations the shared UI must place (Energy / Vehicle / Sensors card), by name:
# tests/e2e/test_satellite.py swaps one into the satellite fixture's /api/state.
EXT_VARIANTS = {
    "none": {},
    "mppt": {"victron_mppt": EXT_SENSORS["victron_mppt"]},
    "mppt+battery": {k: EXT_SENSORS[k] for k in ("victron_mppt", "victron_battery")},
    "two-mppt": {"dach": EXT_SENSORS["victron_mppt"], "heck": MPPT_100_20},
    "govee": {"govee": EXT_SENSORS["govee"]},
    "all": EXT_SENSORS,
    "unknown": {"acme": {"type": "acme_gas", "co2": 800, "age_s": 5}},  # a type this UI does not know
}


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
            "ota": dict(OTA_IDLE),
            "time": {"source": "sntp", "synced": True, "boot": 17, "up": 3912400},
            "sd": {"state": "ok", "total_mb": 30413, "free_mb": 28774, "last_seq": 1042, "dropped": 0},
            "res": {"heap_free_kb": 61, "heap_min_kb": 38, "psram_free_kb": 7302, "cpu_pct": 4},
            "battery": {"pct": 64, "mv": 3890, "usb": False, "charging": False, "runtime_min": 130},
            "display": dict(DISPLAY_ON),
            "auto_camper": {"enabled": True, "state": "idle"},
            "pi_wake": dict(PI_WAKE),
            "ble": {"mode": "hold", "probing": False, "exp": None, "unit_adv_seen_ms_ago": None},
            "ux": {"enabled": True},
            "experimental": {"sensors": False},
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
                    "ota": dict(OTA_IDLE),
                    "time": {"source": "sntp", "synced": False, "boot": 1, "up": 41250},
                    "sd": {
                        "state": "absent",
                        "total_mb": None,
                        "free_mb": None,
                        "last_seq": None,
                        "dropped": 0,
                    },
                    "res": {"heap_free_kb": 74, "heap_min_kb": 52, "psram_free_kb": 7390, "cpu_pct": 2},
                    "battery": {"pct": 87, "mv": 4120, "usb": True, "charging": True, "runtime_min": None},
                    "display": dict(DISPLAY_ON),
                    "auto_camper": {"enabled": True, "state": "idle"},
                    "pi_wake": dict(PI_WAKE),
                    "ble": {"mode": "hold", "probing": False, "exp": None, "unit_adv_seen_ms_ago": None},
                    "ux": {"enabled": True},
                    "experimental": {"sensors": False},
                    "water_held": False,
                },
            },
            "/api/wifi": dict(
                setup_wifi,
                last_error=None,
                reverted=False,
                scan=[
                    {"ssid": "HomeNet", "rssi": -52, "secure": True},
                    {"ssid": "Campsite-Guest", "rssi": -71, "secure": True},
                    {"ssid": "Neighbour-5G", "rssi": -83, "secure": True},
                ],
            ),
        },
        "station": {
            "/api/state": station_state,
            "/api/wifi": dict(station_wifi, last_error=None, reverted=False, scan=[]),
        },
        "satellite": {  # + the experimental sensor track on: top-level ext (R_FW_SENSORS)
            "/api/state": dict(
                station_state,
                fn=_esp_fn(DEFAULT_SEED),
                device=dict(station_state["device"], experimental={"sensors": True}),
                ext=EXT_SENSORS,
            ),
            "/api/wifi": dict(station_wifi, last_error=None, reverted=False, scan=[]),
        },
        "calictl": {
            "/api/state": calictl_state(_esp_fn(DEFAULT_SEED)),
            "/api/pairing": {"state": "bonded", "address": "C0:FF:EE:CA:11:F0"},
        },
    }
    fx["satellite"]["/api/sensors"] = {  # Satellite settings' Sensors box polls it while the track is on
        "configured": [
            {
                "id": 0x1001 + 2 * i,  # boot-local ids, never a MAC
                "type": e["type"],
                "alias": alias,
                "last_seen_s": e["age_s"],
                "value": {k: v for k, v in e.items() if k not in ("age_s", "type")},
                "has_key": e["type"] != "govee",
            }
            for i, (alias, e) in enumerate(EXT_SENSORS.items())
        ],
        "seen": [],
        "scan": "ok",
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


def _pi_halt(dev, body):
    """POST /api/pi_halt: the firmware's halt pulse as device.pi_wake shows it."""
    dev["pi_wake"].update(halted=True, halt_last_s=0, halt_reason="manual", halt_in_s=None)


def _pi_setting(dev, body):
    """POST /api/pi_wake_setting: any subset of enabled / halt_after_min / halt_soc_pct."""
    dev["pi_wake"].update(body)


def _set(member, key):
    """``device[member][key] = body[key]`` when the POST carries it (the firmware's settings rule)."""

    def apply(dev, body):
        if key in body:
            dev.setdefault(member, {})[key] = body[key]

    return apply


# The satellite's settings endpoints (web.c) as the Satellite-settings screen posts them: what each
# POST changes in device (/api/ota only starts work, the state moves on by itself).
_SETTINGS = {
    "/api/auto_camper": _set("auto_camper", "enabled"),
    "/api/pi_wake_setting": _pi_setting,
    "/api/pi_halt": _pi_halt,
    "/api/pi_wake": lambda dev, body: dev["pi_wake"].update(last_s=0, reason="manual"),
    "/api/ux_setting": _set("ux", "enabled"),
    "/api/experimental": _set("experimental", "sensors"),
    "/api/display": lambda dev, body: dev["display"].update(body),
    "/api/ota": lambda dev, body: dev["ota"].update({k: v for k, v in body.items() if k == "channel"}),
}


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
        ``idle``, ``pairing_scan_polls = N``: start -> ``scanning`` first, ``waiting_passkey`` from the
        N-th GET /api/pairing on (the firmware's walk), reset (``confirm: true``) -> ``idle`` without a bond; ``pairing_status`` other than 200
        answers ``{"error": pairing_error}`` instead. ``POST /api/ux`` (the UI's usage beacons,
        R_UX_TELEMETRY) appends the parsed body to ``ux_beacons`` and answers ``ux_status`` (204; 404 =
        a firmware without the endpoint). ``POST /api/sensors`` applies the firmware's add/remove/rename
    rules (``_sensors``; bodies in ``sensor_posts``), a removed sensor is seen again on the next
    ``GET /api/sensors``. ``POST /api/wifi`` appends the body to ``wifi_posts`` and answers
    ``{"ok": true}``; a POST to a settings endpoint (``_SETTINGS``: auto camper, usage data,
    experimental, display, OTA) is recorded in ``posts`` as ``(path, body)``, applied to the fixture's
    ``device`` and answered ``{"ok": true}`` (``post_reply[path] = (status, body)`` answers that
    instead, changing nothing); ``down = True`` makes the stub answer nothing at all (the satellite left the
    network to join another one) until it is set back. ``GET /app`` is the UI bundle in every mode, and the
        manifest + icons (``gen_c_dict.pwa_assets``) are served in every mode too."""

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
        self.ux_beacons = []  # POST /api/ux bodies, in order
        self.ux_status = 204
        self.sensor_posts = []  # POST /api/sensors bodies, in order
        self.wifi_posts = []  # POST /api/wifi bodies, in order
        self.down = False  # True: every request gets no answer (the connection just closes)
        self.posts = []  # (path, body) of every POST to a _SETTINGS endpoint, in order
        self.post_reply = {}  # path -> (status, body) instead of applying the POST and {"ok": true}
        self._sens_back = []  # removed sensors, seen again on the next GET /api/sensors
        self._sens_model = {}  # id -> the advert's model, kept while configured
        self.pairing_passkey = "123456"  # the code the stub "unit" shows
        self.pairing_scan_polls = (
            0  # start -> "scanning" for this many GET /api/pairing (the firmware's walk)
        )
        self._scan_left = 0
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
        pwa = {path: (ctype, data) for path, ctype, data in gen_c_dict.pwa_assets()}
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
                if stub.down:
                    return  # no answer: the server closes the connection
                if path == "/api/wifi":
                    stub.wifi_posts.append(body)
                    self._json(200, {"ok": True})
                    return
                if path in _SETTINGS:
                    stub.posts.append((path, body))
                    if path in stub.post_reply:
                        self._json(*stub.post_reply[path])
                        return
                    _SETTINGS[path](stub.fixtures[stub.mode]["/api/state"]["device"], body)
                    self._json(200, {"ok": True})
                    return
                if path == "/api/pairing":
                    stub.pairing_posts.append(body)
                    if stub.pairing_status != 200:
                        self._json(stub.pairing_status, {"error": stub.pairing_error})
                        return
                    self._json(200, stub._pair(body))
                    return
                if path == "/api/ux":
                    stub.ux_beacons.append(body)
                    self.send_response(stub.ux_status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path == "/api/sensors":
                    stub.sensor_posts.append(body)
                    self._json(*stub._sensors(body))
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
                if stub.down:
                    return
                fx = stub.fixtures[stub.mode]
                enc = None
                if path == "/api/pairing" and stub.pairing_get_delay_s:
                    with stub._count:
                        stub._pairing_gets += 1
                        stub.pairing_gets_max = max(stub.pairing_gets_max, stub._pairing_gets)
                    time.sleep(stub.pairing_get_delay_s)
                    with stub._count:
                        stub._pairing_gets -= 1
                snap = fx.get("/api/pairing")
                if path == "/api/pairing" and snap and snap["state"] == "scanning":  # the unit found later
                    stub._scan_left -= 1
                    if stub._scan_left <= 0:
                        snap["state"] = fx["/api/state"]["device"]["pairing"]["state"] = "waiting_passkey"
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
                if path == "/api/sensors" and path in fx:  # the next window sees removed ones again
                    fx[path]["seen"] += stub._sens_back
                    stub._sens_back = []
                if path in ("/", "/app"):  # web.c: the UI bundle, every mode
                    body, ctype, enc = bundle, "text/html; charset=utf-8", "gzip"
                elif path == "/device":  # the recovery page
                    body, ctype = page, "text/html; charset=utf-8"
                elif path in fx:
                    body, ctype = json.dumps(fx[path]).encode(), "application/json"
                elif path in pwa:  # web.c: the manifest + icons, every mode
                    ctype, body = pwa[path]
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                if enc:
                    self.send_header("Content-Encoding", enc)
                    self.send_header("Cache-Control", "no-cache")
                elif path in pwa:
                    self.send_header("Cache-Control", "max-age=86400")  # web.c's PWA assets
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
            self._scan_left = self.pairing_scan_polls
            snap.update(state="scanning" if self._scan_left else "waiting_passkey", attempts=0, error=None)
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

    def _sensors(self, body):
        """``POST /api/sensors`` by the firmware's rules (``sensors.c``, ``web.c``) in miniature.

        :returns: ``(status, body)`` — 200 + the GET body, or 400 ``{"ok": false, "error": code}``.
        """
        s = self.fixtures[self.mode]["/api/sensors"]
        cfg, seen, alias = s["configured"], s["seen"], body.get("alias")

        def by_id(rows, key):
            return next((r for r in rows if r["id"] == body.get(key)), None)

        def taken(a, me=None):
            return any(c["alias"] == a and c is not me for c in cfg)

        bad = None
        if alias is not None and not re.fullmatch(r"[a-z0-9_-]{1,16}", alias):
            bad = "bad_alias"
        elif "add" in body:
            r, key = by_id(seen, "add"), body.get("key")
            if r is None:
                bad = "unknown_id"
            elif alias is None and any(c["type"] == r["type"] for c in cfg):
                bad = "bad_alias"  # the default alias only for the first of a type
            elif taken(alias or r["type"]):
                bad = "bad_alias"
            elif (r["type"].startswith("victron") or key is not None) and not re.fullmatch(
                r"[0-9a-fA-F]{32}", key or ""
            ):
                bad = "bad_key"
            else:
                seen.remove(r)
                self._sens_model[r["id"]] = r["model"]
                cfg.append(
                    {
                        "id": r["id"],
                        "type": r["type"],
                        "alias": alias or r["type"],
                        "last_seen_s": None,
                        "value": None,
                        "has_key": key is not None,
                    }
                )
        elif "remove" in body:
            c = by_id(cfg, "remove")
            if c is None:
                bad = "unknown_id"
            else:
                cfg.remove(c)
                model = self._sens_model.pop(c["id"], c["type"])
                self._sens_back.append({"id": c["id"], "type": c["type"], "model": model, "rssi": -70})
        elif "id" in body:
            c = by_id(cfg, "id")
            if c is None:
                bad = "unknown_id"
            elif alias is None or taken(alias, c):
                bad = "bad_alias"
            else:
                c["alias"] = alias
        else:
            bad = "bad_json"
        return (400, {"ok": False, "error": bad}) if bad else (200, s)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def capture_esp(out_dir):
    """Screenshot, over the setup hotspot (light, English, into out_dir), the web UI's WiFi setup
    screen and the ``/device`` recovery page.

    :returns: the PNG paths (``esp-setup-page.png``, ``esp-recovery-page.png``)
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
        for path, name in (("/", "esp-setup-page.png"), ("/device", "esp-recovery-page.png")):
            pg = ctx.new_page()
            pg.goto(stub.base + path)
            pg.wait_for_selector("#ssid option", state="attached")
            pg.fill("#psk", "example-passphrase")
            pg.wait_for_timeout(4500)  # the follow-up re-scan read clears "Searching…"
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
        help="only the ESP32 satellite shots (WiFi setup + recovery page, from fixtures)",
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
