# App-Replica Webserver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone web UI that mirrors the CaliforniaOnTour app's Vehicle-tab UX (dashboard → per-feature control screens), served by the `calictl serve` daemon and wired to the live unit through `on_command` + the poll cache.

**Architecture:** A build step compiles `ui/screens/*.yaml` + neutral `ui/labels.yaml` into `calictl/webui/screens.json`. Authored static HTML/CSS/JS in `calictl/webui/` fetch that JSON and render the app-like UI client-side. A stdlib `http.server` in `calictl/web.py` (embedded in the one BLE-owning `serve` process) serves those assets plus a JSON API (`/api/state`, `/api/screens`, `/api/command`) backed by a small `Backend` interface. No new BLE connection, no MQTT broker.

**Tech Stack:** Python stdlib (`http.server`, `json`) at runtime; PyYAML in the build tool only; vanilla HTML/CSS/JS frontend (no framework). Tests: pytest, backed by `tools/mock_unit.py`.

## Global Constraints

- **Runtime `calictl/*` is stdlib-only at import.** `calictl/web.py` imports only stdlib; it is lazy-imported from `serve`. It reads pre-built `screens.json` (stdlib `json`) — never parses YAML at runtime.
- **`serve` is the single BLE owner.** The web layer never opens BLE: state from the poll cache, commands via `asyncio.run_coroutine_threadsafe(serve.on_command(...), loop)` under the existing lock.
- **No VW copyrighted content committed.** Committed assets carry only neutral labels + placeholder icons; a CI/test guard enforces it. Real text/icons load locally at build time only (`--strings`, `--icons`), gitignored.
- **Scope = Vehicle-tab control flow:** screens `home, coolbox, camping-mode, lighting, air-heater, roof, water, energy, vehicle-info`. Uninstalled features are `Installed`-gated hidden.
- **Safety:** cooler + camping actuate; lighting renders but reports `not_applied`; roof requires an explicit `confirm`; `--read-only` disables all commands.
- **Python floor 3.11**, matrix `3.11/3.12/3.13` (existing CI).

---

### Task 1: Build tool — compile screens + neutral labels to `screens.json`

**Files:**
- Create: `ui/labels.yaml`
- Create: `tools/build_web.py`
- Test: `tests/test_build_web.py`

**Interfaces:**
- Produces: `tools.build_web.humanize(label_key: str|None) -> str|None`; `tools.build_web.resolve_label(label_key, labels: dict, strings: dict|None) -> str|None`; `tools.build_web.build(strings: dict|None=None) -> dict` returning `{"screens": {name: {"screen","function","title","widgets":[{"id","type","label","range","controls"}]}}, "order": [name,...]}`; `tools.build_web.main(argv=None)` writing `calictl/webui/screens.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_web.py
import json
import pytest
from tools import build_web


def test_humanize_strips_affixes_and_splits_camelcase():
    assert build_web.humanize("coolboxPage_coolingLevelSlider_coolingLevel_text") == "Cooling level"
    assert build_web.humanize("campingModePage_usbWidget_usbCharger_text") == "Usb charger"
    assert build_web.humanize(None) is None


def test_resolve_label_prefers_strings_then_labels_then_humanize():
    labels = {"coolboxPage_x_text": "Refrigerator box"}
    # local overlay (real VW text) wins when present
    assert build_web.resolve_label("coolboxPage_x_text", labels, {"coolboxPage_x_text": "Kühlbox"}) == "Kühlbox"
    # committed neutral label next
    assert build_web.resolve_label("coolboxPage_x_text", labels, None) == "Refrigerator box"
    # humanized fallback last
    assert build_web.resolve_label("aPage_y_fooBar_text", {}, None) == "Foo bar"


def test_build_covers_control_scope_and_carries_controls():
    data = build_web.build()
    assert set(build_web.CONTROL_SCREENS) <= set(data["screens"])
    cooler = data["screens"]["coolbox"]
    assert cooler["function"] == "cooler"
    power = next(w for w in cooler["widgets"] if w["id"] == "refrigeratorBoxWidget")
    assert power["controls"] == {"field": "State", "action": "U0"}
    assert isinstance(power["label"], str) and power["label"]


def test_no_raw_label_keys_leak_into_output():
    data = build_web.build()
    for scr in data["screens"].values():
        assert "_text" not in (scr["title"] or "")
        for w in scr["widgets"]:
            assert "_text" not in (w["label"] or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_build_web.py -q`
