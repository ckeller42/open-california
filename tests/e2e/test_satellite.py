"""Browser e2e of the ESP32 satellite UI: the calictl web UI bundle the firmware serves at GET /
(station mode), fed the firmware's RAW /api/state — no firmware, BLE or radio.

``tools.ux_gallery.EspStub`` serves the exact gzipped bytes of ``firmware/web/app_bundle_gen.h``
with an ESP-shaped ``/api/state`` decoded from the mock unit's seed (``satellite`` mode), or the
Python-interpreted calictl ``/api/state`` of the same frames (``calictl`` mode) as the reference.
Every test fails on an uncaught JS / console error (``error_gated_page``) unless it says otherwise.
OPT-IN like test_gui.py: skipped without Playwright + Chromium.
"""

import calendar
import contextlib
import datetime
import json
import re
import threading
from zoneinfo import ZoneInfo

import pytest

from tools import gen_c_dict, ux_gallery

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

TILES = ("Cooler", "Camping mode", "Lighting", "Air heater", "Water", "Energy", "Roof", "Vehicle")
# + the wizard endpoint (R_FW_PAIRING_WIZARD) + the home-screen manifest/icons a browser may fetch
# + the usage beacons (R_UX_TELEMETRY)
ALLOWED = {"/", "/api/state", "/api/pairing", "/api/ux"} | {p for p, _, _ in gen_c_dict.pwa_assets()}
ALLOWED_LIVE = ALLOWED | {"/api/command"}
ELSEWHERE = "Only via buspi or the app"
LIVE = "() => !!(STATE._meta && STATE._meta.satellite && STATE._meta.read_only === false)"
ENABLED_CONTROLS = (
    "#app :is(button,input,select,textarea):enabled, #app [role=button]:not([aria-disabled=true]),"
    " #app [role=switch]:not([disabled])"
)


@pytest.fixture
def stub():
    with ux_gallery.EspStub("satellite") as s:
        yield s


def _display_only(stub):
    """A firmware without the control path (no device.control): the display-only satellite."""
    stub.fixtures["satellite"]["/api/state"]["device"].pop("control", None)


@pytest.fixture
def sat(stub, error_gated_page):
    _display_only(stub)
    with error_gated_page(stub.base) as pg:
        expect(pg.get_by_text("Satellite — display only")).to_be_visible()
        yield pg


@pytest.fixture
def live(stub, error_gated_page):
    with error_gated_page(stub.base) as pg:
        pg.wait_for_function(LIVE)
        yield pg


def _wait_commands(pg, stub, n=1):
    for _ in range(50):
        if len(stub.commands) >= n:
            return
        pg.wait_for_timeout(100)
    raise AssertionError("no command reached the stub: %r" % (stub.commands,))


def _open(pg, name):
    pg.locator(".tile", has_text=name).first.click()
    expect(pg.locator(".tilegrid")).to_have_count(
        0
    )  # "Vehicle" is also the home title: wait for the screen swap
    expect(pg.locator("#title")).to_have_text(name)


def _lighting_settings(pg):
    """Expand the Lighting page's collapsed "Functions & settings" (wake-up, door, save)."""
    if pg.locator("details.lfuncs").get_attribute("open") is None:  # the open state is remembered
        pg.locator("details.lfuncs > summary").click()
    expect(pg.locator("details.lfuncs")).to_have_attribute("open", "")


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


def test_satellite_hides_calictl_only_chrome(stub, error_gated_page):
    _display_only(stub)
    stub.fixtures["satellite"]["/api/state"]["device"].pop("auto_camper", None)  # an older firmware
    with error_gated_page(stub.base) as sat:
        expect(sat.get_by_text("Satellite — display only")).to_be_visible()
        _hides_calictl_only_chrome(sat)


def _hides_calictl_only_chrome(sat):
    assert sat.locator(".session-pill").count() == 0
    _open(sat, "Camping mode")
    assert sat.get_by_text("Restore camping after you park").count() == 0
    _home(sat)
    _open(sat, "Energy")
    assert sat.locator(".echart-card").count() == 0
    _home(sat)
    sat.click("#menu")
    menu = sat.locator(".menupop")
    expect(menu.get_by_text("Device status")).to_be_visible()
    # the pairing wizard runs on the satellite too (R_FW_PAIRING_WIZARD); the fixture holds a bond
    # no pairing in the menu (owner 2026-10-15): banner, Device status and /device hold it
    assert menu.locator("[role=menuitem]").all_inner_texts() == ["Device status", "Help", "Deutsch"]


def _ds_card(pg, title):
    """A Device-status card by its heading ("Camper unit", "Satellite", "buspi")."""
    return pg.locator("#app .card", has=pg.get_by_role("heading", name=title, exact=True))


def _row(card, label):
    return card.locator(".row", has=card.page.get_by_text(label, exact=True))


def test_menu_device_status_screen(sat, stub):
    """The in-app Device-status screen as two cards (UX review 3.2 / M4): "Camper unit" (connection,
    pairing with the MAC behind Details, Unpair…) and "Satellite" (firmware, WiFi ssid · ip · dBm,
    uptime, storage, battery, resources, Satellite settings ›); no buspi card, no "BLE link" row
    duplicating the connection, the battery labelled like /device (L6). Satellite settings › opens
    the settings screen in the same app."""
    sat.click("#menu")
    sat.locator(".menupop").get_by_text("Device status").click()
    expect(sat.locator("#title")).to_have_text("Device status")
    unit, s = _ds_card(sat, "Camper unit"), _ds_card(sat, "Satellite")
    expect(_row(unit, "Connection")).to_contain_text("online")
    expect(_row(unit, "Pairing")).to_contain_text("Paired ✓")
    expect(sat.get_by_text("C0:FF:EE:CA:11:F0")).to_be_hidden()  # the MAC only behind Details
    unit.locator("summary", has_text="Details").click()
    expect(unit.get_by_text("C0:FF:EE:CA:11:F0")).to_be_visible()  # bonded unit (/api/pairing)
    expect(unit.get_by_role("button", name="Unpair…")).to_be_visible()
    expect(unit.get_by_role("button", name="Pair…", exact=True)).to_have_count(0)
    expect(_row(s, "Firmware")).to_contain_text("bef07f1")  # satellite firmware (device.fw)
    expect(_row(s, "WiFi")).to_contain_text("HomeNet · 192.168.1.57 · -58 dBm")
    # the SD log's card (R_FW_SD_LOG, device.sd) and the satellite's own resources (device.res)
    expect(_row(s, "Storage")).to_contain_text("SD 28.1 of 29.7 GB free · log #1042")
    expect(_row(s, "Resources")).to_contain_text("RAM 61 KB free (lowest 38) · PSRAM 7302 KB free · CPU 4 %")
    # the satellite's own battery (R_FW_BATTERY, device.battery)
    expect(_row(s, "Battery")).to_contain_text("64 % · 3.89 V · on battery · ~2 h 10 min left")
    assert sat.get_by_text("BLE link").count() == 0
    expect(_ds_card(sat, "buspi")).to_have_count(0)
    s.get_by_role("button", name="Satellite settings ›").click()  # satellite-only link
    expect(sat.locator("#title")).to_have_text("Satellite settings")
    assert "/device" not in stub.requests


def test_device_status_in_german(stub, error_gated_page):
    _display_only(stub)
    with error_gated_page(stub.base, locale="de-DE") as pg:
        expect(pg.get_by_text("Satellit — nur Anzeige")).to_be_visible()
        pg.click("#menu")
        pg.locator(".menupop").get_by_text("Gerätestatus").click()
        unit = _ds_card(pg, "Camper-Einheit")
        expect(_row(unit, "Kopplung")).to_contain_text("Gekoppelt ✓")
        expect(unit.locator("summary")).to_have_text("Details")
        expect(unit.get_by_role("button", name="Entkoppeln…")).to_be_visible()
        expect(
            _ds_card(pg, "Satellit").get_by_role("button", name="Satellit-Einstellungen ›")
        ).to_be_visible()


