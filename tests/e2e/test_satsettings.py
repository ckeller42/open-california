"""Browser e2e of the satellite's settings as screens of the shared web UI (one-web-UI design, U1):
``satsettings`` ("Satellite settings", from Device status' Satellite card or ``#satsettings``) and
``wifisetup`` (the landing screen while the satellite runs its setup hotspot), over
``tools.ux_gallery.EspStub`` — no firmware, BLE or radio. They port the boxes of the firmware's own
/device page (tests/e2e/test_esp_page.py, which U2 deletes with that page). Every test fails on an
uncaught JS / console error (``error_gated_page``) unless it says otherwise.

.. test:: Satellite settings and WiFi setup are screens of the shared UI (EN + DE, 390 px)
   :id: T_UI_SAT_SETTINGS
   :links: R_FW_OTA, R_FW_AUTO_CAMPER, R_UX_TELEMETRY, R_FW_DISPLAY_WAKE, R_FW_TIME
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

LOCALES = [("en-US", "en"), ("de-DE", "de")]
PHONE = {"width": 390, "height": 844}
_DE_SRC = (Path(__file__).resolve().parents[2] / "calictl/webui/strings.de.js").read_text(encoding="utf-8")
DE = {
    json.loads('"%s"' % k): json.loads('"%s"' % v)
    for k, v in re.findall(r'^\s*"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)",?\s*(?://.*)?$', _DE_SRC, re.M)
}


def tr(en, lang, **kw):
    """The UI's text for ``en`` in ``lang`` (strings.de.js), ``{name}`` filled from ``kw``."""
    s = DE[en] if lang == "de" else en
    for k, v in kw.items():
        s = s.replace("{%s}" % k, str(v))
    return s


def _lang(locale):
    return "de" if locale.startswith("de") else "en"


@contextlib.contextmanager
def _open(stub, path="/#satsettings", allow=(), **kw):
    """A page on ``path`` (default: the Satellite-settings deep link); fails on a page error or a
    console error, except a "Failed to load resource" line whose text contains one of ``allow``."""
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(**kw)
        errors = []
        pg.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
        pg.on(
            "console",
            lambda m: (
                errors.append("console.error: %s" % m.text)
                if m.type == "error"
                and "favicon" not in m.text
                and not ("Failed to load resource" in m.text and any(a in m.text for a in allow))
                else None
            ),
        )
        pg.goto(stub.base + path)
        try:
            yield pg
        finally:
            browser.close()
        assert not errors, errors


def _settings(stub, locale="en-US", **kw):
    """``with _settings(stub) as pg:`` — the Satellite-settings screen, opened by its deep link."""
    return _open(stub, locale=locale, **kw)


def _title(pg, lang, en="Satellite settings"):
    expect(pg.locator("#title")).to_have_text(tr(en, lang))


def _expect_switch(pg, row, on):
    """The one on/off pattern: label left, a real switch right (``role=switch`` + ``aria-checked``)
    named by that label."""
    sw = pg.locator("#%s-toggle" % row)
    expect(sw).to_have_attribute("role", "switch")
    expect(sw).to_have_attribute("aria-checked", "true" if on else "false")
    expect(sw).to_have_attribute("aria-labelledby", "%s-status" % row)
    expect(pg.get_by_role("switch", name=pg.locator("#%s-status" % row).inner_text())).to_have_count(1)


def _posts(stub, path):
    return [b for p, b in stub.posts if p == path]


def _wait(pg, cond, what):
    for _ in range(60):
        if cond():
            return
        pg.wait_for_timeout(100)
    raise AssertionError("timed out: %s" % what)


@pytest.fixture
def station():
    with ux_gallery.EspStub("station") as s:
        yield s


@pytest.fixture
def setup():
    with ux_gallery.EspStub("setup") as s:
        yield s


# --- navigation: Device status <-> Satellite settings, the deep link, the setup landing ----------