Expected: FAIL (`ModuleNotFoundError: tools.build_web`).

- [ ] **Step 3: Create the neutral labels file**

```yaml
# ui/labels.yaml — NEUTRAL, generic control labels. NO VW app text (citations-only).
# label_key -> plain label. Unmapped keys fall back to mechanical humanization.
# The real VW strings load locally via `build_web --strings <apk.cvr.json>` (never committed).
coolboxPage_pageTitle_coolbox_text: Cooler
coolboxPage_refrigeratorBoxWidget_refrigeratorBox_text: Refrigerator box
coolboxPage_coolingLevelSlider_coolingLevel_text: Cooling level
campingModePage_pageTitle_camping_text: Camping mode
lightingPage_pageTitle_lighting_text: Lighting
airHeaterPage_pageTitle_airHeater_text: Air heater
roofPage_pageTitle_roof_text: Roof
waterPage_pageTitle_water_text: Water
energyPage_pageTitle_energy_text: Energy
vehicleInfoPage_pageTitle_vehicle_text: Vehicle
homePage_pageTitle_home_text: Vehicle
```

- [ ] **Step 4: Write the build tool**

```python
# tools/build_web.py
"""Compile ui/screens/*.yaml + ui/labels.yaml -> calictl/webui/screens.json.

Tooling (PyYAML allowed). Committed output carries only neutral labels; the optional local
overlay (--strings a pre-extracted APK .cvr JSON map of label_key->real text) is never
committed. See docs/superpowers/specs/2026-07-08-app-replica-webserver-design.md.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENS_DIR = ROOT / "ui" / "screens"
LABELS_FILE = ROOT / "ui" / "labels.yaml"
OUT_FILE = ROOT / "calictl" / "webui" / "screens.json"

# Vehicle-tab control-flow scope (dashboard + per-feature control screens).
CONTROL_SCREENS = ["home", "coolbox", "camping-mode", "lighting", "air-heater",
                   "roof", "water", "energy", "vehicle-info"]


def humanize(label_key):
    """Mechanical neutral label from a compose resource key (no VW text)."""
    if not label_key:
        return None
    s = re.sub(r"^[A-Za-z]+Page_", "", label_key)
    s = re.sub(r"_(text|button|title)$", "", s)
    seg = s.split("_")[-1]
    seg = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", seg)   # camelCase -> spaced
    seg = seg.replace("_", " ").strip().lower()
    return seg[:1].upper() + seg[1:] if seg else None


def resolve_label(label_key, labels, strings=None):
    """Local overlay (real VW text) > committed neutral label > humanized fallback."""
    if not label_key:
        return None
    if strings and label_key in strings:
        return strings[label_key]
    if label_key in labels:
        return labels[label_key]
    return humanize(label_key)


def build(strings=None):
    import yaml   # lazy: tooling only
    labels = yaml.safe_load(LABELS_FILE.read_text()) or {}
    screens = {}
    for name in CONTROL_SCREENS:
        spec = yaml.safe_load((SCREENS_DIR / (name + ".yaml")).read_text())
        widgets = [{
            "id": w.get("id"), "type": w.get("type"),
            "label": resolve_label(w.get("label_key"), labels, strings),
            "range": w.get("range"), "controls": w.get("controls"),
        } for w in (spec.get("widgets") or [])]
        screens[name] = {
            "screen": spec.get("screen"), "function": spec.get("function"),
            "title": resolve_label(spec.get("title_key"), labels, strings),
            "widgets": widgets,
        }
    return {"screens": screens, "order": CONTROL_SCREENS}


def main(argv=None):
    ap = argparse.ArgumentParser(description="compile the web UI screen data")
    ap.add_argument("--strings", help="local APK .cvr strings JSON (label_key->text); never committed")
    ap.add_argument("--out", default=str(OUT_FILE))
    args = ap.parse_args(argv)
    strings = json.loads(Path(args.strings).read_text()) if args.strings else None
    data = build(strings)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print("wrote %s (%d screens)" % (out, len(data["screens"])))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_build_web.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add tools/build_web.py ui/labels.yaml tests/test_build_web.py
git commit -m "feat(web): build tool compiles screens + neutral labels to screens.json"
```

