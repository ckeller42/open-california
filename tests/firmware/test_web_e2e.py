"""Host firmware: WiFi setup flow + status page/API over the real HTTP core, fake WiFi, fake unit.

``cali-host --http PORT --fake-wifi SCRIPT`` runs the WiFi runner (``wifi_run.c``: the WiFi SM over
``cali_net``), the captive DNS and the web endpoints (``web.c`` on ``http_core.c``) on the same
100 ms tick as the BLE session, with ``net_host.c``'s scripted fake WiFi standing in for the radio
and the Bumble fake unit for the camper. Requests go to ``127.0.0.1:PORT`` with ``urllib``.

.. test:: WiFi provisioning and the status API end to end (host tier)
   :id: T_FW_WEB_E2E
   :links: R_FW_WIFI_PROVISION, R_FW_HTTP_STATUS, R_FW_WIFI_BLE_COEX

.. test:: The satellite UI over the real firmware HTTP core equals Python semantics (host tier)
   :id: T_FW_SHARED_UI_HOST
   :links: R_FW_SHARED_UI
"""

import gzip
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from calictl import anchors, protocol, semantics
from calictl.serve import ServeBackend
from tests.test_semantics_js_parity import same  # bool-strict deep equality (True != 1), one definition
from tools import gen_c_dict
from tools.gen_semantics_vectors import UI_FUNCTIONS
from tools.wifi_consts import CONSTS

from .conftest import build_host
from .test_host_e2e import _beats_seen, _funcs, _pair, _serve_raw, _served_frames

# One xdist worker: the host_fw fixture runs `make` in firmware/host (like test_host_e2e.py).
pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

