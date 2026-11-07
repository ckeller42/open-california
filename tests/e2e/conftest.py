"""Shared e2e helper: a Playwright page whose uncaught JS errors FAIL the test (see test_gui.py's
``page`` fixture for why every e2e test doubles as a runtime-error detector)."""

import contextlib

import pytest


@contextlib.contextmanager
def _error_gated_page(url, engine="chromium", init_script=None, requests=None, allow_errors=(), **page_kw):
    """``engine``: a Playwright browser type name (``chromium`` default, ``webkit``, ``firefox``);
    ``init_script``: JS run before the page's own scripts; ``requests``: a list that gets every request's URL;
    ``allow_errors``: texts of console errors the test expects (e.g. a failed request it provoked)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = getattr(p, engine).launch()
        pg = browser.new_page(**page_kw)
        js_errors = []
        pg.on("pageerror", lambda err: js_errors.append("pageerror: %s" % err))
        pg.on(
            "console",
            lambda msg: (
                js_errors.append("console.error: %s" % msg.text)
                if msg.type == "error" and not any(a in msg.text for a in ("favicon", *allow_errors))
                else None
            ),
        )
        if init_script:
            pg.add_init_script(init_script)
        if requests is not None:
            pg.on("request", lambda r: requests.append(r.url))
        pg.goto(url)
        try:
            yield pg
        finally:
            browser.close()
        assert not js_errors, "uncaught JS errors during this test:\n  " + "\n  ".join(js_errors)


@pytest.fixture
def error_gated_page():
    """``with error_gated_page(url, locale=...) as page:`` — fails on any pageerror/console.error."""
    return _error_gated_page
