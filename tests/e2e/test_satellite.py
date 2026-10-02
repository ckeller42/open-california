"""Browser e2e of the ESP32 satellite UI: the calictl web UI bundle the firmware serves at GET /
(station mode), fed the firmware's RAW /api/state — no firmware, BLE or radio.

``tools.ux_gallery.EspStub`` serves the exact gzipped bytes of ``firmware/web/app_bundle_gen.h``
with an ESP-shaped ``/api/state`` decoded from the mock unit's seed (``satellite`` mode), or the
Python-interpreted calictl ``/api/state`` of the same frames (``calictl`` mode) as the reference.
Every test fails on an uncaught JS / console error (``error_gated_page``) unless it says otherwise.
OPT-IN like test_gui.py: skipped without Playwright + Chromium.
"""

import pytest

from tools import ux_gallery

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

TILES = ("Cooler", "Camping mode", "Lighting", "Air heater", "Water", "Energy", "Roof", "Vehicle")
ALLOWED = {"/", "/api/state"}
ENABLED_CONTROLS = (
    "#app :is(button,input,select,textarea):enabled, #app [role=button]:not([aria-disabled=true]),"
    " #app [role=switch]:not([disabled])"
)


@pytest.fixture
def stub():
    with ux_gallery.EspStub("satellite") as s:
        yield s


@pytest.fixture
def sat(stub, error_gated_page):
    with error_gated_page(stub.base) as pg:
        expect(pg.get_by_text("Satellite — display only")).to_be_visible()
        yield pg


def _open(pg, name):
    pg.locator(".tile", has_text=name).first.click()
    expect(pg.locator(".tilegrid")).to_have_count(
        0
    )  # "Vehicle" is also the home title: wait for the screen swap
    expect(pg.locator("#title")).to_have_text(name)


def _home(pg):
    pg.evaluate("document.getElementById('back').click()")
    expect(pg.locator(".tilegrid")).to_be_visible()


def test_every_tile_renders_and_every_control_is_disabled(sat, stub):
    sat.wait_for_selector(".tilegrid .tile")
    expect(sat.locator(".tile")).to_have_count(len(TILES))  # a new dashboard tile must join TILES
    for name in TILES:
        _open(sat, name)
        assert sat.locator("#app").inner_text().strip(), "%s screen rendered empty" % name
        sat.wait_for_timeout(2100)  # past a state poll's re-render, which must not re-enable a control
        expect(sat.locator(ENABLED_CONTROLS), "%s has an enabled control" % name).to_have_count(0)
        _home(sat)
    sat.wait_for_timeout(2500)  # at least one more state poll
    assert set(stub.requests) <= ALLOWED, stub.requests


def test_satellite_hides_calictl_only_chrome(sat):
    assert sat.locator(".session-pill").count() == 0
    _open(sat, "Camping mode")
    assert sat.get_by_text("Restore camping after you park").count() == 0
    _home(sat)
    _open(sat, "Energy")
    assert sat.locator(".echart-card").count() == 0
    _home(sat)
    sat.click("#menu")
    menu = sat.locator(".menupop")
    expect(menu.get_by_text("Device & WiFi")).to_be_visible()
    assert menu.get_by_text("Bluetooth pairing…").count() == 0 and menu.get_by_text("Unpair…").count() == 0


def test_menu_device_entry_opens_the_firmware_page(sat, stub):
    sat.click("#menu")
    sat.locator(".menupop").get_by_text("Device & WiFi").click()
    sat.wait_for_url(stub.base + "/device")
    sat.wait_for_selector("#device h2")
    sat.wait_for_selector('#device a[href="/"]')  # the link back (station mode)


def _tile_texts(pg):
    pg.wait_for_selector(".tilegrid .tile")
    return pg.locator(".tile").all_inner_texts(), pg.locator(".summary").inner_text()


def test_tiles_and_summary_equal_calictl_for_the_same_frames(stub, error_gated_page):
    with error_gated_page(stub.base) as pg:
        expect(pg.get_by_text("Satellite — display only")).to_be_visible()
        satellite = _tile_texts(pg)
    stub.mode = "calictl"
    with error_gated_page(stub.base) as pg:
        expect(pg.get_by_text("🔒 Read-only — control is disabled on this daemon.")).to_be_visible()
        assert _tile_texts(pg) == satellite


def test_german_banner_and_menu(stub, error_gated_page):
    with error_gated_page(stub.base, locale="de-DE") as pg:
        expect(pg.get_by_text("Satellit — nur Anzeige")).to_be_visible()
        pg.click("#menu")
        expect(pg.locator(".menupop").get_by_text("Gerät & WLAN")).to_be_visible()


@pytest.mark.parametrize("up,age_ms", [(False, 1200), (True, None), (True, 10001)])
def test_lost_or_stale_link_shows_the_offline_banner(stub, error_gated_page, up, age_ms):
    stub.fixtures["satellite"]["/api/state"]["device"]["link"].update(up=up, last_snap_age_ms=age_ms)
    with error_gated_page(stub.base) as pg:
        expect(pg.locator(".offline")).to_be_visible()
        expect(pg.locator("#status")).to_have_text("offline")


def test_a_failed_first_poll_never_reaches_calictl_only_endpoints(stub):
    """Review focus: the first /api/state answers 503 (the core busy/booting) -> no /api/pairing etc.
    Not error-gated: the 503 itself is a console error by design."""
    stub.fail_state = 1
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(stub.base)
        expect(pg.get_by_text("Satellite — display only")).to_be_visible(timeout=10000)
        pg.wait_for_timeout(2500)
        browser.close()
    assert set(stub.requests) <= ALLOWED, stub.requests


def test_unpaired_satellite_shows_the_banner_without_a_wizard_button(stub, error_gated_page):
    # The satellite pairs over its console: the banner's "Set up remote control" (-> the wizard ->
    # /api/pairing every 1 s against the single-connection core) must not exist there.
    stub.fixtures["satellite"]["/api/state"]["device"]["pairing"].update(state="idle", address=None)
    with error_gated_page(stub.base) as pg:
        expect(pg.locator("#unpaired-banner")).to_be_visible()
        assert pg.locator("#unpaired-banner button").count() == 0
        assert pg.get_by_text("Set up remote control").count() == 0
        pg.wait_for_timeout(2500)  # at least one more state poll
    assert set(stub.requests) <= ALLOWED, stub.requests


def test_api_refuses_every_path_but_state_on_the_satellite(sat, stub):
    # Defence in depth for any future caller: api() throws before fetch() for a non-/api/state path.
    for path in ("/api/pairing", "/api/command", "/api/history", "/api/session", "/api/auto_camper"):
        got = sat.evaluate("p => api(p).then(() => 'fetched', e => e.message)", path)
        assert got == "satellite: no " + path
    assert set(stub.requests) <= ALLOWED, stub.requests


def test_device_page_links_back_only_in_station_mode(stub, error_gated_page):
    with error_gated_page(stub.base + "/device") as pg:
        pg.wait_for_selector("#device h2")
        pg.wait_for_selector('#device a[href="/"]')
    stub.mode = "setup"
    with error_gated_page(stub.base + "/device") as pg:
        pg.wait_for_selector("#device h2")
        pg.wait_for_timeout(1500)  # a state poll has landed
        assert pg.locator('#device a[href="/"]').count() == 0