---

### Task 2: Generate and commit `calictl/webui/screens.json`

**Files:**
- Create: `calictl/webui/screens.json` (generated)
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Produces: the committed `calictl/webui/screens.json` file (loaded at runtime by `calictl/web.py`, Task 3).

- [ ] **Step 1: Write the failing test (no-VW-content guard)**

```python
# tests/test_web_assets.py
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "calictl" / "webui" / "screens.json"


def test_screens_json_exists_and_matches_build():
    from tools import build_web
    assert SCREENS.exists(), "run: python3 -m tools.build_web"
    on_disk = json.loads(SCREENS.read_text())
    assert on_disk == build_web.build(), "screens.json stale — rebuild with tools.build_web"


def test_committed_assets_have_no_vw_app_text():
    # Guard: the committed data must not carry the real VW resource text. Neutral labels
    # never contain a compose key affix, and we deny a few known German app words.
    text = SCREENS.read_text()
    for banned in ("_text", "Kühlbox", "Standheizung", "Aufstelldach"):
        assert banned not in text, "possible VW app content committed: %r" % banned
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_assets.py -q`
Expected: FAIL (`screens.json` missing).

- [ ] **Step 3: Generate the asset**

Run: `python3 -m tools.build_web`
Expected: `wrote .../calictl/webui/screens.json (9 screens)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_web_assets.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add calictl/webui/screens.json tests/test_web_assets.py
git commit -m "feat(web): commit generated screens.json (neutral labels)"
```

---

### Task 3: Runtime server — `calictl/web.py` (API over a `Backend` interface)

**Files:**
- Create: `calictl/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: a `backend` duck-typed object with attributes/methods: `read_only: bool`, `state() -> dict` (`{function: interpreted_dict_including_"installed"}`), `screens_bytes() -> bytes` (the `screens.json` bytes), `command(function: str, what: str, value, confirm: bool=False) -> dict` (`{"ok": bool, "applied": bool|None, "state": dict|None, "error": str|None}`).
- Produces: `calictl.web.make_handler(backend, webui_dir: str) -> type` (an `http.server.BaseHTTPRequestHandler` subclass); `calictl.web.serve_http(backend, webui_dir, host: str, port: int) -> http.server.ThreadingHTTPServer` (created, `serve_forever` in a daemon thread); `calictl.web.ROOF_FUNCTION = "roof"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web.py
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from calictl import web

WEBUI = str(Path(__file__).resolve().parent.parent / "calictl" / "webui")


class FakeBackend:
    def __init__(self, read_only=False):
        self.read_only = read_only
        self.commands = []
        self._state = {"cooler": {"installed": True, "on": False, "level": 3},
                       "stairs": {"installed": False}}

    def state(self):
        return self._state

    def screens_bytes(self):
        return b'{"screens": {}, "order": []}'

    def command(self, function, what, value, confirm=False):
        self.commands.append((function, what, value, confirm))
        if function == "cooler" and what == "power":
            self._state["cooler"]["on"] = (str(value).lower() in ("on", "true", "1"))
            return {"ok": True, "applied": True, "state": self._state["cooler"], "error": None}
        if function == "lighting":
            return {"ok": True, "applied": False, "state": {}, "error": None}
        return {"ok": True, "applied": True, "state": {}, "error": None}


