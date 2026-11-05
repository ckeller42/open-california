"""Browser e2e of the cabin thermostat card on the Air heater screen (R_FW_THERMOSTAT_API).

The card exists only on the ESP satellite (``GET /api/thermostat``) and only while a Govee
thermometer is configured. The AUTO switch is always there: a tap with AUTO off opens a confirmation
every time, which enables the thermostat and starts AUTO in one POST. Every test fails on an uncaught
JS error (``error_gated_page``). OPT-IN like test_satellite.py: skipped without Playwright + Chromium.
"""

import json

import pytest

from calictl import thermostat
from tools import ux_gallery

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

GOVEES = {
    "cabin": {"type": "govee", "temp_c": 19.6, "humidity": 54, "battery": 80, "age_s": 60},
    "bed": {"type": "govee", "temp_c": 18.2, "humidity": 58, "battery": 80, "age_s": 120},
    "outside": {"type": "govee", "temp_c": 3.4, "humidity": 81, "battery": 80, "age_s": 60},
    "fridge": {"type": "govee", "temp_c": 5.1, "humidity": 60, "battery": 80, "age_s": 1200},
}


@pytest.fixture
def stub():
    with ux_gallery.EspStub("satellite") as s:
        s.fixtures["satellite"]["/api/state"]["ext"] = json.loads(json.dumps(GOVEES))
        s.fixtures["satellite"]["/api/thermostat"] = ux_gallery.thermostat_fixture()
        yield s


def _enable(stub, **status):
    th = stub.fixtures["satellite"]["/api/thermostat"]
    th["status"].update(enabled=True, **status)


def _open(stub, error_gated_page, lang="en"):
    cm = error_gated_page(stub.base, locale=lang)
    pg = cm.__enter__()
    pg.wait_for_function("() => !!(STATE._meta && STATE._meta.satellite && STATE._meta.read_only === false)")
    pg.locator(".tile", has_text="Luftstandheizung" if lang == "de" else "Air heater").first.click()
    return cm, pg


@pytest.fixture
def heater(stub, error_gated_page):
    cm, pg = _open(stub, error_gated_page)
    yield pg
    cm.__exit__(None, None, None)


def _card(pg):
    return pg.locator("#app .card", has=pg.get_by_role("heading", name="Thermostat", exact=True))


def test_card_absent_without_thermometer(stub, error_gated_page):
    # no Govee configured (no ext) -> no card even though the firmware answers /api/thermostat
    stub.fixtures["satellite"]["/api/state"]["ext"] = {}
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(pg.locator("#title")).to_have_text("Air heater")
        pg.wait_for_timeout(2300)
        assert pg.get_by_role("heading", name="Thermostat", exact=True).count() == 0
    finally:
        cm.__exit__(None, None, None)


def test_card_absent_on_buspi(error_gated_page):
    # buspi (calictl, not the satellite) has no /api/thermostat: no card, no request
    with ux_gallery.EspStub("calictl") as stub:
        stub.fixtures["calictl"]["/api/state"]["ext"] = json.loads(json.dumps(GOVEES))
        with error_gated_page(stub.base) as pg:
            pg.locator(".tile", has_text="Air heater").first.click()
            expect(pg.locator("#title")).to_have_text("Air heater")
            pg.wait_for_timeout(2300)
            assert pg.get_by_role("heading", name="Thermostat", exact=True).count() == 0
            assert "/api/thermostat" not in stub.requests


def _posted(stub, pg, n=1):
    for _ in range(50):
        if len(stub.thermostat_posts) >= n:
            break
        pg.wait_for_timeout(100)
    return stub.thermostat_posts


def test_switch_shown_while_disabled(heater):
    """The AUTO switch shows while the thermostat is disabled."""
    expect(_card(heater).get_by_role("switch", name="AUTO")).to_be_enabled()
    assert _card(heater).get_by_text("Thermostat disabled").count() == 0


