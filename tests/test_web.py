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
