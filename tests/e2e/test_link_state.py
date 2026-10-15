"""Browser e2e of the page's link state (R_UI_LINK_STATE): the offline banner tells "the van is
asleep" (amber, the backend answered) from "calictl / the satellite is not reachable" (red, no
answer for 15 s), and one ``linkState()`` drives both the banner (``#conn``) and the status pill.

A fixed calictl state over ``calictl.web`` (the same server buspi runs) stands in for the daemon;
``page.route`` takes the backend away and ``page.clock`` skips the 15 s instead of waiting it out.
Service workers are blocked so every /api/state reaches ``page.route``. Failed loads log console
errors on purpose here, so only uncaught page errors fail a test. OPT-IN like test_gui.py.
"""

import contextlib
import copy
import re
from pathlib import Path

import pytest

from calictl import web
from tools import ux_gallery

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
try:
    with sync_api.sync_playwright() as _p:
        _p.chromium.launch().close()
except Exception as _e:  # noqa: BLE001 — any launch failure (missing executable, deps) -> skip
    pytest.skip("Playwright chromium browser not installed: %s" % _e, allow_module_level=True)

WEBUI = str(Path(web.__file__).resolve().parent / "webui")
NAME = "calictl (127.0.0.1)"
ANSWERED = "() => typeof STATE !== 'undefined' && !!STATE._meta && !STATE._meta.restored"


class _Backend:
    def __init__(self):
        fix = ux_gallery.esp_fixtures()["calictl"]
        self.st = copy.deepcopy(fix["/api/state"])
        self.pairing = fix["/api/pairing"]

    def state(self):
        return self.st

    def pairing_snapshot(self):
        return self.pairing

    def screens_bytes(self):
        return b'{"screens": {}, "order": []}'


@pytest.fixture
def backend():
    b = _Backend()
    srv = web.serve_http(b, WEBUI, "127.0.0.1", 0)
    b.base = "http://127.0.0.1:%d" % srv.server_address[1]
    yield b
    srv.shutdown()
    srv.server_close()


def _asleep(b):
    b.st["_meta"].update(online=False)  # the daemon answers, the unit does not: van asleep


@contextlib.contextmanager
def _page(b, locale="en-US", init_script=None, before=None):
    """A Chromium page on a fake clock (installed before the load, so every timer is ours).
    ``before(pg)`` runs before the load (a route); then the first answer is not awaited."""
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale=locale, service_workers="block")
        if init_script:
            ctx.add_init_script(init_script)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.clock.install()
        if before:
            before(pg)
        pg.goto(b.base)
        if not before:
            pg.wait_for_function(ANSWERED)
        try:
            yield pg
        finally:
            browser.close()
        assert not errs, errs


def _tick(pg, ms):
    """Advance the page clock ``ms`` in 2 s ticks, letting each tick's real fetch settle."""
    while ms > 0:
        step = min(2000, ms)
        pg.clock.run_for(step)
        pg.wait_for_timeout(150)
        ms -= step


def _down(pg):
    pg.route("**/api/state", lambda route: route.abort())


def _conn(pg):
    return pg.locator("#conn")


def test_asleep_van_shows_the_amber_banner(backend):
    """Scenario 1: the backend answers ``online=false`` -> amber "van asleep", pill offline, no red.

    .. test:: An answering backend with a sleeping unit shows the amber asleep banner
       :id: T_UI_LINK_ASLEEP
       :links: R_UI_LINK_STATE
    """
    _asleep(backend)
    with _page(backend) as pg:
        expect(_conn(pg)).to_contain_text("Offline — van asleep. Last data")
        expect(pg.locator("#status")).to_have_text("offline")
        assert pg.locator("#conn.unreachable").count() == 0
        assert pg.locator(".offline").count() == 1


def test_backend_down_turns_red_at_15_s_and_recovers(backend):
    """Scenario 2: after a good poll the backend goes away: nothing at 10 s, red at 15 s; back ->
    both clear on the next tick, no reload.

    .. test:: An unreachable backend shows the red banner after 15 s and clears on the first answer
       :id: T_UI_LINK_UNREACHABLE
       :links: R_UI_LINK_STATE
    """
    with _page(backend) as pg:
        expect(pg.locator("#status")).to_have_text("live")
        _down(pg)
        _tick(pg, 10000)
        assert pg.locator("#conn.unreachable").count() == 0
        assert pg.locator("#status").inner_text() != "unreachable"
        _tick(pg, 8000)
        expect(pg.locator("#conn.unreachable")).to_contain_text("⚠ %s not reachable since" % NAME)
        expect(pg.locator("#conn")).to_contain_text("Last data")
        expect(pg.locator("#status")).to_have_text("unreachable")
        pg.unroute("**/api/state")
        _tick(pg, 2000)
        expect(pg.locator("#status")).to_have_text("live")
        expect(pg.locator("#conn.offline")).to_have_count(0)


def test_one_failed_poll_changes_nothing(backend):
    """Scenario 3: one aborted poll (a blip) -> no banner, the pill keeps ``live``."""
    with _page(backend) as pg:
        expect(pg.locator("#status")).to_have_text("live")
        _down(pg)
        _tick(pg, 2000)  # one failed poll
        pg.wait_for_timeout(300)
        assert pg.locator("#status").inner_text() == "live"
        assert pg.locator("#conn.offline").count() == 0
        pg.unroute("**/api/state")
        _tick(pg, 2000)
        assert pg.locator("#status").inner_text() == "live"
        assert pg.locator("#conn.offline").count() == 0


