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
    _display_only(stub)
    with error_gated_page(stub.base) as pg:
        expect(pg.get_by_text("Satellite — display only")).to_be_visible()
        satellite = _tile_texts(pg)
    stub.mode = "calictl"
    with error_gated_page(stub.base) as pg:
        expect(pg.get_by_text("🔒 Read-only — control is disabled on this daemon.")).to_be_visible()
        assert _tile_texts(pg) == satellite


def test_german_banner_and_menu(stub, error_gated_page):
    _display_only(stub)
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


def test_unpaired_satellite_shows_the_banner_without_a_wizard_button(stub, error_gated_page):
    # The satellite pairs over its console: the banner's "Set up remote control" (-> the wizard ->
    # /api/pairing every 1 s against the single-connection core) must not exist there.
    _display_only(stub)
    stub.fixtures["satellite"]["/api/state"]["device"]["pairing"].update(state="idle", address=None)
    with error_gated_page(stub.base) as pg:
        expect(pg.locator("#unpaired-banner")).to_be_visible()
        assert pg.locator("#unpaired-banner button").count() == 0
        assert pg.get_by_text("Set up remote control").count() == 0
        pg.wait_for_timeout(2500)  # at least one more state poll
    assert set(stub.requests) <= ALLOWED, stub.requests


def test_api_refuses_every_path_but_state_on_the_satellite(sat, stub):
    # Defence in depth for any future caller: api() throws before fetch() for a non-/api/state path.
    # A display-only satellite (no device.control) still refuses /api/command.
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


def test_roof_and_wakeup_are_greyed_with_the_reason(live, stub):
    _open(live, "Roof")
    for name in ("open", "close", "stop"):
        b = live.locator(".btnrow button", has_text=name)
        expect(b).to_be_disabled()
        expect(b).to_have_attribute("title", ELSEWHERE)
    expect(live.get_by_text(ELSEWHERE, exact=True).first).to_be_visible()
    _home(live)
    _open(live, "Lighting")
    # the satellite never knows the wake-up config: the reason up front, no (empty) inputs
    expect(live.get_by_role("switch", name="Wake-up light")).to_have_count(0)
    expect(live.get_by_label("Wake-up time")).to_have_count(0)
    expect(live.locator(".arealist")).to_have_count(0)
    expect(live.get_by_text(ELSEWHERE, exact=True).first).to_be_visible()
    # the rest of the lighting screen is live
    expect(live.get_by_role("switch", name="Sliding door lighting")).to_be_enabled()
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
        _home(pg)
        _open(pg, "Beleuchtung")  # the wake-up card: the reason, no empty area checkboxes
        expect(pg.locator(".arealist")).to_have_count(0)
        expect(pg.get_by_role("switch", name="Wake-up light")).to_have_count(0)
        expect(pg.get_by_text("Nur über buspi oder die App", exact=True).first).to_be_visible()


def test_api_still_refuses_calictl_only_paths_on_the_live_satellite(live, stub):
    for path in ("/api/pairing", "/api/history", "/api/session", "/api/auto_camper"):
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
            assert pg.locator(".offline").count() == 0
            pg.wait_for_timeout(250)
        assert "Sending…" in seen and "offline" not in seen, seen
        expect(pg.locator(".toast")).to_have_text("Sent — the unit didn't confirm it", timeout=9000)
        expect(pg.locator("#status")).to_have_text("live")
    # the UI sends at most one poll at a time: exactly one was waiting on the stalled core
    assert stub.polls_while_pending == 1, (stub.polls_while_pending, stub.requests)
