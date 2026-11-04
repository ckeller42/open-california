"""Browser e2e of the guided first-time setup of a fresh satellite (``#setup/<step>``): welcome, WiFi,
pair, Victron, sensors, home screen, done — over ``tools.ux_gallery.EspStub``, no firmware, BLE or
radio. WiFi cannot be skipped; every later step can. A reload on the station address resumes at the
first unfinished step (``setupStep``). Every test fails on an uncaught JS / console error.
"""

import re

import pytest

from tests.e2e.test_satsettings import LOCALES, PHONE, _open, _posts, tr

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

from tools import ux_gallery  # noqa: E402

LOCAL = "http://calictl-esp.local/**"
NO_MDNS = ("ERR_NAME_NOT_RESOLVED", "ERR_FAILED", "calictl-esp.local")
GOVEE = [
    {"id": 0x2001 + 2 * i, "type": "govee", "model": "GVH5074_000%d" % i, "rssi": -60 - i} for i in range(3)
]
MPPT = {"id": 0x2011, "type": "victron_mppt", "model": "SmartSolar", "rssi": -72}


def _pending(stub, seen=(), sensors=False):
    """The station fixture as a satellite just joined from the hotspot: setup pending, no bond yet,
    the Sensors switch ``sensors``, ``seen`` nearby."""
    fx = stub.fixtures["station"]
    dev = fx["/api/state"]["device"]
    dev["setup"] = {"pending": True}
    dev["pairing"] = {"state": "idle", "address": None}
    dev["link"].update(up=False, ready=False, last_snap_age_ms=None)
    dev["experimental"] = {"sensors": sensors}
    fx["/api/pairing"].update(state="idle", address=None)
    fx["/api/sensors"] = {"configured": [], "seen": [dict(r) for r in seen], "scan": "ok"}
    return stub


@pytest.fixture
def setup():
    with ux_gallery.EspStub("setup") as s:
        yield s


@pytest.fixture
def station_pending():
    with ux_gallery.EspStub("station") as s:
        yield _pending(s)


@pytest.fixture
def station_pending_3govee():
    with ux_gallery.EspStub("station") as s:
        yield _pending(s, GOVEE + [MPPT], sensors=True)


def _page(stub, path, locale, **kw):
    return _open(stub, path, locale=locale, viewport=PHONE, **kw)


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_a_fresh_satellite_lands_on_welcome(setup, locale, lang):
    with _page(setup, "/", locale) as pg:
        expect(pg).to_have_url(re.compile(r"#setup/welcome$"))
        expect(pg.locator("#title")).to_have_text(tr("Set up the satellite", lang))
        expect(pg.locator("#setup-welcome")).to_contain_text("VictronConnect")
        pg.get_by_role("button", name=tr("Next", lang), exact=True).click()
        expect(pg).to_have_url(re.compile(r"#setup/wifi$"))
        pg.go_back()
        expect(pg).to_have_url(re.compile(r"#setup/welcome$"))


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_wifi_cannot_be_skipped(setup, locale, lang):
    with _page(setup, "/#setup/wifi", locale) as pg:
        expect(pg.locator("#wifi-form")).to_be_visible()
        expect(pg.locator("#setup-step")).to_contain_text(
            "Persönlicher Hotspot" if lang == "de" else "Personal Hotspot"
        )
        expect(pg.get_by_role("button", name=tr("Skip", lang))).to_have_count(0)


def test_over_the_hotspot_a_later_step_falls_back_to_welcome(setup):
    """Nothing after WiFi works without the home WiFi: a deep link there opens the welcome step."""
    with _page(setup, "/#setup/victron", "en-US") as pg:
        expect(pg).to_have_url(re.compile(r"#setup/welcome$"))


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_after_the_join_the_page_names_the_new_address(setup, locale, lang):
    setup.joins = True
    with _page(setup, "/#setup/wifi", locale) as pg:
        pg.wait_for_selector("#ssid option[value=HomeNet]", state="attached")
        pg.select_option("#ssid", "HomeNet")
        pg.fill("#psk", "secret123")
        pg.click("#connect")
        addr = pg.locator("#setup-next-addr")
        expect(addr).to_contain_text("http://192.168.", timeout=15000)
        expect(addr).to_contain_text("scanne den neuen QR-Code" if lang == "de" else "scan the new QR code")
        expect(addr.locator('a[href="http://192.168.1.57/#setup"]')).to_be_visible()
        pg.wait_for_timeout(2500)  # a station poll later the page still shows it (no jump to pairing)
        expect(pg).to_have_url(re.compile(r"#setup/wifi$"))
    assert setup.wifi_posts == [{"ssid": "HomeNet", "psk": "secret123"}]


def test_a_reload_on_the_station_address_resumes_at_pairing(station_pending):
    with _page(station_pending, "/#setup/welcome", "en-US") as pg:
        expect(pg).to_have_url(re.compile(r"#setup/pair$"))


def test_the_qr_link_without_a_step_resumes_too(station_pending):
    """The display's station QR is ``http://<ip>/#setup``: no step, so the state decides."""
    with _page(station_pending, "/#setup", "en-US") as pg:
        expect(pg).to_have_url(re.compile(r"#setup/pair$"))