@pytest.fixture
def server():
    be = FakeBackend()
    srv = web.serve_http(be, WEBUI, "127.0.0.1", 0)   # port 0 -> ephemeral
    port = srv.server_address[1]
    yield be, "http://127.0.0.1:%d" % port
    srv.shutdown()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def _post(url, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_state_endpoint_returns_interpreted_state(server):
    _, base = server
    status, body = _get(base + "/api/state")
    assert status == 200
    assert json.loads(body)["cooler"]["installed"] is True


def test_command_routes_to_backend(server):
    be, base = server
    status, body = _post(base + "/api/command", {"function": "cooler", "what": "power", "value": "on"})
    assert status == 200 and body["applied"] is True
    assert be.commands[-1] == ("cooler", "power", "on", False)


def test_lighting_reports_not_applied(server):
    _, base = server
    _, body = _post(base + "/api/command", {"function": "lighting", "what": "brightness", "value": 8})
    assert body["ok"] is True and body["applied"] is False


def test_roof_requires_confirm(server):
    _, base = server
    status, body = _post(base + "/api/command", {"function": "roof", "what": "open"})
    assert status == 400 and body["error"] == "confirm_required"
    status2, _ = _post(base + "/api/command", {"function": "roof", "what": "open", "confirm": True})
    assert status2 == 200


def test_read_only_rejects_commands():
    be = FakeBackend(read_only=True)
    srv = web.serve_http(be, WEBUI, "127.0.0.1", 0)
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        status, body = _post(base + "/api/command", {"function": "cooler", "what": "power", "value": "on"})
        assert status == 405 and body["error"] == "read_only"
        assert be.commands == []
    finally:
        srv.shutdown()


def test_import_is_stdlib_only():
    import sys
    before = set(sys.modules)
    import importlib
    importlib.reload(web)
    for banned in ("bleak", "yaml", "paho", "influxdb_client"):
        assert banned not in sys.modules or banned in before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web.py -q`
Expected: FAIL (`AttributeError: module 'calictl.web' has no attribute 'serve_http'`).

- [ ] **Step 3: Write `calictl/web.py`**

```python
# calictl/web.py
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
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, body, ctype, status=200):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                return self._send_json(backend.state())
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
            length = int(self.headers.get("Content-Length", 0))
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
            if fn == ROOF_FUNCTION and not confirm:
                return self._send_json({"error": "confirm_required"}, 400)
            try:
                result = backend.command(fn, what, value, confirm=confirm)
            except Exception as e:  # surface, don't crash the server thread
                return self._send_json({"error": "command_failed", "detail": str(e)}, 500)
            return self._send_json(result)

    return Handler


def serve_http(backend, webui_dir, host="0.0.0.0", port=8080):
    """Create a ThreadingHTTPServer and start serving in a daemon thread. Returns it."""
    httpd = ThreadingHTTPServer((host, port), make_handler(backend, webui_dir))
    Thread(target=httpd.serve_forever, name="calictl-web", daemon=True).start()
    return httpd
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_web.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add calictl/web.py tests/test_web.py
git commit -m "feat(web): stdlib HTTP server + JSON API over a Backend interface"
```

---

### Task 4: Wire the web server into `serve` + CLI flags

**Files:**
- Modify: `calictl/serve.py`
- Modify: `calictl/cli.py`
- Test: `tests/test_web_serve.py`

**Interfaces:**
- Consumes: `calictl.web.serve_http` (Task 3); `serve.Server` with `_last` (decoded state cache), `on_command`, and its event loop.
- Produces: `serve.ServeBackend(server, loop)` implementing the Task-3 backend interface; `Server.run(..., web_port=None, read_only=False)` starting the web server when `web_port` is set.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_serve.py
import asyncio
from calictl import serve, protocol, overrides


def test_serve_backend_state_interprets_cache():
    s = serve.Server(influx_enabled=False)
    funcs = protocol.load(); overrides.apply(funcs)
    s._last = {"cooler": {"Installed": 1, "State": 1, "Level": 3, "Mode": 4}}
    be = serve.ServeBackend(s, loop=None)
    st = be.state()
    assert st["cooler"]["installed"] is True and st["cooler"]["on"] is True


def test_serve_backend_command_bridges_to_on_command(monkeypatch):
    s = serve.Server(influx_enabled=False)
    calls = []
    async def fake_on_command(fn, what, value):
        calls.append((fn, what, value)); s._last.setdefault(fn, {})
    monkeypatch.setattr(s, "on_command", fake_on_command)

    async def _run():
        loop = asyncio.get_running_loop()
        s._ble = asyncio.Lock()
        be = serve.ServeBackend(s, loop)
        # command() is sync (called from the web thread); run it off-loop
        return await asyncio.to_thread(be.command, "cooler", "power", "on", False)

    result = asyncio.run(_run())
    assert calls == [("cooler", "power", "on")]
    assert result["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_serve.py -q`
Expected: FAIL (`AttributeError: module 'calictl.serve' has no attribute 'ServeBackend'`).

- [ ] **Step 3: Add `ServeBackend` + `--web` wiring to `serve.py`**

