"""The /device page's Sensors box (R_FW_SENSORS) against the ESP stub's stateful ``/api/sensors``:
add (default name, own name, a second of a type, a key type), the client-side name check, the
in-page Remove confirmation, rename, and the 10 s poll leaving an open step alone. EN + DE where
text is asserted; every test fails on an uncaught page error (``error_gated_page``).

.. test:: The /device Sensors box: add / remove / rename against the stub (EN + DE)
   :id: T_FW_SENSORS_UI_E2E
   :links: R_FW_SENSORS
"""

import contextlib
import json
from pathlib import Path

import pytest

from tools import ux_gallery

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

STR = json.loads(
    (Path(__file__).resolve().parents[2] / "firmware/web/strings.json").read_text(encoding="utf-8")
)
LOCALES = [("en-US", "en"), ("de-DE", "de")]
GOVEE_A = {"id": 0x2001, "type": "govee", "model": "GVH5074_0001", "rssi": -61}
GOVEE_B = {"id": 0x2003, "type": "govee", "model": "GVH5074_0002", "rssi": -66}
MPPT = {"id": 0x2005, "type": "victron_mppt", "model": "SmartSolar", "rssi": -72}
POLL = "async () => renderSens((await fetchJson('/api/sensors')).body)"  # the 10 s poll's render, now


@pytest.fixture
def stub():
    """Satellite mode, nothing configured yet, two Govees and a solar charger in range."""
    with ux_gallery.EspStub("satellite") as s:
        sens = s.fixtures["satellite"]["/api/sensors"]
        sens["configured"], sens["seen"] = [], [dict(GOVEE_A), dict(GOVEE_B), dict(MPPT)]
        yield s


def _open(error_gated_page, stub, lang):
    return error_gated_page(stub.base + "/device", locale="de-DE" if lang == "de" else "en-US")


@contextlib.contextmanager
def _open_400(stub, lang):
    """Like ``error_gated_page``, but the firmware's own 400 answers are expected: Chromium logs each
    as a console.error ("Failed to load resource"), so only those are let through."""
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(locale="de-DE" if lang == "de" else "en-US")
        errors = []
        pg.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
        pg.on(
            "console",
            lambda m: errors.append(m.text) if m.type == "error" and "status of 400" not in m.text else None,
        )
        pg.goto(stub.base + "/device")
        try:
            yield pg
        finally:
            browser.close()
        assert not errors, errors


def _s(key, lang, **kw):
    s = STR[key][lang]
    for k, v in kw.items():
        s = s.replace("{%s}" % k, v)
    return s


def _seen_row(pg, model):
    return pg.locator("#sens-seen .row", has_text=model)


def _cfg_row(pg, alias):
    return pg.locator("#sens-cfg .row", has_text=" · %s · " % alias)


def _add(pg, lang, model, name=None, key=None):
    row = _seen_row(pg, model)
    row.get_by_role("button", name=_s("sens_add", lang)).click()
    if name is not None:
        row.get_by_role("textbox", name=_s("sens_alias", lang)).fill(name)
    if key is not None:
        row.get_by_role("textbox", name=_s("sens_key", lang)).fill(key)
    row.get_by_role("button", name=_s("sens_add", lang)).click()
    return row


def _key():
    return bytes(range(0x00, 0x100, 0x11)).hex()  # 32 hex digits, built at runtime (gitleaks)


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_add_without_a_name_takes_the_types_default(stub, error_gated_page, locale, lang):
    with _open(error_gated_page, stub, lang) as pg:
        row = _seen_row(pg, "GVH5074_0001")
        row.get_by_role("button", name=_s("sens_add", lang)).click()
        name = row.get_by_role("textbox", name=_s("sens_alias", lang))
        expect(name).to_have_attribute("placeholder", "govee")  # optional: the default shows
        row.get_by_role("button", name=_s("sens_add", lang)).click()
        expect(_cfg_row(pg, "govee")).to_contain_text(_s("sens_t_govee", lang))
        assert stub.sensor_posts == [{"add": GOVEE_A["id"]}]


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_add_with_a_name(stub, error_gated_page, locale, lang):
    with _open(error_gated_page, stub, lang) as pg:
        _add(pg, lang, "GVH5074_0001", name="Innen")  # lowercased, as the firmware wants it
        expect(_cfg_row(pg, "innen")).to_be_visible()
        assert stub.sensor_posts == [{"add": GOVEE_A["id"], "alias": "innen"}]


def test_a_second_sensor_of_a_type_needs_a_name(stub, error_gated_page):
    with _open(error_gated_page, stub, "en") as pg:
        _add(pg, "en", "GVH5074_0001")
        expect(_cfg_row(pg, "govee")).to_be_visible()
        _add(pg, "en", "GVH5074_0002")  # no name: refused on the page, nothing posted
        expect(pg.locator("#sens-msg")).to_have_text(STR["sens_err_name"]["en"])  # a name is needed
        assert len(stub.sensor_posts) == 1
        _add(pg, "en", "GVH5074_0002", name="aussen")
        expect(_cfg_row(pg, "aussen")).to_be_visible()
        expect(pg.locator("#sens-msg")).to_have_text("")