@pytest.mark.parametrize(
    "lang,hint,link,title",
    [
        ("en", "Camping mode is off", "Open camping mode ›", "Camping mode"),
        ("de", "Campingmodus ist aus", "Campingmodus öffnen ›", "Campingmodus"),
    ],
)
def test_blocked_no_camping_dims_switch_and_links(stub, error_gated_page, lang, hint, link, title):
    """A blocked start dims the switch and names the reason, with a link to camping mode."""
    _enable(stub, blocked="NO_CAMPING")
    cm, pg = _open(stub, error_gated_page, lang)
    try:
        card = _card(pg)
        expect(card.get_by_role("switch", name="AUTO")).to_be_disabled()
        expect(card.locator(".thermo-hint")).to_contain_text(hint)
        card.get_by_role("button", name=link).click()
        expect(pg.locator("#title")).to_have_text(title)
        assert stub.thermostat_posts == []
    finally:
        cm.__exit__(None, None, None)


def test_blocked_no_reference_opens_the_sheet(stub, error_gated_page):
    _enable(stub, blocked="NO_REFERENCE")
    stub.fixtures["satellite"]["/api/thermostat"]["ref"] = None
    cm, pg = _open(stub, error_gated_page)
    try:
        _card(pg).get_by_role("button", name="Choose a thermometer first").click()
        expect(pg.get_by_role("radiogroup", name="Regulate on")).to_be_visible()
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize(
    "code,want",
    [("TIMER_ARMED", "Stop the timer first"), ("IGNITION", "Ignition is on or not known yet")],
)
def test_blocked_text_only(stub, error_gated_page, code, want):
    _enable(stub, blocked=code)
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(_card(pg).locator(".thermo-hint")).to_have_text(want)
        expect(_card(pg).get_by_role("switch", name="AUTO")).to_be_disabled()
        assert _card(pg).locator(".thermo-hint button").count() == 0
    finally:
        cm.__exit__(None, None, None)


def test_select_reference_posts_alias(stub, error_gated_page):
    _enable(stub)
    cm, pg = _open(stub, error_gated_page)
    try:
        _card(pg).get_by_role("button", name="Thermometer: cabin").click()
        group = pg.get_by_role("radiogroup", name="Regulate on")
        expect(group.get_by_role("radio")).to_have_count(
            4
        )  # cabin, bed, outside + the stale fridge (disabled)
        group.get_by_text("bed", exact=False).first.click()
        for _ in range(50):
            if stub.thermostat_posts:
                break
            pg.wait_for_timeout(100)
        assert stub.thermostat_posts == [{"ref": "bed", "rev": 1}]
        expect(group.get_by_role("radio", name="bed")).to_be_checked()  # from the unit's next answer
    finally:
        cm.__exit__(None, None, None)


def test_stale_sensor_not_selectable(stub, error_gated_page):
    _enable(stub)
    cm, pg = _open(stub, error_gated_page)
    try:
        _card(pg).get_by_role("button", name="Thermometer: cabin").click()
        stale = pg.get_by_role("radiogroup", name="Regulate on").get_by_role("radio", name="fridge")
        expect(stale).to_be_disabled()
        expect(pg.get_by_role("radiogroup", name="Regulate on").locator(".stale")).to_have_count(1)
        assert stub.thermostat_posts == []
    finally:
        cm.__exit__(None, None, None)


def test_auto_needs_confirm(stub, error_gated_page):
    """AUTO asks every time; OK enables and starts in one POST."""
    cm, pg = _open(stub, error_gated_page)
    try:
        seen = []

        def on_dialog(d):
            seen.append(d.message)
            d.dismiss() if len(seen) == 1 else d.accept()

        pg.on("dialog", on_dialog)
        sw = _card(pg).get_by_role("switch", name="AUTO")
        sw.click()
        pg.wait_for_timeout(400)
        assert len(seen) == 1 and stub.thermostat_posts == []  # dismissed: nothing sent
        sw.click()
        for _ in range(50):
            if stub.thermostat_posts:
                break
            pg.wait_for_timeout(100)
        assert stub.thermostat_posts == [{"enabled": True, "mode": "AUTO", "confirm": True, "rev": 1}]
        assert "21.0 °C" in seen[0] and "cabin" in seen[0] and "120 min" in seen[0]
        assert "not live-verified" not in seen[0]
    finally:
        cm.__exit__(None, None, None)