Add near the top of `calictl/serve.py` (after the existing imports):

```python
import os as _os
from pathlib import Path as _Path

_WEBUI_DIR = str(_Path(__file__).resolve().parent / "webui")


class ServeBackend:
    """Adapts a running Server to calictl.web's backend interface (no BLE here)."""

    def __init__(self, server, loop, read_only=False):
        self._s = server
        self._loop = loop
        self.read_only = read_only

    def state(self):
        from . import semantics, protocol, overrides
        if not hasattr(self, "_funcs"):
            self._funcs = protocol.load(); overrides.apply(self._funcs)
        out = {}
        for fn, decoded in (self._s._last or {}).items():
            out[fn] = semantics.interpret(fn, decoded)
        return out

    def screens_bytes(self):
        p = _os.path.join(_WEBUI_DIR, "screens.json")
        with open(p, "rb") as f:
            return f.read()

    def command(self, function, what, value, confirm=False):
        import asyncio
        fut = asyncio.run_coroutine_threadsafe(
            self._s.on_command(function, what, value), self._loop)
        fut.result(timeout=90)                       # block the web thread, not the loop
        state = semantics_state = None
        interp = self.state().get(function)
        applied = None
        # applied-ness: on_command already read back; re-derive from cache if possible
        return {"ok": True, "applied": applied, "state": interp, "error": None}
```

> NOTE: `applied` reporting is refined in Step 3b once `on_command` returns a result.

- [ ] **Step 3b: Make `on_command` return applied-ness and use it**

In `calictl/serve.py`, change `on_command` to return a bool (or None for lighting/unknown). Find its current body (`serve.py:86` region) and ensure it returns `True`/`False`/`None` after the readback (True if the targeted field matches the request, False if not, None if no readback). Then in `ServeBackend.command`, replace the `applied = None` block with:

```python
        applied = fut.result(timeout=90)             # on_command now returns applied-ness
        interp = self.state().get(function)
        return {"ok": True, "applied": applied, "state": interp, "error": None}
```

(Delete the earlier `fut.result(...)`/`applied = None` lines so `fut.result` is called once.)

- [ ] **Step 3c: Start the web server from `Server.run`**

In `Server.run`, after the event loop and `self._ble` lock exist and the first poll has populated `_last`, add (guarded by a `web_port` attribute set from the CLI):

```python
        if getattr(self, "_web_port", None) is not None:
            from . import web
            backend = ServeBackend(self, loop, read_only=getattr(self, "_read_only", False))
            self._httpd = web.serve_http(backend, _WEBUI_DIR, "0.0.0.0", self._web_port)
            print("web UI on http://0.0.0.0:%d%s" % (
                self._web_port, "  (read-only)" if self._read_only else ""), flush=True)
```

- [ ] **Step 4: Add CLI flags in `calictl/cli.py`**

In `build_parser`, on the `serve` subparser (`sv`), add:

```python
    sv.add_argument("--web", nargs="?", const=8080, type=int, default=None,
                    metavar="PORT", help="serve the replica web UI on PORT (default 8080)")
    sv.add_argument("--read-only", action="store_true", help="web UI: reads only, reject commands")
```

In `main`, in the `serve` branch, pass them onto the Server before `.run()`:

```python
        srv = serve.Server(args.addr, interval=args.interval,
                           influx_enabled=not args.no_influx)
        srv._web_port = args.web
        srv._read_only = args.read_only
        srv.run()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_web_serve.py tests/test_calictl.py -q`
Expected: PASS (new tests + existing CLI tests still green).

- [ ] **Step 6: Commit**

```bash
git add calictl/serve.py calictl/cli.py tests/test_web_serve.py
git commit -m "feat(web): embed the web server in serve (--web PORT / --read-only)"
```

---

### Task 5: Authored frontend — dashboard + feature screens

**Files:**
- Create: `calictl/webui/index.html`
- Create: `calictl/webui/app.css`
- Create: `calictl/webui/app.js`
- Test: `tests/test_web_frontend.py`

**Interfaces:**
- Consumes: the Task-3 endpoints `GET /api/screens`, `GET /api/state`, `POST /api/command`.
- Produces: the served UI; a smoke test asserts the server returns the assets and they reference the API.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_frontend.py
import urllib.request
from pathlib import Path