@pytest.mark.parametrize("bad", ["mein sensor", "a" * 17, "küche"])
@pytest.mark.parametrize("locale,lang", LOCALES)
def test_an_invalid_name_is_refused_before_posting(stub, error_gated_page, locale, lang, bad):
    with _open(error_gated_page, stub, lang) as pg:
        row = _add(pg, lang, "GVH5074_0001", name=bad)
        expect(pg.locator("#sens-msg")).to_have_text(_s("sens_err_name", lang))
        expect(row.get_by_role("textbox")).to_be_visible()  # the form stays open to fix it
        assert stub.sensor_posts == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_a_taken_name_shows_the_firmwares_error(stub, locale, lang):
    with _open_400(stub, lang) as pg:
        _add(pg, lang, "GVH5074_0001", name="innen")
        expect(_cfg_row(pg, "innen")).to_be_visible()
        row = _add(pg, lang, "GVH5074_0002", name="innen")
        expect(pg.locator("#sens-msg")).to_have_text(_s("sens_err_bad_alias", lang))
        expect(row.get_by_role("textbox", name=_s("sens_alias", lang))).to_have_value("innen")
        assert len(stub.sensor_posts) == 2


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_a_victron_needs_its_key(stub, locale, lang):
    with _open_400(stub, lang) as pg:
        _add(pg, lang, "SmartSolar", key="12ab")
        expect(pg.locator("#sens-msg")).to_have_text(_s("sens_err_bad_key", lang))
        assert stub.sensor_posts == [{"add": MPPT["id"], "key": "12ab"}]
        _add(pg, lang, "SmartSolar", name="solar", key=_key())
        expect(_cfg_row(pg, "solar")).to_contain_text(_s("sens_t_victron_mppt", lang))
        expect(pg.locator("#sens-msg")).to_have_text("")


def test_cancel_closes_the_add_form(stub, error_gated_page):
    with _open(error_gated_page, stub, "en") as pg:
        row = _seen_row(pg, "GVH5074_0001")
        row.get_by_role("button", name="Add").click()
        row.get_by_role("button", name=STR["sens_cancel"]["en"]).click()
        expect(row.get_by_role("textbox")).to_have_count(0)
        expect(row.get_by_role("button", name="Add")).to_be_visible()
        assert stub.sensor_posts == []


def _configured(stub, *rows):
    sens = stub.fixtures["satellite"]["/api/sensors"]
    sens["configured"] = [
        {"id": r["id"], "type": r["type"], "alias": a, "last_seen_s": 6, "value": v, "has_key": False}
        for r, a, v in rows
    ]
    sens["seen"] = [s for s in sens["seen"] if s["id"] not in {r["id"] for r, _, _ in rows}]
    stub._sens_model.update((r["id"], r["model"]) for r, _, _ in rows)  # as if added through the page


GOVEE_VALUE = {"temp_c": 16.5, "humidity": 58.6, "battery": 49}


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_remove_asks_in_the_page_and_cancel_keeps_it(stub, error_gated_page, locale, lang):
    _configured(stub, (GOVEE_A, "innen", GOVEE_VALUE))
    with _open(error_gated_page, stub, lang) as pg:
        row = _cfg_row(pg, "innen")
        pg.on("dialog", lambda d: pytest.fail("a native dialog: %s" % d.message))
        row.get_by_role("button", name=_s("sens_remove", lang)).click()
        expect(row).to_contain_text(_s("sens_confirm_remove", lang, alias="innen"))
        row.get_by_role("button", name=_s("sens_cancel", lang)).click()
        expect(row.get_by_role("button", name=_s("sens_rename", lang))).to_be_visible()
        expect(row).not_to_contain_text(_s("sens_confirm_remove", lang, alias="innen"))
        assert stub.sensor_posts == []


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_remove_confirmed_removes_it_and_the_next_poll_finds_it(stub, error_gated_page, locale, lang):
    _configured(stub, (GOVEE_A, "innen", GOVEE_VALUE))
    with _open(error_gated_page, stub, lang) as pg:
        row = _cfg_row(pg, "innen")
        row.get_by_role("button", name=_s("sens_remove", lang)).click()
        row.get_by_role("button", name=_s("sens_remove", lang)).click()  # the confirm button
        expect(pg.locator("#sens-cfg .row")).to_have_count(0)
        assert stub.sensor_posts == [{"remove": GOVEE_A["id"]}]
        pg.evaluate(POLL)
        expect(_seen_row(pg, "GVH5074_0001")).to_be_visible()