def test_target_buttons_step_half_a_degree(stub, error_gated_page):
    _enable(stub)
    cm, pg = _open(stub, error_gated_page)
    try:
        _card(pg).get_by_role("button", name="Warmer").click()
        for _ in range(50):
            if stub.thermostat_posts:
                break
            pg.wait_for_timeout(100)
        assert stub.thermostat_posts == [{"target": 2150, "rev": 1}]
        expect(_card(pg).get_by_text("21.5 °C")).to_be_visible()
    finally:
        cm.__exit__(None, None, None)


def test_quick_target_taps_on_a_slow_satellite_add_up(stub, error_gated_page):
    """Four quick taps on + with slow POST replies end at +2 °C: an earlier reply or the poll never
    shows an older target in between, and no tap is lost."""
    _enable(stub)
    stub.thermostat_delay_s = 0.8
    cm, pg = _open(stub, error_gated_page)
    try:
        card = _card(pg)
        expect(card.get_by_text("21.0 °C")).to_be_visible()
        pg.evaluate("""() => { window.__seen = [];
          setInterval(() => { const v = [...document.querySelectorAll('.card.thermo .row .sval')]
            .map(e => e.textContent).find(x => /^\\d+\\.\\d °C$/.test(x));
            if (window.__seen[window.__seen.length - 1] !== v) window.__seen.push(v); }, 20); }""")
        for _ in range(4):
            card.get_by_role("button", name="Warmer").click()
            pg.wait_for_timeout(250)
        _posted(stub, pg, 4)
        pg.wait_for_function(
            "() => document.querySelectorAll('.card.thermo').length && window.__seen.includes('23.0 °C')",
            timeout=8000,
        )
        pg.wait_for_timeout(2500)  # replies and polls after the last tap
        assert [b["target"] for b in stub.thermostat_posts] == [2150, 2200, 2250, 2300]
        seen = pg.evaluate("window.__seen")
        assert seen == sorted(seen) and seen[-1] == "23.0 °C", seen
    finally:
        cm.__exit__(None, None, None)


def test_stale_rev_reloads_and_tells_the_user(stub):
    # Chromium logs the firmware's own 409 as a console.error: only that one is let through
    _enable(stub)
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
        pg.on(
            "console",
            lambda m: errors.append(m.text) if m.type == "error" and "status of 409" not in m.text else None,
        )
        pg.goto(stub.base)
        try:
            pg.wait_for_function(
                "() => !!(STATE._meta && STATE._meta.satellite && STATE._meta.read_only === false)"
            )
            pg.locator(".tile", has_text="Air heater").first.click()
            stub.thermostat_error = (409, {"ok": False, "error": "stale_rev"})
            _card(pg).get_by_role("button", name="Warmer").click()
            expect(pg.locator(".toast")).to_contain_text("changed elsewhere")
        finally:
            browser.close()
        assert not errors, errors


@pytest.mark.parametrize("lang", ["en", "de"])
def test_reason_text_en_and_de(stub, error_gated_page, lang):
    # the ignition may be on OR not yet known (-1): the text claims neither alone
    _enable(stub, reason="AUTO_REFUSED_IGNITION", end="none")
    cm, pg = _open(stub, error_gated_page, lang)
    try:
        want = "Ignition is on or not known yet" if lang == "en" else "Zündung ist an oder noch nicht bekannt"
        expect(pg.locator("#app .card .thermo-line")).to_have_text(want)
    finally:
        cm.__exit__(None, None, None)