PSK = "test-psk-1234"
PAGE = Path(__file__).resolve().parents[2] / "firmware" / "web" / "index_gen.html"
AP_UP = "wifi: setup hotspot up (%s)" % CONSTS["NET_AP_SSID"]
STRINGS = json.loads(
    (Path(__file__).resolve().parents[2] / "firmware" / "web" / "strings.json").read_text(encoding="utf-8")
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None  # a 3xx comes back as the HTTPError below, not followed


_OPENER = urllib.request.build_opener(_NoRedirect)


def _request(fw, method, path, body=None, timeout=10.0):
    """One request to the firmware's HTTP port -> the response (an ``HTTPError`` for 3xx/4xx/5xx,
    which carries ``.status`` and ``.read()`` too). Retries a refused connect while the process
    starts listening."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (fw.http_port, path), data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    end = time.monotonic() + timeout
    while True:
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                r.body = r.read()
                return r
        except urllib.error.HTTPError as e:
            e.body = e.read()
            return e
        except (ConnectionRefusedError, urllib.error.URLError) as e:
            reason = getattr(e, "reason", e)
            if not isinstance(reason, ConnectionRefusedError) or time.monotonic() > end:
                raise
            time.sleep(0.1)


def get(fw, path):
    return _request(fw, "GET", path)


def get_json(fw, path):
    r = get(fw, path)
    assert r.status == 200, (r.status, r.body)
    return json.loads(r.body)


def post_json(fw, path, body):
    return json.loads(_request(fw, "POST", path, body).body)


def _wifi_script(tmp_path, text):
    p = tmp_path / "wifi.txt"
    p.write_text(text)
    return p


def test_fresh_device_opens_setup_and_joins(host_fw, hci_unit, tmp_path):
    wifi = _wifi_script(tmp_path, "ap minsel -55 1\nap other -80 1\njoin minsel ok 192.168.1.42\n")
    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)
    fw.expect("LOG", lambda l: l == AP_UP)
    w = get_json(fw, "/api/wifi")
    assert w["mode"] == "setup" and [a["ssid"] for a in w["scan"]] == ["minsel", "other"]
    assert get(fw, "/generate_204").status == 302
    assert post_json(fw, "/api/wifi", {"ssid": "minsel", "psk": PSK}) == {"ok": True}
    fw.expect("LOG", lambda l: l == "wifi: joining minsel")
    fw.expect("LOG", lambda l: l == "wifi: online 192.168.1.42")
    w = get_json(fw, "/api/wifi")
    assert (w["mode"], w["ssid"], w["ip"], w["rssi"]) == ("station", "minsel", "192.168.1.42", -55)
    assert not any(PSK in line for line in fw.log)  # the passphrase is never printed


def test_wrong_password_returns_to_setup(host_fw, hci_unit, tmp_path):
    wifi = _wifi_script(tmp_path, "ap minsel -55 1\njoin minsel fail auth\n")
    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)
    assert post_json(fw, "/api/wifi", {"ssid": "minsel", "psk": "wrong-psk-99"}) == {"ok": True}
    fw.expect("LOG", lambda l: l == "wifi: failed auth")
    fw.expect("LOG", lambda l: l == "wifi: credentials cleared")
    w = get_json(fw, "/api/wifi")
    assert w["mode"] == "setup" and w["ssid"] is None
    assert w["last_error"] == "auth"  # the page says why (R23)


def test_boot_with_saved_creds_reconnects_and_forget(host_fw, hci_unit, tmp_path):
    wifi = _wifi_script(tmp_path, "ap minsel -55 1\njoin minsel ok 192.168.1.42\n")
    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)
    fw.expect("LOG", lambda l: l.startswith("wifi: setup hotspot up"))
    post_json(fw, "/api/wifi", {"ssid": "minsel", "psk": PSK})
    fw.expect("LOG", lambda l: l == "wifi: online 192.168.1.42")
    fw.stop()

    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)  # reboot: same store
    fw.expect("LOG", lambda l: l == "wifi: joining minsel")
    fw.expect("LOG", lambda l: l == "wifi: online 192.168.1.42")
    assert not any(line.startswith("LOG wifi: setup hotspot") for line in fw.log)
    fw.send("status")
    st = fw.expect("STATE", lambda s: "wifi" in s)
    assert st["wifi"] == {"mode": "station", "ssid": "minsel", "ip": "192.168.1.42"}

    fw.send("wifi forget")
    fw.expect("LOG", lambda l: l == "wifi: credentials cleared")
    fw.expect("LOG", lambda l: l == AP_UP)
    assert get_json(fw, "/api/wifi")["mode"] == "setup"
    fw.stop()

    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)  # the forget reached the store
    fw.expect("LOG", lambda l: l.startswith("wifi: setup hotspot up"))
    assert not any(line == "LOG wifi: joining minsel" for line in fw.log)


def test_api_state_matches_snap_after_pairing(host_fw, hci_unit, tmp_path):
    fw = host_fw(hci_unit, http=True)  # no script: no networks (R2)
    assert _pair(fw, hci_unit)["state"] == "bonded"
    snap = fw.expect("SNAP", timeout=40)
    body = get_json(fw, "/api/state")
    latest = [json.loads(line[5:]) for line in fw.log if line.startswith("SNAP ")][-1]
    assert body["fn"] == latest["fn"] == snap["fn"]
    assert body["device"]["pairing"] == {"state": "bonded", "address": "C0:FF:EE:CA:11:F0"}
    assert body["device"]["link"]["up"] is True
    assert body["device"]["wifi"] == {"mode": "setup", "ssid": None, "ip": None, "rssi": None}


def test_wifi_loss_keeps_ble_link(host_fw, hci_unit, tmp_path):
    """Review focus: the WiFi dropping (``drop-after``) and the runner retrying never touch the BLE
    session — no ``LOG session:`` line, the heartbeat keeps beating at the unit, pushes still SNAP."""
    wifi = _wifi_script(tmp_path, "ap minsel -55 1\njoin minsel ok 192.168.1.42\ndrop-after 3000\n")
    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)
    _pair(fw, hci_unit)
    fw.expect("SNAP", timeout=40)
    mark = len(fw.log)
    before = hci_unit.call(_beats_seen, hci_unit.unit)
    fw.send("wifi set minsel %s" % PSK)
    fw.expect("LOG", lambda l: l == "wifi: online 192.168.1.42")
    fw.expect("LOG", lambda l: l == "wifi: lost", timeout=20)
    fw.expect("LOG", lambda l: l == "wifi: online 192.168.1.42", timeout=20)  # the retry rejoins
    window = fw.log[mark:]
    assert not [line for line in window if line.startswith("LOG session:")], window
    assert hci_unit.call(_beats_seen, hci_unit.unit) > before
    fn = "cooler"
    old = hci_unit.call(_served_frames, hci_unit.unit)[fn]
    new = bytes([old[0] ^ 0xFF]) + old[1:]
    want = protocol.decode(_funcs()[fn], new)
    hci_unit.call(_serve_raw, hci_unit.unit, fn, new, True)
    fw.expect("SNAP", lambda s: s["fn"].get(fn) == want, timeout=15)  # still flowing
    assert not [line for line in fw.log[mark:] if line.startswith("LOG session:")]


def _chromium():
    """(sync_playwright, None) or (None, skip reason) — like tests/e2e/test_gui.py, a missing
    Playwright or browser is a skip, never an error."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright not installed"
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
        return None, "Playwright chromium browser not installed: %s" % e
    return sync_playwright, None


def _require_chromium():
    """sync_playwright, or skip — a failure instead under ``CALI_REQUIRE_CHROMIUM=1`` (CI
    firmware-host-e2e / tools/ci.sh firmware), so the browser tests can never silently skip there."""
    sync_playwright, why = _chromium()
    if sync_playwright is None:
        if os.environ.get("CALI_REQUIRE_CHROMIUM") == "1":
            pytest.fail("CALI_REQUIRE_CHROMIUM=1 but " + why)
        pytest.skip(why)
    return sync_playwright


def test_station_root_is_the_calictl_ui_equal_to_python_semantics(host_fw, hci_unit, tmp_path):
    """Station mode: GET / is the gzipped calictl UI (no-cache), /device the status page; in Chromium
    the bundle's semantics.js turns the firmware's real /api/state into exactly what Python semantics
    makes of the same fn; no JS error; nothing requested but / and /api/state."""
    sync_playwright = _require_chromium()
    wifi = _wifi_script(tmp_path, "ap minsel -55 1\njoin minsel ok 192.168.1.42\n")
    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)
    _pair(fw, hci_unit)
    fw.expect("SNAP", timeout=40)
    fw.send("wifi set minsel %s" % PSK)
    fw.expect("LOG", lambda l: l == "wifi: online 192.168.1.42")

    r = get(fw, "/")
    assert (
        r.status == 200
        and r.headers["Content-Encoding"] == "gzip"
        and r.headers["Cache-Control"] == "no-cache"
    )
    assert gzip.decompress(r.body) == gen_c_dict.render_app_bundle().encode("utf-8")
    assert get(fw, "/device").body == PAGE.read_bytes()

    errors, paths = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("request", lambda q: paths.append(urllib.parse.urlsplit(q.url).path))
        page.goto("http://127.0.0.1:%d/" % fw.http_port)
        page.wait_for_function(
            "() => !!(STATE._meta && STATE._meta.satellite && STATE._meta.online)", timeout=15000
        )
        # one fresh body, interpreted by the page's own semantics.js: no race with the app's 2 s poll
        got = page.evaluate(
            "async () => { const b = await (await fetch('/api/state')).json();"
            " return {fn: b.fn, state: adaptSatellite(b, Date.now())}; }"
        )
        page.get_by_text("Satellite — display only").wait_for(timeout=3000)
        browser.close()
    py = {name: semantics.interpret(name, dict(f)) for name, f in got["fn"].items()}
    semantics.apply_sw_corrections(py)
    ui = sorted(set(UI_FUNCTIONS) & set(py))
    assert "cooler" in ui, sorted(py)
    want = json.loads(json.dumps({k: py[k] for k in ui}))
    assert same({k: got["state"][k] for k in ui}, want), (got["state"], want)
    assert got["state"]["_meta"]["read_only"] is True
    # the satellite _meta the UI reads (firmware warning, anchors) equals calictl's for the same frames
    assert same(got["state"]["_meta"]["firmware"], ServeBackend._firmware_meta(py.get("general")))
    assert same(got["state"]["_meta"]["anchors"], anchors.check(py))
    assert not errors, errors
    assert set(paths) <= {"/", "/api/state"}, paths


