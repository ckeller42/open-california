"""Browser e2e of the ESP32 status/setup page (``firmware/web/index_gen.html``) over
``tools.ux_gallery.EspStub`` — no firmware, BLE or radio; Playwright reroutes what a test needs
(a failing POST). Every test fails on an uncaught JS / console error (``page``), except the
browser's own "Failed to load resource" line for a request a test aborts on purpose.
OPT-IN like test_gui.py: skipped without Playwright + Chromium. The same page against the real
firmware HTTP core: tests/firmware/test_web_e2e.py (Linux host tier).
"""

import contextlib
import json
import re
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


def _expect_switch(pg, row, on):
    """The one on/off pattern (UX review M2, owner 2026-10-15): one compact row per setting, its label
    left, a real switch right (``role=switch`` + ``aria-checked``) named by that label."""
    sw = pg.locator("#%s-toggle" % row)
    expect(sw).to_have_attribute("role", "switch")
    expect(sw).to_have_attribute("aria-checked", "true" if on else "false")
    expect(sw).to_have_attribute("aria-labelledby", "%s-status" % row)
    expect(pg.get_by_role("switch", name=pg.locator("#%s-status" % row).inner_text())).to_have_count(1)


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
        expect(status).to_have_text(
            STRINGS["fw_running"][lang].replace("{sha}", "bef07f1") + " · " + STRINGS["fw_ch_stable"][lang]
        )
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
        # the channel select (T_FW_OTA_CHANNEL_UI covers the real firmware): it posts {"channel"}
        expect(pg.locator("#fw-channel")).to_have_value("stable")
        with pg.expect_response("**/api/ota"):
            pg.select_option("#fw-channel", "latest")
        assert posts[2] == {"channel": "latest"}
        expect(pg.locator("#fw-msg")).to_have_text(STRINGS["fw_ch_error"][lang])  # the stub's 409
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
        expect(pg.locator("#fw-status")).to_contain_text(STRINGS["station_only"]["en"])
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
        _expect_switch(pg, "ac", True)
        ac.update(state="armed")
        expect(status).to_have_text(STRINGS["ac_on"][lang] + " · " + STRINGS["ac_armed"][lang])
        toggle.click()
        expect(status).to_have_text(STRINGS["ac_off"][lang] + " · " + STRINGS["ac_armed"][lang])
        _expect_switch(pg, "ac", False)
        assert posts == [{"enabled": False}]
        toggle.click()
        expect(pg.locator("#ac-msg")).to_have_text(STRINGS["ac_error"][lang])
        assert posts[1] == {"enabled": True}
    with ux_gallery.EspStub("setup") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#ac-status")).to_contain_text(STRINGS["station_only"][lang])
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
        _expect_switch(pg, "ux", True)
        toggle.click()
        expect(status).to_have_text(STRINGS["ux_off"][lang])
        _expect_switch(pg, "ux", False)
        assert posts == [{"enabled": False}]
        toggle.click()
        expect(pg.locator("#ux-msg")).to_have_text(STRINGS["ux_error"][lang])
        assert posts[1] == {"enabled": True}
    with ux_gallery.EspStub("setup") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#ux-status")).to_have_text(
            STRINGS["ux_on"][lang] + " · " + STRINGS["station_only"][lang]
        )
        expect(pg.locator("#ux-toggle")).to_be_hidden()


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_screen_row_shows_state_and_switches(page, locale, lang):
    """The /device Screen row (R_FW_DISPLAY_WAKE): state, brightness with its range, room light, a
    sensor fault; each on/off row ("<label>: on" + one button naming the target state) posts its one
    member to ``/api/display``, the -/+ stepper the offset in 5-point steps; over the setup hotspot
    the rows say "needs the home WiFi" and show no control; without a screen (host build, QEMU)
    the row is hidden.

    .. test:: The /device page shows device.display and switches its settings, EN + DE
       :id: T_FW_DISPLAY_WAKE_PAGE
       :links: R_FW_DISPLAY_WAKE
    """
    posts = []

    def row(key, on):
        return STRINGS[key][lang] + ": " + STRINGS["scr_on" if on else "scr_off"][lang]

    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        disp = stub.fixtures["station"]["/api/state"]["device"]["display"]

        def on_post(route):
            posts.append(json.loads(route.request.post_data))
            disp.update(posts[-1])
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

        pg.route("**/api/display", on_post)
        status, auto = pg.locator("#scr-status"), pg.locator("#scr-auto-toggle")
        expect(pg.locator("#scr-title")).to_have_text(STRINGS["scr_title"][lang])
        expect(status).to_have_text(STRINGS["scr_on"][lang] + " · 68 % (8–100) · 42 lx")
        expect(pg.locator("#scr-auto-status")).to_have_text(row("scr_auto", True))
        _expect_switch(pg, "scr-auto", True)
        expect(pg.locator("#scr-prox-status")).to_have_text(row("scr_prox", True))
        expect(pg.locator("#scr-motion-status")).to_have_text(row("scr_motion", True))
        expect(pg.locator("#scr-off")).to_have_text(STRINGS["scr_offset"][lang].replace("{n}", "0"))
        auto.click()
        expect(pg.locator("#scr-auto-status")).to_have_text(row("scr_auto", False))
        _expect_switch(pg, "scr-auto", False)
        pg.locator("#scr-up").click()
        expect(pg.locator("#scr-off")).to_have_text(STRINGS["scr_offset"][lang].replace("{n}", "+5"))
        pg.locator("#scr-dn").click()
        pg.locator("#scr-dn").click()
        expect(pg.locator("#scr-off")).to_have_text(STRINGS["scr_offset"][lang].replace("{n}", "-5"))
        pg.locator("#scr-motion-toggle").click()
        expect(pg.locator("#scr-motion-status")).to_have_text(row("scr_motion", False))
        disp.update(state="off", brightness_pct=0, lux=None, sensor="error")
        expect(status).to_have_text(
            STRINGS["scr_off"][lang] + " · 0 % (8–100) · " + STRINGS["scr_nolux"][lang]
        )
        assert posts == [
            {"auto": False},
            {"offset": 5},
            {"offset": 0},
            {"offset": -5},
            {"motion_wake": False},
        ]
    with ux_gallery.EspStub("setup") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#scr-auto-status")).to_have_text(
            row("scr_auto", True) + " · " + STRINGS["station_only"][lang]
        )
        for hidden in ("#scr-auto-toggle", "#scr-up", "#scr-motion-toggle"):
            expect(pg.locator(hidden)).to_be_hidden()
        stub.fixtures["setup"]["/api/state"]["device"]["display"].update(state=None)
        expect(pg.locator("#scr")).to_be_hidden()