def test_reason_line_shows_the_reason_not_the_end_kind(stub, error_gated_page):
    # the firmware's status.end is only "run" or "none": the line is status.reason, never "run"
    _enable(stub, mode="AUTO", heater="RUNNING", level=4, reason="HOLD", end="run")
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(pg.locator("#app .card .thermo-line")).to_have_text("Holding the level")
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize(
    "mode,active,want",
    [
        ("AUTO", "bed", "level 4 · on backup"),
        ("AUTO", "cabin", "level 4 · running"),
        ("DEGRADED", "bed", "level 4 · degraded"),
    ],
)
def test_state_on_backup_and_degraded(stub, error_gated_page, mode, active, want):
    _enable(stub, mode=mode, heater="RUNNING", level=4, active=active, reason="AUTO_ON", end="run")
    stub.fixtures["satellite"]["/api/thermostat"].update(mode=mode, backup="bed")
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(_card(pg).get_by_text(want, exact=True)).to_be_visible()
    finally:
        cm.__exit__(None, None, None)


def test_auto_off_posts_without_rev_even_without_state(stub, error_gated_page):
    _enable(stub, mode="AUTO", heater="RUNNING", level=4, reason="AUTO_ON", end="run")
    stub.fixtures["satellite"]["/api/thermostat"]["mode"] = "AUTO"
    cm, pg = _open(stub, error_gated_page)
    try:
        _card(pg).get_by_role("switch", name="AUTO").click()  # on -> off: no confirm
        for _ in range(50):
            if stub.thermostat_posts:
                break
            pg.wait_for_timeout(100)
        assert stub.thermostat_posts == [{"mode": "OFF"}]  # never blocked by a stale rev
        pg.evaluate("() => { THERMO = null; thermoPost({ mode: 'OFF' }); }")  # no state loaded: still sent
        for _ in range(50):
            if len(stub.thermostat_posts) == 2:
                break
            pg.wait_for_timeout(100)
        assert stub.thermostat_posts == [{"mode": "OFF"}, {"mode": "OFF"}]
    finally:
        cm.__exit__(None, None, None)


def test_every_reason_code_has_en_and_de(stub, error_gated_page):
    cm, pg = _open(stub, error_gated_page)
    try:
        bad = pg.evaluate(
            "(codes) => codes.filter(c => { const en = thermoReasonText(c, 'en');"
            " return !en || en === c || (!STRINGS_DE[en] && !/\\d/.test(en)); })",
            list(thermostat.REASONS),
        )
        assert bad == []
    finally:
        cm.__exit__(None, None, None)


def test_switch_names_follow_the_ui_language(stub, error_gated_page):
    _enable(stub)
    cm, pg = _open(stub, error_gated_page, "de")
    try:
        _card(pg).get_by_role("button", name="Thermometer: cabin").click()
        expect(_card(pg).get_by_role("switch", name="Außentemperatur-Ausgleich")).to_be_visible()
        expect(_card(pg).get_by_role("switch", name="AUTO")).to_be_visible()
    finally:
        cm.__exit__(None, None, None)


def test_night_choice_posts_and_confirm_names_the_panel(stub, error_gated_page):
    """Night in the run-time select posts night; its confirmation names the panel steps."""
    cm, pg = _open(stub, error_gated_page, "de")
    try:
        seen = []
        pg.on("dialog", lambda d: (seen.append(d.message), d.accept()))
        _card(pg).get_by_role("combobox", name="Laufzeit").select_option("night")
        assert _posted(stub, pg) == [{"night": True, "rev": 1}]
        expect(_card(pg).get_by_role("combobox", name="Laufzeit")).to_have_value("night")
        _card(pg).get_by_role("switch", name="AUTO").click()
        assert _posted(stub, pg, 2)[1] == {"enabled": True, "mode": "AUTO", "confirm": True, "rev": 2}
        assert "Dauerbetrieb" in seen[0] and "Bedienteil" in seen[0] and "„Ja“" in seen[0]
    finally:
        cm.__exit__(None, None, None)