def test_device_status_unpair_confirms_and_opens_the_wizard_at_the_top(stub, error_gated_page):
    """Unpair… in Device status: the one confirm text (PR A); accepted, the pairing is reset and the
    wizard opens at the top of Home, in view at phone size."""
    asked = []

    def answer(d):
        asked.append(d.message)
        d.accept()

    with error_gated_page(stub.base, viewport=PHONE) as pg:
        pg.wait_for_function(LIVE)
        pg.on("dialog", answer)
        pg.click("#menu")
        pg.locator(".menupop").get_by_text("Device status").click()
        _ds_card(pg, "Camper unit").get_by_role("button", name="Unpair…").click()
        card = pg.locator("#pairing-card")
        expect(card.get_by_role("button", name="Pair now")).to_be_in_viewport()
        above = card.evaluate(
            "c => c.getBoundingClientRect().bottom"
            " <= document.querySelector('.tilegrid').getBoundingClientRect().top"
        )
        assert above, "the wizard card renders below the tiles"
    assert asked == [UNPAIR_CONFIRM], asked
    assert stub.pairing_posts == [{"action": "reset", "confirm": True}], stub.pairing_posts


def test_device_status_offers_pair_when_not_paired(stub, error_gated_page):
    """No bond: the Camper unit card says "Not paired" and offers Pair… only (no Details, no Unpair…);
    Pair… opens the wizard at the top of Home."""
    _display_only(stub)
    for snap in (
        stub.fixtures["satellite"]["/api/pairing"],
        stub.fixtures["satellite"]["/api/state"]["device"]["pairing"],
    ):
        snap.update(state="idle", address=None)
    with error_gated_page(stub.base, viewport=PHONE) as pg:
        expect(pg.get_by_text("Satellite — display only")).to_be_visible()
        pg.click("#menu")
        pg.locator(".menupop").get_by_text("Device status").click()
        unit = _ds_card(pg, "Camper unit")
        expect(_row(unit, "Pairing")).to_contain_text("Not paired")
        expect(unit.locator("details")).to_have_count(0)
        expect(unit.get_by_role("button", name="Unpair…")).to_have_count(0)
        unit.get_by_role("button", name="Pair…", exact=True).click()
        expect(pg.locator("#pairing-card")).to_be_in_viewport()


def test_device_status_retries_a_failed_pairing_fetch(stub):
    """A failed load-time /api/pairing shows "—" (unknown), never "not paired"; opening the
    screen re-fetches, so a later open recovers the pairing (CodeRabbit, PR #267).
    Not error-gated: the 503s themselves are console errors by design."""
    _display_only(stub)
    stub.fail_pairing = 2  # the load-time fetch AND the first screen-open retry fail
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(stub.base)
        expect(pg.get_by_text("Satellite — display only")).to_be_visible(timeout=10000)
        pg.click("#menu")
        pg.locator(".menupop").get_by_text("Device status").click()
        row = _row(_ds_card(pg, "Camper unit"), "Pairing")
        expect(row).to_contain_text("—")
        assert pg.get_by_text("Not paired").count() == 0
        pg.click("#back")  # leave and re-open: this retry succeeds
        pg.click("#menu")
        pg.locator(".menupop").get_by_text("Device status").click()
        expect(row).to_contain_text("Paired ✓")
        browser.close()


def test_a_failed_first_poll_never_reaches_calictl_only_endpoints(stub):
    """Review focus: the first /api/state answers 503 (the core busy/booting) -> no /api/pairing
    before a state answered (an unknown runtime), nothing calictl-only ever.
    Not error-gated: the 503 itself is a console error by design."""
    _display_only(stub)
    stub.fail_state = 1
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(stub.base)
        expect(pg.get_by_text("Satellite — display only")).to_be_visible(timeout=10000)
        pg.wait_for_timeout(2500)
        browser.close()
    assert set(stub.requests) <= ALLOWED, stub.requests
    states = [i for i, r in enumerate(stub.requests) if r == "/api/state"]
    assert "/api/pairing" not in stub.requests[: states[1]], stub.requests  # not before a state answered


def test_api_refuses_every_path_but_state_on_the_satellite(sat, stub):
    # Defence in depth for any future caller: api() throws before fetch() for a non-/api/state path.
    # A display-only satellite (no device.control) still refuses /api/command.
    for path in ("/api/command", "/api/session", "/api/auto_camper"):
        got = sat.evaluate("p => api(p).then(() => 'fetched', e => e.message)", path)
        assert got == "satellite: no " + path
    assert set(stub.requests) <= ALLOWED, stub.requests


def test_device_page_links_to_the_ui_in_every_mode(stub, error_gated_page):
    """Station mode: back to / (the UI). Setup hotspot: to /app — the UI with the pairing wizard,
    since / is the setup page there."""
    with error_gated_page(stub.base + "/device") as pg:
        pg.wait_for_selector('#device a[href="/"]')
    stub.mode = "setup"
    with error_gated_page(stub.base + "/device") as pg:
        expect(pg.locator('#device a[href="/app"]')).to_have_text("‹ Camper UI")
        expect(pg.locator('#pair-link[href="/app#pair"]')).to_have_text("Pair now ›")
        assert pg.locator('#device a[href="/"]').count() == 0


def test_device_page_manage_pairing_opens_device_status(stub, error_gated_page):
    """Paired: /device's Camper unit box offers "Manage pairing ›" (not "Pair…", owner 2026-10-15),
    which opens the camper UI's Device status, its Camper-unit card with Unpair…; the hash is consumed."""
    _display_only(stub)
    with error_gated_page(stub.base + "/device", viewport=PHONE) as pg:
        link = pg.locator("#pair-link")
        expect(link).to_have_text("Manage pairing ›")
        expect(link).to_have_attribute("href", "/#devicestatus")
        link.click()
        expect(pg.locator("#title")).to_have_text("Device status")
        expect(_ds_card(pg, "Camper unit").get_by_role("button", name="Unpair…")).to_be_visible()
        assert pg.evaluate("location.hash") == ""


def test_device_page_pair_now_opens_the_wizard_at_the_top(stub, error_gated_page):
    """Not paired: "Pair now ›" opens the camper UI with the pairing wizard at the top of Home."""
    _display_only(stub)
    for snap in (
        stub.fixtures["satellite"]["/api/pairing"],
        stub.fixtures["satellite"]["/api/state"]["device"]["pairing"],
    ):
        snap.update(state="idle", address=None)
    with error_gated_page(stub.base + "/device", viewport=PHONE) as pg:
        link = pg.locator("#pair-link")
        expect(link).to_have_text("Pair now ›")
        expect(link).to_have_attribute("href", "/#pair")
        link.click()
        card = pg.locator("#pairing-card")
        expect(card.get_by_text("Pair the camper unit")).to_be_in_viewport()
        assert pg.evaluate("location.hash") == ""


# -- the live satellite (device.control.writes true: station mode) ---------------------------------


def test_controls_are_live_and_post_calictls_command_shape(live, stub):
    """Writes on -> no "display only" banner, every control enabled; a fridge toggle POSTs calictl's
    body shape to /api/command and the page requests nothing else.

    .. test:: The satellite's controls go live when the firmware accepts writes
       :id: T_SAT_UI_LIVE
       :links: R_FW_SHARED_UI, R_FW_CONTROL_API
    """
    live.on("dialog", lambda d: d.accept())
    expect(live.locator(".readonly")).to_have_count(0)
    _open(live, "Cooler")
    sw = live.get_by_role("switch", name="Refrigerator box")
    expect(sw).to_be_enabled()
    sw.click()
    _wait_commands(live, stub)
    assert stub.commands[0] == {"function": "cooler", "what": "power", "value": "on", "confirm": True}
    # the stub's state never changes: after the confirm window, the honest "not confirmed"
    expect(live.locator(".toast")).to_have_text("Sent — the unit didn't confirm it", timeout=9000)
    assert set(stub.requests) <= ALLOWED_LIVE, stub.requests


@pytest.mark.parametrize("locale,tile", [("en-US", "Cooler"), ("de-DE", "Kühlbox")])
def test_a_command_is_confirmed_from_the_units_own_state(stub, error_gated_page, locale, tile):
    """The ESP answers ``applied: null`` by design (no readback), so every success used to toast the
    warning. The UI now watches the next polls for the targeted field: the unit's push (here the
    stub's state flip) -> the normal success toast, no warning.

    .. test:: The satellite UI confirms a command from the unit's reported state
       :id: T_SAT_UI_CONFIRM_FROM_STATE
       :links: R_FW_SHARED_UI
    """

    def unit_applies(body):
        if (body["function"], body["what"]) == ("cooler", "power"):
            stub.fixtures["satellite"]["/api/state"]["fn"]["cooler"]["State"] = (
                1 if body["value"] == "on" else 0
            )

    stub.on_command = unit_applies
    with error_gated_page(stub.base, locale=locale) as pg:
        pg.wait_for_function(LIVE)
        pg.on("dialog", lambda d: d.accept())
        _open(pg, tile)
        pg.get_by_role("switch", name="Refrigerator box").click()
        _wait_commands(pg, stub)
        expect(pg.locator(".toast")).to_have_text(
            "✓ Übernommen" if locale == "de-DE" else "✓ Applied", timeout=6000
        )
        pg.wait_for_timeout(5500)  # past the window: no late warning either
        expect(pg.locator(".toast.warn")).to_have_count(0)


