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


def test_setup_page_gets_scan_at_most_once_per_interval(host_fw, hci_unit, tmp_path):
    """Bench walk #154: every setup-mode GET /api/wifi used to start a scan, and a real scan takes
    the shared radio off the hotspot's channel — the phone on the page dropped and its Connect POST
    failed. Repeated GETs inside NET_SCAN_MIN_INTERVAL_MS start no scan beyond the setup start's
    own one (net_host.c logs each fake scan)."""
    wifi = _wifi_script(tmp_path, "ap minsel -55 1\n")
    fw = host_fw(hci_unit, http=True, fake_wifi=wifi)
    fw.expect("LOG", lambda l: l == AP_UP)
    for _ in range(6):
        assert [a["ssid"] for a in get_json(fw, "/api/wifi")["scan"]] == ["minsel"]
        time.sleep(0.3)  # a few 100 ms ticks: each scan's SCAN_DONE lands, so none is "in flight"
    assert [line for line in fw.log if line == "LOG net: fake scan"] == ["LOG net: fake scan"], fw.log


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
    makes of the same fn; the UI is live (``device.control.writes`` -> ``_meta.read_only`` false);
    no JS error; nothing requested but / and /api/state."""
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
        page.wait_for_function("() => STATE._meta.read_only === false", timeout=3000)
        browser.close()
    py = {name: semantics.interpret(name, dict(f)) for name, f in got["fn"].items()}
    semantics.apply_sw_corrections(py)
    ui = sorted(set(UI_FUNCTIONS) & set(py))
    assert "cooler" in ui, sorted(py)
    want = json.loads(json.dumps({k: py[k] for k in ui}))
    assert same({k: got["state"][k] for k in ui}, want), (got["state"], want)
    assert got["state"]["_meta"]["read_only"] is False
    # the satellite _meta the UI reads (firmware warning, anchors) equals calictl's for the same frames
    assert same(got["state"]["_meta"]["firmware"], ServeBackend._firmware_meta(py.get("general")))
    assert same(got["state"]["_meta"]["anchors"], anchors.check(py))
    assert not errors, errors
    assert set(paths) <= {"/", "/api/state", "/api/pairing"}, paths  # + the one-off wizard fetch


LIVE_UI = (
    "() => !!(STATE._meta && STATE._meta.satellite && STATE._meta.online && STATE._meta.read_only === false)"
)
UI_PATHS = {"/", "/api/state", "/api/command", "/api/pairing"}


def _live_ui(p, fw, locale="en-US"):
    """Chromium on the live satellite UI: (browser, page, errors, paths) once the page holds an
    online, writable satellite state; every page/console error lands in ``errors``."""
    browser = p.chromium.launch()
    page = browser.new_context(locale=locale).new_page()
    errors, paths = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("request", lambda q: paths.append(urllib.parse.urlsplit(q.url).path))
    page.on("dialog", lambda d: d.accept())
    page.goto("http://127.0.0.1:%d/" % fw.http_port)
    page.wait_for_function(LIVE_UI, timeout=15000)
    return browser, page, errors, paths


def _open_tile(page, name):
    page.locator(".tile", has_text=name).first.click()
    page.locator("#title").get_by_text(name, exact=True).wait_for(timeout=3000)


def test_live_cooler_toggle_reaches_the_unit_byte_exact(host_fw, rec_unit, tmp_path):
    """The satellite UI in Chromium: the fridge switch -> POST /api/command -> the real sequencer ->
    NimBLE -> the fake unit records exactly ``control.build``'s frame. The unit ACKs 2.5 s late, so
    the POST pends and the single-connection core serves no /api/state meanwhile (Task 4 review
    minor 6): the page shows "Sending…" the whole time, never the offline banner. ``applied`` is
    never true on the ESP, so the UI confirms from the unit's own state: the unit's push of the new
    fridge state reaches the next ``/api/state`` poll -> "✓ Applied", not the warning.

    .. test:: A UI control on the live satellite reaches the unit with calictl's bytes
       :id: T_FW_UI_LIVE_CONTROL
       :links: R_FW_SHARED_UI, R_FW_CONTROL_API
    """
    import tools.esplab_control_walk as walker
    from calictl import control

    from .test_control_e2e import _online  # lazily: that module imports this one

    sync_playwright = _require_chromium()
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    before = get_json(fw, "/api/state")["fn"]["cooler"]
    value = "off" if before["State"] else "on"  # the switch sends the opposite of what the unit reports
    want = control.build(_funcs(), "cooler", "power", value, before).hex()
    from .test_control_e2e import _ack_after

    hu.call(_ack_after, hu.unit, 2.5)
    with sync_playwright() as p:
        browser, page, errors, paths = _live_ui(p, fw)
        _open_tile(page, "Cooler")
        sw = page.get_by_role("switch", name="Refrigerator box")
        assert sw.is_enabled() and sw.get_attribute("aria-checked") == (
            "true" if before["State"] else "false"
        )
        sw.click()
        seen = set()
        for _ in range(7):  # ~1.8-2 s of the 2.5 s stall (margin for a slow runner)
            seen.add(page.locator("#status").inner_text())
            assert page.locator(".offline").count() == 0
            page.wait_for_timeout(250)
        assert "Sending…" in seen and "offline" not in seen, seen
        page.locator(".toast").get_by_text("✓ Applied").wait_for(timeout=8000)
        assert page.locator(".toast.warn").count() == 0
        page.wait_for_function("() => document.getElementById('status').textContent === 'live'", timeout=5000)
        browser.close()
    assert not errors, errors
    assert set(paths) <= UI_PATHS, paths
    assert [(c, h) for c, h, _ in walker.unit_writes(rec)] == [("1101", want)]


@pytest.mark.parametrize(
    "locale,roof,hint",
    [
        ("en-US", "Roof", "Only via buspi or the app"),
        ("de-DE", "Aufstelldach", "Nur über buspi oder die App"),
    ],
)
def test_roof_stays_with_buspi_or_the_app(host_fw, rec_unit, tmp_path, locale, roof, hint):
    """On the live satellite the roof buttons are greyed with the firmware's own refusal text
    (EN + DE); the fridge switch next door is live; nothing is written."""
    import tools.esplab_control_walk as walker

    from .test_control_e2e import _online  # lazily: that module imports this one

    sync_playwright = _require_chromium()
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    with sync_playwright() as p:
        browser, page, errors, paths = _live_ui(p, fw, locale)
        _open_tile(page, roof)
        btns = page.locator(".btnrow button")
        assert btns.count() == 3
        for i in range(3):
            assert btns.nth(i).is_disabled() and btns.nth(i).get_attribute("title") == hint
        assert page.get_by_text(hint, exact=True).count() >= 1
        page.evaluate("document.getElementById('back').click()")
        _open_tile(page, "Kühlbox" if locale == "de-DE" else "Cooler")
        assert page.get_by_role("switch", name="Refrigerator box").is_enabled()
        browser.close()
    assert not errors, errors
    assert set(paths) <= {"/", "/api/state", "/api/pairing"}, paths  # + the one-off wizard fetch
    assert walker.unit_writes(rec) == []


def test_live_wakeup_edit_reaches_the_unit_with_the_pages_clock(host_fw, rec_unit, tmp_path):
    """Chromium -> the satellite UI -> POST with local_now -> the sequencer -> NimBLE -> the fake unit
    records control.build's wake-up frame for that clock and the unit's latched config.

    .. test:: A wake-up edit on the live satellite page reaches the unit byte-exact with the page's clock
       :id: T_FW_UI_LIVE_WAKEUP
       :links: R_FW_SHARED_UI, R_FW_WAKEUP
    """
    import tools.esplab_control_walk as walker
    from tools import gen_control_vectors
    from tools.mock_unit import _pack_state

    from .test_control_e2e import _online  # lazily: that module imports this one

    sync_playwright = _require_chromium()
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    funcs = _funcs()
    wake = {"Mode": 20, "ProfileNumber": 14, "Timestamp": 6 * 3600, "LightValue": 0x1101}
    frame = _pack_state(funcs["lighting"], wake)
    hu.call(_serve_raw, hu.unit, "lighting", frame, True)  # the unit reports its wake-up config
    end = time.monotonic() + 10
    while get_json(fw, "/api/state")["fn"]["lighting"].get("WakeupTimestamp") != 6 * 3600:
        assert time.monotonic() < end, "the firmware never latched the unit's wake-up config"
        time.sleep(0.2)
    with sync_playwright() as p:
        browser, page, errors, _ = _live_ui(p, fw)
        bodies = []
        page.on("request", lambda q: bodies.append(q.post_data) if q.url.endswith("/api/command") else None)
        _open_tile(page, "Lighting")
        tm = page.get_by_label("Wake-up time")
        assert tm.is_enabled() and tm.input_value() == "06:00"
        tm.fill("08:00")
        page.locator(".toast").first.wait_for(timeout=10000)
        browser.close()
    assert not errors, errors
    body = json.loads(bodies[0])
    assert (body["function"], body["what"], body["value"]) == ("lighting", "wakeup", "08:00 1 0 0")
    states = {
        "lighting": {
            **protocol.decode(funcs["lighting"], frame),
            "WakeupTimestamp": 6 * 3600,
            "WakeupLightValue": 0x1101,
        }
    }
    e = gen_control_vectors.expect(funcs, "lighting", "wakeup", body["value"], states, body["local_now"])
    assert e["frames"], e
    assert [(c, h) for c, h, _ in walker.unit_writes(rec)] == [(f["char"], f["hex"]) for f in e["frames"]]


async def _hold_wakeup(unit, wakeup):
    """The unit holds a wake-up config it has not pushed: it reports it only in a REQUEST_CONFIG reply
    (``unit`` = the Bumble FakeUnit, ``unit.unit`` its MockCamperUnit model)."""
    unit.unit.wakeup = wakeup


def test_wakeup_time_edit_with_no_config_pulls_then_lands(host_fw, rec_unit, tmp_path):
    """Review m2 over real NimBLE: the satellite has no config latched, so only the time is live; the
    edit posts the time alone; the sequencer pulls REQUEST_CONFIG, the unit answers with its Mode-20
    config, and the wake-up frame calictl builds for that config and the page's clock follows."""
    import tools.esplab_control_walk as walker
    from tools import gen_control_vectors
    from tools.mock_unit import _pack_state

    from .test_control_e2e import _online  # lazily: that module imports this one

    sync_playwright = _require_chromium()
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    funcs = _funcs()
    held = {"Timestamp": 6 * 3600, "LightValue": 0x1101}
    hu.call(_hold_wakeup, hu.unit, dict(held))
    assert "WakeupTimestamp" not in get_json(fw, "/api/state")["fn"]["lighting"]
    with sync_playwright() as p:
        browser, page, errors, _ = _live_ui(p, fw)
        bodies = []
        page.on("request", lambda q: bodies.append(q.post_data) if q.url.endswith("/api/command") else None)
        _open_tile(page, "Lighting")
        tm = page.get_by_label("Wake-up time")
        assert tm.is_enabled() and tm.input_value() == ""
        assert page.get_by_role("switch", name="Wake-up light").is_disabled()
        tm.fill("08:00")
        page.locator(".toast").first.wait_for(timeout=12000)
        toast = page.locator(".toast").first.inner_text()
        browser.close()
    assert not errors, errors
    body = json.loads(bodies[0])
    assert (body["function"], body["what"], body["value"]) == ("lighting", "wakeup", "08:00")
    assert toast == "✓ Applied", (toast, walker.unit_writes(rec))
    frame = _pack_state(funcs["lighting"], {"Mode": 20, **held})
    states = {
        "lighting": {
            **protocol.decode(funcs["lighting"], frame),
            "WakeupTimestamp": held["Timestamp"],
            "WakeupLightValue": held["LightValue"],
        }
    }
    e = gen_control_vectors.expect(funcs, "lighting", "wakeup", "08:00", states, body["local_now"])
    pull = [(f["char"], f["hex"]) for f in walker.config_pull()["frames"]]
    assert [(c, h) for c, h, _ in walker.unit_writes(rec)] == pull + [
        (f["char"], f["hex"]) for f in e["frames"]
    ]


def test_busy_satellite_tells_the_user_to_retry(host_fw, rec_unit, tmp_path):
    """A console ``set`` holds the sequencer (slow ACK); the UI's click meanwhile is the firmware's
    ``409 busy``, shown as a sentence the owner can act on, not a bare code."""
    from .test_control_e2e import _ack_after, _online  # lazily: that module imports this one

    sync_playwright = _require_chromium()
    hu, rec = rec_unit
    fw = _online(host_fw, hu, tmp_path)
    hu.call(_ack_after, hu.unit, 2.5)
    with sync_playwright() as p:
        browser, page, errors, paths = _live_ui(p, fw)
        _open_tile(page, "Cooler")
        fw.send("set lighting kitchen 5")
        fw.expect("LOG", lambda line: line == "control: lighting/kitchen sending")
        page.get_by_role("switch", name="Refrigerator box").click()
        page.locator(".toast").get_by_text(
            "The satellite is still sending the previous command — try again in a moment"
        ).wait_for(timeout=3000)
        browser.close()
    # the 409 itself is the browser's "Failed to load resource" console line, by design
    assert not [e for e in errors if "409" not in e], errors


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