def test_auto_waits_for_a_pending_night_post(stub, error_gated_page):
    """AUTO waits for a pending run-time POST, so its confirmation names the chosen run."""
    cm, pg = _open(stub, error_gated_page)
    held = []
    try:
        pg.route(
            "**/api/thermostat", lambda r: held.append(r) if r.request.method == "POST" else r.continue_()
        )
        _card(pg).get_by_role("combobox", name="Run time").select_option("night")
        for _ in range(30):
            if held:
                break
            pg.wait_for_timeout(100)
        expect(_card(pg).get_by_role("switch", name="AUTO")).to_be_disabled()
        held.pop().continue_()
        expect(_card(pg).get_by_role("switch", name="AUTO")).to_be_enabled()
        seen = _first_dialog(pg, lambda: _card(pg).get_by_role("switch", name="AUTO").click())
        assert seen and "Heat for the night" in seen[0]
    finally:
        for r in held:
            r.continue_()
        pg.unroute("**/api/thermostat")
        cm.__exit__(None, None, None)


def test_minute_choice_clears_night(stub, error_gated_page):
    stub.fixtures["satellite"]["/api/thermostat"]["night"] = True
    cm, pg = _open(stub, error_gated_page)
    try:
        sel = _card(pg).get_by_role("combobox", name="Run time")
        expect(sel).to_have_value("night")
        sel.select_option("60")
        assert _posted(stub, pg) == [{"night": False, "runtime_min": 60, "rev": 1}]
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize(
    "lang,want",
    [
        ("en", "Switch on “Dauerbetrieb” on the panel now and confirm with “Yes”. Waiting 4:32 min."),
        (
            "de",
            "Jetzt am Bedienteil „Dauerbetrieb“ einschalten und mit „Ja“ bestätigen. Wartet noch 4:32 min.",
        ),
    ],
)
def test_waiting_shows_the_countdown(stub, error_gated_page, lang, want):
    """WAITING tells the owner the panel step and counts down."""
    _enable(stub, mode="WAITING", reason="WAITING_FOR_CONTINUOUS", wait_left_s=272, night=True)
    stub.fixtures["satellite"]["/api/thermostat"].update(mode="WAITING", night=True)
    cm, pg = _open(stub, error_gated_page, lang)
    try:
        expect(_card(pg).locator(".thermo-line")).to_have_text(want)
        _card(pg).get_by_role("switch", name="AUTO").click()  # WAITING -> off, no confirm
        assert _posted(stub, pg) == [{"mode": "OFF"}]
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize("reason", ["RUN_ENDED", "CONTINUOUS_ENDED", "NIGHT_WAIT_ENDED", "USER_OFF"])
def test_quiet_endings_show_no_reason(stub, error_gated_page, reason):
    """A run that ends by itself shows nothing on the card."""
    _enable(stub, reason=reason)
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(_card(pg).get_by_role("switch", name="AUTO")).to_have_attribute("aria-checked", "false")
        expect(_card(pg).locator(".thermo-line")).to_have_count(0)
    finally:
        cm.__exit__(None, None, None)


def test_hold_at_min_level_text(stub, error_gated_page):
    _enable(stub, mode="AUTO", heater="CONTINUOUS", level=1, reason="HOLD_AT_MIN_LEVEL", night=True)
    stub.fixtures["satellite"]["/api/thermostat"].update(mode="AUTO", night=True)
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(_card(pg).locator(".thermo-line")).to_have_text("Warmer than the target at the lowest level")
    finally:
        cm.__exit__(None, None, None)


def test_night_auto_shows_no_end_in_german(stub, error_gated_page):
    _enable(stub, mode="AUTO", heater="CONTINUOUS", level=4, reason="HOLD", night=True)
    stub.fixtures["satellite"]["/api/thermostat"].update(mode="AUTO", night=True)
    cm, pg = _open(stub, error_gated_page, "de")
    try:
        expect(_card(pg).get_by_text("Nacht · Stufe 4 · kein Ende", exact=True)).to_be_visible()
    finally:
        cm.__exit__(None, None, None)