def _device_status(pg):
    pg.click("#menu")
    pg.locator(".menupop [role=menuitem]").first.click()
    expect(pg.locator("#title")).to_have_text(re.compile("Device status|Gerätestatus"))


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_satellite_card_opens_the_settings_screen_and_back_returns(station, locale, lang):
    """Device status' Satellite card "Satellite settings ›" opens the screen in the same app (no page
    load); its ‹ and the browser's back both return to Device status."""
    with _open(station, "/", locale=locale) as pg:
        _device_status(pg)
        pg.get_by_role("button", name=tr("Satellite settings ›", lang)).click()
        _title(pg, lang)
        assert pg.evaluate("location.pathname") == "/"
        assert "/device" not in station.requests
        pg.click("#back")
        _title(pg, lang, "Device status")
        pg.get_by_role("button", name=tr("Satellite settings ›", lang)).click()
        _title(pg, lang)
        pg.go_back()
        _title(pg, lang, "Device status")
        pg.go_back()
        expect(pg.locator("#title")).to_have_text("Open California")  # Home


def test_deep_link_opens_the_settings_and_the_hash_is_consumed(station):
    with _settings(station) as pg:
        _title(pg, "en")
        assert pg.evaluate("location.hash") == ""
        expect(pg.locator("#back")).to_be_visible()


def test_setup_mode_lands_on_wifi_setup_and_station_mode_shows_home(setup):
    """The setup hotspot: the app opens on the WiFi-setup screen (the join form + the pairing entry);
    once the satellite runs in station mode the app shows Home as usual."""
    with _open(setup, "/app", locale="en-US") as pg:
        _title(pg, "en", "WiFi setup")
        expect(pg.locator("#setup-title")).to_have_text("Connect to your WiFi")
        expect(pg.locator("#wifi-form")).to_be_visible()
        expect(pg.locator("#unpaired-banner").get_by_role("button", name="Pair…")).to_be_visible()
        expect(pg.locator("#pair")).to_contain_text(
            "Controls work only on your home WiFi — not over the setup hotspot"
        )
        expect(pg.locator("#ac")).to_have_count(0)  # the settings stay on Satellite settings
        setup.fixtures["setup"]["/api/state"]["device"]["wifi"] = {
            "mode": "station",
            "ssid": "HomeNet",
            "ip": "192.168.1.57",
            "rssi": -58,
        }
        _title(pg, "en", "Open California")
        expect(pg.locator(".tilegrid")).to_be_visible()


def test_wifi_setup_pairs_in_place(setup):
    """The pairing entry on WiFi setup: the unpaired banner's Pair… opens the existing wizard right there
    (over the hotspot, no home WiFi needed), and a code pairs the unit."""
    with _open(setup, "/app", locale="en-US") as pg:
        _title(pg, "en", "WiFi setup")
        pg.locator("#unpaired-banner").get_by_role("button", name="Pair…").click()
        card = pg.locator("#pairing-card")
        expect(card).to_be_visible()
        _title(pg, "en", "WiFi setup")
        card.locator("#pairing-ready").check()
        card.get_by_role("button", name="Pair now").click()
        pg.fill("#pairing-passkey", "123456")
        card.get_by_role("button", name="Send").click()
        expect(card).to_contain_text("✓ Paired")
        assert [b["action"] for b in setup.pairing_posts] == ["start", "passkey"]


def test_setup_landing_lets_the_owner_go_home(setup):
    """WiFi setup is a landing, not a trap: ‹ goes to Home and stays there."""
    with _open(setup, "/app", locale="en-US") as pg:
        _title(pg, "en", "WiFi setup")
        pg.click("#back")
        _title(pg, "en", "Open California")
        pg.wait_for_timeout(2 * CONSTS["NET_PAGE_POLL_MS"] + 500)
        _title(pg, "en", "Open California")


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_device_status_shows_the_clock_row(station, locale, lang):
    """Every status line lives in Device status' Satellite card: the clock (R_FW_TIME) with the boot
    number joins storage, battery and resources; Satellite settings shows none of them."""
    with _open(station, "/", locale=locale) as pg:
        _device_status(pg)
        card = pg.locator("#app .card", has=pg.get_by_role("heading", name=tr("Satellite", lang), exact=True))
        row = card.locator(".row", has=pg.get_by_text(tr("Clock", lang), exact=True))
        expect(row).to_contain_text(tr("synced (SNTP) · boot {boot}", lang, boot=17))
        clock = station.fixtures["station"]["/api/state"]["device"]["time"]
        clock.update(synced=False)
        expect(row).to_contain_text(tr("not synced yet · boot {boot}", lang, boot=17))
        clock.update(source="unit")
        expect(row).to_contain_text(tr("from the camper unit · boot {boot}", lang, boot=17))
        clock.update(source="none")
        expect(row).to_contain_text(tr("no source · boot {boot}", lang, boot=17))
        pg.get_by_role("button", name=tr("Satellite settings ›", lang)).click()
        _title(pg, lang)
        for gone in ("Storage", "Resources", "Clock"):
            expect(pg.locator("#app").get_by_text(tr(gone, lang), exact=True)).to_have_count(0)