@pytest.mark.parametrize("unconfirmed", [True, False])
def test_an_unconfirmed_write_is_watched_across_the_reconnect(stub, error_gated_page, unconfirmed):
    """``unconfirmed: true`` (#264): the link dropped after the write left, so the unit's state shows
    up only after the reconnect + read-all — the page watches 20 s, not 5 s. The state flips 8 s in:
    flagged -> ✓ Applied; unflagged -> the 5 s window already gave up.

    .. test:: The satellite UI waits out the reconnect for an unconfirmed write
       :id: T_SAT_UI_UNCONFIRMED_WRITE
       :links: R_FW_SHARED_UI
    """
    stub.command_reply = {"ok": True, "applied": None, "state": None, "error": None, "function": "cooler"}
    if unconfirmed:
        stub.command_reply["unconfirmed"] = True

    def unit_applies_after_reconnect(body):
        def flip():
            stub.fixtures["satellite"]["/api/state"]["fn"]["cooler"]["State"] = (
                1 if body["value"] == "on" else 0
            )

        threading.Timer(8.0, flip).start()

    stub.on_command = unit_applies_after_reconnect
    with error_gated_page(stub.base) as pg:
        pg.wait_for_function(LIVE)
        pg.on("dialog", lambda d: d.accept())
        _open(pg, "Cooler")
        pg.get_by_role("switch", name="Refrigerator box").click()
        _wait_commands(pg, stub)
        if unconfirmed:
            expect(pg.locator(".toast")).to_have_text("✓ Applied", timeout=14000)
            expect(pg.locator(".toast.warn")).to_have_count(0)
        else:
            expect(pg.locator(".toast")).to_have_text(NOT_CONFIRMED, timeout=7000)


def test_roof_is_greyed_with_the_reason(live, stub):
    _open(live, "Roof")
    for name in ("open", "close", "stop"):
        b = live.locator(".btnrow button", has_text=name)
        expect(b).to_be_disabled()
        expect(b).to_have_attribute("title", ELSEWHERE)
    expect(live.get_by_text(ELSEWHERE, exact=True).first).to_be_visible()
    _home(live)
    _open(live, "Lighting")
    _lighting_settings(live)
    # the rest of the lighting screen is live; the roof's reason is not repeated on the wake-up card
    expect(live.get_by_role("switch", name="Sliding door lighting")).to_be_enabled()
    expect(live.get_by_text(ELSEWHERE, exact=True)).to_have_count(0)
    assert not stub.commands


def test_german_elsewhere_hint(stub, error_gated_page):
    with error_gated_page(stub.base, locale="de-DE") as pg:
        pg.wait_for_function(LIVE)
        _open(pg, "Aufstelldach")
        expect(pg.get_by_text("Nur über buspi oder die App", exact=True).first).to_be_visible()
        for name in ("öffnen", "schließen", "stopp"):
            expect(pg.locator(".btnrow button", has_text=name)).to_have_attribute(
                "title", "Nur über buspi oder die App"
            )


# the satellite's raw lighting object carries the firmware session's config latch (snapshot.c)
LATCH = {
    "WakeupTimestamp": 6 * 3600,
    "WakeupLightValue": 0x1101,
    "DoorContact": 1,
    "FavouritesStored": 0b10001,
}


@pytest.mark.parametrize(
    "locale,tile,head", [("en-US", "Lighting", "Wake-up light"), ("de-DE", "Beleuchtung", "Wecklicht")]
)
def test_wakeup_card_is_live_and_sends_the_browsers_wall_clock(stub, error_gated_page, locale, tile, head):
    """Review focus 1: in Auckland the posted local_now is Auckland's wall clock read as UTC; the card
    shows the unit's latched config (time, switch, vertical areas), the door row and the favourite marks.

    .. test:: The satellite's wake-up card is live and sends the page's clock
       :id: T_SAT_UI_WAKEUP
       :links: R_FW_SHARED_UI, R_FW_WAKEUP
    """
    stub.fixtures["satellite"]["/api/state"]["fn"]["lighting"].update(LATCH)
    with error_gated_page(stub.base, locale=locale, timezone_id="Pacific/Auckland") as pg:
        pg.wait_for_function(LIVE)
        _open(pg, tile)
        _lighting_settings(pg)
        # the card heading (a profile <option> carries the same words)
        expect(pg.locator("div.note", has_text=re.compile("^%s$" % head)).first).to_be_visible()
        tm = pg.get_by_label("Wake-up time")
        expect(tm).to_be_enabled()
        expect(tm).to_have_value("06:00")
        expect(pg.get_by_role("switch", name="Wake-up light")).to_have_attribute("aria-checked", "true")
        expect(pg.locator(".arealist input[type=checkbox]")).to_have_count(4)
        expect(pg.get_by_role("switch", name="Sliding door lighting")).to_have_attribute(
            "aria-checked", "true"
        )
        assert pg.evaluate("STATE.lighting.favourites_stored") == [1, 5]
        expect(pg.get_by_text(ELSEWHERE, exact=True)).to_have_count(0)
        expect(pg.get_by_text("Nur über buspi oder die App", exact=True)).to_have_count(0)
        # the browser's own wall clock read as UTC, taken at the moment of the edit
        js_wall = pg.evaluate(
            "() => { const d = new Date(); return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(),"
            " d.getHours(), d.getMinutes(), d.getSeconds()) / 1000; }"
        )
        tm.fill("07:00")
        _wait_commands(pg, stub)
    body = stub.commands[0]
    want = calendar.timegm(
        datetime.datetime.now(ZoneInfo("Pacific/Auckland")).replace(tzinfo=None).timetuple()
    )
    assert {k: body[k] for k in ("function", "what", "value", "confirm")} == {
        "function": "lighting",
        "what": "wakeup",
        "value": "07:00 1 0 0",
        "confirm": True,
    }
    assert isinstance(body["local_now"], int) and not isinstance(body["local_now"], bool)
    assert abs(body["local_now"] - want) <= 5 and abs(body["local_now"] - js_wall) <= 5
    # Auckland is never UTC: the clock is the zone's wall clock, not the epoch
    assert abs(body["local_now"] - int(datetime.datetime.now(datetime.UTC).timestamp())) >= 11 * 3600


WAKEUP_UNKNOWN = "wake-up config not known yet (the unit has not reported it): give on|off with the edit"
WAKEUP_UNKNOWN_DE = (
    "Wecklicht-Einstellungen noch nicht bekannt (die Einheit hat sie nicht gemeldet): Ein/Aus mit angeben"
)
NOT_CONFIRMED = "Sent — the unit didn't confirm it"


def _refused(body):
    """web.c's refusal shape (``control_run``: the REQUEST_CONFIG pull got no wake-up frame)."""
    return {
        "ok": True,
        "applied": False,
        "refused": WAKEUP_UNKNOWN,
        "state": None,
        "error": None,
        "function": body.get("function", "lighting"),
    }