def test_day_auto_shows_the_end_time(stub, error_gated_page):
    """Day AUTO on a running heater shows when the run ends."""
    _enable(stub, mode="AUTO", heater="RUNNING", level=4, active="cabin", reason="HOLD", end="run")
    stub.fixtures["satellite"]["/api/thermostat"]["mode"] = "AUTO"
    # the unit's own 1702: an immediate run at level 4 with 30 min left
    stub.fixtures["satellite"]["/api/state"]["fn"]["airheater"].update(
        NormalOperation=1, HeatingLevel=4, RunningTime=60, RunningTimeinAction=30
    )
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(_card(pg).locator(".sval").first).to_contain_text("level 4 · ends ")
        # the card rendered before this evaluate: across a minute boundary it may show the minute before
        want = pg.evaluate(
            "() => [0, 1].map((k) => { const d = new Date(Date.now() + (30 - k) * 60000);"
            " return hhmm(d.getHours(), d.getMinutes()); })"
        )
        assert _card(pg).locator(".sval").first.text_content() in ["level 4 · ends " + w for w in want]
    finally:
        cm.__exit__(None, None, None)


def test_day_auto_hides_the_end_time_on_old_data(stub, error_gated_page):
    """With the satellite's data older than its offline limit the card names no end time."""
    _enable(stub, mode="AUTO", heater="RUNNING", level=4, active="cabin", reason="HOLD", end="run")
    stub.fixtures["satellite"]["/api/thermostat"]["mode"] = "AUTO"
    state = stub.fixtures["satellite"]["/api/state"]
    state["fn"]["airheater"].update(NormalOperation=1, HeatingLevel=4, RunningTime=60, RunningTimeinAction=30)
    state["device"]["link"]["last_snap_age_ms"] = 600000
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(_card(pg).get_by_text("level 4 · running", exact=True)).to_be_visible()
        assert "ends" not in _card(pg).locator(".sval").first.text_content()
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize(
    "lang,want",
    [("en", "Night · level 4 · on backup · no end"), ("de", "Nacht · Stufe 4 · auf Reserve · kein Ende")],
)
def test_night_on_backup_keeps_the_night_label(stub, error_gated_page, lang, want):
    _enable(stub, mode="AUTO", heater="CONTINUOUS", level=4, active="bed", reason="HOLD", night=True)
    stub.fixtures["satellite"]["/api/thermostat"].update(mode="AUTO", night=True, backup="bed")
    cm, pg = _open(stub, error_gated_page, lang)
    try:
        expect(_card(pg).get_by_text(want, exact=True)).to_be_visible()
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize(
    "lang,want,never",
    [
        ("en", "Take over the running “Dauerbetrieb”", "120 min"),
        ("de", "Den laufenden „Dauerbetrieb“ mit dem Thermostat übernehmen?", "120 min"),
    ],
)
def test_day_confirm_on_continuous_names_no_run_time(stub, error_gated_page, lang, want, never):
    """With Dauerbetrieb already on, the day confirmation says the thermostat holds it until switched off."""
    _enable(stub, heater="CONTINUOUS", level=6)
    stub.fixtures["satellite"]["/api/state"]["fn"]["airheater"].update(
        NormalOperation=1, PermanentOperation=1, HeatingLevel=6
    )
    cm, pg = _open(stub, error_gated_page, lang)
    try:
        seen = []
        pg.on("dialog", lambda d: (seen.append(d.message), d.dismiss()))
        _card(pg).get_by_role("switch", name="AUTO").click()
        for _ in range(30):
            if seen:
                break
            pg.wait_for_timeout(100)
        assert seen and want in seen[0] and never not in seen[0] and "21.0 °C" in seen[0]
    finally:
        cm.__exit__(None, None, None)


def test_night_degraded_keeps_the_night_label(stub, error_gated_page):
    _enable(stub, mode="DEGRADED", heater="CONTINUOUS", level=4, reason="DEGRADED", night=True)
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(_card(pg).get_by_text("Night · level 4 · degraded · no end", exact=True)).to_be_visible()
    finally:
        cm.__exit__(None, None, None)