# --- the boxes ---------------------------------------------------------------------------------


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_boxes_in_order_and_camper_unit_links_to_device_status(station, locale, lang):
    with _settings(station, locale=locale) as pg:
        heads = pg.locator("#app h2:visible")
        expect(heads).to_have_text(
            [
                tr(h, lang)
                for h in (
                    "WiFi",
                    "Camper unit",
                    "Restore camping after you park",
                    "Screen",
                    "Firmware update",
                    "Advanced",
                    "Experimental",
                )
            ]
        )
        expect(pg.locator("#pair-status")).to_have_text(tr("Paired ✓", lang))
        pg.locator("#pair").get_by_role("button", name=tr("Device status ›", lang)).click()
        _title(pg, lang, "Device status")


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_firmware_row_checks_installs_and_switches_channel(station, locale, lang):
    """R_FW_OTA: the running build + channel, Check, then "Install <sha7>"; each posts /api/ota's
    action; a refusal shows its code; the channel select posts ``{"channel"}``."""
    sha = "0123456789abcdef0123456789abcdef01234567"
    with _settings(station, locale=locale, allow=("409",)) as pg:
        status = pg.locator("#fw-status")
        expect(status).to_have_text(
            tr("Running {sha}", lang, sha="bef07f1") + " · " + tr("stable (tested)", lang)
        )
        expect(pg.locator("#fw-install")).to_be_hidden()
        pg.click("#fw-check")
        _wait(pg, lambda: _posts(station, "/api/ota"), "the check POST")
        assert _posts(station, "/api/ota") == [{"action": "check"}]
        station.fixtures["station"]["/api/state"]["device"]["ota"].update(
            state="available", available_sha=sha
        )
        install = pg.locator("#fw-install")
        expect(install).to_have_text(tr("Install {sha}", lang, sha=sha[:7]))
        expect(status).to_contain_text(tr("{sha} is available", lang, sha=sha[:7]))
        station.post_reply["/api/ota"] = (409, {"ok": False, "error": "busy"})
        install.click()
        expect(pg.locator("#fw-msg")).to_have_text(tr("update failed ({error})", lang, error="busy"))
        assert _posts(station, "/api/ota")[1] == {"action": "install"}
        expect(pg.locator("#fw-channel")).to_have_value("stable")
        pg.select_option("#fw-channel", "latest")
        expect(pg.locator("#fw-msg")).to_have_text(tr("Couldn't change the channel", lang))
        assert _posts(station, "/api/ota")[2] == {"channel": "latest"}
        expect(pg.locator("#fw-channel")).to_have_value("stable")  # back to what the satellite runs
        station.fixtures["station"]["/api/state"]["device"]["ota"].update(
            state="downloading", progress_pct=42
        )
        expect(status).to_contain_text(tr("downloading {pct} %", lang, pct=42))
        expect(install).to_be_hidden()
        expect(pg.locator("#fw-check")).to_be_hidden()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_auto_camper_usage_data_and_experimental_switch(station, locale, lang):
    """R_FW_AUTO_CAMPER, R_UX_TELEMETRY, R_FW_EXPERIMENTAL: each switch posts the opposite and follows
    the satellite's state; a refusal shows the row's error."""
    dev = station.fixtures["station"]["/api/state"]["device"]
    with _settings(station, locale=locale, allow=("500",)) as pg:
        expect(pg.locator("#ac-status")).to_have_text(tr("Auto camper on", lang))
        _expect_switch(pg, "ac", True)
        dev["auto_camper"].update(state="armed")
        expect(pg.locator("#ac-status")).to_have_text(
            tr("Auto camper on", lang) + " · " + tr("⟳ will restore on park", lang)
        )
        pg.click("#ac-toggle")
        expect(pg.locator("#ac-status")).to_have_text(
            tr("Auto camper off", lang) + " · " + tr("⟳ will restore on park", lang)
        )
        _expect_switch(pg, "ac", False)
        assert _posts(station, "/api/auto_camper") == [{"enabled": False}]
        station.post_reply["/api/auto_camper"] = (500, {"ok": False, "error": "store"})
        pg.click("#ac-toggle")
        expect(pg.locator("#ac-msg")).to_have_text(tr("Couldn't change auto camper", lang))

        expect(pg.locator("#ux-status")).to_have_text(tr("Send usage data: on", lang))
        pg.click("#ux-toggle")
        expect(pg.locator("#ux-status")).to_have_text(tr("Send usage data: off", lang))
        _expect_switch(pg, "ux", False)
        assert _posts(station, "/api/ux_setting") == [{"enabled": False}]

        expect(pg.locator("#exp-status")).to_have_text(tr("Sensors (experimental): off", lang))
        expect(pg.locator("#sens")).to_be_hidden()
        station.fixtures["station"]["/api/sensors"] = {"configured": [], "seen": [], "scan": "ok"}
        pg.click("#exp-toggle")
        expect(pg.locator("#exp-status")).to_have_text(tr("Sensors (experimental): on", lang))
        assert _posts(station, "/api/experimental") == [{"sensors": True}]
        expect(pg.locator("#sens")).to_be_visible()  # the Sensors box follows the switch


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_screen_box_rows_and_brightness_stepper(station, locale, lang):
    """R_FW_DISPLAY_WAKE: state, brightness with its range, room light; each switch posts its one
    member to /api/display, the -/+ stepper the offset in 5-point steps; without a screen the box
    is hidden."""

    def row(key, on):
        return tr(key, lang) + ": " + tr("on" if on else "off", lang)

    disp = station.fixtures["station"]["/api/state"]["device"]["display"]
    with _settings(station, locale=locale) as pg:
        status = pg.locator("#scr-status")
        expect(status).to_have_text(tr("on", lang) + " · 68 % (8–100) · 42 lx")
        expect(pg.locator("#scr-auto-status")).to_have_text(row("Brightness from room light", True))
        _expect_switch(pg, "scr-auto", True)
        expect(pg.locator("#scr-prox-status")).to_have_text(row("Wake when a hand is near", True))
        expect(pg.locator("#scr-off")).to_have_text(tr("Brightness {n}", lang, n="0"))
        pg.click("#scr-auto-toggle")
        expect(pg.locator("#scr-auto-status")).to_have_text(row("Brightness from room light", False))
        pg.click("#scr-up")
        expect(pg.locator("#scr-off")).to_have_text(tr("Brightness {n}", lang, n="+5"))
        pg.click("#scr-dn")
        pg.click("#scr-dn")
        expect(pg.locator("#scr-off")).to_have_text(tr("Brightness {n}", lang, n="-5"))
        pg.click("#scr-motion-toggle")
        expect(pg.locator("#scr-motion-status")).to_have_text(row("Wake on motion", False))
        disp.update(state="off", brightness_pct=0, lux=None, sensor="error")
        expect(status).to_have_text(
            tr("off", lang) + " · 0 % (8–100) · " + tr("no light sensor reading", lang)
        )
        assert _posts(station, "/api/display") == [
            {"auto": False},
            {"offset": 5},
            {"offset": 0},
            {"offset": -5},
            {"motion_wake": False},
        ]
        disp.update(state=None)
        expect(pg.locator("#scr")).to_be_hidden()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_over_the_hotspot_every_station_row_says_it_needs_the_home_wifi(setup, locale, lang):
    """One rule over the setup hotspot: every station-only row ends in the same suffix and shows no
    control; the firmware row offers no update."""
    suffix = " · " + tr("needs the home WiFi", lang)
    with _settings(setup, locale=locale) as pg:
        _title(pg, lang)
        for row in ("ac", "ux", "exp", "scr-auto", "scr-prox", "scr-motion"):
            expect(pg.locator("#%s-status" % row)).to_have_text(re.compile(re.escape(suffix) + "$"))
            expect(pg.locator("#%s-toggle" % row)).to_be_hidden()
        expect(pg.locator("#fw-status")).to_have_text(re.compile(re.escape(suffix) + "$"))
        for hidden in ("#scr-dn", "#scr-up", "#fw-channel", "#fw-check", "#fw-install", "#wifi-change"):
            expect(pg.locator(hidden)).to_be_hidden()
        expect(pg.locator("#wifi-form")).to_be_visible()  # the join form, as on WiFi setup