@pytest.mark.parametrize(
    "locale,device,functions", [("en-US", "Device", "Camper unit"), ("de-DE", "Gerät", "Camper-Einheit")]
)
def test_page_renders(host_fw, hci_unit, tmp_path, locale, device, functions):
    """GET / renders the device box and a function block within 3 s, in the browser's language;
    any uncaught page error fails the test."""
    sync_playwright = _require_chromium()
    fw = host_fw(hci_unit, http=True)
    _pair(fw, hci_unit)
    fw.expect("SNAP", timeout=40)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(locale=locale).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://127.0.0.1:%d/" % fw.http_port)
        page.wait_for_selector("#device h2", timeout=3000)
        page.wait_for_selector("#functions .box h2", timeout=3000)
        assert page.inner_text("#device h2") == device
        assert page.inner_text("#functions-title") == functions
        browser.close()
    assert not errors, errors


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_setup_flow_in_browser(host_fw, hci_unit, tmp_path, locale, lang):
    """The owner's first-flash path, clicked in Chromium (final review I2): pick a network, type a
    wrong password -> the page says why (R23: the auth text); pick the right one -> the
    ``http://<NET_HOSTNAME>.local`` link. The password field is emptied after each submit; any
    uncaught page error fails the test. Texts come from ``strings.json``, in the browser's language."""
    sync_playwright = _require_chromium()
    wifi = _wifi_script(
        tmp_path, "ap minsel -55 1\nap typo -60 1\njoin minsel ok 192.168.1.42\njoin typo fail auth\n"
    )
    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)
    fw.expect("LOG", lambda l: l == AP_UP)
    wrong = STRINGS["join_failed_auth"][lang]
    link = "http://%s.local" % CONSTS["NET_HOSTNAME"]
    wait_ms = 10 * CONSTS["NET_PAGE_POLL_MS"]  # a few page polls
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(locale=locale).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://127.0.0.1:%d/" % fw.http_port)
        page.wait_for_selector("#setup", state="visible", timeout=wait_ms)
        page.wait_for_selector("#ssid option[value=typo]", state="attached", timeout=wait_ms)
        assert page.inner_text("#connect") == STRINGS["connect"][lang]

        page.select_option("#ssid", "typo")
        page.fill("#psk", "wrong-psk-99")
        page.click("#connect")
        page.wait_for_function(
            "t => document.getElementById('setup-msg').textContent === t", arg=wrong, timeout=wait_ms
        )
        assert page.input_value("#psk") == ""
        assert fw.expect("LOG", lambda l: l == "wifi: failed auth")

        page.select_option("#ssid", "minsel")
        page.fill("#psk", PSK)
        page.click("#connect")
        a = page.wait_for_selector('#setup-msg a[href="%s"]' % link, timeout=wait_ms)
        assert a.inner_text() == link
        assert page.input_value("#psk") == ""
        assert wrong not in page.inner_text("#setup-msg")
        browser.close()
    assert not errors, errors
    assert not any(PSK in line for line in fw.log)


