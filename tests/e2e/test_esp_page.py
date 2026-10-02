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
        pg.wait_for_timeout(3500)
        assert len(posts) == 2


@pytest.mark.parametrize(
    "ms,text",
    [
        (45_000, "45 s"),
        (59_999, "59 s"),
        (60_000, "1 min"),
        (45 * 60_000, "45 min"),
        ((3 * 60 + 12) * 60_000, "3 h 12 min"),
        ((2 * 24 + 4) * 3_600_000, "2 d 4 h"),
    ],
)
def test_device_page_uptime_is_readable(stub, page, ms, text):
    """Bench walk #154: /device showed uptime as raw seconds ("11520 s"). It reads like the
    device's own screen now: "45 s", "N min", "H h M min", "D d H h" (no /api/state change)."""
    stub.mode = "station"
    stub.fixtures["station"]["/api/state"]["device"]["uptime_ms"] = ms
    with page(stub.base + "/device", locale="en-US") as pg:
        row = pg.locator("#device dt", has_text="Uptime").locator("xpath=following-sibling::dd[1]")
        expect(row).to_have_text(text)


@pytest.mark.parametrize("locale,lang", [("en-US", "en"), ("de-DE", "de")])
def test_device_page_says_paired_when_bonded_and_linked(stub, page, locale, lang):
    """Bench walk #154: a satellite that reconnected by its stored bond reports pairing state
    "idle" (the pairing flow never ran this boot) — /device said "idle" next to a paired unit and a
    live link. With an address and the link up it says paired; without the link the raw state."""
    stub.mode = "station"
    dev = stub.fixtures["station"]["/api/state"]["device"]
    dev["pairing"] = {"state": "idle", "address": "C0:FF:EE:CA:11:F0"}
    label = "dt:text-is('%s')" % STRINGS["pairing"][lang]
    with page(stub.base + "/device", locale=locale) as pg:
        row = pg.locator("#device " + label).locator("xpath=following-sibling::dd[1]")
        expect(row).to_have_text(STRINGS["paired"][lang])
        dev["link"]["up"] = False
        expect(row).to_have_text("idle", timeout=6000)  # the next poll
