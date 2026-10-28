import gzip
import http.client
import json
import re
import urllib.request
from pathlib import Path

import pytest

from calictl import influx, web

WEBUI = str(Path(__file__).resolve().parent.parent / "calictl" / "webui")


class FakeBackend:
    def __init__(self, read_only=False):
        self.read_only = read_only
        self.commands = []
        self._state = {"cooler": {"installed": True, "on": False, "level": 3}, "stairs": {"installed": False}}

    def state(self):
        return self._state

    def screens_bytes(self):
        return b'{"screens": {}, "order": []}'

    def roof_view(self, action):
        self.commands.append(("roof_view", action))
        return {"ok": True, "viewing": action == "view"}

    def command(self, function, what, value, confirm=False):
        self.commands.append((function, what, value, confirm))
        if function == "cooler" and what == "power":
            self._state["cooler"]["on"] = str(value).lower() in ("on", "true", "1")
            return {"ok": True, "applied": True, "state": self._state["cooler"], "error": None}
        if function == "lighting":
            return {"ok": True, "applied": False, "state": {}, "error": None}
        return {"ok": True, "applied": True, "state": {}, "error": None}


@pytest.fixture
def server():
    be = FakeBackend()
    srv = web.serve_http(be, WEBUI, "127.0.0.1", 0)  # port 0 -> ephemeral
    port = srv.server_address[1]
    yield be, "http://127.0.0.1:%d" % port
    srv.shutdown()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def _post(url, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
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


@pytest.mark.parametrize("ln", [1791354615, "garbage", None, 1.5])
def test_command_accepts_and_ignores_local_now(server, ln):
    """The shared UI sends the page's clock with every wake-up; the daemon keeps its own clock, so the
    key is accepted (whatever its value) and never reaches the backend (spec 2026-10-07 §1)."""
    be, base = server
    body = {"function": "lighting", "what": "wakeup", "value": "07:00", "local_now": ln}
    status, _ = _post(base + "/api/command", body)
    assert status == 200 and be.commands == [("lighting", "wakeup", "07:00", False)]


def test_lighting_reports_not_applied(server):
    _, base = server
    _, body = _post(base + "/api/command", {"function": "lighting", "what": "brightness", "value": 8})
    assert body["ok"] is True and body["applied"] is False


def test_roof_view_routes_to_backend_and_is_refused_read_only(server):
    """``POST /api/roof`` (the roof page's view/leave) reaches the backend; a bad action is a 400 and
    a read-only daemon refuses it (the view streams vehicle writes)."""
    be, base = server
    assert _post(base + "/api/roof", {"action": "view"}) == (200, {"ok": True, "viewing": True})
    assert be.commands[-1] == ("roof_view", "view")
    assert _post(base + "/api/roof", {"action": "open"})[0] == 400
    be.read_only = True
    assert _post(base + "/api/roof", {"action": "view"})[0] == 405


def test_roof_requires_confirm(server):
    _, base = server
    status, body = _post(base + "/api/command", {"function": "roof", "what": "open"})
    assert status == 400 and body["error"] == "confirm_required"
    status2, _ = _post(base + "/api/command", {"function": "roof", "what": "open", "confirm": True})
    assert status2 == 200


def test_airheater_requires_confirm(server):
    be, base = server
    status, body = _post(base + "/api/command", {"function": "airheater", "what": "power", "value": "on"})
    assert status == 400 and body["error"] == "confirm_required"
    assert be.commands == []  # unconfirmed request must never reach the backend
    status2, body2 = _post(
        base + "/api/command", {"function": "airheater", "what": "power", "value": "on", "confirm": True}
    )
    assert status2 == 200 and body2["ok"] is True
    assert be.commands[-1] == ("airheater", "power", "on", True)


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


def _raw_post(base, path, body_bytes, headers):
    """POST with full control over headers (bypasses urllib's auto Content-Length)."""
    host_port = base.split("//", 1)[1]
    host, port = host_port.split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    try:
        conn.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders(body_bytes if body_bytes is not None else None)
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
        return status, raw
    finally:
        conn.close()


def test_bad_content_length_header_is_clean_400(server):
    be, base = server
    body = json.dumps({"function": "cooler", "what": "power", "value": "on"}).encode()
    status, raw = _raw_post(
        base, "/api/command", body, {"Content-Type": "application/json", "Content-Length": "not-a-number"}
    )
    assert status == 400
    assert json.loads(raw)["error"] == "bad_request"
    assert be.commands == []


def test_negative_content_length_header_is_clean_400_not_hang(server):
    be, base = server
    body = json.dumps({"function": "cooler", "what": "power", "value": "on"}).encode()
    status, raw = _raw_post(
        base, "/api/command", body, {"Content-Type": "application/json", "Content-Length": "-5"}
    )
    assert status == 400
    assert json.loads(raw)["error"] == "bad_request"
    assert be.commands == []


def test_non_json_content_type_rejected_415(server):
    be, base = server
    req = urllib.request.Request(
        base + "/api/command",
        data=json.dumps({"function": "cooler", "what": "power", "value": "on"}).encode(),
        method="POST",
        headers={"Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            status, body = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        status, body = e.code, json.loads(e.read())
    assert status == 415
    assert body["error"] == "unsupported_media_type"
    assert be.commands == []


def test_command_failure_does_not_leak_exception_text(server):
    be, base = server

    def boom(*_a, **_kw):
        raise RuntimeError("secret internal detail")

    be.command = boom
    status, body = _post(base + "/api/command", {"function": "cooler", "what": "power", "value": "on"})
    assert status == 500
    assert body["error"] == "command_failed"
    assert "detail" not in body
    assert "secret internal detail" not in json.dumps(body)


def test_state_failure_does_not_leak_exception_text(server):
    be, base = server

    def boom(*_a, **_kw):
        raise RuntimeError("secret internal detail")

    be.state = boom
    try:
        status, body = _get(base + "/api/state")
    except urllib.error.HTTPError as e:
        status = e.code
        body = json.loads(e.read())
    assert status == 500
    assert body["error"] == "state_failed"
    assert "detail" not in body
    assert "secret internal detail" not in json.dumps(body)


def test_path_traversal_returns_404(server):
    _, base = server
    for path in ("/../../etc/passwd", "/a/b/../../../../etc/passwd"):
        try:
            status, _ = _get(base + path)
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 404


def test_responses_are_no_cache(server):
    import urllib.request

    _, base = server
    with urllib.request.urlopen(base + "/") as r:
        assert r.headers.get("Cache-Control") == "no-cache"
    with urllib.request.urlopen(base + "/api/state") as r:  # live state: never a cached copy
        assert r.headers.get("Cache-Control") == "no-store"


def _cond_get(base, path, inm=None):
    conn = http.client.HTTPConnection(base.split("//", 1)[1])
    conn.request("GET", path, headers={"If-None-Match": inm} if inm else {})
    r = conn.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    conn.close()
    return out


def test_static_etag_and_if_none_match(server):
    """Static webui files carry a strong ETag; a matching If-None-Match is a body-less 304 (ETag +
    no-cache kept), a stale one the full 200."""
    _, base = server
    for path in ("/", "/app.js", "/sw.js", "/manifest.webmanifest", "/icon-192.png"):
        status, h, body = _cond_get(base, path)
        assert status == 200 and body and h["cache-control"] == "no-cache", path
        tag = h["etag"]
        assert tag.startswith('"') and tag.endswith('"'), path
        for inm in (tag, "W/" + tag, '"x", ' + tag, "*"):
            status, h304, body = _cond_get(base, path, inm)
            assert (status, body) == (304, b""), (path, inm)
            assert h304["etag"] == tag and h304["cache-control"] == "no-cache"
        status, _, body = _cond_get(base, path, '"0000000000000000"')
        assert status == 200 and body, path


def test_api_state_etag_ignores_the_ticking_members_and_answers_304(server):
    """R_FW_STATE_ETAG on buspi: /api/state carries a weak ETag over its content without the members
    that tick on their own (``_meta.age_s``, sensor ``age_s``, …), ``no-store`` so the browser cache
    never answers for it; a matching If-None-Match is a body-less 304, a real change a new ETag.

    .. test:: calictl /api/state: a weak ETag without the ticking members, 304 while it matches
       :id: T_WEB_STATE_ETAG
       :links: R_FW_STATE_ETAG
    """
    be, base = server
    be._state["_meta"] = {"age_s": 3, "last_seen": 1793198800.0, "online": True}
    status, h, body = _cond_get(base, "/api/state")
    tag = h["etag"]
    assert status == 200 and re.fullmatch(r'W/"[0-9a-f]{16}"', tag) and h["cache-control"] == "no-store"
    assert _cond_get(base, "/api/state", tag)[::2] == (304, b"")
    be._state["_meta"]["age_s"] = 9  # time moved on: the same content
    status, h304, _ = _cond_get(base, "/api/state", tag)
    assert status == 304 and h304["etag"] == tag and h304["cache-control"] == "no-store"
    be._state["cooler"]["level"] = 4  # a real change
    status, h, body = _cond_get(base, "/api/state", tag)
    assert status == 200 and h["etag"] != tag and json.loads(body)["cooler"]["level"] == 4


def test_api_json_is_gzipped_when_the_client_accepts_it(server):
    """The JSON API answers gzip-compressed to a client that accepts it (the state poll: about a
    third of the bytes), plain otherwise; ``Vary: Accept-Encoding`` either way."""
    _, base = server
    plain = _cond_get(base, "/api/state")
    conn = http.client.HTTPConnection(base.split("//", 1)[1])
    conn.request("GET", "/api/state", headers={"Accept-Encoding": "gzip, deflate, br"})
    r = conn.getresponse()
    h, raw = {k.lower(): v for k, v in r.getheaders()}, r.read()
    conn.close()
    assert h["content-encoding"] == "gzip" and gzip.decompress(raw) == plain[2]
    assert int(h["content-length"]) == len(raw) and h["vary"] == "Accept-Encoding"
    assert "content-encoding" not in plain[1] and plain[1]["vary"] == "Accept-Encoding"


def test_pwa_manifest_and_icons_served_with_correct_types(server):
    # Installable web app: the manifest + icons must serve with correct content-types, and
    # index.html must link them (else "Add to Home Screen" gets no name/icon).
    import urllib.request

    _, base = server
    for path, ctype, magic in (
        ("/manifest.webmanifest", "application/manifest+json", b"{"),
        ("/icon-192.png", "image/png", b"\x89PNG"),
        ("/icon-512.png", "image/png", b"\x89PNG"),
        ("/apple-touch-icon.png", "image/png", b"\x89PNG"),
        ("/maskable-icon-192.png", "image/png", b"\x89PNG"),
        ("/maskable-icon-512.png", "image/png", b"\x89PNG"),
        ("/favicon-32x32.png", "image/png", b"\x89PNG"),
        ("/favicon.ico", "image/x-icon", b"\x00\x00"),
    ):
        with urllib.request.urlopen(base + path) as r:
            assert r.status == 200, path
            assert r.headers["Content-Type"] == ctype, path
            assert r.read().startswith(magic), path
    with urllib.request.urlopen(base + "/") as r:
        html = r.read().decode()
    assert 'rel="manifest"' in html and 'rel="apple-touch-icon"' in html


class RaisingBackend(FakeBackend):
    def command(self, function, what, value, confirm=False):
        if function == "lighting" and what == "color":
            from calictl import control

            raise control.CommandError(
                "lighting color was retired: the app recolours a saved favourite — use save_profile N <colour>"
            )
        return super().command(function, what, value, confirm)


def test_api_command_retired_color_is_a_400_not_sent():
    be = RaisingBackend()
    srv = web.serve_http(be, WEBUI, "127.0.0.1", 0)
    try:
        status, body = _post(
            "http://127.0.0.1:%d/api/command" % srv.server_address[1],
            {"function": "lighting", "what": "color", "value": "red"},
        )
    finally:
        srv.shutdown()
    assert status == 400 and "retired" in body["error"]


class InternalValueErrorBackend(FakeBackend):
    def command(self, function, what, value, confirm=False):
        raise ValueError("invalid literal for int() with base 10: 'x'")  # not a user-input error


def test_api_command_internal_value_error_stays_a_500():
    srv = web.serve_http(InternalValueErrorBackend(), WEBUI, "127.0.0.1", 0)
    try:
        status, body = _post(
            "http://127.0.0.1:%d/api/command" % srv.server_address[1],
            {"function": "cooler", "what": "level", "value": "x"},
        )
    finally:
        srv.shutdown()
    assert status == 500 and body["error"] == "command_failed"


# ---- POST /api/ux (R_UX_TELEMETRY) ----------------------------------------------------------------

UX_BATCH = {
    "sid": "3f9a1c0b7d2e4a65",
    "ua_class": "ios",
    "standalone": True,
    "origin": "lan",
    "events": [
        {"type": "view", "screen": "cooler"},
        {"type": "control", "fn": "cooler", "control": "power"},
        {"type": "timing", "ttfb": 120, "dcl": 340, "tiles": 560},
        {"type": "error", "msg": "TypeError: x is undefined @ app.js:123"},
    ],
}


def _beacon(url, body, ctype="text/plain;charset=UTF-8"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def ux_server():
    be = FakeBackend()
    be.ux_enabled = True
    be.beacons = []
    be.ux = be.beacons.append
    srv = web.serve_http(be, WEBUI, "127.0.0.1", 0)
    yield be, "http://127.0.0.1:%d/api/ux" % srv.server_address[1]
    srv.shutdown()


def test_ux_beacon_recorded_any_content_type(ux_server):
    """A sendBeacon string (text/plain) is accepted: 204, handed to the backend as validated.

    .. test:: calictl POST /api/ux validates a beacon and records it; off -> 204, nothing recorded
       :id: T_UX_CALICTL_API
       :links: R_UX_TELEMETRY
    """
    be, url = ux_server
    assert _beacon(url, UX_BATCH) == (204, b"")
    assert _beacon(url, UX_BATCH, "application/json") == (204, b"")
    assert be.beacons == [UX_BATCH, UX_BATCH]


def test_ux_switched_off_records_nothing(ux_server):
    be, url = ux_server
    be.ux_enabled = False
    assert _beacon(url, UX_BATCH) == (204, b"")
    assert _beacon(url, b"not json") == (204, b"")  # off: not even validated
    assert be.beacons == []


def test_ux_backend_without_ux_is_off(server):
    """A backend without the hook (older tooling) answers 204 and records nothing."""
    _be, base = server
    assert _beacon(base + "/api/ux", UX_BATCH) == (204, b"")


def test_ux_control_value_is_optional_printable_ascii_up_to_32(ux_server):
    """A control event may carry ``value`` (string, printable ASCII, 1-32 chars); without it, as before.

    .. test:: calictl accepts a control's value (printable ASCII <= 32), rejects the rest of the beacon
       :id: T_UX_VALUE_CALICTL
       :links: R_UX_TELEMETRY
    """
    be, url = ux_server
    for v in ("3", "on", "21:30", "x" * 32, "a b~!"):
        b = _ev(type="control", fn="cooler", control="level", value=v)
        assert _beacon(url, b) == (204, b"")
        assert be.beacons[-1] == b
    assert influx.ux_label(be.beacons[-1]["events"][0]) == "control:cooler.level=a b~!"
    assert influx.ux_label({"type": "control", "fn": "cooler", "control": "power"}) == "control:cooler.power"


def _with(**kw):
    return {**UX_BATCH, **kw}


def _ev(**e):
    return _with(events=[e])


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        _with(sid="XYZ"),  # not hex
        _with(sid="abc"),  # too short
        _with(ua_class="windows"),
        _with(origin="internet"),
        _with(standalone=1),
        _with(events=[]),
        _with(events=[{"type": "view", "screen": "home"}] * 17),
        _with(extra=1),
        {k: v for k, v in UX_BATCH.items() if k != "origin"},
        _ev(type="view"),  # missing screen
        _ev(type="view", screen="home", value=3),  # never a value
        _ev(type="control", fn="cooler", control="level", value=7),  # a string, not a number
        _ev(type="control", fn="cooler", control="level", value=None),  # omit the key instead
        _ev(type="control", fn="cooler", control="level", value=""),
        _ev(type="control", fn="cooler", control="level", value="7" * 33),
        _ev(type="control", fn="cooler", control="level", value="caf\u00e9"),
        _ev(type="control", fn="cooler", control="level", value="a\nb"),
        _ev(type="control", fn="cooler", control="level", value=["7"]),
        _ev(type="timing", ttfb=1, dcl=1, tiles=1, value="7"),  # only controls carry a value
        _ev(type="error", msg="x", value="7"),
        _ev(type="view", screen="Cooler"),  # ids are lower-case
        _ev(type="view", screen="x" * 33),
        _ev(type="timing", ttfb=-1, dcl=0, tiles=0),
        _ev(type="timing", ttfb=True, dcl=0, tiles=0),
        _ev(type="timing", ttfb=1.5, dcl=0, tiles=0),
        _ev(type="error", msg="x" * 201),
        _ev(type="error", msg="line\nbreak"),
        _ev(type="error", msg=""),
        _ev(type="click", id="x"),
    ],
)
def test_ux_rejects_anything_but_the_shape(ux_server, body):
    be, url = ux_server
    assert _beacon(url, body) == (400, {"error": "bad_ux"})
    assert be.beacons == []


def test_ux_body_over_2kb_is_413(ux_server):
    be, url = ux_server
    big = _ev(type="error", msg="x" * 200)
    big["sid"] = "a" * 32
    raw = json.dumps(big).encode() + b" " * 2048
    assert _beacon(url, raw) == (413, {"error": "too_large"})
    assert be.beacons == []