def test_rescan_then_connect_keeps_the_join_result_in_browser(host_fw, hci_unit, tmp_path):
    """Regression for the CodeRabbit review on #220: ``scan()``'s delayed re-read (``2 *
    CFG.pollMs`` after the click) must not blindly overwrite ``#setup-msg``. Click "Search again",
    then "Connect" right after (before that delayed read lands) — the join's success link must
    still be there once the delayed read fires. The old code cleared the text unconditionally,
    which also hides ``#setup`` (its visibility check looks for that link once WiFi mode is no
    longer "setup")."""
    sync_playwright = _require_chromium()
    wifi = _wifi_script(tmp_path, "ap minsel -55 1\njoin minsel ok 192.168.1.42\n")
    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)
    fw.expect("LOG", lambda l: l == AP_UP)
    link = "http://%s.local" % CONSTS["NET_HOSTNAME"]
    wait_ms = 10 * CONSTS["NET_PAGE_POLL_MS"]
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context().new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://127.0.0.1:%d/" % fw.http_port)
        page.wait_for_selector("#setup", state="visible", timeout=wait_ms)
        page.wait_for_selector("#ssid option[value=minsel]", state="attached", timeout=wait_ms)

        page.click("#rescan")  # starts scan()'s pending delayed re-read, 2 * CFG.pollMs out
        page.select_option("#ssid", "minsel")
        page.fill("#psk", PSK)
        page.click("#connect")  # quickly, before that delayed read lands
        a = page.wait_for_selector('#setup-msg a[href="%s"]' % link, timeout=wait_ms)
        assert a.inner_text() == link

        # outlive the rescan's pending timer (2 * CFG.pollMs after the click above)
        page.wait_for_timeout(2 * CONSTS["NET_PAGE_POLL_MS"] + 500)
        assert page.query_selector('#setup-msg a[href="%s"]' % link) is not None
        assert page.is_visible("#setup")
        browser.close()
    assert not errors, errors


@pytest.mark.parametrize("bad", ["0", "abc", "80x", "65536", "-1", ""])
def test_http_port_must_be_a_valid_port(bad):
    """``--http 0`` or a non-numeric/out-of-range port is a usage error (exit 2), never a silent run
    without the network side."""
    r = subprocess.run(
        [str(build_host()), "--hci-port", "1", "--http", bad], capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 2 and "usage:" in r.stderr