from calictl import web

WEBUI = str(Path(__file__).resolve().parent.parent / "calictl" / "webui")


class _StubBackend:
    read_only = False
    def state(self): return {}
    def screens_bytes(self): return b'{"screens":{},"order":[]}'
    def command(self, *a, **k): return {"ok": True, "applied": True, "state": {}, "error": None}


def _serve():
    srv = web.serve_http(_StubBackend(), WEBUI, "127.0.0.1", 0)
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def test_index_and_assets_served():
    srv, base = _serve()
    try:
        for path, needle in (("/", b"<div id=\"app\""), ("/app.js", b"/api/state"),
                             ("/app.css", b"--card")):
            with urllib.request.urlopen(base + path) as r:
                assert r.status == 200
                assert needle in r.read()
    finally:
        srv.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_web_frontend.py -q`
Expected: FAIL (assets missing → 404).

- [ ] **Step 3: Write `calictl/webui/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VW California — Web Control</title>
<link rel="stylesheet" href="/app.css">
</head>
<body>
<header id="topbar"><button id="back" hidden>‹</button><h1 id="title">Vehicle</h1></header>
<main id="app"></main>
<script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `calictl/webui/app.css`**

```css
:root { color-scheme: light dark;
  --bg:#eef1f5; --fg:#1a1d24; --muted:#62697a; --card:#fff; --border:#dfe3ea;
  --accent:#2f6fed; --accent-fg:#fff; --track-off:#c9ced8; }
@media (prefers-color-scheme: dark){ :root{
  --bg:#14161c; --fg:#eef0f4; --muted:#9aa1b0; --card:#1d2029; --border:#2a2e39;
  --track-off:#3a3f4c; } }
*{box-sizing:border-box} body{margin:0;font:16px -apple-system,system-ui,sans-serif;
  background:var(--bg);color:var(--fg)}
#topbar{display:flex;align-items:center;gap:.5rem;padding:1rem;position:sticky;top:0;
  background:var(--bg);border-bottom:1px solid var(--border)}
#topbar h1{font-size:1.1rem;margin:0} #back{font-size:1.4rem;background:none;border:0;color:var(--accent)}
#app{max-width:640px;margin:0 auto;padding:1rem;display:grid;gap:.75rem}
.tilegrid{display:grid;grid-template-columns:repeat(2,1fr);gap:.75rem}
.card,.tile{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1rem}
.tile{cursor:pointer;text-align:left} .tile h3{margin:.2rem 0 0;font-size:1rem}
.tile .v{color:var(--muted);font-size:.85rem}
.row{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.4rem 0}
.switch{width:52px;height:30px;border-radius:15px;background:var(--track-off);position:relative;
  border:0;cursor:pointer;transition:background .15s} .switch[aria-checked="true"]{background:var(--accent)}
.switch::after{content:"";position:absolute;top:3px;left:3px;width:24px;height:24px;border-radius:50%;
  background:#fff;transition:left .15s} .switch[aria-checked="true"]::after{left:25px}
input[type=range]{width:60%} .muted{color:var(--muted)} .warn{color:#c0392b}
.badge{font-size:.7rem;padding:.1rem .4rem;border-radius:6px;background:var(--track-off);color:var(--muted)}
```

- [ ] **Step 5: Write `calictl/webui/app.js`**

