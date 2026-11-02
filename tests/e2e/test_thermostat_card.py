"""Browser e2e of the cabin thermostat card on the Air heater screen (R_FW_THERMOSTAT_API).

The card exists only on the ESP satellite (``GET /api/thermostat``) and only while a Govee
thermometer is configured; it ships DISABLED (no AUTO switch). Every test fails on an uncaught JS
error (``error_gated_page``). OPT-IN like test_satellite.py: skipped without Playwright + Chromium.
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


def test_disabled_card_has_no_auto_switch(heater):
    expect(_card(heater).get_by_text("Thermostat disabled")).to_be_visible()
    assert _card(heater).get_by_role("switch", name="AUTO").count() == 0


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
    _enable(stub)
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
        assert stub.thermostat_posts == [{"mode": "AUTO", "confirm": True, "rev": 1}]
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
        ("AUTO", "bed", "level 4 • on backup"),
        ("AUTO", "cabin", "level 4 • running"),
        ("DEGRADED", "bed", "level 4 • degraded"),
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