@pytest.mark.parametrize("locale,lang", LOCALES)
def test_rename(stub, error_gated_page, locale, lang):
    _configured(stub, (GOVEE_A, "govee", GOVEE_VALUE))
    with _open(error_gated_page, stub, lang) as pg:
        row = _cfg_row(pg, "govee")
        row.get_by_role("button", name=_s("sens_rename", lang)).click()
        name = row.get_by_role("textbox", name=_s("sens_alias", lang))
        expect(name).to_have_value("govee")
        name.fill("kueche")
        row.get_by_role("button", name=_s("sens_save", lang)).click()
        expect(_cfg_row(pg, "kueche")).to_be_visible()
        assert stub.sensor_posts == [{"id": GOVEE_A["id"], "alias": "kueche"}]


def test_configured_rows_read_as_type_name_values_age(stub, error_gated_page):
    _configured(stub, (GOVEE_A, "innen", GOVEE_VALUE))
    with _open(error_gated_page, stub, "en") as pg:
        row = _cfg_row(pg, "innen")
        expect(row).to_contain_text(
            "Thermo-hygrometer (Govee) · innen · 16.5\u00a0°C · 58.6\u00a0% · 6 s"  # no sensor battery: two bare % read alike
        )


@pytest.mark.parametrize("step", ["remove", "rename", "add"])
def test_the_poll_leaves_an_open_step_alone(stub, error_gated_page, step):
    _configured(stub, (GOVEE_A, "innen", GOVEE_VALUE))
    with _open(error_gated_page, stub, "en") as pg:
        if step == "add":
            row = _seen_row(pg, "GVH5074_0002")
            row.get_by_role("button", name="Add").click()
            row.get_by_role("textbox").fill("aussen")
        else:
            row = _cfg_row(pg, "innen")
            row.get_by_role("button", name=STR["sens_" + step]["en"]).click()
        before = row.inner_text()
        pg.evaluate(POLL)
        pg.evaluate(POLL)
        assert row.inner_text() == before
        if step == "add":
            expect(row.get_by_role("textbox")).to_have_value("aussen")


def test_the_key_field_shows_what_is_typed(stub, error_gated_page):
    """A 32-hex key copied by hand: visible (no password field), no autofill or spell-check."""
    with _open(error_gated_page, stub, "en") as pg:
        row = _seen_row(pg, "SmartSolar")
        row.get_by_role("button", name="Add").click()
        key = row.get_by_role("textbox", name=STR["sens_key"]["en"])
        expect(key).to_have_js_property("type", "text")
        expect(key).to_have_attribute("autocomplete", "off")
        expect(key).to_have_attribute("spellcheck", "false")


@pytest.mark.parametrize("step", ["add", "remove"])
def test_a_double_tap_on_ok_posts_once_and_shows_no_error(stub, error_gated_page, step):
    _configured(stub, (GOVEE_A, "innen", GOVEE_VALUE))
    with _open(error_gated_page, stub, "en") as pg:
        if step == "add":
            row = _seen_row(pg, "GVH5074_0002")
            row.get_by_role("button", name="Add").click()
            row.get_by_role("textbox").fill("aussen")
            row.get_by_role("button", name="Add").dblclick()
            expect(_cfg_row(pg, "aussen")).to_be_visible()
        else:
            row = _cfg_row(pg, "innen")
            row.get_by_role("button", name="Remove").click()
            row.locator("button.danger").dblclick()
            expect(pg.locator("#sens-cfg .row")).to_have_count(0)
        pg.wait_for_timeout(300)  # a second answer would land by now
        assert len(stub.sensor_posts) == 1, stub.sensor_posts
        expect(pg.locator("#sens-msg")).to_have_text("")


@pytest.mark.parametrize("step", ["add", "rename", "remove"])
def test_an_opened_step_takes_the_focus(stub, error_gated_page, step):
    """The tapped button is gone: focus moves to the name field (Cancel for the remove question)."""
    _configured(stub, (GOVEE_A, "innen", GOVEE_VALUE))
    with _open(error_gated_page, stub, "en") as pg:
        row = _seen_row(pg, "GVH5074_0002") if step == "add" else _cfg_row(pg, "innen")
        row.get_by_role("button", name=STR["sens_" + step]["en"]).click()
        target = row.get_by_role("button", name="Cancel") if step == "remove" else row.get_by_role("textbox")
        expect(target).to_be_focused()


def test_an_error_is_announced_and_scrolled_into_view(stub, error_gated_page):
    with error_gated_page(stub.base + "/device", viewport={"width": 390, "height": 400}) as pg:
        msg = pg.locator("#sens-msg")
        expect(msg).to_have_attribute("aria-live", "polite")
        _add(pg, "en", "GVH5074_0001", name="mein sensor")  # the click scrolled its row into view
        expect(msg).to_have_text(STR["sens_err_name"]["en"])
        expect(msg).to_be_in_viewport()