```javascript
// Renders the CaliforniaOnTour Vehicle-tab replica from /api/screens + /api/state.
const app = document.getElementById("app");
const titleEl = document.getElementById("title");
const backEl = document.getElementById("back");
let SCREENS = {order: [], screens: {}};
let STATE = {};
let view = "home";
let poll;

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}
async function refreshState() { STATE = await api("/api/state"); render(); }

async function command(function_, what, value, confirm) {
  const body = {function: function_, what, value, confirm: !!confirm};
  const res = await api("/api/command", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (res.applied === false) toast("Sent — but the unit did not apply it (known gap).");
  await refreshState();
  return res;
}

function installed(fn) { return !fn || !STATE[fn] || STATE[fn].installed !== false; }
function toast(msg){ const d=document.createElement("div"); d.className="card"; d.textContent=msg;
  d.style.position="fixed"; d.style.bottom="1rem"; d.style.left="50%"; d.style.transform="translateX(-50%)";
  document.body.appendChild(d); setTimeout(()=>d.remove(), 3500); }

function goto(v){ view=v; render(); }

function render() {
  backEl.hidden = (view === "home");
  backEl.onclick = () => goto("home");
  app.innerHTML = "";
  if (view === "home") return renderDashboard();
  renderScreen(view);
}

function renderDashboard() {
  titleEl.textContent = "Vehicle";
  const grid = document.createElement("div"); grid.className = "tilegrid";
  for (const name of SCREENS.order) {
    const scr = SCREENS.screens[name];
    if (name === "home" || !installed(scr.function)) continue;
    const tile = document.createElement("button"); tile.className = "tile";
    tile.innerHTML = `<h3>${scr.title || name}</h3><div class="v">${summary(scr)}</div>`;
    tile.onclick = () => goto(name);
    grid.appendChild(tile);
  }
  app.appendChild(grid);
}

function summary(scr) {
  const st = STATE[scr.function]; if (!st) return "";
  if ("on" in st) return st.on ? "On" : "Off";
  if ("percent" in st && st.percent != null) return st.percent + "%";
  return "";
}

function renderScreen(name) {
  const scr = SCREENS.screens[name]; titleEl.textContent = scr.title || name;
  const st = STATE[scr.function] || {};
  for (const w of scr.widgets) app.appendChild(renderWidget(scr, w, st));
}

function renderWidget(scr, w, st) {
  const card = document.createElement("div"); card.className = "card";
  const row = document.createElement("div"); row.className = "row";
  const label = document.createElement("span"); label.textContent = w.label || w.id;
  row.appendChild(label);
  const fn = scr.function;
  if (w.type === "toggle") {
    const sw = document.createElement("button"); sw.className = "switch";
    const on = !!(st.on ?? st[fieldToState(w)] );
    sw.setAttribute("aria-checked", on ? "true" : "false");
    sw.onclick = () => command(fn, toggleWhat(scr, w), on ? "off" : "on");
    row.appendChild(sw);
  } else if (w.type === "slider" && w.range) {
    const [lo, hi] = w.range.split("..").map(Number);
    const inp = document.createElement("input"); inp.type = "range";
    inp.min = lo; inp.max = hi; inp.value = st.level ?? lo;
    inp.onchange = () => command(fn, "level", Number(inp.value));
    row.appendChild(inp);
  } else {
    const b = document.createElement("span"); b.className = "badge"; b.textContent = w.type;
    row.appendChild(b);
  }
  card.appendChild(row);
  if (fn === "roof") card.appendChild(roofControls(fn));
  if (fn === "lighting") { const n=document.createElement("div"); n.className="muted";
    n.textContent="Note: lighting actuation is not yet supported by the unit over BLE."; card.appendChild(n); }
  return card;
}

// map a widget to the on/off "what" the command API expects (cooler power, camping master/lights/usb...)
function toggleWhat(scr, w) {
  if (scr.function === "cooler") return "power";
  const id = (w.id || "").toLowerCase();
  if (id.includes("usb")) return "usb";
  if (id.includes("light")) return "lights";
  if (id.includes("master") || id.includes("camping")) return "master";
  return "power";
}
function fieldToState(w){ return (w.controls && w.controls.field || "").toLowerCase(); }

function roofControls(fn) {
  const box = document.createElement("div");
  for (const dir of ["open", "close", "stop"]) {
    const b = document.createElement("button"); b.textContent = dir; b.className = "badge";
    b.style.marginRight = ".5rem";
    b.onclick = () => { if (confirm("Roof "+dir+": this physically moves the pop-top and is "
        +"UNVERIFIED on this vehicle. Ensure the roof path is clear. Continue?"))
        command(fn, dir, null, true); };
    box.appendChild(b);
  }
  const warn = document.createElement("div"); warn.className = "warn";
  warn.textContent = "Roof control is safety-sensitive and not live-verified."; box.appendChild(warn);
  return box;
}

async function main() {
  SCREENS = await api("/api/screens");
  await refreshState();
  poll = setInterval(refreshState, 1500);
}
main();
```

- [ ] **Step 6: Rebuild assets and run the smoke test**

Run: `python3 -m pytest tests/test_web_frontend.py -q`
Expected: PASS.