# --- Satellite settings: title, back link, WiFi box (change network), pairing box, hotspot rule ----


def _t(key, lang, **kw):
    out = STRINGS[key][lang]
    for k, v in kw.items():
        out = out.replace("{%s}" % k, str(v))
    return out


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_title_back_link_and_pairing_box(page, locale, lang):
    """The page names itself "Satellite settings" (also the tab title) and leads back to the camper
    UI: ``/`` in station mode, ``/app`` over the setup hotspot; the Camper unit box says whether a
    unit is paired and links to the pairing wizard."""
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#title")).to_have_text(STRINGS["title"][lang])
        expect(pg).to_have_title(STRINGS["title"][lang])
        back = pg.locator("#device a")
        expect(back).to_have_text(STRINGS["app_link"][lang])
        expect(back).to_have_attribute("href", "/")
        expect(pg.locator("#pair-status")).to_have_text(STRINGS["pair_on"][lang])
        expect(pg.locator("#pair-link")).to_have_text(STRINGS["pair_manage"][lang])  # paired
        expect(pg.locator("#pair-link")).to_have_attribute("href", "/#devicestatus")
    with ux_gallery.EspStub("setup") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#device a")).to_have_attribute("href", "/app")
        expect(pg.locator("#pair-status")).to_have_text(STRINGS["pair_off"][lang])
        expect(pg.locator("#pair-link")).to_have_text(STRINGS["pair_now"][lang])  # not paired
        expect(pg.locator("#pair-link")).to_have_attribute("href", "/app#pair")


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_station_rows_say_they_need_the_home_wifi_over_the_hotspot(page, locale, lang):
    """One rule over the setup hotspot: every station-only row ends in the same suffix and shows no
    control (no bare ``disabled``); no stray Channel label (``[hidden]`` wins over ``label``)."""
    suffix = " · " + STRINGS["station_only"][lang]
    with ux_gallery.EspStub("setup") as stub, page(stub.base + "/device", locale=locale) as pg:
        for row in ("ac", "ux", "exp", "scr-auto", "scr-prox", "scr-motion"):
            expect(pg.locator("#%s-status" % row)).to_have_text(re.compile(re.escape(suffix) + "$"))
            expect(pg.locator("#%s-toggle" % row)).to_be_hidden()
        expect(pg.locator("#scr-status")).to_contain_text(suffix)
        expect(pg.locator("#fw-status")).to_have_text(re.compile(re.escape(suffix) + "$"))
        for hidden in ("#scr-dn", "#scr-up", "#fw-channel-l", "#fw-channel", "#fw-check", "#wifi-change"):
            expect(pg.locator(hidden)).to_be_hidden()
        expect(pg.locator("#adv-title")).to_have_text(STRINGS["adv_title"][lang])
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#ux-toggle")).to_be_visible()
        expect(pg.locator("#ux-status")).to_have_text(STRINGS["ux_on"][lang])  # no suffix at home


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_station_wifi_box_shows_the_network_and_opens_the_form(page, locale, lang):
    """Online, the WiFi box names the network (ssid · ip · dBm) and offers Change network…, which opens
    the one join form with Search again (the firmware scans online too, G1); an empty list says so
    and takes a typed name; "Other network…" reveals that field; the eye shows the password."""
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        expect(pg.locator("#wifi-now")).to_have_text(
            _t("wifi_now", lang, ssid="HomeNet") + " · 192.168.1.57 · -58 dBm"
        )
        expect(pg.locator("#wifi-form")).to_be_hidden()
        pg.click("#wifi-change")
        expect(pg.locator("#wifi-form")).to_be_visible()
        expect(pg.locator("#rescan")).to_be_visible()  # G1: GET /api/wifi scans online too
        expect(pg.locator("#scan-empty")).to_have_text(STRINGS["no_networks"][lang])
        expect(pg.locator("#ssid-name")).to_be_visible()
        stub.fixtures["station"]["/api/wifi"]["scan"] = [{"ssid": "minsel", "rssi": -60, "secure": True}]
        pg.click("#rescan")  # the new list replaces the empty one: the hint goes
        pg.wait_for_selector("#ssid option[value=minsel]", state="attached")
        expect(pg.locator("#scan-empty")).to_be_hidden()
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        stub.fixtures["station"]["/api/wifi"]["scan"] = [{"ssid": "minsel", "rssi": -60, "secure": True}]
        pg.click("#wifi-change")
        pg.wait_for_selector("#ssid option[value=minsel]", state="attached")
        expect(pg.locator("#scan-empty")).to_be_hidden()
        expect(pg.locator("#ssid-name")).to_be_hidden()
        pg.select_option("#ssid", label=STRINGS["other_network"][lang])
        expect(pg.locator("#ssid-name")).to_be_visible()
        expect(pg.locator("#psk")).to_have_attribute("type", "password")
        pg.check("#psk-show")
        expect(pg.locator("#psk")).to_have_attribute("type", "text")
        pg.uncheck("#psk-show")
        expect(pg.locator("#psk")).to_have_attribute("type", "password")


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_change_network_asks_first_and_cancel_posts_nothing(page, locale, lang):
    """Connect online asks in the page first, with the honest warning naming the current network it
    returns to on a wrong password (G2); Cancel puts the buttons back and posts nothing."""
    warn = _t("wifi_warn", lang, ssid="HomeNet")
    assert "HomeNet" in warn and CONSTS["NET_AP_SSID"] not in warn
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        pg.click("#wifi-change")
        pg.fill("#ssid-name", "minsel")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        expect(pg.locator("#wifi-acts")).to_contain_text(warn)
        cancel = pg.locator("#wifi-acts").get_by_role("button", name=STRINGS["sens_cancel"][lang])
        expect(cancel).to_be_focused()
        pg.wait_for_timeout(CONSTS["NET_PAGE_POLL_MS"] + 500)  # a poll while the step is open
        expect(pg.locator("#banner")).to_be_hidden()
        expect(pg.locator("#wifi-acts")).to_contain_text(warn)
        cancel.click()
        expect(pg.locator("#connect")).to_be_visible()
        expect(pg.locator("#wifi-switch")).to_be_hidden()
        pg.wait_for_timeout(300)
        assert stub.wifi_posts == []


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_change_network_switches_and_comes_back(page, locale, lang):
    """Confirm renders the static "Switching networks" panel BEFORE the POST (the phone loses the page
    once the satellite leaves), posts ``{ssid, psk}``, and keeps polling: while the satellite is gone
    nothing claims success; when it answers again on the network, the "Connected as…" result shows."""
    host = "http://%s.local" % CONSTS["NET_HOSTNAME"]
    held = []
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        pg.route("**/api/wifi", lambda r: held.append(r) if r.request.method == "POST" else r.continue_())
        pg.click("#wifi-change")
        pg.fill("#ssid-name", "minsel")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        pg.locator("#wifi-acts button.danger").click()
        panel = pg.locator("#wifi-switch")
        expect(panel).to_be_visible()  # the POST is still held: the panel came first
        assert len(held) == 1 and stub.wifi_posts == []
        expect(panel).to_contain_text(_t("switch_other", lang, ssid="minsel"))
        expect(panel).to_contain_text(
            _t("switch_lost", lang, old="HomeNet", ap=CONSTS["NET_AP_SSID"], appsk=CONSTS["NET_AP_PSK"])
        )
        expect(panel.locator('a[href="%s"]' % host).first).to_be_visible()
        expect(panel.locator('a[href="http://%s"]' % CONSTS["NET_AP_ADDR"])).to_be_visible()
        held[0].continue_()
        for _ in range(50):
            if stub.wifi_posts:
                break
            pg.wait_for_timeout(100)
        assert stub.wifi_posts == [{"ssid": "minsel", "psk": "test-psk-1234"}]
        stub.down = True  # the satellite left for minsel: no answers at all
        pg.wait_for_timeout(3 * CONSTS["NET_PAGE_POLL_MS"])
        expect(pg.locator("#setup-msg a")).to_have_count(0)  # no success while it is gone
        stub.down = False
        msg = pg.locator("#setup-msg")
        expect(msg).to_contain_text(_t("joined", lang, ip="192.168.1.57", ssid="minsel"), timeout=10000)
        expect(msg.locator('a[href="%s"]' % host)).to_be_visible()
        expect(panel).to_be_hidden()


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_change_to_the_same_network_posts_ssid_and_psk(page, locale, lang):
    """Same network, new password: the panel says reload; the stub receives exactly ``{ssid, psk}``."""
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        stub.fixtures["station"]["/api/wifi"]["scan"] = [{"ssid": "HomeNet", "rssi": -52, "secure": True}]
        pg.click("#wifi-change")
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.select_option("#ssid", "HomeNet")
        pg.fill("#psk", "new-psk-5678")
        pg.click("#connect")
        with pg.expect_response("**/api/wifi"):
            pg.locator("#wifi-acts button.danger").click()
        expect(pg.locator("#wifi-switch")).to_contain_text(STRINGS["switch_same"][lang])
        assert stub.wifi_posts == [{"ssid": "HomeNet", "psk": "new-psk-5678"}]
        expect(pg.locator("#setup-msg")).to_have_text(_t("joining", lang, ssid="HomeNet"))


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_nothing_renders_empty_before_the_first_answer(locale, lang):
    """Live check 2026-10-15: before the first /api/state (and /api/wifi) answer the page flashed the
    hotspot join form and empty box headers. Until the first answer only a neutral "Loading…" line
    shows; then the boxes for the satellite's mode."""
    held = []
    with ux_gallery.EspStub("station") as stub, sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(locale=locale)
        released = []
        pg.route("**/api/*", lambda r: r.continue_() if released else held.append(r))
        pg.goto(stub.base + "/device")
        expect(pg.locator("#loading")).to_have_text(STRINGS["loading"][lang])
        pg.wait_for_timeout(2 * CONSTS["NET_PAGE_POLL_MS"])
        for box in (
            "#setup",
            "#wifi-form",
            "#pair",
            "#ac",
            "#scr",
            "#fw",
            "#adv-title",
            "#ux",
            "#exp",
            "#sens",
        ):
            expect(pg.locator(box)).to_be_hidden()
        assert not pg.get_by_role("button", name=STRINGS["connect"][lang]).is_visible()
        released.append(True)
        for r in held:
            r.continue_()
        expect(pg.locator("#wifi-now")).to_contain_text("HomeNet")
        expect(pg.locator("#loading")).to_be_hidden()
        for box in ("#setup", "#pair", "#ac", "#fw", "#ux", "#exp"):
            expect(pg.locator(box)).to_be_visible()
        expect(pg.locator("#wifi-form")).to_be_hidden()  # online: the form only after Change network…
        browser.close()