def test_settings_fit_a_phone_with_44px_targets(station):
    """390 px: no horizontal scroll; every switch and stepper button at least 44 × 44 px, the switch
    right of its label on the same line; Space toggles a focused switch."""
    with _settings(station, viewport=PHONE) as pg:
        expect(pg.locator("#scr-prox-toggle")).to_be_visible()
        assert pg.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
        for row in ("scr-auto", "scr-prox", "scr-motion", "ac", "ux", "exp"):
            lbl = pg.locator("#%s-status" % row).bounding_box()
            sw = pg.locator("#%s-toggle" % row).bounding_box()
            assert sw["x"] > lbl["x"] + lbl["width"] - 1, row
            assert sw["y"] < lbl["y"] + lbl["height"] and lbl["y"] < sw["y"] + sw["height"], row
            assert sw["width"] >= 44 and sw["height"] >= 44, (row, sw)
        for b in ("#scr-dn", "#scr-up"):
            box = pg.locator(b).bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44, (b, box)
        pg.locator("#scr-prox-toggle").focus()
        pg.keyboard.press("Space")
        expect(pg.locator("#scr-prox-toggle")).to_have_attribute("aria-checked", "false")
        assert _posts(station, "/api/display") == [{"prox_wake": False}]


# --- WiFi: change network online, Search again, the switch flow, reverted, the hotspot join -------


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_station_wifi_box_names_the_network_and_opens_the_form(station, locale, lang):
    """Online: "Connected to <ssid> · ip · dBm" + Change network…, which opens the join form with
    Search again (GET /api/wifi scans online too); an empty list says so and takes a typed name;
    "Other network…" reveals that field; Show password reveals the password."""
    wifi = station.fixtures["station"]["/api/wifi"]
    with _settings(station, locale=locale) as pg:
        expect(pg.locator("#wifi-now")).to_have_text(
            tr("Connected to {ssid}", lang, ssid="HomeNet") + " · 192.168.1.57 · -58 dBm"
        )
        expect(pg.locator("#wifi-form")).to_be_hidden()
        pg.click("#wifi-change")
        expect(pg.locator("#wifi-form")).to_be_visible()
        expect(pg.locator("#ssid")).to_be_focused()
        expect(pg.locator("#wifi-change")).to_be_hidden()  # [hidden] wins over .btnrow's display
        expect(pg.locator("#rescan")).to_be_visible()
        expect(pg.locator("#scan-empty")).to_have_text(
            tr("No networks listed — type the network's name.", lang)
        )
        expect(pg.locator("#ssid-name")).to_be_visible()
        wifi["scan"] = [
            {"ssid": "minsel", "rssi": -60, "secure": True},
            {"ssid": "open", "rssi": -70, "secure": False},
        ]
        pg.click("#rescan")
        pg.wait_for_selector("#ssid option[value=minsel]", state="attached")
        expect(pg.locator("#ssid option[value=open]")).to_be_disabled()  # an open network: no passphrase
        expect(pg.locator("#scan-empty")).to_be_hidden()
        expect(pg.locator("#ssid-name")).to_be_hidden()
        pg.select_option("#ssid", label=tr("Other network…", lang))
        expect(pg.locator("#ssid-name")).to_be_visible()
        expect(pg.locator("#psk")).to_have_attribute("type", "password")
        pg.check("#psk-show")
        expect(pg.locator("#psk")).to_have_attribute("type", "text")


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_change_network_asks_first_and_cancel_posts_nothing(station, locale, lang):
    """Connect online asks in the page first with the honest warning (a wrong password comes back by
    itself); Cancel puts the buttons back and posts nothing; a poll leaves the step and the typed
    password alone."""
    warn = tr(
        "The satellite leaves {ssid} now. If the new password is wrong, it returns to {ssid} by itself.",
        lang,
        ssid="HomeNet",
    )
    with _settings(station, locale=locale) as pg:
        pg.click("#wifi-change")
        pg.fill("#ssid-name", "minsel")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        acts = pg.locator("#wifi-acts")
        expect(acts).to_contain_text(warn)
        cancel = acts.get_by_role("button", name=tr("Cancel", lang))
        expect(cancel).to_be_focused()
        pg.wait_for_timeout(CONSTS["NET_PAGE_POLL_MS"] + 500)  # a poll while the step is open
        expect(acts).to_contain_text(warn)
        expect(pg.locator("#psk")).to_have_value("test-psk-1234")
        cancel.click()
        expect(pg.locator("#connect")).to_be_visible()
        expect(pg.locator("#wifi-switch")).to_be_hidden()
        pg.wait_for_timeout(300)
        assert station.wifi_posts == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_change_network_switches_and_comes_back(station, locale, lang):
    """Confirm renders the static "Switching networks" panel BEFORE the POST (the phone loses the page
    once the satellite leaves), posts ``{ssid, psk}`` and keeps polling: while the satellite is gone
    nothing claims success; once it answers on the network, "Connected as…" shows."""
    host = "http://%s.local" % CONSTS["NET_HOSTNAME"]
    held = []
    with _settings(station, locale=locale, allow=("ERR_EMPTY_RESPONSE",)) as pg:
        pg.route("**/api/wifi", lambda r: held.append(r) if r.request.method == "POST" else r.continue_())
        pg.click("#wifi-change")
        pg.fill("#ssid-name", "minsel")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        pg.locator("#wifi-acts button.danger").click()
        panel = pg.locator("#wifi-switch")
        expect(panel).to_be_visible()
        assert len(held) == 1 and station.wifi_posts == []
        expect(panel).to_contain_text(tr("Join {ssid} with this phone, then open:", lang, ssid="minsel"))
        expect(panel).to_contain_text(
            tr(
                "Back on {old} within a minute if the new password was wrong; otherwise join the setup hotspot {ap} (password {appsk}) and open:",
                lang,
                old="HomeNet",
                ap=CONSTS["NET_AP_SSID"],
                appsk=CONSTS["NET_AP_PSK"],
            )
        )
        expect(panel.locator('a[href="%s"]' % host).first).to_be_visible()
        expect(panel.locator('a[href="http://%s"]' % CONSTS["NET_AP_ADDR"])).to_be_visible()
        held[0].continue_()
        _wait(pg, lambda: station.wifi_posts, "the join POST")
        assert station.wifi_posts == [{"ssid": "minsel", "psk": "test-psk-1234"}]
        station.down = True  # the satellite left for minsel: no answers at all
        pg.wait_for_timeout(3 * CONSTS["NET_PAGE_POLL_MS"])
        expect(pg.locator("#setup-msg a")).to_have_count(0)
        station.down = False
        msg = pg.locator("#setup-msg")
        expect(msg).to_contain_text(
            tr(
                "Connected as {ip}. Join {ssid} with this phone too, then open:",
                lang,
                ip="192.168.1.57",
                ssid="minsel",
            ),
            timeout=10000,
        )
        expect(msg.locator('a[href="%s"]' % host)).to_be_visible()
        expect(panel).to_be_hidden()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_a_failed_change_says_it_is_back_on_the_old_network(station, locale, lang):
    """PR D: the satellite answers again on the old network with ``reverted`` — the box says the new
    network didn't work (why, in words) and that it is back; no success line."""
    with _settings(station, locale=locale, allow=("ERR_EMPTY_RESPONSE",)) as pg:
        pg.click("#wifi-change")
        pg.fill("#ssid-name", "typo")
        pg.fill("#psk", "wrong-psk-99")
        pg.click("#connect")
        pg.locator("#wifi-acts button.danger").click()
        _wait(pg, lambda: station.wifi_posts, "the join POST")
        station.down = True
        pg.wait_for_timeout(2 * CONSTS["NET_PAGE_POLL_MS"])
        station.fixtures["station"]["/api/wifi"].update(reverted=True, last_error="auth")
        station.down = False
        msg = pg.locator("#setup-msg")
        expect(msg).to_have_text(
            tr(
                "The new network didn't work ({why}); back on {ssid}.",
                lang,
                why=tr("wrong password", lang),
                ssid="HomeNet",
            ),
            timeout=10000,
        )
        expect(msg.locator("a")).to_have_count(0)
        expect(pg.locator("#wifi-switch")).to_be_hidden()


