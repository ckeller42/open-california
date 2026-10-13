"""Browser e2e of the ESP32 status/setup page (``firmware/web/index_gen.html``) over
``tools.ux_gallery.EspStub`` — no firmware, BLE or radio; Playwright reroutes what a test needs
(a failing POST). Every test fails on an uncaught JS / console error (``page``), except the
browser's own "Failed to load resource" line for a request a test aborts on purpose.
OPT-IN like test_gui.py: skipped without Playwright + Chromium. The same page against the real
firmware HTTP core: tests/firmware/test_web_e2e.py (Linux host tier).
"""

import contextlib
import json
from pathlib import Path

import pytest

from tools import ux_gallery
from tools.wifi_consts import CONSTS

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

STRINGS = json.loads(
    (Path(__file__).resolve().parents[2] / "firmware" / "web" / "strings.json").read_text(encoding="utf-8")
)


@pytest.fixture
def stub():
    with ux_gallery.EspStub("setup") as s:
        yield s


@pytest.fixture
def page():
    """``with page(url, locale=...) as pg:`` — like tests/e2e/conftest.py's error_gated_page, but an
    aborted request's console line is expected here."""

    @contextlib.contextmanager
    def _open(url, **kw):
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page(**kw)
            errors = []
            pg.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
            pg.on(
                "console",
                lambda m: (
                    errors.append("console.error: %s" % m.text)
                    if m.type == "error" and "Failed to load resource" not in m.text
                    else None
                ),
            )
            pg.goto(url)
            try:
                yield pg
            finally:
                browser.close()
            assert not errors, errors

    return _open


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_connect_retries_once_after_a_network_error(stub, page, locale, lang):
    """Bench walk #154: the phone can drop off the setup hotspot for a moment (a scan, the join's
    channel switch), so the Connect POST gets no answer. The page says it retries, waits 3 s and
    posts once more; the second answer counts."""
    posts = []

    def on_post(route):
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(route.request.post_data)
        if len(posts) == 1:
            route.abort("internetdisconnected")
        else:
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    with page(stub.base, locale=locale) as pg:
        pg.route("**/api/wifi", on_post)
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.select_option("#ssid", "HomeNet")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        expect(pg.locator("#setup-msg")).to_have_text(STRINGS["retrying"][lang])
        assert len(posts) == 1
        expect(pg.locator("#setup-msg")).to_have_text(
            STRINGS["joining"][lang].replace("{ssid}", "HomeNet"), timeout=6000
        )
        assert len(posts) == 2 and posts[0] == posts[1]
        assert pg.input_value("#psk") == ""


def test_a_hanging_connect_times_out_into_the_retry(stub, page):
    """Review M4: the answer is lost but the connection stays open (the phone is still associated):
    the POST must not hang for good — after NET_CONNECT_TIMEOUT_MS it takes the retry path."""
    posts = []

    def on_post(route):
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(1)
        if len(posts) > 1:
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        # the first one: never answered (left pending)

    wait = CONSTS["NET_CONNECT_TIMEOUT_MS"] + CONSTS["NET_CONNECT_RETRY_MS"] + 3000
    with page(stub.base, locale="en-US") as pg:
        pg.route("**/api/wifi", on_post)
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.select_option("#ssid", "HomeNet")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        expect(pg.locator("#setup-msg")).to_have_text(
            STRINGS["retrying"]["en"], timeout=CONSTS["NET_CONNECT_TIMEOUT_MS"] + 2000
        )
        expect(pg.locator("#setup-msg")).to_have_text("Connecting to HomeNet…", timeout=wait)
        assert len(posts) == 2


def test_connect_gives_up_after_the_one_retry(stub, page):
    """Both POSTs unanswered: the page says the device did not answer (no endless retry)."""
    posts = []

    def on_post(route):
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(1)
        route.abort("internetdisconnected")

    with page(stub.base, locale="en-US") as pg:
        pg.route("**/api/wifi", on_post)
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.select_option("#ssid", "HomeNet")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        expect(pg.locator("#setup-msg")).to_have_text(STRINGS["err_net"]["en"], timeout=6000)
        pg.wait_for_timeout(CONSTS["NET_CONNECT_RETRY_MS"] + 500)
        assert len(posts) == 2


SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_firmware_row_checks_and_installs(page, locale, lang):
    """The /device firmware row (R_FW_OTA): the running build, Check, then "Install <sha7>" once the
    firmware reports a newer build; each button posts /api/ota's action; a refusal shows its code.

    .. test:: The /device page's firmware row drives POST /api/ota
       :id: T_FW_OTA_PAGE
       :links: R_FW_OTA
    """
    posts = []

    def on_ota(route):
        posts.append(json.loads(route.request.post_data))
        ok = len(posts) == 1
        route.fulfill(
            status=200 if ok else 409,
            content_type="application/json",
            body='{"ok":true}' if ok else '{"ok":false,"error":"busy"}',
        )

    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        pg.route("**/api/ota", on_ota)
        status = pg.locator("#fw-status")
        expect(status).to_have_text(STRINGS["fw_running"][lang].replace("{sha}", "bef07f1"))
        expect(pg.locator("#fw-install")).to_be_hidden()
        with pg.expect_response("**/api/ota"):
            pg.click("#fw-check")
            expect(pg.locator("#fw-check")).to_be_hidden()  # no second click while it is in flight
        assert posts == [{"action": "check"}]
        expect(pg.locator("#fw-msg")).to_have_text("")
        stub.fixtures["station"]["/api/state"]["device"]["ota"].update(state="available", available_sha=SHA)
        install = pg.locator("#fw-install")
        expect(install).to_have_text(STRINGS["fw_install"][lang].replace("{sha}", SHA[:7]))
        expect(status).to_contain_text(STRINGS["ota_available"][lang].replace("{sha}", SHA[:7]))
        install.click()
        expect(pg.locator("#fw-msg")).to_have_text(STRINGS["ota_error"][lang].replace("{error}", "busy"))
        assert posts[1] == {"action": "install"}
        stub.fixtures["station"]["/api/state"]["device"]["ota"].update(state="downloading", progress_pct=42)
        expect(status).to_contain_text(STRINGS["ota_downloading"][lang].replace("{pct}", "42"))
        expect(install).to_be_hidden()
        expect(pg.locator("#fw-check")).to_be_hidden()


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_clock_line_shows_sync_and_boot(page, locale, lang):
    """The /device clock line (R_FW_TIME): device.time's source/synced picks the text, with the boot number.

    .. test:: The /device page shows device.time
       :id: T_FW_TIME_PAGE
       :links: R_FW_TIME
    """
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        line = pg.locator("#time")
        expect(line).to_have_text(STRINGS["time_synced"][lang].replace("{boot}", "17"))
        clock = stub.fixtures["station"]["/api/state"]["device"]["time"]
        clock.update(synced=False)
        expect(line).to_have_text(STRINGS["time_waiting"][lang].replace("{boot}", "17"))
        clock.update(source="none")
        expect(line).to_have_text(STRINGS["time_none"][lang].replace("{boot}", "17"))


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_storage_and_resources_lines(page, locale, lang):
    """The /device Storage line (R_FW_SD_LOG: device.sd ok / absent / error, + dropped records) and
    the Resources line (device.res), EN + DE; the unit-clock text of the clock line (R_FW_TIME).

    .. test:: The /device page shows device.sd and device.res
       :id: T_FW_SD_LOG_PAGE
       :links: R_FW_SD_LOG
    """

    def s(key, **kw):
        out = STRINGS[key][lang]
        for k, v in kw.items():
            out = out.replace("{%s}" % k, str(v))
        return out

    storage = s("sd_title") + ": "
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        dev = stub.fixtures["station"]["/api/state"]["device"]
        line = pg.locator("#sd")
        expect(line).to_have_text(
            storage + s("sd_ok", free="28.1", total="29.7") + " · " + s("sd_log", seq=1042)
        )
        expect(pg.locator("#res")).to_have_text(
            s("res_title")
            + ": "
            + " · ".join([s("res_ram", free=61, min=38), s("res_psram", free=7302), s("res_cpu", pct=4)])
        )
        dev["sd"].update(dropped=3)
        expect(line).to_have_text(
            storage
            + " · ".join([s("sd_ok", free="28.1", total="29.7"), s("sd_log", seq=1042), s("sd_dropped", n=3)])
        )
        dev["sd"].update(state="absent", total_mb=None, free_mb=None, dropped=0)
        expect(line).to_have_text(storage + s("sd_absent"))
        dev["sd"].update(state="error")
        expect(line).to_have_text(storage + s("sd_error"))
        dev["res"].update(heap_free_kb=None, heap_min_kb=None, psram_free_kb=None, cpu_pct=None)
        expect(pg.locator("#res")).to_have_text("")
        dev["time"].update(source="unit", synced=False)
        expect(pg.locator("#time")).to_have_text(s("time_unit", boot=17))


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_battery_line(page, locale, lang):
    """The /device Battery line (R_FW_BATTERY: device.battery), EN + DE: on battery with and without
    the runtime estimate, on USB charging / full, and empty without a power chip.

    .. test:: The /device page shows device.battery
       :id: T_FW_BATTERY_PAGE
       :links: R_FW_BATTERY
    """

    def s(key, **kw):
        out = STRINGS[key][lang]
        for k, v in kw.items():
            out = out.replace("{%s}" % k, str(v))
        return out

    head = s("batt_title") + ": "
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        b = stub.fixtures["station"]["/api/state"]["device"]["battery"]
        line = pg.locator("#battery")
        expect(line).to_have_text(
            head + " · ".join(["64 %", "3.89 V", s("batt_on"), s("batt_left", time="2 h 10 min")])
        )
        b.update(runtime_min=None)
        expect(line).to_have_text(head + " · ".join(["64 %", "3.89 V", s("batt_on")]))
        b.update(pct=87, mv=4120, usb=True, charging=True, runtime_min=None)
        expect(line).to_have_text(head + " · ".join(["87 %", "4.12 V", s("batt_charging")]))
        b.update(pct=100, charging=False)
        expect(line).to_have_text(head + " · ".join(["100 %", "4.12 V", s("batt_usb")]))
        b.update(pct=None, mv=None, usb=None, charging=None)
        expect(line).to_have_text("")