@pytest.mark.parametrize(
    "locale,tile,refusal",
    [("en-US", "Lighting", WAKEUP_UNKNOWN), ("de-DE", "Beleuchtung", WAKEUP_UNKNOWN_DE)],
)
def test_wakeup_time_is_editable_before_the_unit_reported(stub, error_gated_page, locale, tile, refusal):
    """No latch yet: only the time is live (empty, never an invented 00:00); the rest waits for the unit.
    A time edit posts the time alone (+ local_now) so the firmware pulls the unit's config (R5); the
    unit not answering -> the refusal reason is shown."""
    stub.command_reply = _refused({})
    with error_gated_page(stub.base, locale=locale) as pg:
        pg.wait_for_function(LIVE)
        _open(pg, tile)
        _lighting_settings(pg)
        tm = pg.get_by_label("Wake-up time")
        expect(tm).to_be_enabled()
        expect(tm).to_have_value("")
        expect(pg.get_by_role("switch", name="Wake-up light")).to_be_disabled()
        expect(pg.get_by_label("Wake-up brightness")).to_be_disabled()
        expect(pg.locator(".arealist input[type=checkbox]:enabled")).to_have_count(0)
        expect(pg.locator(".card select:disabled").first).to_be_visible()
        assert pg.locator(".note", has_text=re.compile("not known yet|noch nicht bekannt")).count() >= 1
        tm.fill("07:00")
        _wait_commands(pg, stub)
        expect(pg.locator(".toast")).to_have_text(refusal, timeout=6000)
    body = stub.commands[0]
    assert set(body) == {"function", "what", "value", "confirm", "local_now"}
    assert (body["function"], body["what"], body["value"]) == ("lighting", "wakeup", "07:00")
    assert isinstance(body["local_now"], int) and body["local_now"] > 1767225600


def test_wakeup_time_edit_lands_once_the_unit_answers_the_pull(stub, error_gated_page):
    """The unit answers the firmware's REQUEST_CONFIG with its Mode-20 config and the edit is written:
    the next poll shows the new time in the latch -> "Applied"."""

    def unit_answers_and_applies(body):
        if body["what"] == "wakeup":
            stub.fixtures["satellite"]["/api/state"]["fn"]["lighting"].update(LATCH, WakeupTimestamp=7 * 3600)

    stub.on_command = unit_answers_and_applies
    with error_gated_page(stub.base) as pg:
        pg.wait_for_function(LIVE)
        _open(pg, "Lighting")
        _lighting_settings(pg)
        pg.get_by_label("Wake-up time").fill("07:00")
        _wait_commands(pg, stub)
        expect(pg.locator(".toast")).to_have_text("✓ Applied", timeout=6000)
        expect(pg.get_by_role("switch", name="Wake-up light")).to_be_enabled()
    assert stub.commands[0]["value"] == "07:00"


def test_wakeup_switch_unconfirmed_when_the_unit_keeps_its_switch(stub, error_gated_page):
    """The switch's command carries the unchanged time/areas: only the enabled bit tells it applied.
    The unit still reports "on" -> the honest warning, never "Applied"."""
    stub.fixtures["satellite"]["/api/state"]["fn"]["lighting"].update(LATCH)
    with error_gated_page(stub.base) as pg:
        pg.wait_for_function(LIVE)
        _open(pg, "Lighting")
        _lighting_settings(pg)
        pg.get_by_role("switch", name="Wake-up light").click()
        _wait_commands(pg, stub)
        expect(pg.locator(".toast")).to_have_text(NOT_CONFIRMED, timeout=9000)
    assert stub.commands[0]["value"].endswith(" off")


def test_wakeup_is_confirmed_from_the_units_own_state(stub, error_gated_page):
    """The ESP answers applied:null; the unit's Mode-20 report (here the stub's latch update) confirms."""
    stub.fixtures["satellite"]["/api/state"]["fn"]["lighting"].update(LATCH)

    def unit_applies(body):
        if body["what"] == "wakeup":
            stub.fixtures["satellite"]["/api/state"]["fn"]["lighting"]["WakeupTimestamp"] = 7 * 3600

    stub.on_command = unit_applies
    with error_gated_page(stub.base) as pg:
        pg.wait_for_function(LIVE)
        _open(pg, "Lighting")
        _lighting_settings(pg)
        pg.get_by_label("Wake-up time").fill("07:00")
        _wait_commands(pg, stub)
        expect(pg.locator(".toast")).to_have_text("✓ Applied", timeout=6000)


def test_wakeup_unconfirmed_when_the_unit_reports_another_config(stub, error_gated_page):
    """The matcher reads the unit's state: a report that still shows 06:00 is never "Applied"."""
    stub.fixtures["satellite"]["/api/state"]["fn"]["lighting"].update(LATCH)
    with error_gated_page(stub.base) as pg:
        pg.wait_for_function(LIVE)
        _open(pg, "Lighting")
        _lighting_settings(pg)
        pg.get_by_label("Wake-up time").fill("07:00")
        _wait_commands(pg, stub)
        # a wake-up edit lights no lamp: the generic warning (review m6)
        expect(pg.locator(".toast")).to_have_text(NOT_CONFIRMED, timeout=9000)


def test_api_still_refuses_calictl_only_paths_on_the_live_satellite(stub, error_gated_page):
    stub.fixtures["satellite"]["/api/state"]["device"].pop("auto_camper", None)  # an older firmware
    with error_gated_page(stub.base) as live:
        live.wait_for_function(LIVE)
        _refuses_calictl_only_paths(live, stub)


def _refuses_calictl_only_paths(live, stub):
    for path in ("/api/session", "/api/auto_camper"):
        got = live.evaluate("p => api(p).then(() => 'fetched', e => e.message)", path)
        assert got == "satellite: no " + path
    assert set(stub.requests) <= ALLOWED_LIVE


@pytest.mark.parametrize(
    "status,reply,locale,text",
    [
        (
            403,
            {"ok": False, "error": "setup_mode"},
            "en-US",
            "Controls work only on your home WiFi — not over the setup hotspot",
        ),
        (
            403,
            {"ok": False, "error": "setup_mode"},
            "de-DE",
            "Steuerung nur im eigenen WLAN — nicht über den Einrichtungs-Hotspot",
        ),
        (
            409,
            {"ok": False, "error": "busy"},
            "en-US",
            "The satellite is still sending the previous command — try again in a moment",
        ),
        (
            409,
            {"ok": False, "error": "busy"},
            "de-DE",
            "Der Satellit sendet noch den vorherigen Befehl — gleich noch einmal versuchen",
        ),
        (
            503,
            {"ok": False, "error": "not_connected"},
            "en-US",
            "Not connected to the camper unit yet — try again in a few seconds",
        ),
        (
            503,
            {"ok": False, "error": "not_connected"},
            "de-DE",
            "Noch nicht mit der Camper-Einheit verbunden — in ein paar Sekunden noch einmal versuchen",
        ),
        (502, {"ok": False, "error": "write_failed"}, "en-US", "Command failed: write_failed"),
        (
            200,
            {
                "ok": True,
                "applied": False,
                "refused": "x needs y",
                "state": None,
                "error": None,
                "function": "cooler",
            },
            "en-US",
            "x needs y",
        ),
    ],
)
def test_firmware_answers_show_a_clear_message(stub, status, reply, locale, text):
    """The ESP's error codes the owner can act on (403 setup_mode, 409 busy, 503 not_connected) get a
    sentence, not a bare code; the others keep calictl's "Command failed: <code>"; a refusal shows
    the unit's reason."""
    stub.command_status, stub.command_reply = status, reply
    # A 4xx/5xx answer is a console.error ("Failed to load resource") by design, so the error
    # status cases gate on uncaught page errors only (like the 503-first-poll test above).
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_context(locale=locale).new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(stub.base)
        pg.wait_for_function(LIVE)
        _open(pg, "Kühlbox" if locale == "de-DE" else "Cooler")
        pg.get_by_role("switch", name="Refrigerator box").click()
        _wait_commands(pg, stub)
        expect(pg.locator(".toast")).to_have_text(text)
        browser.close()
    assert not errors, errors


def _engine_or_skip(engine):
    try:
        with sync_api.sync_playwright() as p:
            getattr(p, engine).launch().close()
    except Exception as e:  # noqa: BLE001 — a missing browser is a skip (CI installs chromium only)
        pytest.skip("Playwright %s not installed: %s" % (engine, e))


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_pending_command_does_not_trip_the_offline_banner(stub, error_gated_page, engine):
    """The ESP's single-connection core answers POST /api/command only after the unit's ACK (<= 4 s)
    and serves no /api/state meanwhile (Task 4 review minor 6): the page must show "Sending…", never
    flash offline, and must send at most ONE poll onto the stalled core. WebKit matters here: it has
    no same-URL cache lock (Chromium/Firefox do), so without the UI's own guard Safari stacks a
    connection per 2 s tick against the ESP's backlog of 2."""
    _engine_or_skip(engine)
    stub.command_delay_s = 4.0  # CALI_CTL_DEADLINE_MS: two 2 s poll ticks fall into the stall
    with error_gated_page(stub.base, engine=engine) as pg:
        pg.wait_for_function(LIVE)
        pg.on("dialog", lambda d: d.accept())
        _open(pg, "Cooler")
        pg.get_by_role("switch", name="Refrigerator box").click()
        seen = set()
        for _ in range(10):  # ~2.5-3 s into the 4 s stall (margin for a slow runner)
            seen.add(pg.locator("#status").inner_text())
            assert pg.locator(".offline").count() == 0  # neither asleep nor unreachable
            pg.wait_for_timeout(250)
        assert "Sending…" in seen and "offline" not in seen and "unreachable" not in seen, seen
        expect(pg.locator(".toast")).to_have_text("Sent — the unit didn't confirm it", timeout=9000)
        expect(pg.locator("#status")).to_have_text("live")
    # the UI sends at most one poll at a time: exactly one was waiting on the stalled core
    assert stub.polls_while_pending == 1, (stub.polls_while_pending, stub.requests)