def _first_dialog(pg, switch_click):
    seen = []
    pg.on("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    switch_click()
    for _ in range(30):
        if seen:
            break
        pg.wait_for_timeout(100)
    return seen


def test_confirm_on_continuous_wins_over_night(stub, error_gated_page):
    _enable(stub, heater="CONTINUOUS", level=6)
    stub.fixtures["satellite"]["/api/thermostat"]["night"] = True
    cm, pg = _open(stub, error_gated_page)
    try:
        seen = _first_dialog(pg, lambda: _card(pg).get_by_role("switch", name="AUTO").click())
        assert (
            seen and "Take over the running “Dauerbetrieb”" in seen[0] and "Heat for the night" not in seen[0]
        )
    finally:
        cm.__exit__(None, None, None)


def test_confirm_on_running_heater_names_no_duration_and_skips_enabled(stub, error_gated_page):
    """A timed run keeps its timer (no runtime is sent), and an enabled feature is not re-enabled."""
    _enable(stub, heater="RUNNING", level=4)
    cm, pg = _open(stub, error_gated_page)
    try:
        pg.on("dialog", lambda d: d.accept())
        _card(pg).get_by_role("switch", name="AUTO").click()
        for _ in range(50):
            if stub.thermostat_posts:
                break
            pg.wait_for_timeout(100)
        assert stub.thermostat_posts == [{"mode": "AUTO", "confirm": True, "rev": 1}]
        assert "at most" not in pg.evaluate("() => thermoConfirmText(THERMO)")
        assert "Take over the running air heater" in pg.evaluate("() => thermoConfirmText(THERMO)")
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize(
    "lang,eta,first,want",
    [
        ("en", 2040, False, "18.0 °C in about 35 min (≈{t})"),
        ("de", 2040, False, "18.0 °C in etwa 35 min (≈{t})"),
        ("en", 60, False, "18.0 °C in about 5 min (≈{t})"),
        ("en", 960, True, "The run time ends before 18.0 °C"),
        ("de", 960, True, "Die Laufzeit endet vor 18.0 °C"),
    ],
)
def test_eta_line(stub, error_gated_page, lang, eta, first, want):
    """While AUTO heats, the card shows the firmware's time-to-target estimate in 5-min steps, always
    "about"; an estimate later than the run's end says so instead of naming a time.

    .. test:: Thermostat card: the time-to-target line
       :id: T_THERMOSTAT_CARD_ETA
       :links: R_THERMOSTAT_ETA, AD_THERMOSTAT_ETA
    """
    _enable(stub, mode="AUTO", heater="RUNNING", level=10, active="cabin", reason="HOLD", end="run",
            target=1800, eta_s=eta, eta_ends_first=first)  # fmt: skip
    stub.fixtures["satellite"]["/api/thermostat"].update(mode="AUTO", target=1800)
    cm, pg = _open(stub, error_gated_page, lang)
    try:
        line = _card(pg).locator(".thermo-eta")
        expect(line).to_have_count(1)
        if "{t}" in want:
            mins = int(want.split(" min")[0].split()[-1])
            ok = pg.evaluate(
                "(m) => [0, 1].map((k) => { const d = new Date(Date.now() + (m - k) * 60000);"
                " return hhmm(d.getHours(), d.getMinutes()); })",
                mins,
            )
            assert line.text_content() in [want.format(t=w) for w in ok]
        else:
            expect(line).to_have_text(want)
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize("mode,eta", [("AUTO", None), ("OFF", 600), ("WAITING", 600)])
def test_eta_line_hidden_without_an_estimate(stub, error_gated_page, mode, eta):
    """No estimate, or a mode other than AUTO: no line."""
    _enable(stub, mode=mode, heater="RUNNING", level=10, reason="HOLD", eta_s=eta)
    stub.fixtures["satellite"]["/api/thermostat"]["mode"] = mode
    cm, pg = _open(stub, error_gated_page)
    try:
        expect(_card(pg).get_by_role("switch", name="AUTO")).to_be_visible()
        expect(_card(pg).locator(".thermo-eta")).to_have_count(0)
    finally:
        cm.__exit__(None, None, None)