def test_firmware_row_offers_no_update_over_the_setup_hotspot(stub, page):
    """Like the firmware's 403: no Check/Install outside station mode, and the row says why."""
    with page(stub.base, locale="en-US") as pg:
        expect(pg.locator("#fw-status")).to_contain_text(STRINGS["fw_station_only"]["en"])
        expect(pg.locator("#fw-check")).to_be_hidden()
        expect(pg.locator("#fw-install")).to_be_hidden()


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_auto_camper_row_shows_and_switches(page, locale, lang):
    """The /device auto-camper row (R_FW_AUTO_CAMPER): on/off, a restore owed, the toggle posts
    ``{"enabled": <the opposite>}``; a refusal shows the error; the setup hotspot has no toggle.

    .. test:: The /device page shows device.auto_camper and switches it
       :id: T_FW_AUTO_CAMPER_PAGE
       :links: R_FW_AUTO_CAMPER
    """
    posts = []

    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        ac = stub.fixtures["station"]["/api/state"]["device"]["auto_camper"]

        def on_post(route):
            posts.append(json.loads(route.request.post_data))
            ok = len(posts) == 1
            if ok:
                ac.update(enabled=posts[-1]["enabled"])
            route.fulfill(
                status=200 if ok else 500,
                content_type="application/json",
                body='{"ok":true,"auto_camper":false}' if ok else '{"ok":false,"error":"store"}',
            )

        pg.route("**/api/auto_camper", on_post)
        status, toggle = pg.locator("#ac-status"), pg.locator("#ac-toggle")
        expect(pg.locator("#ac-title")).to_have_text(STRINGS["ac_title"][lang])
        expect(status).to_have_text(STRINGS["ac_on"][lang])
        expect(toggle).to_have_text(STRINGS["ac_turn_off"][lang])
        ac.update(state="armed")
        expect(status).to_have_text(STRINGS["ac_on"][lang] + " · " + STRINGS["ac_armed"][lang])
        toggle.click()
        expect(status).to_have_text(STRINGS["ac_off"][lang] + " · " + STRINGS["ac_armed"][lang])
        expect(toggle).to_have_text(STRINGS["ac_turn_on"][lang])
        assert posts == [{"enabled": False}]
        toggle.click()
        expect(pg.locator("#ac-msg")).to_have_text(STRINGS["ac_error"][lang])
        assert posts[1] == {"enabled": True}
    with ux_gallery.EspStub("setup") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#ac-status")).to_contain_text(STRINGS["ac_station_only"][lang])
        expect(pg.locator("#ac-toggle")).to_be_hidden()


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_usage_data_row_shows_and_switches(page, locale, lang):
    """The /device "Send usage data" row (R_UX_TELEMETRY): device.ux on/off, the toggle posts
    ``{"enabled": <the opposite>}`` to /api/ux_setting; a refusal shows the error; no toggle over
    the setup hotspot.

    .. test:: The /device page shows device.ux and switches it
       :id: T_UX_DEVICE_PAGE
       :links: R_UX_TELEMETRY
    """
    posts = []

    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        ux = stub.fixtures["station"]["/api/state"]["device"]["ux"]

        def on_post(route):
            posts.append(json.loads(route.request.post_data))
            ok = len(posts) == 1
            if ok:
                ux.update(enabled=posts[-1]["enabled"])
            route.fulfill(
                status=200 if ok else 500,
                content_type="application/json",
                body='{"ok":true,"ux":false}' if ok else '{"ok":false,"error":"store"}',
            )

        pg.route("**/api/ux_setting", on_post)
        status, toggle = pg.locator("#ux-status"), pg.locator("#ux-toggle")
        expect(status).to_have_text(STRINGS["ux_on"][lang])
        expect(toggle).to_have_text(STRINGS["ac_turn_off"][lang])
        toggle.click()
        expect(status).to_have_text(STRINGS["ux_off"][lang])
        expect(toggle).to_have_text(STRINGS["ac_turn_on"][lang])
        assert posts == [{"enabled": False}]
        toggle.click()
        expect(pg.locator("#ux-msg")).to_have_text(STRINGS["ux_error"][lang])
        assert posts[1] == {"enabled": True}
    with ux_gallery.EspStub("setup") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#ux-status")).to_have_text(STRINGS["ux_on"][lang])
        expect(pg.locator("#ux-toggle")).to_be_hidden()


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_screen_row_shows_state_and_switches(page, locale, lang):
    """The /device Screen row (R_FW_DISPLAY_WAKE): state, brightness with its range, room light, a
    sensor fault; each switch posts its one member to ``/api/display``, the -/+ stepper the offset in
    5-point steps; over the setup hotspot the controls are greyed; without a screen (host build, QEMU)
    the row is hidden.

    .. test:: The /device page shows device.display and switches its settings, EN + DE
       :id: T_FW_DISPLAY_WAKE_PAGE
       :links: R_FW_DISPLAY_WAKE
    """
    posts = []

    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        disp = stub.fixtures["station"]["/api/state"]["device"]["display"]

        def on_post(route):
            posts.append(json.loads(route.request.post_data))
            disp.update(posts[-1])
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

        pg.route("**/api/display", on_post)
        status, auto = pg.locator("#scr-status"), pg.locator("#scr-auto")
        expect(pg.locator("#scr-title")).to_have_text(STRINGS["scr_title"][lang])
        expect(status).to_have_text(STRINGS["scr_on"][lang] + " · 68 % (8–100) · 42 lx")
        expect(pg.locator("#scr-auto-l")).to_have_text(STRINGS["scr_auto"][lang])
        expect(auto).to_be_checked()
        expect(pg.locator("#scr-prox")).to_be_checked()
        expect(pg.locator("#scr-off")).to_have_text(STRINGS["scr_offset"][lang].replace("{n}", "0"))
        auto.uncheck()
        for _ in range(50):  # the POST lands before the fixture flips the next poll's state
            if posts:
                break
            pg.wait_for_timeout(100)
        pg.locator("#scr-up").click()
        expect(pg.locator("#scr-off")).to_have_text(STRINGS["scr_offset"][lang].replace("{n}", "+5"))
        pg.locator("#scr-dn").click()
        pg.locator("#scr-dn").click()
        expect(pg.locator("#scr-off")).to_have_text(STRINGS["scr_offset"][lang].replace("{n}", "-5"))
        disp.update(state="off", brightness_pct=0, lux=None, sensor="error")
        expect(status).to_have_text(
            STRINGS["scr_off"][lang] + " · 0 % (8–100) · " + STRINGS["scr_nolux"][lang]
        )
        expect(auto).not_to_be_checked()
        assert posts == [{"auto": False}, {"offset": 5}, {"offset": 0}, {"offset": -5}]
    with ux_gallery.EspStub("setup") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#scr-auto")).to_be_disabled()
        expect(pg.locator("#scr-up")).to_be_disabled()
        stub.fixtures["setup"]["/api/state"]["device"]["display"].update(state=None)
        expect(pg.locator("#scr")).to_be_hidden()