# -- the pairing wizard on the satellite (R_FW_PAIRING_WIZARD) ---------------------------------------

BUSPI_ONLY = (
    "buspi",
    "this Pi",
    "diesem Pi",
    "CALICTL_ADDR",
    "/etc/buspi",
    "Home Assistant",
    "daemon",
    "Dienst",
)
WIZARD_TEXT = {
    "en-US": {
        "go": "Pair…",
        "title": "Pair the camper unit",
        "ready": "I'm on that screen",
        "connect": "Pair now",
        "send": "Send",
        "step": "until the satellite connects",
        "saved": "Saved on the satellite — survives a restart. No further action needed.",
        "paired": "✓ Paired — C0:FF:EE:CA:11:F0",
    },
    "de-DE": {
        "go": "Koppeln…",
        "title": "Camper-Einheit koppeln",
        "ready": "Ich bin auf diesem Bildschirm",
        "connect": "Jetzt koppeln",
        "send": "Senden",
        "step": "bis der Satellit sich verbindet",
        "saved": "Auf dem Satelliten gespeichert — übersteht einen Neustart. Keine weitere Aktion nötig.",
        "paired": "✓ Gekoppelt — C0:FF:EE:CA:11:F0",
    },
}


def _unpaired(stub, mode="satellite"):
    stub.fixtures[mode]["/api/state"]["device"]["pairing"].update(state="idle", address=None)
    stub.fixtures[mode]["/api/pairing"].update(state="idle", address=None)


def _card_text(pg):
    return (
        pg.locator(".card", has_text=WIZARD_TEXT["en-US"]["title"])
        .or_(pg.locator(".card", has_text=WIZARD_TEXT["de-DE"]["title"]))
        .first.inner_text()
    )


def _pair_in_wizard(pg, stub, words):
    expect(pg.locator("#unpaired-banner")).to_be_visible()
    pg.locator("#unpaired-banner").get_by_role("button", name=words["go"]).click()
    expect(pg.get_by_text(words["step"], exact=False)).to_be_visible()
    shown = _card_text(pg)
    assert not [w for w in BUSPI_ONLY if w in shown], shown
    pg.get_by_label(words["ready"]).check()
    pg.get_by_role("button", name=words["connect"]).click()
    pg.locator("#pairing-passkey").fill("123456")
    pg.get_by_role("button", name=words["send"]).click()
    expect(pg.get_by_text(words["paired"])).to_be_visible()
    expect(pg.get_by_text(words["saved"])).to_be_visible()
    shown = _card_text(pg)
    assert not [w for w in BUSPI_ONLY if w in shown], shown


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
def test_wizard_pairs_the_satellite(stub, error_gated_page, locale):
    """The shared wizard, unchanged, on the satellite: the unpaired banner opens it, start ->
    passkey -> bonded over calictl's /api/pairing; only the device-specific hints differ (no Pi,
    daemon or CALICTL_ADDR advice), in the browser's language.

    .. test:: The shared pairing wizard runs on the satellite, EN + DE
       :id: T_SAT_UI_PAIRING_WIZARD
       :links: R_FW_PAIRING_WIZARD
    """
    _unpaired(stub)
    with error_gated_page(stub.base, locale=locale) as pg:
        _pair_in_wizard(pg, stub, WIZARD_TEXT[locale])
    assert stub.pairing_posts == [{"action": "start"}, {"action": "passkey", "value": "123456"}]
    assert set(stub.requests) <= ALLOWED_LIVE, stub.requests


def test_wizard_over_the_setup_hotspot_via_app(stub, error_gated_page):
    """No home WiFi at the van: on the setup hotspot / is the setup page, /app the UI — display only
    (no control over the hotspot) but with the pairing wizard.

    .. test:: The wizard is reachable and pairs over the setup hotspot
       :id: T_SAT_UI_PAIRING_HOTSPOT
       :links: R_FW_PAIRING_WIZARD
    """
    stub.mode = "setup"
    _unpaired(stub, "setup")
    with error_gated_page(stub.base + "/app") as pg:
        expect(pg.get_by_text("Satellite — display only")).to_be_visible()
        _pair_in_wizard(pg, stub, WIZARD_TEXT["en-US"])
    assert [b["action"] for b in stub.pairing_posts] == ["start", "passkey"]


@pytest.mark.parametrize(
    "error,locale,hint",
    [
        ("timeout", "en-US", "that the satellite is in range"),
        ("timeout", "de-DE", "dass der Satellit in Reichweite ist"),
        ("connect_failed", "en-US", "a phone or a Raspberry Pi with calictl may still hold"),
        ("connect_failed", "de-DE", "ein Handy oder ein Raspberry Pi mit calictl belegt"),
    ],
)
def test_wizard_error_hints_are_the_satellites(stub, error_gated_page, error, locale, hint):
    """A failed flow shows the satellite's hint (never the Pi's scanner advice), translated."""
    stub.fixtures["satellite"]["/api/pairing"].update(state="error", error=error, attempts=3)
    with error_gated_page(stub.base, locale=locale) as pg:
        pg.wait_for_function("() => !!(STATE._meta && STATE._meta.satellite)")
        pg.evaluate("location.hash = '#pair'")  # /device's Camper-unit link (no menu entry since PR B)
        expect(pg.get_by_text(hint, exact=False)).to_be_visible()
        shown = _card_text(pg)
        assert not [w for w in BUSPI_ONLY if w in shown], shown


PHONE = {"width": 390, "height": 844}
UNPAIR_CONFIRM = "Unpair the camper unit? Data stops until you pair again."


def test_pair_link_shows_the_wizard_at_the_top(stub, error_gated_page):
    """On a paired, online van at phone size, the #pair link (/device) must show the wizard where the user looks:
    above the summary and the tiles, scrolled into view (UX review H2: it used to land below the
    eight tiles, out of sight)."""
    with error_gated_page(stub.base, viewport=PHONE) as pg:
        pg.wait_for_function(LIVE)
        pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")  # the user scrolled down
        pg.evaluate("location.hash = '#pair'")
        card = pg.locator("#pairing-card")
        expect(card.get_by_text("Pair the camper unit")).to_be_in_viewport()
        expect(card.get_by_role("button", name="Unpair…")).to_be_in_viewport()  # the bonded step
        above = card.evaluate(
            "c => c.getBoundingClientRect().bottom"
            " <= document.querySelector('.tilegrid').getBoundingClientRect().top"
        )
        assert above, "the wizard card renders below the tiles"


def test_unpair_has_one_word_and_one_confirm(stub, error_gated_page):
    """Device status's Unpair… and the wizard's own button are one action: the same label and the
    same confirm text (UX review H3); accepting it removes the pairing."""
    asked = []

    def answer(d):
        asked.append(d.message)
        d.dismiss() if len(asked) == 1 else d.accept()

    with error_gated_page(stub.base, viewport=PHONE) as pg:
        pg.wait_for_function(LIVE)
        pg.on("dialog", answer)
        pg.click("#menu")
        pg.locator(".menupop").get_by_text("Device status").click()
        pg.get_by_role("button", name="Unpair…").click()  # dismissed: nothing happens
        pg.evaluate("location.hash = '#pair'")
        pg.locator("#pairing-card").get_by_role("button", name="Unpair…").click()
        expect(pg.get_by_role("button", name="Pair now")).to_be_visible()
    assert asked == [UNPAIR_CONFIRM, UNPAIR_CONFIRM], asked
    assert stub.pairing_posts == [{"action": "reset", "confirm": True}], stub.pairing_posts