def test_change_to_the_same_network_posts_ssid_and_psk(station):
    with _settings(station) as pg:
        station.fixtures["station"]["/api/wifi"]["scan"] = [{"ssid": "HomeNet", "rssi": -52, "secure": True}]
        pg.click("#wifi-change")
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.select_option("#ssid", "HomeNet")
        pg.fill("#psk", "new-psk-5678")
        pg.click("#connect")
        pg.locator("#wifi-acts button.danger").click()
        expect(pg.locator("#wifi-switch")).to_contain_text("Reload this page in ~30 s, or open:")
        _wait(pg, lambda: station.wifi_posts, "the join POST")
        assert station.wifi_posts == [{"ssid": "HomeNet", "psk": "new-psk-5678"}]
        expect(pg.locator("#setup-msg")).to_have_text("Connecting to HomeNet…")


def test_the_form_checks_name_and_password_before_posting(station):
    with _settings(station) as pg:
        pg.click("#wifi-change")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        expect(pg.locator("#setup-msg")).to_have_text("Choose a network (1–32 characters).")
        pg.fill("#ssid-name", "minsel")
        pg.fill("#psk", "short")
        pg.click("#connect")
        expect(pg.locator("#setup-msg")).to_have_text("The password needs 8–63 characters.")
        assert station.wifi_posts == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_hotspot_join_posts_at_once_and_retries_once(setup, locale, lang):
    """Over the hotspot (WiFi setup) Connect posts at once (no question: nothing to leave); a lost
    answer is retried once after NET_CONNECT_RETRY_MS (the phone drops off the hotspot for a moment)."""
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

    with _open(setup, "/app", locale=locale, allow=("ERR_INTERNET_DISCONNECTED",)) as pg:
        pg.route("**/api/wifi", on_post)
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.select_option("#ssid", "HomeNet")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        expect(pg.locator("#setup-msg")).to_have_text(tr("Retrying the connection…", lang))
        expect(pg.locator("#setup-msg")).to_have_text(
            tr("Connecting to {ssid}…", lang, ssid="HomeNet"), timeout=CONSTS["NET_CONNECT_RETRY_MS"] + 3000
        )
        assert len(posts) == 2 and posts[0] == posts[1]
        assert json.loads(posts[0]) == {"ssid": "HomeNet", "psk": "test-psk-1234"}
        expect(pg.locator("#psk")).to_have_value("")


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_hotspot_join_failure_says_why(setup, locale, lang):
    with _open(setup, "/app", locale=locale) as pg:
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.fill("#psk", "test-psk-1234")
        pg.click("#connect")
        _wait(pg, lambda: setup.wifi_posts, "the join POST")
        setup.fixtures["setup"]["/api/wifi"]["last_error"] = "auth"
        expect(pg.locator("#setup-msg")).to_have_text(tr("Wrong password — try again.", lang), timeout=15000)