- [ ] **Step 7: Manual check (optional, needs a running serve or the mock)**

Run: `python3 -m tools.run_against_mock serve --web 8080 &` then open `http://127.0.0.1:8080` — dashboard tiles appear for installed functions; tapping Cooler shows the toggle/slider; toggling flips it (against the mock). Stop the process afterward.

> NOTE: `tools/run_against_mock.py` dispatches to `cli.main`; `serve --web` will run the daemon against the mock unit. If serve's first poll needs the mock's `read_all`, that already works (Task-1 mock).

- [ ] **Step 8: Commit**

```bash
git add calictl/webui/index.html calictl/webui/app.css calictl/webui/app.js tests/test_web_frontend.py
git commit -m "feat(web): authored dashboard + feature-screen frontend"
```

---

### Task 6: CI, gitignore, README

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: `tools.build_web` (Task 1), `tests/test_web_assets.py` (Task 2).

- [ ] **Step 1: Add a CI check that screens.json is fresh + carries no VW content**

In `.github/workflows/ci.yml`, in the `test` job after the audit step, add:

```yaml
      - name: Web assets are fresh (screens.json == build_web output)
        run: |
          python -m tools.build_web --out /tmp/screens.json
          diff -q /tmp/screens.json calictl/webui/screens.json \
            || { echo "::error::screens.json stale — run python -m tools.build_web"; exit 1; }
```

The existing `test` job already runs `pytest tests/` which includes `test_web_assets.py`'s no-VW-content guard.

- [ ] **Step 2: Gitignore the local overlay outputs**

Append to `.gitignore`:

```
# app-replica web UI: locally-overlaid VW strings/icons are never committed
ui/strings-local.json
calictl/webui/icons/
```

- [ ] **Step 3: Add a README line**

Under the "Raspberry Pi setup" or a new short "Web UI" note in `README.md`:

```markdown
## Web UI

`calictl serve --web 8080` also serves a browser replica of the app's Vehicle-tab controls
(dashboard → per-feature screens) at `http://<pi>:8080` — same process, no extra BLE
connection. `--read-only` disables commands. Labels are neutral; point the build at your own
APK for the exact app text (`python -m tools.build_web --strings <apk.cvr.json>`, local only).
See `docs/raspberry-pi-setup.md`.
```

- [ ] **Step 4: Verify + commit**

Run: `python3 -m pytest tests/ -q && sh -n install.sh` (whole suite green).
```bash
git add .github/workflows/ci.yml .gitignore README.md
git commit -m "ci+docs: web-assets freshness/no-VW-content guard; README web UI note"
```

---

## Verification (end-to-end)

- `python3 -m pytest tests/ -q` — all green (incl. build_web, web API, serve backend, frontend smoke).
- `python3 -m tools.build_web` — regenerates `screens.json` identically (CI enforces freshness).
- `python3 -m tools.run_against_mock serve --web 8080` then browse `http://127.0.0.1:8080`:
  dashboard shows installed tiles; Cooler toggles against the mock; Lighting shows the
  not-applied note; Roof requires the confirm dialog.
- Import-clean: `python3 -c "import sys, calictl.web; assert 'yaml' not in sys.modules and 'bleak' not in sys.modules"`.
- No VW content: `grep -R "_text" calictl/webui/screens.json` returns nothing.
- On buspi (after merge): add `--web 8080` to the systemd unit's `ExecStart`, restart, browse.

## Self-review notes

- **Spec coverage:** build step (T1/T2), runtime web.py + API (T3), serve/cli embedding + read-only + roof-confirm (T4), authored frontend + installed-gating + lighting-not-applied + roof-guard (T5), CI no-VW-content guard + gitignore overlay + README (T6). Labels neutral + local overlay: T1 (`--strings`) + T6 (gitignore). All spec sections mapped.
- **Type consistency:** backend interface (`state`/`screens_bytes`/`command(…, confirm=False)`/`read_only`) is identical across T3 (consumer), T3 tests (FakeBackend), and T4 (ServeBackend). `command` returns `{ok, applied, state, error}` in all three.
- **Open follow-up (not blocking):** `on_command` returning applied-ness (T4 Step 3b) touches existing daemon code — verify the current return shape when implementing and keep the MQTT path working (it ignores the return value).