@pytest.mark.parametrize(
    "locale,text",
    [
        ("en-US", "The satellite is still sending the previous command — try again in a moment"),
        ("de-DE", "Der Satellit sendet noch den vorherigen Befehl — gleich noch einmal versuchen"),
    ],
)
def test_wizard_start_while_a_command_runs_says_busy(stub, locale, text):
    """409 busy (a control command holds the single link) is the firmware's sentence, not a code.
    Not error-gated: the 409 is a console error by design."""
    _unpaired(stub)
    stub.pairing_status, stub.pairing_error = 409, "busy"
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_context(locale=locale).new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(stub.base)
        words = WIZARD_TEXT[locale]
        pg.locator("#unpaired-banner").get_by_role("button", name=words["go"]).click()
        pg.get_by_label(words["ready"]).check()
        pg.get_by_role("button", name=words["connect"]).click()
        expect(pg.locator(".toast")).to_have_text(text)
        browser.close()
    assert not errors, errors


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_wizard_poll_sends_one_request_at_a_time(stub, error_gated_page, engine):
    """The wizard polls /api/pairing every 1 s; against a stalled single-connection core (3.5 s per
    answer) it must keep at most ONE poll in flight — WebKit has no same-URL lock and would stack a
    connection per tick against the ESP's backlog of 2 (review M1, like refreshState's guard)."""
    _engine_or_skip(engine)
    _unpaired(stub)
    with error_gated_page(stub.base, engine=engine) as pg:
        words = WIZARD_TEXT["en-US"]
        pg.locator("#unpaired-banner").get_by_role("button", name=words["go"]).click()
        pg.get_by_label(words["ready"]).check()
        pg.get_by_role("button", name=words["connect"]).click()
        expect(pg.locator("#pairing-passkey")).to_be_visible()
        stub.pairing_get_delay_s = 3.5
        pg.wait_for_timeout(6000)  # ~6 poll ticks into the stall
        stub.pairing_get_delay_s = 0.0
    assert stub.pairing_gets_max == 1, stub.pairing_gets_max


def _hold_water(stub, age_s):
    """Serve the water as the HELD reading in both modes; ``age_s`` None = age unknown."""
    import time

    dev = stub.fixtures["satellite"]["/api/state"]["device"]
    dev["water_held"] = True
    water = stub.fixtures["calictl"]["/api/state"]["water"]
    water["fresh"]["stale"] = water["waste"]["stale"] = True
    water["stale_since"] = time.time() - 60
    if age_s is not None:
        dev["water_held_age_ms"] = age_s * 1000
        water["measured_at"] = time.time() - age_s


@pytest.mark.parametrize("mode", ["satellite", "calictl"])
@pytest.mark.parametrize(
    "locale,age_s,text",
    [
        ("en-US", 2 * 3600, "🕒 measured 2 h ago"),
        ("de-DE", 2 * 3600, "🕒 vor 2 Std gemessen"),
        ("en-US", 30, "🕒 measured just now"),
        ("en-US", None, "🕒 last measured"),
        ("de-DE", None, "🕒 zuletzt gemessen"),
    ],
)
def test_held_water_shows_its_age(stub, error_gated_page, mode, locale, age_s, text):
    """The held water reading shows how old it is (like the app's "Updated: x ago"): calictl's
    ``water.measured_at`` and the ESP's ``device.water_held_age_ms`` render the same text; with no
    known age the plain marker stays."""
    _display_only(stub)
    _hold_water(stub, age_s)
    stub.mode = mode
    with error_gated_page(stub.base, locale=locale) as pg:
        _open(pg, "Wasser" if locale == "de-DE" else "Water")
        expect(pg.locator("#app").get_by_text(text)).to_have_count(2)  # fresh + waste readouts


# ---- usage beacons (R_UX_TELEMETRY) -------------------------------------------------------------

PAGEHIDE = "dispatchEvent(new PageTransitionEvent('pagehide'))"  # the flush a closing tab triggers


@contextlib.contextmanager
def _page(url):
    """A page that RECORDS (not fails on) page errors + console errors: these tests throw on purpose
    or let a beacon 404. Yields ``(page, errors)``."""
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
        pg.on("console", lambda m: errors.append("console: %s" % m.text) if m.type == "error" else None)
        pg.goto(url)
        try:
            yield pg, errors
        finally:
            browser.close()


def _beacons(pg, stub, n=1):
    for _ in range(50):
        if len(stub.ux_beacons) >= n:
            return stub.ux_beacons
        pg.wait_for_timeout(100)
    raise AssertionError("no beacon reached the stub")


def test_beacons_carry_views_controls_values_errors(stub):
    """View, control (ids and the value the control was set to: the cooler toggle's "on"), timing and a
    script error, batched into one sendBeacon on pagehide, with the per-tab session fields.

    .. test:: The web UI's usage beacons carry ids, control values, timings and errors
       :id: T_UX_BEACONS
       :links: R_UX_TELEMETRY
    """
    with _page(stub.base) as (pg, errors):
        pg.wait_for_function(LIVE)
        pg.on("dialog", lambda d: d.accept())
        _open(pg, "Cooler")
        pg.get_by_role("switch", name="Refrigerator box").click()
        _wait_commands(pg, stub)
        pg.evaluate("setTimeout(() => { throw new Error('boom ' + 'x'.repeat(300)) })")
        pg.wait_for_timeout(200)
        pg.evaluate(PAGEHIDE)
        (b,) = _beacons(pg, stub)
        pg.evaluate(PAGEHIDE)  # nothing new: no empty beacon
        pg.wait_for_timeout(300)
    assert len(stub.ux_beacons) == 1
    assert [e for e in errors if "boom" not in e] == []  # only the error thrown on purpose
    assert set(b) == {"sid", "ua_class", "standalone", "origin", "events"}
    assert re.fullmatch(r"[0-9a-f]{16}", b["sid"]) and b["standalone"] is False
    assert b["origin"] == "lan" and b["ua_class"] in ("mac", "other")  # 127.0.0.1, headless Chromium
    evs = b["events"]
    assert evs[:3] == [{"type": "view", "screen": "home"}, evs[1], {"type": "view", "screen": "cooler"}]
    assert evs[1]["type"] == "timing" and set(evs[1]) == {"type", "ttfb", "dcl", "tiles"}
    assert {"type": "control", "fn": "cooler", "control": "power", "value": "on"} in evs
    err = [e for e in evs if e["type"] == "error"]
    assert (
        len(err) == 1 and err[0]["msg"].startswith("Uncaught Error: boom xxx") and len(err[0]["msg"]) == 200
    )
    assert set(stub.requests) <= ALLOWED_LIVE, stub.requests


def test_a_control_without_a_value_omits_the_key_and_a_number_is_a_string(live):
    """``command()`` -> the control event: ``value`` is ``String(value)`` (printable ASCII, <= 32),
    absent for null/undefined.

    .. test:: The UI's control events carry the value as a short string, omitted when there is none
       :id: T_UX_VALUE_UI
       :links: R_UX_TELEMETRY
    """
    evs = live.evaluate(
        """() => { uxQueue = [];
          command("cooler", "level", 3); command("cooler", "power", null);
          command("cooler", "x", "a\u00e9" + "b".repeat(40));
          return uxQueue; }"""
    )
    assert evs[0] == {"type": "control", "fn": "cooler", "control": "level", "value": "3"}
    assert evs[1] == {"type": "control", "fn": "cooler", "control": "power"}
    assert evs[2]["value"] == "a?" + "b" * 30


def test_secrets_never_reach_a_beacon(stub, error_gated_page):
    """The pairing passkey (/api/pairing) goes through no ``command()``: run the wizard with the
    usage switch on and flush; the passkey string is in no beacon.

    .. test:: A pairing passkey never appears in a usage beacon
       :id: T_UX_NO_SECRETS
       :links: R_UX_TELEMETRY
    """
    _unpaired(stub)
    with error_gated_page(stub.base) as pg:
        pg.wait_for_function("() => !!(STATE._meta && STATE._meta.ux)")
        _pair_in_wizard(pg, stub, WIZARD_TEXT["en-US"])
        pg.evaluate(PAGEHIDE)
        _beacons(pg, stub)
    assert stub.pairing_posts[-1] == {"action": "passkey", "value": "123456"}
    assert "123456" not in json.dumps(stub.ux_beacons)