def test_a_pending_setup_on_home_lands_in_the_wizard(station_pending):
    with _page(station_pending, "/", "en-US") as pg:
        expect(pg).to_have_url(re.compile(r"#setup/pair$"))


def test_a_finished_setup_lands_on_home(station_pending):
    station_pending.fixtures["station"]["/api/state"]["device"]["setup"] = {"pending": False}
    with _page(station_pending, "/", "en-US") as pg:
        expect(pg.locator(".tilegrid")).to_be_visible()
        assert pg.evaluate("location.hash") == ""


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_the_pair_step_pairs_with_a_countdown(station_pending, locale, lang):
    with _page(station_pending, "/#setup/pair", locale) as pg:
        step = pg.locator("#setup-step")
        expect(step).to_contain_text("Einstellungen → Bluetooth → Gerät verbinden")
        pg.get_by_role("button", name=tr("Pair now", lang), exact=True).click()
        expect(pg.locator("#setup-count")).to_contain_text(re.compile(tr("{n} s left", lang, n=r"\d+")))
        pg.fill("#pairing-passkey", "123456")
        pg.get_by_role("button", name=tr("Send", lang)).click()
        expect(pg.locator("#pairing-card")).to_contain_text(tr("✓ Paired — ", lang))
        pg.get_by_role("button", name=tr("Next", lang), exact=True).click()
        expect(pg).to_have_url(re.compile(r"#setup/victron$"))
    assert [b["action"] for b in station_pending.pairing_posts] == ["start", "passkey"]


def test_the_pair_step_can_be_skipped(station_pending):
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.get_by_role("button", name="Skip").click()
        expect(pg).to_have_url(re.compile(r"#setup/victron$"))
    assert station_pending.pairing_posts == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_answering_victron_yes_turns_the_sensors_switch_on(station_pending, locale, lang):
    station_pending.fixtures["station"]["/api/sensors"]["seen"] = [dict(MPPT)] + [dict(g) for g in GOVEE]
    with _page(station_pending, "/#setup/victron", locale) as pg:
        pg.get_by_role("button", name=tr("Yes", lang), exact=True).click()
        expect(pg.locator("#setup-step")).to_contain_text("Instant readout details")
        rows = pg.locator("#setup-sens .seen-row")
        expect(rows).to_have_count(1)  # Victron only: the Govee ones come on the sensors step
        expect(rows).to_contain_text("SmartSolar")
        expect(
            pg.locator("#setup-victron-next")
        ).to_be_focused()  # the answered question took the focus along
    assert {"sensors": True} in _posts(station_pending, "/api/experimental")


def test_answering_victron_no_leaves_the_switch_off(station_pending):
    with _page(station_pending, "/#setup/victron", "en-US") as pg:
        pg.get_by_role("button", name="No", exact=True).click()
        expect(pg).to_have_url(re.compile(r"#setup/sensors$"))
    assert _posts(station_pending, "/api/experimental") == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_all_seen_govee_sensors_are_listed(station_pending_3govee, locale, lang):
    with _page(station_pending_3govee, "/#setup/sensors", locale) as pg:
        expect(pg.locator("#setup-step")).to_contain_text("innen, dach")
        expect(pg.locator("#setup-sens .seen-row")).to_have_count(3)


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_no_mdns_still_shows_the_ip_and_the_reservation_hint(station_pending, locale, lang):
    with _page(station_pending, "/#setup/home", locale, allow=NO_MDNS) as pg:
        pg.route(LOCAL, lambda r: r.abort())
        home = pg.locator("#setup-home")
        expect(home).to_contain_text("DHCP-Reservierung" if lang == "de" else "DHCP reservation")
        expect(home.locator('a[href="http://192.168.1.57/"]')).to_be_visible()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_done_posts_setup_done_and_shows_skipped_rows(station_pending, locale, lang):
    with _page(station_pending, "/#setup/done", locale) as pg:
        check = pg.locator("#setup-check")
        expect(check).to_contain_text("HomeNet ✓")
        pg.get_by_role("button", name=tr("Done", lang), exact=True).click()
        expect(pg.locator("#setup-done-msg")).to_contain_text(tr("✓ Setup done", lang))
        assert {"done": True} in _posts(station_pending, "/api/setup")
        expect(check).to_contain_text(tr("not set up", lang))
        pg.get_by_role("button", name=tr("Open the app", lang)).click()
        expect(pg.locator(".tilegrid")).to_be_visible()
        expect(pg.locator("#pairing-card")).to_have_count(0)  # the wizard's pairing card stays in the wizard
        pg.go_back()
        check.get_by_role("button", name=tr("Satellite settings ›", lang)).first.click()
        expect(pg.locator("#title")).to_have_text(tr("Satellite settings", lang))


def test_leaving_after_wifi_counts_as_done(station_pending):
    """ "Done" = finished, or left the wizard after WiFi: ‹ from a step posts it once."""
    with _page(station_pending, "/#setup/pair", "en-US") as pg:
        pg.click("#back")
        expect(pg.locator(".tilegrid")).to_be_visible()
        expect(pg.locator("#pairing-card")).to_have_count(0)
        for _ in range(30):
            if _posts(station_pending, "/api/setup"):
                break
            pg.wait_for_timeout(100)
    assert _posts(station_pending, "/api/setup") == [{"done": True}]
