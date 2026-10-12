"""Browser e2e of the offline shell (R_UI_PWA_OFFLINE): ``calictl/webui/sw.js`` + app.js's saved state.

127.0.0.1 is a secure context, so the page registers the service worker here as it does over the
tailnet HTTPS fronts. buspi's flavor runs ``calictl.web`` over a temporary copy of ``calictl/webui``
(so a test can "deploy" a changed file) and a fixed calictl state; the satellite's runs
``tools.ux_gallery.EspStub`` (the generated bundle + PWA assets). Plain http on a LAN address is
not a secure context; that case is stubbed with ``isSecureContext = false``. OPT-IN like
test_gui.py: skipped without Playwright + Chromium.
"""

import contextlib
import shutil
import threading
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

WEBUI = Path(web.__file__).resolve().parent / "webui"
FIX = ux_gallery.esp_fixtures()["calictl"]
CACHED = "async () => (await (await caches.open('oc-shell-v1')).keys()).map((r) => new URL(r.url).pathname)"


class _Backend:
    def state(self):
        return FIX["/api/state"]

    def pairing_snapshot(self):
        return FIX["/api/pairing"]

    def screens_bytes(self):
        return b'{"screens": {}, "order": []}'


@pytest.fixture
def daemon(tmp_path):
    """buspi's web server over a copy of calictl/webui: ``(webui_dir, base_url, stop)``."""
    d = tmp_path / "webui"
    shutil.copytree(WEBUI, d)
    srv = web.serve_http(_Backend(), str(d), "127.0.0.1", 0)
    stopped = threading.Event()

    def stop():
        if not stopped.is_set():
            stopped.set()
            srv.shutdown()
            srv.server_close()

    yield d, "http://127.0.0.1:%d" % srv.server_address[1], stop
    stop()


@contextlib.contextmanager
def _page(init_script=None):
    """A Chromium page whose uncaught JS errors fail the test (console errors are expected: a
    stopped server logs failed loads)."""
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        if init_script:
            ctx.add_init_script(init_script)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        try:
            yield pg
        finally:
            browser.close()
        assert not errs, errs


def _installed(pg):
    """Wait until the worker is active (its install cached the shell) and the page saved a state."""
    pg.evaluate("async () => { await navigator.serviceWorker.ready; }")
    pg.wait_for_function("() => !!localStorage.getItem('calictl.last_state')")
    return set(pg.evaluate(CACHED))


def test_offline_reload_shows_the_last_state(daemon):
    """Server gone: the reload comes from the worker's cache and shows the saved state as offline.

    .. test:: The shell opens offline from the cache with the last state behind the offline banner
       :id: T_UI_PWA_OFFLINE
       :links: R_UI_PWA_OFFLINE
    """
    _, base, stop = daemon
    with _page() as pg:
        pg.goto(base + "/")
        cached = _installed(pg)
        assert {
            "/",
            "/app.js",
            "/app.css",
            "/semantics.js",
            "/strings.de.js",
            "/manifest.webmanifest",
        } <= cached
        assert not any(p.startswith("/api/") for p in cached), cached
        stop()  # the van's network is gone
        pg.reload()
        expect(pg.locator(".offline")).to_contain_text("Offline — van asleep. Last data")
        expect(pg.get_by_text("Cooler").first).to_be_visible()
        expect(pg.locator("#status")).to_have_text("offline")
        assert pg.evaluate("() => STATE._meta.online") is False


def test_a_changed_shell_offers_a_reload(daemon):
    """A changed file behind an unchanged page (a deploy) is noticed by its ETag on the next open.

    .. test:: A deployed UI change shows a reload toast; the reload shows the new shell
       :id: T_UI_PWA_UPDATE
       :links: R_UI_PWA_OFFLINE
    """
    webui, base, _ = daemon
    with _page() as pg:
        pg.goto(base + "/")
        _installed(pg)
        pg.reload()  # now served by the worker: the same ETags, no toast
        pg.wait_for_function("() => typeof STATE !== 'undefined' && STATE._meta && STATE._meta.online")
        pg.wait_for_timeout(500)
        assert pg.locator(".toast.update").count() == 0
        with open(webui / "app.css", "a") as f:
            f.write("\n/* deploy v2 */\n")
        pg.reload()
        toast = pg.locator(".toast.update")
        expect(toast).to_have_text("New version — tap to reload")
        expect(toast).to_have_attribute("role", "button")
        with pg.expect_navigation():
            toast.click()
        css = pg.evaluate("async () => (await (await caches.match('/app.css')).text())")
        assert "deploy v2" in css


def test_no_worker_and_no_saved_state_on_an_insecure_origin(daemon):
    """Plain http on the LAN (not a secure context) stays exactly as before: no service worker, no
    saved state.

    .. test:: No service worker outside a secure context
       :id: T_UI_PWA_INSECURE
       :links: R_UI_PWA_OFFLINE
    """
    _, base, _ = daemon
    with _page("Object.defineProperty(window, 'isSecureContext', { value: false })") as pg:
        pg.goto(base + "/")
        pg.wait_for_function("() => typeof STATE !== 'undefined' && STATE._meta && STATE._meta.online")
        pg.wait_for_timeout(1000)
        assert pg.evaluate("async () => (await navigator.serviceWorker.getRegistrations()).length") == 0
        assert pg.evaluate("() => localStorage.getItem('calictl.last_state')") is None


def test_satellite_app_opens_offline():
    """The satellite's home-screen start_url /app opens from the cached bundle with the last state.

    .. test:: The satellite's /app opens offline
       :id: T_UI_PWA_SATELLITE
       :links: R_UI_PWA_OFFLINE
    """
    stub = ux_gallery.EspStub("satellite")
    with stub, _page() as pg:
        pg.goto(stub.base + "/app")
        cached = _installed(pg)
        assert {"/", "/manifest.webmanifest", "/apple-touch-icon.png"} <= cached, cached
        stub.__exit__(None, None, None)
        pg.reload()
        expect(pg.locator(".offline")).to_contain_text("Offline — van asleep. Last data")
        assert pg.evaluate("() => STATE._meta.satellite") is True