@pytest.mark.parametrize(
    "host,origin",
    [
        ("busnet.example.ts.net", "busnet"),  # the van router's tailnet HTTPS proxy
        ("buspi", "buspi"),
        ("buspi.example.ts.net", "buspi"),
        ("192.168.8.183", "lan"),  # the van LAN is a LAN
        ("calictl-esp.local", "lan"),
        ("127.0.0.1", "lan"),
    ],
)
def test_beacon_origin_from_the_host_name(sat, host, origin):
    assert sat.evaluate("uxOrigin(%r)" % host) == origin


def test_no_beacons_while_switched_off(stub, error_gated_page):
    stub.fixtures["satellite"]["/api/state"]["device"]["ux"] = {"enabled": False}
    with error_gated_page(stub.base) as pg:
        pg.wait_for_function("() => !!(STATE._meta && STATE._meta.satellite)")
        _open(pg, "Cooler")
        pg.evaluate(PAGEHIDE)
        pg.wait_for_timeout(500)
    assert stub.ux_beacons == [] and "/api/ux" not in stub.requests


def test_a_firmware_without_the_endpoint_never_breaks_the_page(stub, error_gated_page):
    """An older firmware: no device.ux (-> _meta.ux false, nothing sent); and a beacon that 404s
    leaves the page working, without a page error."""
    stub.fixtures["satellite"]["/api/state"]["device"].pop("ux")
    with error_gated_page(stub.base) as pg:
        pg.wait_for_selector(".tilegrid .tile")
        pg.evaluate(PAGEHIDE)
        pg.wait_for_timeout(300)
    assert stub.ux_beacons == []
    stub.fixtures["satellite"]["/api/state"]["device"]["ux"] = {"enabled": True}
    stub.ux_status = 404
    with _page(stub.base) as (pg, errors):
        pg.wait_for_selector(".tilegrid .tile")
        pg.evaluate(PAGEHIDE)
        _beacons(pg, stub)
        _open(pg, "Cooler")  # still navigates + renders after the 404
        n = len(stub.requests)
        pg.wait_for_timeout(2500)
        assert stub.requests.count("/api/state") and len(stub.requests) > n  # still polling
    assert not [e for e in errors if e.startswith("pageerror")], errors


@pytest.mark.parametrize(
    "locale,tile,label",
    [
        ("en-US", "Camping mode", "Restore camping after you park"),
        ("de-DE", "Campingmodus", "Campingmodus nach dem Parken wiederherstellen"),
    ],
)
def test_auto_camper_toggle_on_a_firmware_that_reports_it(stub, error_gated_page, locale, tile, label):
    """A firmware reporting ``device.auto_camper`` gets calictl's Camping-screen toggle (EN + DE): it
    shows the restore owed, POSTs ``{"enabled": <the opposite>}`` to the satellite's
    ``/api/auto_camper`` and is disabled while the satellite is read-only (the setup hotspot).

    .. test:: The satellite's Camping screen switches auto camper mode
       :id: T_SAT_UI_AUTO_CAMPER
       :links: R_FW_SHARED_UI, R_FW_AUTO_CAMPER
    """
    dev = stub.fixtures["satellite"]["/api/state"]["device"]
    dev["auto_camper"] = {"enabled": True, "state": "armed"}
    posts = []
    with error_gated_page(stub.base, locale=locale) as pg:

        def on_post(route):
            posts.append(json.loads(route.request.post_data))
            dev["auto_camper"]["enabled"] = posts[-1]["enabled"]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "auto_camper": posts[-1]["enabled"]}),
            )

        pg.route("**/api/auto_camper", on_post)
        pg.wait_for_function(LIVE)
        _open(pg, tile)
        expect(pg.get_by_text(label)).to_be_visible()
        expect(pg.get_by_text("⟳", exact=False)).to_be_visible()  # armed: a restore is owed
        sw = pg.get_by_role("switch", name="Auto camper mode")
        expect(sw).to_have_attribute("aria-checked", "true")
        sw.click()
        expect(sw).to_have_attribute("aria-checked", "false")
        assert posts == [{"enabled": False}]
        dev["control"]["writes"] = False  # the setup hotspot: read-only
        expect(pg.get_by_role("switch", name="Auto camper mode")).to_be_disabled()


# ---- external sensors (R_FW_SENSORS, #371): placed by their type ------------------------------

# The fixtures' aliases are the firmware's defaults (the type id), one entry per type: no alias
# prefix (it would read doubled, "Victron_battery Battery sensor"); a renamed entry or two of a type
# lead every row with "<Alias> " (test_two_solar_chargers_carry_their_alias, test_a_renamed_sensor_…).
EXT_LABELS = {
    "victron_mppt": ("Solar power", "Charge phase", "Solar yield today"),
    "victron_battery": ("Battery sensor",),
    "govee": ("Temperature", "Humidity"),
}


def _ext(stub, variant):
    """Swap one of ``ux_gallery.EXT_VARIANTS`` into the satellite's /api/state ext; returns it."""
    st = stub.fixtures["satellite"]["/api/state"]
    st["ext"] = json.loads(json.dumps(ux_gallery.EXT_VARIANTS[variant]))
    return st["ext"]


def _readouts(pg):
    """[(label, value)] of the open screen's readout rows, in order."""
    pg.wait_for_selector(".row.ro")
    return [
        tuple(x)
        for x in pg.locator(".row.ro").evaluate_all(
            "rs => rs.map(r => [r.querySelector('.lbl').textContent, r.querySelector('.val').textContent])"
        )
    ]


def _around(rows, label, before=0, after=0):
    i = [lbl for lbl, _ in rows].index(label)
    return rows[i - before : i] if before else rows[i + 1 : i + 1 + after]


def _sensor_rows(pg):
    pg.wait_for_selector(".tilegrid .tile")
    return pg.locator(".card.sensors .sensor")


ENERGY = {
    "en-US": ("Energy", "Second battery power", "Issues", [("Battery sensor", "13.32 V · 17.3 °C")],
              [("Solar power", "0 W · 0 A · 13.2 V"), ("Charge phase", "off"), ("Solar yield today", "0.19 kWh")]),
    "de-DE": ("Energie", "Zweitbatterie-Leistung", "Probleme", [("Batteriesensor", "13.32 V · 17.3 °C")],
              [("Solarstrom", "0 W · 0 A · 13.2 V"), ("Ladephase", "aus"), ("Solarertrag heute", "0.19 kWh")]),
}  # fmt: skip
VEHICLE = {
    "en-US": ("Vehicle", "Vehicle clock", [("Temperature", "16.5 °C"), ("Humidity", "59 %")]),
    "de-DE": ("Fahrzeug", "Fahrzeuguhr", [("Temperatur", "16.5 °C"), ("Luftfeuchte", "59 %")]),
}


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
def test_victron_readings_on_the_energy_screen(stub, error_gated_page, locale):
    """The ESP's Victron readings join the Energy screen (W · A · V, the charge phase on its own row):
    the solar charger's rows before Issues (after the unit's own Solar power row), the battery sensor right
    after the second battery's current; no Sensors card for known types.

    .. test:: The shared UI places Victron readings on the Energy screen by their type
       :id: T_SAT_UI_EXT_ENERGY
       :links: R_FW_SHARED_UI, R_FW_SENSORS
    """
    _ext(stub, "all")
    screen, after, before, battery, solar = ENERGY[locale]
    with error_gated_page(stub.base, locale=locale) as pg:
        expect(_sensor_rows(pg)).to_have_count(0)
        expect(pg.locator(".card.sensors")).to_have_count(0)
        _open(pg, screen)
        rows = _readouts(pg)
        assert _around(rows, after, after=1) == battery
        assert _around(rows, before, before=len(solar)) == solar
        assert dict(rows)[before] == ("none" if locale == "en-US" else "keine")


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
def test_govee_readings_on_the_vehicle_screen_and_tile(stub, error_gated_page, locale):
    """The Govee's temperature + humidity follow the vehicle clock; its own battery is no row; the
    Vehicle tile shows the climate.

    .. test:: The shared UI places the Govee on the Vehicle screen and tile
       :id: T_SAT_UI_EXT_VEHICLE
       :links: R_FW_SHARED_UI, R_FW_SENSORS
    """
    _ext(stub, "all")
    screen, after, govee = VEHICLE[locale]
    with error_gated_page(stub.base, locale=locale) as pg:
        pg.wait_for_selector(".tilegrid .tile")
        expect(pg.locator(".tile", has_text=screen).first).to_contain_text("16.5 °C · 59 %")
        _open(pg, screen)
        assert _around(_readouts(pg), after, after=2) == govee
        expect(pg.get_by_text("49 %")).to_have_count(0)  # the sensor's battery is fine: no row, no hint