@pytest.mark.parametrize("width", [390])
def test_screen_box_is_compact_one_row_per_setting(page, width):
    """The Screen box (owner 2026-10-15): one row per setting, label left and the switch right on the
    same line, the brightness -/+ stepper on one row; every target at least 44 px; the switch posts
    its one member; keyboard: Space toggles a focused switch."""
    posts = []
    with (
        ux_gallery.EspStub("station") as stub,
        page(stub.base + "/device", viewport={"width": width, "height": 844}) as pg,
    ):
        disp = stub.fixtures["station"]["/api/state"]["device"]["display"]

        def on_post(route):
            posts.append(json.loads(route.request.post_data))
            disp.update(posts[-1])
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

        pg.route("**/api/display", on_post)
        expect(pg.locator("#scr-prox-toggle")).to_be_visible()
        for row in ("scr-auto", "scr-prox", "scr-motion", "ac", "ux", "exp"):
            lbl, sw = (
                pg.locator("#%s-status" % row).bounding_box(),
                pg.locator("#%s-toggle" % row).bounding_box(),
            )
            assert sw["x"] > lbl["x"] + lbl["width"] - 1, row  # the switch right of its label
            assert sw["y"] < lbl["y"] + lbl["height"] and lbl["y"] < sw["y"] + sw["height"], row  # same line
            assert sw["width"] >= 44 and sw["height"] >= 44, (row, sw)
            assert sw["x"] + sw["width"] <= width, row
        dn, off, up = (pg.locator(i).bounding_box() for i in ("#scr-dn", "#scr-off", "#scr-up"))
        assert abs(dn["y"] - up["y"]) < 2 and dn["height"] >= 44 and up["width"] >= 44
        assert off["y"] < dn["y"] + dn["height"]
        pg.locator("#scr-prox-toggle").focus()
        pg.keyboard.press("Space")
        expect(pg.locator("#scr-prox-toggle")).to_have_attribute("aria-checked", "false")
        assert posts == [{"prox_wake": False}]
        box = pg.locator("#scr").bounding_box()
        assert box["height"] < 330, box  # was ~560 px: three full-width buttons + their status lines


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_failed_change_says_it_is_back_on_the_old_network(page, locale, lang):
    """G2: the satellite answers again on the old network with ``reverted`` true — the WiFi box says
    the new network didn't work (the reason in words) and that it is back on the old one; no
    "Connected as…" success line."""
    with ux_gallery.EspStub("station") as stub, page(stub.base + "/device", locale=locale) as pg:
        pg.click("#wifi-change")
        pg.fill("#ssid-name", "typo")
        pg.fill("#psk", "wrong-psk-99")
        pg.click("#connect")
        with pg.expect_response("**/api/wifi"):
            pg.locator("#wifi-acts button.danger").click()
        stub.down = True  # gone while it tries "typo"
        pg.wait_for_timeout(2 * CONSTS["NET_PAGE_POLL_MS"])
        stub.fixtures["station"]["/api/wifi"].update(reverted=True, last_error="auth")
        stub.down = False
        msg = pg.locator("#setup-msg")
        want = _t("wifi_reverted", lang, why=STRINGS["why_auth"][lang], ssid="HomeNet")
        expect(msg).to_have_text(want, timeout=10000)
        expect(msg.locator("a")).to_have_count(0)
        expect(pg.locator("#wifi-switch")).to_be_hidden()