def test_calictl_never_offers_the_satellite_screens(error_gated_page):
    """calictl (no satellite device block): no Satellite card, ``#satsettings`` opens Home, and the
    screens cannot be reached by navigation either."""
    with ux_gallery.EspStub("calictl") as stub, error_gated_page(stub.base + "/#satsettings") as pg:
        expect(pg.locator(".tilegrid")).to_be_visible()
        _title(pg, "en", "Open California")
        pg.evaluate("goto('satsettings')")
        _title(pg, "en", "Open California")
        pg.evaluate("goto('wifisetup')")
        _title(pg, "en", "Open California")
        _device_status(pg)
        expect(pg.get_by_role("button", name="Satellite settings ›")).to_have_count(0)
        assert not {"/api/wifi", "/api/sensors"} & set(stub.requests)


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_both_screens_have_a_help_sheet(station, setup, locale, lang):
    """⋮ → Help on Satellite settings and WiFi setup, translated (a missing DE key would fall back to
    English)."""
    texts = []
    for stub, path in ((station, "/#satsettings"), (setup, "/app")):
        with _open(stub, path, locale=locale) as pg:
            expect(pg.locator("#title")).to_have_text(
                tr("Satellite settings" if stub is station else "WiFi setup", lang)
            )
            pg.click("#menu")
            pg.get_by_role("menuitem", name=tr("Help", lang), exact=True).click()
            sheet = pg.locator("dialog.help")
            expect(sheet).to_be_visible()
            texts.append(sheet.locator("p").all_inner_texts())
    assert all(texts)
    if lang == "de":
        assert "WLAN" in " ".join(texts[1])