@pytest.mark.parametrize(
    "variant,types,card",
    [
        ("none", (), 0),
        ("mppt", ("victron_mppt",), 0),
        ("mppt+battery", ("victron_mppt", "victron_battery"), 0),
        ("govee", ("govee",), 0),
        ("all", ("victron_mppt", "victron_battery", "govee"), 0),
        ("unknown", (), 1),
    ],
)
def test_each_sensor_combination_renders_where_its_type_belongs(stub, error_gated_page, variant, types, card):
    _ext(stub, variant)
    want = {lbl for ty in types for lbl in EXT_LABELS[ty]}
    every = {lbl for v in EXT_LABELS.values() for lbl in v}
    with error_gated_page(stub.base) as pg:
        expect(_sensor_rows(pg)).to_have_count(card)
        govee = "govee" in types
        expect(pg.locator(".tile", has_text="Vehicle").first).to_contain_text(
            "16.5 °C · 59 %" if govee else "Ignition"
        )
        got = set()
        for screen in ("Energy", "Vehicle"):
            _open(pg, screen)
            got |= {lbl for lbl, _ in _readouts(pg)} & every
            _home(pg)
        assert got == want


def test_two_solar_chargers_carry_their_alias(stub, error_gated_page):
    """Two entries of one type: each row's label names its alias, the entries in alias order."""
    _ext(stub, "two-mppt")
    with error_gated_page(stub.base) as pg:
        pg.wait_for_selector(".tilegrid .tile")
        _open(pg, "Energy")
        assert _around(_readouts(pg), "Issues", before=6) == [
            ("Dach Solar power", "0 W · 0 A · 13.2 V"),
            ("Dach Charge phase", "off"),
            ("Dach Solar yield today", "0.19 kWh"),
            ("Heck Solar power", "87 W · 2.3 A · 13.5 V"),
            ("Heck Charge phase", "bulk"),
            ("Heck Solar yield today", "0.42 kWh"),
        ]


def test_a_renamed_sensor_leads_its_rows_with_its_alias(stub, error_gated_page):
    """An alias the owner gave (not the type's default) leads every row: "Dach Solar power"."""
    stub.fixtures["satellite"]["/api/state"]["ext"] = {"dach": dict(ux_gallery.EXT_SENSORS["victron_mppt"])}
    with error_gated_page(stub.base) as pg:
        pg.wait_for_selector(".tilegrid .tile")
        _open(pg, "Energy")
        assert [lbl for lbl, _ in _around(_readouts(pg), "Issues", before=3)] == [
            "Dach Solar power",
            "Dach Charge phase",
            "Dach Solar yield today",
        ]


def test_a_default_alias_keeps_its_prefix_next_to_the_units_own_solar_row(stub, error_gated_page):
    """With the unit's own solar input fitted, its "Solar power" row shows: the solar charger with the
    default alias keeps "Victron mppt " so the two rows never read the same."""
    st = stub.fixtures["satellite"]["/api/state"]
    st["fn"]["energy"]["PvInstalled"] = 1
    st["ext"] = {"victron_mppt": dict(ux_gallery.EXT_SENSORS["victron_mppt"])}
    with error_gated_page(stub.base) as pg:
        pg.wait_for_selector(".tilegrid .tile")
        _open(pg, "Energy")
        labels = [lbl for lbl, _ in _readouts(pg)]
        assert labels.count("Solar power") == 1  # the unit's own
        assert "Victron mppt Solar power" in labels and "Victron mppt Charge phase" in labels


def test_old_missing_low_and_faulty_readings(stub, error_gated_page):
    """An old reading (> 10 min) keeps its value and carries "🕒 <ago>" (never colour alone); a value
    not read yet is "—"; a Govee battery below 20 % joins the temperature; a solar charger error
    joins the Issues row."""
    ext = _ext(stub, "all")
    ext["govee"].update(age_s=720, humidity=None, battery=15)
    ext["victron_mppt"].update(charge_state=4, error_code=2, pv_power=None)
    with error_gated_page(stub.base) as pg:
        pg.wait_for_selector(".tilegrid .tile")
        _open(pg, "Vehicle")
        assert _around(_readouts(pg), "Vehicle clock", after=2) == [
            ("Temperature", "16.5 °C · 🔋 15 % 🕒 12 min ago"),
            ("Humidity", "— 🕒 12 min ago"),
        ]
        _home(pg)
        expect(pg.locator(".tile", has_text="Vehicle").first).to_contain_text("16.5 °C · — 🕒")
        _open(pg, "Energy")
        rows = _readouts(pg)
        assert _around(rows, "Issues", before=3)[:2] == [
            ("Solar power", "— W · 0 A · 13.2 V"),
            ("Charge phase", "absorption"),
        ]
        assert dict(rows)["Issues"] == "Solar charger error 2 (Victron mppt)"


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
def test_sensors_card_shows_only_unknown_types(stub, error_gated_page, locale):
    """A type this UI does not place (a future firmware's) keeps the Sensors card after the summary:
    its alias, its raw fields and the age. Known types never appear there.

    .. test:: The shared UI shows unknown external sensor types on a Sensors card
       :id: T_SAT_UI_SENSORS_CARD
       :links: R_FW_SHARED_UI, R_FW_SENSORS
    """
    _ext(stub, "all").update(ux_gallery.EXT_VARIANTS["unknown"])
    with error_gated_page(stub.base, locale=locale) as pg:
        rows = _sensor_rows(pg)
        expect(rows).to_have_count(1)
        expect(rows.locator(".sname")).to_have_text("acme")
        expect(rows.locator(".sv")).to_have_text("co2 800")
        expect(rows.locator(".val")).to_have_text("just now" if locale == "en-US" else "gerade eben")
        expect(pg.locator(".card.summary + .card.sensors")).to_have_count(1)  # placed after the summary


def test_unknown_sensor_marks_an_old_reading(stub, error_gated_page):
    ext = _ext(stub, "unknown")
    ext["acme"].update(age_s=720, co2=None)
    with error_gated_page(stub.base) as pg:
        row = _sensor_rows(pg).first
        expect(row).to_have_class(re.compile(r"\bstale\b"))
        expect(row.locator(".val")).to_have_text("🕒 12 min ago")
        expect(row.locator(".sv")).to_have_text("co2 —")


@pytest.mark.parametrize("mode", ["satellite", "calictl"])
def test_no_ext_no_card_no_rows(stub, error_gated_page, mode):
    """No ``ext`` (the sensor track off, or calictl's /api/state) -> no Sensors card, no sensor rows,
    the Vehicle tile as before."""
    stub.fixtures["satellite"]["/api/state"].pop("ext")
    stub.mode = mode
    every = {lbl for v in EXT_LABELS.values() for lbl in v}
    with error_gated_page(stub.base) as pg:
        expect(_sensor_rows(pg)).to_have_count(0)
        expect(pg.locator(".card.sensors")).to_have_count(0)
        expect(pg.locator(".tile", has_text="Vehicle").first).to_contain_text("Ignition")
        for screen in ("Energy", "Vehicle"):
            _open(pg, screen)
            assert not {lbl for lbl, _ in _readouts(pg)} & every
            _home(pg)


def test_two_govees_each_lead_their_rows_with_their_alias(stub, error_gated_page):
    """Two Govees with different aliases -> one row pair each, sorted by alias, every label led by
    its own alias; the Vehicle tile summarises the first by alias."""
    g = dict(ux_gallery.EXT_SENSORS["govee"])
    stub.fixtures["satellite"]["/api/state"]["ext"] = {
        "innen": dict(g, temp_c=21.0, humidity=48.0),
        "aussen": dict(g, temp_c=9.5, humidity=81.0),
    }
    with error_gated_page(stub.base) as pg:
        pg.wait_for_selector(".tilegrid .tile")
        expect(pg.locator(".tile", has_text="Vehicle").first).to_contain_text("9.5 °C · 81 %")
        _open(pg, "Vehicle")
        assert _around(_readouts(pg), "Vehicle clock", after=4) == [
            ("Aussen Temperature", "9.5 °C"),
            ("Aussen Humidity", "81 %"),
            ("Innen Temperature", "21.0 °C"),
            ("Innen Humidity", "48 %"),
        ]