def test_a_hung_poll_turns_red_at_15_s(backend):
    """Scenario 4: a poll that never answers (dead peer) still turns red: the threshold is time,
    not a failure count, and the 8 s abort lets the next tick retry."""
    held = []
    with _page(backend) as pg:
        expect(pg.locator("#status")).to_have_text("live")
        pg.route("**/api/state", lambda route: held.append(route))  # never fulfilled
        _tick(pg, 10000)
        assert pg.locator("#conn.unreachable").count() == 0
        _tick(pg, 8000)
        expect(pg.locator("#conn.unreachable")).to_contain_text("not reachable since")
        expect(pg.locator("#status")).to_have_text("unreachable")
        assert len(held) >= 2, held  # the 8 s abort freed the poll: a later tick sent another


def test_hidden_tab_does_not_count_down(backend):
    """Scenario 7: 10 min hidden (no polls), then visible with the backend gone: no instant red,
    red only 15 s after the first poll on return."""
    hide = (
        "window.__hidden = false;"
        "Object.defineProperty(document, 'hidden', { get: () => window.__hidden });"
        "Object.defineProperty(document, 'visibilityState',"
        " { get: () => (window.__hidden ? 'hidden' : 'visible') });"
    )
    with _page(backend, init_script=hide) as pg:
        expect(pg.locator("#status")).to_have_text("live")
        pg.evaluate(
            "() => { window.__hidden = true; document.dispatchEvent(new Event('visibilitychange')); }"
        )
        _down(pg)
        pg.clock.run_for(600000)
        pg.evaluate(
            "() => { window.__hidden = false; document.dispatchEvent(new Event('visibilitychange')); }"
        )
        pg.wait_for_timeout(300)
        assert pg.locator("#conn.unreachable").count() == 0
        # hours-old values are not "live": no fresh answer yet -> connecting
        assert pg.locator("#status").inner_text() == "connecting…"
        _tick(pg, 18000)
        expect(pg.locator("#conn.unreachable")).to_be_visible()


def test_asleep_then_unreachable_shows_only_the_red_banner(backend):
    """Scenario 8: the last answer said asleep, now the backend is gone: exactly one banner, red."""
    _asleep(backend)
    with _page(backend) as pg:
        expect(_conn(pg)).to_contain_text("van asleep")
        _down(pg)
        _tick(pg, 18000)
        expect(pg.locator("#conn.unreachable")).to_contain_text("not reachable since")
        assert pg.locator(".offline").count() == 1
        assert "asleep" not in _conn(pg).inner_text()


def test_german_copy_and_a_quiet_live_region(backend):
    """Scenario 9: DE copy of both banners; ``#conn`` is a ``role=status`` region whose text is set
    only on a state change (the ticking age lives in an aria-hidden child)."""
    _asleep(backend)
    with _page(backend, locale="de-DE") as pg:
        expect(_conn(pg)).to_contain_text("Offline — Fahrzeug im Ruhezustand. Letzte Daten")
        expect(_conn(pg)).to_have_attribute("role", "status")
        pg.evaluate("() => { window.__first = document.getElementById('conn').firstChild; }")
        _tick(pg, 6000)  # good polls: same state
        assert pg.evaluate("() => document.getElementById('conn').firstChild === window.__first")
        _down(pg)
        _tick(pg, 18000)
        expect(pg.locator("#conn.unreachable")).to_contain_text("⚠ %s nicht erreichbar seit" % NAME)
        expect(_conn(pg)).to_contain_text("Letzte Daten")
        expect(pg.locator("#status")).to_have_text("nicht erreichbar")


@pytest.mark.parametrize(
    "locale,banner,pill",
    [
        ("en-US", "Not paired yet — no data.", "not paired"),
        ("de-DE", "Noch nicht gekoppelt — keine Daten.", "nicht gekoppelt"),
    ],
)
def test_unpaired_never_says_not_paired_not_asleep(backend, locale, banner, pill):
    """No unit paired and no data ever: the cause is "not paired", not a sleeping van (UX review M6).

    .. test:: With no unit paired the banner says "not paired", never "asleep"
       :id: T_UI_LINK_UNPAIRED
       :links: R_UI_LINK_STATE
    """
    backend.st["_meta"].update(online=False, last_seen=None, paired=False)
    with _page(backend, locale=locale) as pg:
        expect(_conn(pg)).to_have_text(banner)
        expect(pg.locator("#status")).to_have_text(pill)
        assert pg.locator("#conn.unreachable").count() == 0


def test_an_error_answer_is_no_answer(backend):
    """A cold open whose /api/state answers 500 {error} (the daemon cannot build its state): plain
    "Connecting…" banner at once, red after 15 s with no "Last data" clause, live once it answers."""

    def err(pg):
        pg.route(
            "**/api/state",
            lambda route: route.fulfill(
                status=500, content_type="application/json", body='{"error": "state_failed"}'
            ),
        )

    with _page(backend, before=err) as pg:
        expect(pg.locator("#conn.connecting")).to_have_text("Connecting…")
        expect(pg.locator("#status")).to_have_text("connecting…")
        _tick(pg, 20000)
        expect(pg.locator("#conn.unreachable")).to_have_text(
            re.compile(r"^⚠ calictl \(127\.0\.0\.1\) not reachable since [^.]+\.$")
        )
        expect(pg.locator("#status")).to_have_text("unreachable")
        pg.unroute("**/api/state")
        _tick(pg, 2000)
        expect(pg.locator("#status")).to_have_text("live")
        expect(pg.locator("#conn.offline")).to_have_count(0)
